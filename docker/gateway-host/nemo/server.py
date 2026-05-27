"""NeMo Guardrails DaaS server for the AI lab.

Exposes a Detection-as-a-Service endpoint that LiteLLM's custom guardrail
(../nemo_guardrail.py) calls pre-call (user prompt) and post-call (completion).

Design (ADR-003):
  * The AUTHORITATIVE block decision comes from detectors.py — pure, deterministic,
    unit-tested (tests/test_detectors.py). This guarantees enforcement.
  * NeMo Guardrails runs the input/output rails (rails-only / DaaS mode, no main
    LLM generation) to produce the `activated_rails` audit record and refusal
    message. The same detectors back the NeMo custom actions, so the two agree.
  * If NeMo fails to initialize for any reason, the service still enforces via the
    detectors and logs a warning (fail-closed on detection, not on framework).

Every decision is logged as one JSON line to stdout AND /var/log/nemo/decisions.log
(logrotate config ships with the image). Matched values are pre-redacted by the
detectors, so raw secrets/PII never reach the log (threat model AI-3/AI-5).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from typing import List, Literal, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

import detectors

CONFIG_PATH = os.environ.get("NEMO_CONFIG_PATH", "/app/config")
DECISION_LOG = os.environ.get("NEMO_DECISION_LOG", "/var/log/nemo/decisions.log")

# --------------------------------------------------------------------------- #
# structured decision log (JSON lines, rotated)
# --------------------------------------------------------------------------- #
_log = logging.getLogger("nemo.decisions")
_log.setLevel(logging.INFO)
_stdout = logging.StreamHandler(sys.stdout)
_stdout.setFormatter(logging.Formatter("%(message)s"))
_log.addHandler(_stdout)
try:
    os.makedirs(os.path.dirname(DECISION_LOG), exist_ok=True)
    _fileh = logging.handlers.RotatingFileHandler(
        DECISION_LOG, maxBytes=10_000_000, backupCount=5)
    _fileh.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_fileh)
except OSError:
    pass  # stdout is enough if the volume isn't mounted yet


# --------------------------------------------------------------------------- #
# NeMo Guardrails (best-effort; detectors are authoritative)
# --------------------------------------------------------------------------- #
_rails = None
_nemo_error: Optional[str] = None


def _init_nemo() -> None:
    global _rails, _nemo_error
    try:
        from nemoguardrails import LLMRails, RailsConfig

        async def detect_input(text: str = "") -> bool:
            return len(detectors.scan_input(text or "")) > 0

        async def detect_output(text: str = "") -> bool:
            return len(detectors.scan_output(text or "")) > 0

        config = RailsConfig.from_path(CONFIG_PATH)
        rails = LLMRails(config)
        rails.register_action(detect_input, name="detect_input")
        rails.register_action(detect_output, name="detect_output")
        _rails = rails
    except Exception as exc:  # noqa: BLE001 - never let NeMo init crash the service
        _nemo_error = f"{type(exc).__name__}: {exc}"


async def _nemo_activated_rails(role: str, content: str) -> List[dict]:
    """Run only the relevant rail and return which rails activated (for the log)."""
    if _rails is None:
        return []
    rail = "input" if role == "user" else "output"
    try:
        resp = await _rails.generate_async(
            messages=[{"role": role, "content": content}],
            options={"rails": [rail], "log": {"activated_rails": True}},
        )
        log = getattr(resp, "log", None)
        activated = getattr(log, "activated_rails", []) if log else []
        return [{"type": getattr(r, "type", None), "name": getattr(r, "name", None)}
                for r in activated]
    except Exception as exc:  # noqa: BLE001
        return [{"type": "error", "name": f"nemo_runtime:{type(exc).__name__}"}]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
app = FastAPI(title="ai-lab NeMo Guardrails DaaS", version="1.0")


class CheckRequest(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = Field(default="")
    request_id: Optional[str] = None
    caller_role: Optional[str] = None  # x-caller-role passthrough (audit only)


class CheckResponse(BaseModel):
    blocked: bool
    findings: list
    activated_rails: list
    message: Optional[str] = None


@app.on_event("startup")
async def _startup() -> None:
    _init_nemo()
    _log.info(json.dumps({"event": "startup", "nemo_enabled": _rails is not None,
                          "nemo_error": _nemo_error}))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "nemo_enabled": _rails is not None}


@app.post("/v1/guardrail/check", response_model=CheckResponse)
async def check(req: CheckRequest) -> CheckResponse:
    t0 = time.time()
    findings = (detectors.scan_input(req.content) if req.role == "user"
                else detectors.scan_output(req.content))
    activated = await _nemo_activated_rails(req.role, req.content)
    blocked = len(findings) > 0
    message = None
    if blocked:
        cats = sorted({f.category for f in findings})
        message = ("Request blocked by the AI-lab guardrail "
                   f"({', '.join(cats)}).")

    _log.info(json.dumps({
        "event": "guardrail_decision",
        "ts": round(t0, 3),
        "duration_ms": round((time.time() - t0) * 1000, 1),
        "role": req.role,
        "request_id": req.request_id,
        "caller_role": req.caller_role,
        "blocked": blocked,
        "findings": [f.as_dict() for f in findings],   # values are pre-redacted
        "activated_rails": activated,
    }))

    return CheckResponse(blocked=blocked,
                         findings=[f.as_dict() for f in findings],
                         activated_rails=activated, message=message)


def _cli_health() -> int:
    """`python server.py --health` for the container HEALTHCHECK (no HTTP needed)."""
    print(json.dumps({"status": "ok"}))
    return 0


if __name__ == "__main__":
    if "--health" in sys.argv:
        sys.exit(_cli_health())
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
