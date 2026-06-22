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

import secrets
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .store import Store


class TeamCreate(BaseModel):
    alias: str
    tier: str = Field(default="dev")
    max_budget: Optional[float] = None
    soft_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    approved_by: Optional[str] = None


class KeyCreate(BaseModel):
    team_id: Optional[str] = None
    alias: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    max_budget: Optional[float] = None
    expires_at: Optional[str] = None


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

    # -- teams -------------------------------------------------------------
    @r.post("/teams")
    async def create_team(body: TeamCreate, request: Request):
        _require_admin(request)
        if body.tier == "gov" and not body.approved_by:
            raise HTTPException(status_code=400, detail={"error": {
                "message": "gov-tier team requires approved_by (ADR-018 approval gate).",
                "type": "invalid_request_error", "code": "approval_required"}})
        return _store(request).create_team(
            alias=body.alias, tier=body.tier, max_budget=body.max_budget,
            soft_budget=body.soft_budget, budget_duration=body.budget_duration,
            models=body.models, approved_by=body.approved_by)

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
            max_budget=body.max_budget, expires_at=body.expires_at)

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
