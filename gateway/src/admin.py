"""Admin control-plane API (Phase 2) — manage teams, keys, budgets, spend.

Master-key protected (Bearer == GATEWAY_MASTER_KEY); if no master key is
configured the whole surface is closed. In the lab this sits behind Cloudflare
Access + Okta (lab-admins) too — defense in depth, the same posture as LiteLLM's
ui_access_mode=admin_only.

Semantics mirror scripts/provision-org.sh so that script can repoint here:
org == team, tier dev|gov (ADR-014), per-team/per-key budgets + model
allow-lists, and the ADR-018 gov approval gate (a gov team requires approved_by).
"""

from __future__ import annotations

import json
import secrets
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .store import Store


class TenantCreate(BaseModel):
    name: str
    tier: str = Field(default="dev")
    plan: Optional[str] = None
    contact_email: Optional[str] = None


class PlanBody(BaseModel):
    plan: Optional[str] = None


class TeamCreate(BaseModel):
    alias: str
    tier: str = Field(default="dev")
    max_budget: Optional[float] = None
    soft_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    approved_by: Optional[str] = None
    tenant_id: Optional[str] = None


class KeyCreate(BaseModel):
    team_id: Optional[str] = None
    alias: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    max_budget: Optional[float] = None
    expires_at: Optional[str] = None
    rpm_limit: Optional[int] = None


class OnboardRequest(BaseModel):
    org: str
    email: Optional[str] = None
    use_case: Optional[str] = None
    tier: str = Field(default="dev")
    boundary: Optional[str] = None
    max_budget: Optional[float] = None
    rpm_limit: Optional[int] = None


class DecisionBody(BaseModel):
    approved_by: Optional[str] = None


def _store(request: Request) -> Store:
    return request.app.state.store


def _require_admin(request: Request) -> None:
    master = request.app.state.settings.master_key
    if not master:
        raise HTTPException(status_code=401, detail={"error": {
            "message": "Admin API disabled (no master key configured).",
            "type": "authentication_error", "code": "admin_disabled"}})
    auth = request.headers.get("authorization", "")
    presented = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not (presented and secrets.compare_digest(presented, master)):
        raise HTTPException(status_code=401, detail={"error": {
            "message": "Admin authentication required.",
            "type": "authentication_error", "code": "unauthorized"}})


def build_router() -> APIRouter:
    r = APIRouter(prefix="/admin", tags=["admin"])

    # -- tenants -----------------------------------------------------------
    # The tenant is the customer isolation boundary; teams/keys/spend/audit hang
    # off it. Suspending a tenant refuses all its keys at the auth gate (control.py).
    @r.post("/tenants")
    async def create_tenant(body: TenantCreate, request: Request):
        _require_admin(request)
        return _store(request).create_tenant(
            name=body.name, tier=body.tier, plan=body.plan,
            contact_email=body.contact_email)

    @r.get("/tenants")
    async def list_tenants(request: Request):
        _require_admin(request)
        return {"data": _store(request).list_tenants()}

    @r.get("/tenants/{tenant_id}")
    async def get_tenant(tenant_id: str, request: Request):
        _require_admin(request)
        t = _store(request).get_tenant(tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail={"error": {
                "message": "tenant not found", "type": "invalid_request_error"}})
        return t

    @r.post("/tenants/{tenant_id}/suspend")
    async def suspend_tenant(tenant_id: str, request: Request):
        _require_admin(request)
        if not _store(request).set_tenant_status(tenant_id, "suspended"):
            raise HTTPException(status_code=404, detail={"error": {
                "message": "tenant not found", "type": "invalid_request_error"}})
        return {"id": tenant_id, "status": "suspended"}

    @r.post("/tenants/{tenant_id}/activate")
    async def activate_tenant(tenant_id: str, request: Request):
        _require_admin(request)
        if not _store(request).set_tenant_status(tenant_id, "active"):
            raise HTTPException(status_code=404, detail={"error": {
                "message": "tenant not found", "type": "invalid_request_error"}})
        return {"id": tenant_id, "status": "active"}

    @r.get("/tenants/{tenant_id}/teams")
    async def tenant_teams(tenant_id: str, request: Request):
        _require_admin(request)
        return {"data": _store(request).list_teams(tenant_id=tenant_id)}

    @r.get("/tenants/{tenant_id}/keys")
    async def tenant_keys(tenant_id: str, request: Request):
        _require_admin(request)
        return {"data": _store(request).list_keys(tenant_id=tenant_id)}

    @r.get("/tenants/{tenant_id}/usage")
    async def tenant_usage(tenant_id: str, request: Request):
        _require_admin(request)
        if not _store(request).get_tenant(tenant_id):
            raise HTTPException(status_code=404, detail={"error": {
                "message": "tenant not found", "type": "invalid_request_error"}})
        return _store(request).tenant_usage(tenant_id)

    # -- billing: plans, usage -> invoice, payment-provider sync -----------
    @r.get("/plans")
    async def list_plans(request: Request):
        _require_admin(request)
        from . import billing
        return {"data": billing.list_plans()}

    @r.post("/tenants/{tenant_id}/plan")
    async def set_plan(tenant_id: str, body: PlanBody, request: Request):
        _require_admin(request)
        from . import billing
        store = _store(request)
        if not store.get_tenant(tenant_id):
            raise HTTPException(status_code=404, detail={"error": {
                "message": "tenant not found", "type": "invalid_request_error"}})
        if body.plan is not None and not billing.is_known_plan(body.plan):
            raise HTTPException(status_code=400, detail={"error": {
                "message": f"unknown plan '{body.plan}'", "type": "invalid_request_error"}})
        store.set_tenant_plan(tenant_id, body.plan)
        return store.get_tenant(tenant_id)

    def _invoice_for(request: Request, tenant_id: str, period: Optional[str]):
        from . import billing
        store = _store(request)
        tenant = store.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail={"error": {
                "message": "tenant not found", "type": "invalid_request_error"}})
        try:
            since, until = billing.month_window(period)
        except ValueError:
            raise HTTPException(status_code=400, detail={"error": {
                "message": "period must be 'YYYY-MM'", "type": "invalid_request_error"}})
        usage = store.tenant_usage(tenant_id, since=since, until=until)
        plan = billing.resolve_plan(tenant.get("plan"))
        return billing.build_invoice(tenant=tenant, usage=usage, plan=plan,
                                     since=since, until=until)

    @r.get("/tenants/{tenant_id}/invoice")
    async def tenant_invoice(tenant_id: str, request: Request,
                             period: Optional[str] = None):
        _require_admin(request)
        return _invoice_for(request, tenant_id, period)

    @r.post("/tenants/{tenant_id}/invoice/sync")
    async def sync_invoice(tenant_id: str, request: Request,
                           period: Optional[str] = None):
        _require_admin(request)
        from . import billing
        invoice = _invoice_for(request, tenant_id, period)
        result = billing.provider_from_env().sync_invoice(invoice)
        return {"invoice": invoice, "sync": result}

    # -- teams -------------------------------------------------------------
    @r.post("/teams")
    async def create_team(body: TeamCreate, request: Request):
        _require_admin(request)
        if body.tier == "gov" and not body.approved_by:
            raise HTTPException(status_code=400, detail={"error": {
                "message": "gov-tier team requires approved_by (ADR-018 approval gate).",
                "type": "invalid_request_error", "code": "approval_required"}})
        if body.tenant_id and not _store(request).get_tenant(body.tenant_id):
            raise HTTPException(status_code=400, detail={"error": {
                "message": f"unknown tenant_id '{body.tenant_id}'",
                "type": "invalid_request_error"}})
        return _store(request).create_team(
            alias=body.alias, tier=body.tier, max_budget=body.max_budget,
            soft_budget=body.soft_budget, budget_duration=body.budget_duration,
            models=body.models, approved_by=body.approved_by, tenant_id=body.tenant_id)

    @r.get("/teams")
    async def list_teams(request: Request):
        _require_admin(request)
        return {"data": _store(request).list_teams()}

    @r.get("/teams/{team_id}")
    async def get_team(team_id: str, request: Request):
        _require_admin(request)
        team = _store(request).get_team(team_id)
        if not team:
            raise HTTPException(status_code=404, detail={"error": {
                "message": "team not found", "type": "invalid_request_error"}})
        return team

    # -- keys --------------------------------------------------------------
    @r.post("/keys")
    async def create_key(body: KeyCreate, request: Request):
        _require_admin(request)
        if body.team_id and not _store(request).get_team(body.team_id):
            raise HTTPException(status_code=400, detail={"error": {
                "message": f"unknown team_id '{body.team_id}'",
                "type": "invalid_request_error"}})
        # The plaintext key is in the response ONCE — deliver over a secure channel.
        return _store(request).create_key(
            team_id=body.team_id, alias=body.alias, models=body.models,
            max_budget=body.max_budget, expires_at=body.expires_at,
            rpm_limit=body.rpm_limit)

    @r.get("/keys")
    async def list_keys(request: Request, team_id: Optional[str] = None):
        _require_admin(request)
        return {"data": _store(request).list_keys(team_id)}

    @r.get("/keys/{key_id}")
    async def get_key(key_id: str, request: Request):
        _require_admin(request)
        key = _store(request).get_key(key_id)
        if not key:
            raise HTTPException(status_code=404, detail={"error": {
                "message": "key not found", "type": "invalid_request_error"}})
        return key

    @r.delete("/keys/{key_id}")
    async def revoke_key(key_id: str, request: Request):
        _require_admin(request)
        if not _store(request).revoke_key(key_id):
            raise HTTPException(status_code=404, detail={"error": {
                "message": "key not found", "type": "invalid_request_error"}})
        return {"id": key_id, "revoked": True}

    # -- spend -------------------------------------------------------------
    @r.get("/spend")
    async def spend(request: Request):
        _require_admin(request)
        return _store(request).spend_summary()

    # -- audit & evidence: the append-only request ledger (the compliance story).
    # Reads the JSON-lines audit log the façade writes for every request and returns
    # the most recent rows, newest first. Identity is already a non-reversible
    # fingerprint and no prompt/response content is logged (see audit.py).
    @r.get("/audit")
    async def audit(request: Request, limit: int = 100):
        _require_admin(request)
        path = request.app.state.settings.audit_log
        limit = max(1, min(limit, 1000))
        rows: List[dict] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for ln in f.readlines()[-limit:]:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rows.append(json.loads(ln))
                    except ValueError:
                        pass
        except FileNotFoundError:
            pass
        rows.reverse()  # newest first
        return {"data": rows}

    # -- audit integrity: verify the tamper-evident hash chain -------------
    @r.get("/audit/verify")
    async def audit_verify(request: Request):
        _require_admin(request)
        from .audit import verify_chain
        return verify_chain(request.app.state.settings.audit_log)

    # -- onboarding: access requests -> approve -> provisioned scoped key ---
    @r.get("/requests")
    async def list_requests(request: Request):
        _require_admin(request)
        return {"data": _store(request).list_requests()}

    @r.post("/requests")
    async def admin_create_request(body: OnboardRequest, request: Request):
        _require_admin(request)
        return _store(request).create_request(
            org=body.org, email=body.email, use_case=body.use_case, tier=body.tier,
            boundary=body.boundary, max_budget=body.max_budget, rpm_limit=body.rpm_limit)

    @r.post("/requests/{rid}/approve")
    async def approve_request(rid: str, body: DecisionBody, request: Request):
        _require_admin(request)
        store = _store(request)
        req = store.get_request(rid)
        if not req:
            raise HTTPException(status_code=404, detail={"error": {
                "message": "request not found", "type": "invalid_request_error"}})
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail={"error": {
                "message": f"request already {req['status']}", "type": "invalid_request_error"}})
        approver = (body.approved_by or "admin").strip()
        if req["tier"] == "gov" and not approver:
            raise HTTPException(status_code=400, detail={"error": {
                "message": "gov-tier approval requires approved_by (ADR-018).",
                "type": "invalid_request_error", "code": "approval_required"}})
        # Approving the request IS the provisioning step: stand up the org as a
        # first-class TENANT, then a team + a scoped key under it, in one action.
        # The gov approval gate is satisfied by approved_by.
        tenant = store.create_tenant(name=req["org"], tier=req["tier"],
                                     contact_email=req.get("email"))
        team = store.create_team(alias=req["org"], tier=req["tier"],
                                 max_budget=req["max_budget"], tenant_id=tenant["id"],
                                 approved_by=approver if req["tier"] == "gov" else None)
        key = store.create_key(team_id=team["id"], tenant_id=tenant["id"],
                               alias=f"{req['org']}-key",
                               max_budget=req["max_budget"], rpm_limit=req["rpm_limit"])
        store.mark_request(rid, status="approved", decided_by=approver,
                           tenant_id=tenant["id"], team_id=team["id"], key_id=key["id"])
        # The plaintext key is returned ONCE — deliver to the org over a secure channel.
        return {"request_id": rid, "status": "approved", "org": req["org"],
                "tenant_id": tenant["id"], "team_id": team["id"], "key": key["key"]}

    @r.post("/requests/{rid}/reject")
    async def reject_request(rid: str, body: DecisionBody, request: Request):
        _require_admin(request)
        store = _store(request)
        if not store.get_request(rid):
            raise HTTPException(status_code=404, detail={"error": {
                "message": "request not found", "type": "invalid_request_error"}})
        store.mark_request(rid, status="rejected", decided_by=(body.approved_by or "admin"))
        return {"request_id": rid, "status": "rejected"}

    # -- live compliance evidence (control map computed from running signals) --
    @r.get("/compliance")
    async def compliance(request: Request):
        _require_admin(request)
        from . import compliance as comp
        return comp.assess(request.app.state.settings.audit_log, _store(request))

    # -- models (proxied from the engine so the branded UI shows them too) --
    @r.get("/models")
    async def models(request: Request):
        _require_admin(request)
        cfg = request.app.state.settings
        up = request.app.state.upstream
        bearer = cfg.upstream_key or cfg.master_key
        try:
            resp = await up.get("/v1/models", {"authorization": f"Bearer {bearer}"})
            data = resp.json()
        except Exception:  # noqa: BLE001
            return {"data": [], "error": "upstream models unavailable"}
        rows = data.get("data", []) if isinstance(data, dict) else []
        # Tag gov-tier models by id prefix so the UI can surface the gov story.
        return {"data": [{"id": m.get("id"),
                          "tier": "gov" if str(m.get("id", "")).startswith("gov/") else "dev"}
                         for m in rows if isinstance(m, dict)]}

    return r
