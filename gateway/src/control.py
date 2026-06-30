"""Control-plane enforcement (Phase 2): authorize a key, enforce budgets, bill.

This is the logic that makes the façade the source of truth instead of LiteLLM:
given a presented virtual key + the requested model, decide allow/deny against
OUR store (active? expired? model allowed? budget remaining?), and after a
successful call, record spend against the key and its team.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from . import pricing
from .store import Store


class _RateLimiter:
    """In-process per-key sliding-window request limiter (requests/min). The façade
    runs a single uvicorn worker, so this is authoritative for the deployment."""

    def __init__(self) -> None:
        self._win: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(self, key_id: str, rpm: int) -> bool:
        if not rpm or rpm <= 0:
            return True
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            dq = self._win[key_id]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= rpm:
                return False
            dq.append(now)
            return True


_RATE = _RateLimiter()


class Denied(Exception):
    """An authorization/budget denial, carrying an HTTP shape for the API."""
    def __init__(self, status: int, code: str, message: str,
                 type_: str = "authentication_error") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.type = type_

    def as_detail(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}


def _expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp
    except ValueError:
        return False  # unparseable expiry -> treat as no expiry (don't lock out)


def bootstrap(store: Store, *, team_alias: str, plaintext_key: str) -> Dict[str, Any]:
    """Idempotently seed a default team + a key with a known plaintext, so a
    control-plane-on deployment is usable on first boot. Safe to run every start.
    The team has no model allow-list (all configured models permitted) and no
    budget cap; operators mint scoped/budgeted keys afterward via the admin API."""
    teams = [t for t in store.list_teams() if t["alias"] == team_alias]
    team = teams[0] if teams else store.create_team(alias=team_alias, tier="dev")
    store.create_key_with_plaintext(plaintext_key, team_id=team["id"],
                                    alias=f"{team_alias}-bootstrap")
    return team


def authorize(store: Store, plaintext_key: str, model: Optional[str],
              enforce_budget: bool = True) -> Dict[str, Any]:
    """Return {'key':..., 'team':...} or raise Denied.

    enforce_budget=False skips the spend ceilings (e.g. for /v1/models listing,
    which costs nothing and shouldn't be locked out by an exhausted budget).
    """
    key = store.get_key_by_plaintext(plaintext_key)
    if key is None:
        raise Denied(401, "invalid_key", "Unknown virtual key.")
    if not key["active"]:
        raise Denied(401, "key_revoked", "Virtual key has been revoked.")
    if _expired(key.get("expires_at")):
        raise Denied(401, "key_expired", "Virtual key has expired.")

    team = store.get_team(key["team_id"]) if key.get("team_id") else None
    tenant = store.get_tenant(key["tenant_id"]) if key.get("tenant_id") else None

    # Tenant lifecycle — a suspended organization is refused at the wire, before
    # any model/budget check (suspension is a real lever, not just a flag). This
    # gates even cost-free listing, so a suspended customer reaches nothing.
    if tenant and tenant.get("status") not in (None, "active"):
        raise Denied(403, "tenant_suspended",
                     f"Organization '{tenant.get('name')}' is suspended.",
                     type_="permission_error")

    # Model allow-list: key list wins; else inherit the team's list. Empty == all.
    allow = key["models"] or (team["models"] if team else [])
    if model and allow and model not in allow:
        raise Denied(403, "model_not_allowed",
                     f"Key is not permitted to use model '{model}'.",
                     type_="permission_error")

    # Budgets — hard ceilings. Check both the key and its team.
    if not enforce_budget:
        return {"key": key, "team": team, "tenant": tenant}
    # Rate limit (per-key requests/min) — shed a flood before budget/upstream work.
    rpm = key.get("rpm_limit")
    if rpm and not _RATE.check_and_record(key["id"], int(rpm)):
        raise Denied(429, "rate_limited",
                     f"Rate limit exceeded ({rpm} requests/min for this key).",
                     type_="rate_limit_error")
    if key.get("max_budget") is not None and key["spend"] >= key["max_budget"]:
        raise Denied(400, "budget_exceeded",
                     "Key budget exhausted.", type_="budget_exceeded")
    if team and team.get("max_budget") is not None and team["spend"] >= team["max_budget"]:
        raise Denied(400, "budget_exceeded",
                     f"Team '{team['alias']}' budget exhausted.", type_="budget_exceeded")

    return {"key": key, "team": team, "tenant": tenant}


def usage_tokens(data: Any) -> Tuple[int, int]:
    u = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(u, dict):
        return 0, 0
    return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)


def record(store: Store, *, request_id: Optional[str], authz: Dict[str, Any],
           model: Optional[str], prompt_tokens: int, completion_tokens: int) -> float:
    cost = pricing.cost_usd(model or "", prompt_tokens, completion_tokens)
    key = authz.get("key") or {}
    team = authz.get("team") or {}
    tenant = authz.get("tenant") or {}
    store.record_spend(
        request_id=request_id, key_id=key.get("id"), team_id=team.get("id") or None,
        tenant_id=tenant.get("id") or None,
        model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        cost=cost)
    return cost
