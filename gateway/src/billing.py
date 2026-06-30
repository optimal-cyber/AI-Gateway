"""Billing (Phase 2) — plans, usage→invoice, and a pluggable payment provider.

The metering layer (control.py + store.spend_log) produces authoritative-or-flagged
per-tenant usage. This module turns that usage into an invoice under a plan, and
syncs it to a payment provider behind an adapter:

  - ConsoleProvider (default): records the invoice locally — usable today, no keys.
  - StripeProvider: live when STRIPE_API_KEY is set. The Stripe-shaped payload is a
    pure, tested function; the send is dependency-injected so it's tested too. NOT a
    faked charge — without a key it returns `not_configured`.

Invoice = base_monthly + (raw_usage * (1 + markup%) − included_usage credit). The
`estimated` portion of usage (fallback-priced, see pricing.py) is surfaced on the
invoice so a non-authoritative figure is never silently billed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

_log = logging.getLogger("gateway.billing")

# Plan catalog. base_monthly = flat platform fee; usage_markup_pct = markup on raw
# provider cost; included_usage = USD of (marked-up) usage included before overage.
_DEFAULT_PLANS: Dict[str, Dict[str, Any]] = {
    "dev":     {"name": "Dev (free)",   "base_monthly": 0.0,    "usage_markup_pct": 0.0,  "included_usage": 0.0},
    "starter": {"name": "Starter",      "base_monthly": 99.0,   "usage_markup_pct": 20.0, "included_usage": 50.0},
    "pro":     {"name": "Pro",          "base_monthly": 499.0,  "usage_markup_pct": 15.0, "included_usage": 500.0},
    "gov":     {"name": "Government",   "base_monthly": 2500.0, "usage_markup_pct": 25.0, "included_usage": 1000.0},
}
# Used when a tenant has no plan (or an unknown one): pure pass-through, no markup.
_PASSTHROUGH = {"name": "Pass-through (unassigned)", "base_monthly": 0.0,
                "usage_markup_pct": 0.0, "included_usage": 0.0}


def _catalog() -> Dict[str, Dict[str, Any]]:
    raw = os.environ.get("GATEWAY_PLANS")
    if not raw:
        return _DEFAULT_PLANS
    try:
        return {**_DEFAULT_PLANS, **json.loads(raw)}
    except Exception:  # noqa: BLE001
        _log.warning("GATEWAY_PLANS is not valid JSON; using default catalog")
        return _DEFAULT_PLANS


def list_plans() -> List[Dict[str, Any]]:
    return [{"id": pid, **p} for pid, p in _catalog().items()]


def resolve_plan(plan_id: Optional[str]) -> Dict[str, Any]:
    cat = _catalog()
    if plan_id and plan_id in cat:
        return {"id": plan_id, **cat[plan_id]}
    return {"id": "passthrough", **_PASSTHROUGH}


def is_known_plan(plan_id: Optional[str]) -> bool:
    return bool(plan_id) and plan_id in _catalog()


def month_window(period: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """'YYYY-MM' -> (since_iso, until_iso) [start of month, start of next month).
    Empty/None -> (None, None) = all-time. Raises ValueError on a malformed period."""
    if not period:
        return None, None
    y, m = period.split("-")
    y, m = int(y), int(m)
    if not 1 <= m <= 12:
        raise ValueError("month out of range")
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=timezone.utc)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def build_invoice(*, tenant: Dict[str, Any], usage: Dict[str, Any],
                  plan: Dict[str, Any], since: Optional[str] = None,
                  until: Optional[str] = None) -> Dict[str, Any]:
    """Pure: turn a tenant + its metered usage + a plan into an invoice. The
    line items sum to `total`. The estimated (fallback-priced) usage is surfaced."""
    raw = round(float(usage.get("total_cost") or 0.0), 6)
    estimated = round(float(usage.get("estimated_cost") or 0.0), 6)
    markup_pct = float(plan.get("usage_markup_pct") or 0.0)
    included = float(plan.get("included_usage") or 0.0)
    base = float(plan.get("base_monthly") or 0.0)

    markup = round(raw * markup_pct / 100.0, 6)
    marked = round(raw + markup, 6)
    credit = round(min(included, marked), 6)
    total = round(base + marked - credit, 2)

    line_items: List[Dict[str, Any]] = [
        {"description": f"{plan['name']} plan — platform fee", "amount": round(base, 2)},
        {"description": "API usage (provider cost)", "amount": raw},
    ]
    if markup:
        line_items.append({"description": f"Usage markup ({markup_pct:g}%)", "amount": markup})
    if credit:
        line_items.append({"description": "Included usage credit", "amount": -credit})

    invoice: Dict[str, Any] = {
        "tenant_id": tenant.get("id"),
        "tenant_name": tenant.get("name"),
        "plan": {"id": plan.get("id"), "name": plan.get("name")},
        "currency": "USD",
        "period": {"since": since, "until": until},
        "line_items": line_items,
        "usage_detail": usage.get("by_model", []),
        "subtotal_usage_raw": raw,
        "estimated_usage": estimated,
        "total": total,
    }
    if estimated > 0:
        invoice["estimated_note"] = (
            f"${estimated:.4f} of usage was priced from the fallback rate "
            "(model not in the pricing table) — verify before final invoicing.")
    return invoice


def stripe_invoice_items(invoice: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stripe-shaped invoice items (amounts in integer cents). Pure + tested."""
    return [{"amount": int(round(li["amount"] * 100)), "currency": "usd",
             "description": li["description"]} for li in invoice["line_items"]]


class ConsoleProvider:
    """Default: record the invoice locally. Real and usable with no external keys."""
    name = "console"

    def sync_invoice(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        ref = "inv_" + hashlib.sha256(
            f"{invoice.get('tenant_id')}|{invoice.get('period')}".encode()).hexdigest()[:12]
        _log.info("billing(console): recorded invoice %s for %s — total $%.2f",
                  ref, invoice.get("tenant_name"), invoice.get("total", 0.0))
        return {"provider": "console", "status": "recorded",
                "invoice_ref": ref, "total": invoice.get("total")}


class StripeProvider:
    """Live when STRIPE_API_KEY is set. The send is injectable (so it's tested);
    without a key it returns not_configured rather than pretending to charge.

    NOTE: `customer` here uses tenant_id as a placeholder — mapping a tenant to a
    Stripe customer id (stored on the tenant) is the remaining wiring, plus the
    Stripe egress on the Squid allowlist (api.stripe.com)."""
    name = "stripe"

    def __init__(self, api_key: str, client: Optional[httpx.Client] = None,
                 base: str = "https://api.stripe.com") -> None:
        self._key = api_key
        self._client = client
        self._base = base

    def sync_invoice(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        if not self._key:
            return {"provider": "stripe", "status": "not_configured",
                    "reason": "set STRIPE_API_KEY to enable Stripe billing"}
        items = stripe_invoice_items(invoice)
        client = self._client or httpx.Client(timeout=30)
        sent = 0
        try:
            for it in items:
                r = client.post(
                    self._base + "/v1/invoiceitems",
                    data={"customer": invoice.get("tenant_id"), "amount": it["amount"],
                          "currency": it["currency"], "description": it["description"]},
                    headers={"Authorization": "Bearer " + self._key})
                if r.status_code >= 400:
                    return {"provider": "stripe", "status": "error", "sent": sent,
                            "detail": r.text[:300]}
                sent += 1
        finally:
            if self._client is None:
                client.close()
        return {"provider": "stripe", "status": "sent", "items": sent,
                "total": invoice.get("total")}


def provider_from_env(client: Optional[httpx.Client] = None):
    name = os.environ.get("GATEWAY_BILLING_PROVIDER", "console").lower()
    if name == "stripe":
        return StripeProvider(os.environ.get("STRIPE_API_KEY", ""), client=client)
    return ConsoleProvider()
