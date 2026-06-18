"""Compliance MCP server (read-only — ADR-005).

FastMCP over streamable HTTP (default) or stdio (MCP_TRANSPORT=stdio). Six tools:
  sam_gov_lookup, federal_register_search, nist_control_lookup, poam_list,
  poam_summary, cmmc_level2_self_assess_status

All read-only. LiteLLM forwards an x-caller-role header (used for the admin-gated
PII unmask) and x-mcp-auth (hashed for the audit log only). Structured JSON logs
go to stdout via structlog: tool_name, caller_virtual_key_hash, duration_ms,
status, redacted_args.

The tool LOGIC lives in module-level `_do_*` helpers so it is unit-testable
without the MCP runtime; the @mcp.tool() wrappers in build_mcp() just delegate.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import sys
import time
from typing import Optional

import structlog

from . import data_store
from .fedreg_client import FederalRegisterClient
from .sam_client import CircuitOpenError, SAMClient, classify_identifier, redact_entity

# --- structured logging ------------------------------------------------------
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger("compliance-mcp")

# --- per-request caller context (set by ASGI middleware) ---------------------
_caller_role: contextvars.ContextVar[str] = contextvars.ContextVar("caller_role", default="")
_caller_auth: contextvars.ContextVar[str] = contextvars.ContextVar("caller_auth", default="")

ADMIN_ROLES = {"proxy_admin", "lab-admins", "admin"}


def _is_admin() -> bool:
    return _caller_role.get("").strip().lower() in ADMIN_ROLES


def _vkey_hash() -> str:
    raw = _caller_auth.get("")
    if not raw:
        return "anonymous"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _audit(tool: str, status: str, t0: float, **redacted_args) -> None:
    log.info("tool_call", tool_name=tool, status=status,
             caller_virtual_key_hash=_vkey_hash(),
             caller_role=_caller_role.get("") or "unknown",
             duration_ms=round((time.time() - t0) * 1000, 1),
             redacted_args=redacted_args)


# --- SAM client (lazy; api key from env; settable for tests) -----------------
_sam: Optional[SAMClient] = None


def _sam_client() -> SAMClient:
    global _sam
    if _sam is None:
        _sam = SAMClient(api_key=os.environ.get("SAM_GOV_API_KEY", ""))
    return _sam


# --- Federal Register client (lazy; keyless; settable for tests) -------------
_fedreg: Optional[FederalRegisterClient] = None


def _fedreg_client() -> FederalRegisterClient:
    global _fedreg
    if _fedreg is None:
        _fedreg = FederalRegisterClient()
    return _fedreg


# --- tool logic (testable; no MCP runtime needed) ----------------------------
async def _do_sam_gov_lookup(uei_or_cage: str, include_pii: bool = False) -> dict:
    t0 = time.time()
    try:
        classify_identifier(uei_or_cage)  # validate shape early
        entity = await _sam_client().lookup(uei_or_cage)
        if entity is None:
            _audit("sam_gov_lookup", "not_found", t0, include_pii=include_pii)
            return {"found": False, "message": "No SAM.gov entity matched."}
        entity = redact_entity(entity, include_pii=include_pii, is_admin=_is_admin())
        _audit("sam_gov_lookup", "ok", t0,
               pii_unmasked=entity.pii_included, include_pii=include_pii)
        return {"found": True, "entity": entity.model_dump()}
    except ValueError as exc:
        _audit("sam_gov_lookup", "invalid_input", t0, error=str(exc))
        return {"found": False, "error": str(exc)}
    except CircuitOpenError as exc:
        _audit("sam_gov_lookup", "circuit_open", t0, error=str(exc))
        return {"found": False, "error": "SAM.gov temporarily unavailable."}
    except Exception as exc:  # noqa: BLE001
        _audit("sam_gov_lookup", "error", t0, error=type(exc).__name__)
        return {"found": False, "error": "lookup failed"}


async def _do_federal_register_search(term: str, doc_type: Optional[str] = None,
                                      agency: Optional[str] = None,
                                      per_page: int = 5) -> dict:
    t0 = time.time()
    try:
        docs = await _fedreg_client().search(term, doc_type=doc_type,
                                             agency=agency, per_page=per_page)
        _audit("federal_register_search", "ok", t0,
               term=term, doc_type=doc_type, agency=agency, count=len(docs))
        return {"count": len(docs),
                "documents": [d.model_dump() for d in docs]}
    except ValueError as exc:
        _audit("federal_register_search", "invalid_input", t0, error=str(exc))
        return {"count": 0, "error": str(exc)}
    except CircuitOpenError as exc:
        _audit("federal_register_search", "circuit_open", t0, error=str(exc))
        return {"count": 0, "error": "Federal Register temporarily unavailable."}
    except Exception as exc:  # noqa: BLE001
        _audit("federal_register_search", "error", t0, error=type(exc).__name__)
        return {"count": 0, "error": "search failed"}


async def _do_nist_control_lookup(control_id: str) -> dict:
    t0 = time.time()
    ctrl = data_store.nist_control_lookup(control_id)
    if ctrl is None:
        _audit("nist_control_lookup", "not_found", t0, control_id=control_id)
        return {"found": False,
                "available": data_store.nist_available_controls(),
                "message": f"{control_id} not in the shipped subset."}
    _audit("nist_control_lookup", "ok", t0, control_id=ctrl.id)
    return {"found": True, "control": ctrl.model_dump()}


async def _do_poam_list(status_filter: Optional[str] = None) -> dict:
    t0 = time.time()
    try:
        items = data_store.poam_list(status_filter)
    except ValueError as exc:
        _audit("poam_list", "invalid_input", t0, status_filter=status_filter)
        return {"error": str(exc)}
    _audit("poam_list", "ok", t0, status_filter=status_filter, count=len(items))
    return {"count": len(items), "poams": [p.model_dump() for p in items]}


async def _do_poam_summary() -> dict:
    t0 = time.time()
    s = data_store.poam_summary()
    _audit("poam_summary", "ok", t0, total=s.total)
    return s.model_dump()


async def _do_cmmc_status() -> dict:
    t0 = time.time()
    status = data_store.cmmc_level2_status()
    _audit("cmmc_level2_self_assess_status", "ok", t0,
           implemented=status.implemented, total=status.total_practices)
    return status.model_dump()


# --- FastMCP wiring ----------------------------------------------------------
def build_mcp():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("compliance", stateless_http=True, json_response=True)

    @mcp.tool()
    async def sam_gov_lookup(uei_or_cage: str, include_pii: bool = False) -> dict:
        """Look up a federal entity in SAM.gov by UEI (12 char) or CAGE (5 char).
        POC email/phone are redacted unless include_pii=true AND caller is admin."""
        return await _do_sam_gov_lookup(uei_or_cage, include_pii)

    @mcp.tool()
    async def federal_register_search(term: str, doc_type: Optional[str] = None,
                                      agency: Optional[str] = None,
                                      per_page: int = 5) -> dict:
        """Search the Federal Register (federalregister.gov, public record) for
        recent rules and notices. `term` is required free text; optional
        `doc_type` is one of rule|proposed|notice|presidential; optional `agency`
        is an agency slug (e.g. 'defense-department'); `per_page` is 1-20.
        Read-only, keyless, newest-first."""
        return await _do_federal_register_search(term, doc_type, agency, per_page)

    @mcp.tool()
    async def nist_control_lookup(control_id: str) -> dict:
        """NIST SP 800-53 Rev 5 control text, related controls, CMMC L2 mapping
        (e.g. control_id='AC-2')."""
        return await _do_nist_control_lookup(control_id)

    @mcp.tool()
    async def poam_list(status_filter: Optional[str] = None) -> dict:
        """List POA&Ms, optionally filtered by status
        (open|in_progress|completed|risk_accepted). Read-only."""
        return await _do_poam_list(status_filter)

    @mcp.tool()
    async def poam_summary() -> dict:
        """Aggregate POA&M counts by severity and by status. Read-only."""
        return await _do_poam_summary()

    @mcp.tool()
    async def cmmc_level2_self_assess_status() -> dict:
        """CMMC 2.0 Level 2 self-assessment progress dashboard (illustrative
        lab data — not an attestation)."""
        return await _do_cmmc_status()

    return mcp


# --- ASGI middleware: capture caller headers into contextvars ----------------
def build_app():
    from starlette.middleware.base import BaseHTTPMiddleware

    mcp = build_mcp()
    app = mcp.streamable_http_app()

    class CallerContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            role_tok = _caller_role.set(request.headers.get("x-caller-role", ""))
            auth_tok = _caller_auth.set(
                request.headers.get("x-mcp-auth")
                or request.headers.get("authorization", ""))
            try:
                return await call_next(request)
            finally:
                _caller_role.reset(role_tok)
                _caller_auth.reset(auth_tok)

    app.add_middleware(CallerContextMiddleware)
    return app


def main() -> None:
    if "--health" in sys.argv:
        print('{"status": "ok"}')
        return
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        build_mcp().run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run(build_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
