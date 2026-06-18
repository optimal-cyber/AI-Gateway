"""Control-plane enforcement on /v1/chat/completions (GATEWAY_CONTROL_PLANE=true).

claude-opus-4-8 @ 1000 prompt + 1000 completion tokens (the mock's usage) costs
0.015 + 0.075 = $0.09 under the placeholder pricing table.
"""

M = "sk-master"
MSG = [{"role": "user", "content": "hi"}]


def _seed(app, **kw):
    store = app.state.store
    t = store.create_team(alias="Acme", max_budget=kw.get("team_budget"))
    k = store.create_key(team_id=t["id"], alias="ci",
                         models=kw.get("models", []), max_budget=kw.get("key_budget"))
    return t, k


def _post(c, key, model="claude-opus-4-8"):
    return c.post("/v1/chat/completions",
                  headers={"Authorization": "Bearer " + key},
                  json={"model": model, "messages": MSG})


def test_valid_key_allowed_meters_spend_and_swaps_upstream_key(make_client):
    c, app = make_client(control_plane=True, master_key=M, upstream_key="sk-upstream")
    t, k = _seed(app)
    r = _post(c, k["key"])
    assert r.status_code == 200

    # spend metered against key + team
    assert round(app.state.store.get_team(t["id"])["spend"], 4) == 0.09
    assert round(app.state.store.get_key(k["id"])["spend"], 4) == 0.09
    # the caller's gateway key was swapped for the upstream service credential
    assert app.state.captured[-1]["auth"] == "Bearer sk-upstream"
    # audit row carries the computed cost
    assert app.state.auditor.rows[-1]["cost"] == 0.09


def test_unknown_key_rejected(make_client):
    c, _ = make_client(control_plane=True, master_key=M)
    r = _post(c, "sk-doesnotexist")
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "invalid_key"


def test_revoked_key_rejected(make_client):
    c, app = make_client(control_plane=True, master_key=M)
    t, k = _seed(app)
    app.state.store.revoke_key(k["id"])
    r = _post(c, k["key"])
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "key_revoked"


def test_model_not_in_allowlist_rejected(make_client):
    c, app = make_client(control_plane=True, master_key=M)
    t, k = _seed(app, models=["gpt-4o"])     # key allow-listed to gpt-4o only
    r = _post(c, k["key"], model="claude-opus-4-8")
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "model_not_allowed"


def test_key_budget_exhausted_blocks(make_client):
    c, app = make_client(control_plane=True, master_key=M)
    t, k = _seed(app, key_budget=0.01)
    app.state.store.record_spend(request_id="r", key_id=k["id"], team_id=t["id"],
                                 model="x", prompt_tokens=0, completion_tokens=0,
                                 cost=0.02)  # push past the $0.01 cap
    r = _post(c, k["key"])
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "budget_exceeded"


def test_control_plane_off_does_not_meter(make_client):
    # Phase-1 behavior preserved: no store enforcement, caller key forwarded.
    c, app = make_client(control_plane=False)
    r = c.post("/v1/chat/completions",
               headers={"Authorization": "Bearer sk-anything"},
               json={"model": "claude-opus-4-8", "messages": MSG})
    assert r.status_code == 200
    assert app.state.captured[-1]["auth"] == "Bearer sk-anything"  # not swapped
