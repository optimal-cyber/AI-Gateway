"""Tenancy spine — the tenant is the first-class customer isolation boundary.

Tenants own teams, keys, spend and audit; reads scope per tenant; suspension is
enforced at the auth gate; and teams created before the spine are backfilled a
tenant on open (no data loss).
"""

from src import control

M = "sk-master"
MSG = [{"role": "user", "content": "hi"}]


# -- store: the tenant entity + auto-association -----------------------------

def test_create_tenant_has_id_slug_active(store):
    t = store.create_tenant(name="Aegis Defense Corp", tier="gov")
    assert t["id"].startswith("tenant_")
    assert t["slug"] == "aegis-defense-corp"
    assert t["status"] == "active" and t["tier"] == "gov"


def test_tenant_slugs_are_unique(store):
    a = store.create_tenant(name="Acme")
    b = store.create_tenant(name="Acme")
    assert a["slug"] == "acme" and b["slug"] == "acme-2"


def test_create_team_autocreates_tenant(store):
    t = store.create_team(alias="Acme", tier="dev")
    assert t["tenant_id"]
    ten = store.get_tenant(t["tenant_id"])
    assert ten and ten["name"] == "Acme"


def test_team_can_attach_to_existing_tenant(store):
    ten = store.create_tenant(name="BigCo")
    t1 = store.create_team(alias="BigCo-prod", tenant_id=ten["id"])
    t2 = store.create_team(alias="BigCo-ci", tenant_id=ten["id"])
    assert t1["tenant_id"] == t2["tenant_id"] == ten["id"]
    assert len(store.list_teams(tenant_id=ten["id"])) == 2


def test_key_inherits_tenant_from_team(store):
    t = store.create_team(alias="Acme")
    k = store.create_key(team_id=t["id"], alias="ci")
    assert k["tenant_id"] == t["tenant_id"]


# -- isolation: reads scope cleanly by tenant -------------------------------

def test_keys_and_usage_isolated_by_tenant(store):
    ta = store.create_team(alias="A")
    tb = store.create_team(alias="B")
    ka = store.create_key(team_id=ta["id"])
    store.create_key(team_id=tb["id"])

    a_keys = store.list_keys(tenant_id=ta["tenant_id"])
    assert [k["id"] for k in a_keys] == [ka["id"]]      # only tenant A's key

    store.record_spend(request_id="r1", key_id=ka["id"], team_id=ta["id"],
                       tenant_id=ta["tenant_id"], model="gpt-4o",
                       prompt_tokens=10, completion_tokens=5, cost=0.10)
    store.record_spend(request_id="r2", key_id=None, team_id=tb["id"],
                       tenant_id=tb["tenant_id"], model="gpt-4o",
                       prompt_tokens=10, completion_tokens=5, cost=0.99)

    ua = store.tenant_usage(ta["tenant_id"])
    assert ua["total_cost"] == 0.10 and ua["total_requests"] == 1
    assert ua["by_model"][0]["model"] == "gpt-4o"
    # the global summary segregates by tenant too
    by_tenant = {row["tenant_id"]: row["cost"]
                 for row in store.spend_summary()["by_tenant"]}
    assert by_tenant[ta["tenant_id"]] == 0.10 and by_tenant[tb["tenant_id"]] == 0.99


# -- migration: legacy teams (tenant_id NULL) get backfilled ----------------

def test_backfill_assigns_tenant_to_legacy_team(store):
    # simulate rows written before the tenancy spine existed (tenant_id NULL)
    with store._lock:
        store._db.execute(
            "INSERT INTO teams(id,alias,tier,models_json,spend,created_at) "
            "VALUES('team_legacy','OldCo','dev','[]',0,'2026-01-01T00:00:00+00:00')")
        store._db.execute(
            "INSERT INTO keys(id,key_hash,alias,team_id,models_json,spend,active,created_at) "
            "VALUES('key_legacy','deadbeef','old','team_legacy','[]',0,1,'2026-01-01T00:00:00+00:00')")
        store._db.commit()

    store._migrate()  # idempotent — re-runs the backfill

    team = store.get_team("team_legacy")
    assert team["tenant_id"]
    assert store.get_tenant(team["tenant_id"])["name"] == "OldCo"
    assert store.get_key("key_legacy")["tenant_id"] == team["tenant_id"]


# -- load-bearing: suspension is enforced at the auth gate ------------------

def test_authorize_blocks_suspended_tenant(store):
    t = store.create_team(alias="Acme")
    k = store.create_key(team_id=t["id"])
    authz = control.authorize(store, k["key"], "gpt-4o")     # active → allowed
    assert authz["tenant"]["id"] == t["tenant_id"]

    assert store.set_tenant_status(t["tenant_id"], "suspended") is True
    try:
        control.authorize(store, k["key"], "gpt-4o")
        assert False, "expected Denied for a suspended tenant"
    except control.Denied as d:
        assert d.status == 403 and d.code == "tenant_suspended"


# -- end-to-end: admin API + enforcement through the real app ----------------

def test_admin_tenant_crud_and_scoped_keys(make_client):
    c, app = make_client(control_plane=True, master_key=M)
    h = {"Authorization": "Bearer " + M}

    tid = c.post("/admin/tenants", headers=h, json={"name": "Acme"}).json()["id"]
    team = app.state.store.create_team(alias="Acme", tenant_id=tid)
    app.state.store.create_key(team_id=team["id"])

    r = c.get(f"/admin/tenants/{tid}/keys", headers=h)
    assert r.status_code == 200 and len(r.json()["data"]) == 1
    assert any(t["id"] == tid for t in c.get("/admin/tenants", headers=h).json()["data"])


def test_admin_tenants_require_master_key(make_client):
    c, _ = make_client(control_plane=True, master_key=M)
    assert c.get("/admin/tenants").status_code == 401


def test_suspended_tenant_key_blocked_end_to_end(make_client):
    c, app = make_client(control_plane=True, master_key=M, upstream_key="sk-up")
    team = app.state.store.create_team(alias="Acme")
    key = app.state.store.create_key(team_id=team["id"])
    body = {"model": "claude-opus-4-8", "messages": MSG}
    kh = {"Authorization": "Bearer " + key["key"]}

    assert c.post("/v1/chat/completions", headers=kh, json=body).status_code == 200

    # suspend via the admin API → the SAME key is now refused at the wire
    c.post(f"/admin/tenants/{team['tenant_id']}/suspend",
           headers={"Authorization": "Bearer " + M})
    r = c.post("/v1/chat/completions", headers=kh, json=body)
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "tenant_suspended"

    # reactivate → it works again
    c.post(f"/admin/tenants/{team['tenant_id']}/activate",
           headers={"Authorization": "Bearer " + M})
    assert c.post("/v1/chat/completions", headers=kh, json=body).status_code == 200


def test_audit_row_is_tenant_tagged(make_client):
    c, app = make_client(control_plane=True, master_key=M, upstream_key="sk-up")
    team = app.state.store.create_team(alias="Acme")
    key = app.state.store.create_key(team_id=team["id"])
    c.post("/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]},
           json={"model": "claude-opus-4-8", "messages": MSG})
    assert app.state.auditor.rows[-1]["tenant"] == team["tenant_id"]


def test_approve_request_provisions_tenant(make_client):
    c, app = make_client(control_plane=True, master_key=M)
    h = {"Authorization": "Bearer " + M}

    rid = c.post("/admin/requests", headers=h,
                 json={"org": "Globex", "tier": "dev"}).json()["id"]
    body = c.post(f"/admin/requests/{rid}/approve", headers=h, json={}).json()

    assert body["tenant_id"] and body["key"].startswith("sk-")
    assert app.state.store.get_tenant(body["tenant_id"])["name"] == "Globex"
    assert len(app.state.store.list_keys(tenant_id=body["tenant_id"])) == 1
