"""Billing — plans, pure usage→invoice math, Stripe-shaped items, and the provider
adapter (Console default; Stripe live-when-keyed, with the send tested via a mock
client). Invoices are computed from real metered usage, never faked."""

import httpx

from src import billing

M = "sk-master"
MSG = [{"role": "user", "content": "hi"}]


def _usage(total, estimated=0.0, by_model=None):
    return {"total_cost": total, "estimated_cost": estimated, "by_model": by_model or []}


# -- plans -------------------------------------------------------------------

def test_resolve_known_and_passthrough():
    assert billing.resolve_plan("pro")["base_monthly"] == 499.0
    assert billing.resolve_plan(None)["id"] == "passthrough"
    assert billing.resolve_plan("nope")["id"] == "passthrough"   # unknown → safe pass-through


def test_list_plans_includes_ids():
    assert {"dev", "starter", "pro", "gov"} <= {p["id"] for p in billing.list_plans()}


# -- invoice math (pure; line items always sum to total) ---------------------

def test_passthrough_invoice_equals_raw_usage():
    inv = billing.build_invoice(tenant={"id": "t", "name": "Acme"},
                                usage=_usage(100.0), plan=billing.resolve_plan(None))
    assert inv["total"] == 100.0
    assert round(sum(li["amount"] for li in inv["line_items"]), 2) == inv["total"]


def test_starter_invoice_math():
    # base 99 + markup 20% on raw 100 (=20) − included-usage credit 50 = 169
    inv = billing.build_invoice(tenant={"id": "t", "name": "Acme"},
                                usage=_usage(100.0), plan=billing.resolve_plan("starter"))
    assert inv["total"] == 169.0
    assert round(sum(li["amount"] for li in inv["line_items"]), 2) == inv["total"]


def test_invoice_flags_estimated_usage():
    inv = billing.build_invoice(tenant={"id": "t", "name": "Acme"},
                                usage=_usage(40.0, estimated=30.0),
                                plan=billing.resolve_plan("dev"))
    assert inv["estimated_usage"] == 30.0 and "estimated_note" in inv


def test_stripe_items_in_cents():
    inv = billing.build_invoice(tenant={"id": "t", "name": "Acme"},
                                usage=_usage(100.0), plan=billing.resolve_plan("starter"))
    items = billing.stripe_invoice_items(inv)
    assert any(i["amount"] == 9900 and i["currency"] == "usd" for i in items)   # $99.00
    assert all(isinstance(i["amount"], int) for i in items)


# -- providers ---------------------------------------------------------------

def test_console_provider_records():
    inv = billing.build_invoice(tenant={"id": "t1", "name": "Acme"},
                                usage=_usage(10.0), plan=billing.resolve_plan("pro"))
    out = billing.ConsoleProvider().sync_invoice(inv)
    assert out["status"] == "recorded" and out["invoice_ref"].startswith("inv_")


def test_stripe_not_configured_without_key():
    assert billing.StripeProvider("").sync_invoice({"line_items": []})["status"] == "not_configured"


def test_stripe_posts_items_with_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "ii_x"})

    client = httpx.Client(transport=httpx.MockTransport(handler),
                          base_url="https://api.stripe.com")
    inv = billing.build_invoice(tenant={"id": "t9", "name": "Acme"},
                                usage=_usage(100.0), plan=billing.resolve_plan("starter"))
    out = billing.StripeProvider("sk_test_x", client=client).sync_invoice(inv)
    assert out["status"] == "sent" and out["items"] == len(inv["line_items"]) == len(calls)
    assert calls[0].headers["authorization"] == "Bearer sk_test_x"
    assert b"customer=t9" in calls[0].content


# -- period window + store filtering -----------------------------------------

def test_month_window():
    s, u = billing.month_window("2026-06")
    assert s.startswith("2026-06-01") and u.startswith("2026-07-01")
    assert billing.month_window("2026-12")[1].startswith("2027-01-01")
    assert billing.month_window(None) == (None, None)


def test_tenant_usage_period_filter(store):
    t = store.create_team(alias="Acme")
    tid = t["tenant_id"]
    with store._lock:
        for ts, cost in [("2026-05-15T00:00:00+00:00", 5.0),
                         ("2026-06-10T00:00:00+00:00", 7.0),
                         ("2026-06-20T00:00:00+00:00", 3.0)]:
            store._db.execute(
                "INSERT INTO spend_log(tenant_id,team_id,model,prompt_tokens,"
                "completion_tokens,cost,estimated,ts) VALUES(?,?,?,?,?,?,?,?)",
                (tid, t["id"], "gpt-4o", 0, 0, cost, 0, ts))
        store._db.commit()
    s, u = billing.month_window("2026-06")
    june = store.tenant_usage(tid, since=s, until=u)
    assert june["total_cost"] == 10.0 and june["total_requests"] == 2
    assert store.tenant_usage(tid)["total_cost"] == 15.0      # all-time


# -- end-to-end admin --------------------------------------------------------

def test_admin_billing_flow(make_client):
    c, app = make_client(control_plane=True, master_key=M, upstream_key="sk-up")
    h = {"Authorization": "Bearer " + M}
    store = app.state.store

    assert {p["id"] for p in c.get("/admin/plans", headers=h).json()["data"]} >= {"pro"}

    team = store.create_team(alias="Acme")
    key = store.create_key(team_id=team["id"])
    c.post("/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]},
           json={"model": "claude-opus-4-8", "messages": MSG})   # $0.09 raw usage
    tid = team["tenant_id"]

    r = c.post(f"/admin/tenants/{tid}/plan", headers=h, json={"plan": "pro"})
    assert r.status_code == 200 and r.json()["plan"] == "pro"
    assert c.post(f"/admin/tenants/{tid}/plan", headers=h,
                  json={"plan": "nope"}).status_code == 400          # unknown plan rejected

    inv = c.get(f"/admin/tenants/{tid}/invoice", headers=h).json()
    assert inv["plan"]["id"] == "pro" and inv["subtotal_usage_raw"] == 0.09
    assert inv["total"] == 499.0                                     # base; usage under included

    s = c.post(f"/admin/tenants/{tid}/invoice/sync", headers=h).json()
    assert s["sync"]["status"] == "recorded" and s["invoice"]["total"] == 499.0


def test_admin_invoice_bad_period_400(make_client):
    c, app = make_client(control_plane=True, master_key=M)
    h = {"Authorization": "Bearer " + M}
    t = app.state.store.create_tenant(name="Acme")
    assert c.get(f"/admin/tenants/{t['id']}/invoice?period=nonsense",
                 headers=h).status_code == 400
