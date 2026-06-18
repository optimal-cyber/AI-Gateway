M = "sk-master"
AH = {"Authorization": "Bearer " + M}


def test_admin_requires_master_key(make_client):
    c, _ = make_client(master_key=M)
    assert c.get("/admin/teams").status_code == 401
    assert c.get("/admin/teams",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/admin/teams", headers=AH).status_code == 200


def test_admin_disabled_without_master_key(make_client):
    c, _ = make_client()  # no master key configured at all
    r = c.get("/admin/teams", headers=AH)
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "admin_disabled"


def test_gov_team_requires_approver(make_client):
    c, _ = make_client(master_key=M)
    r = c.post("/admin/teams", headers=AH, json={"alias": "Gov", "tier": "gov"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "approval_required"

    ok = c.post("/admin/teams", headers=AH,
                json={"alias": "Gov", "tier": "gov", "approved_by": "ryan"})
    assert ok.status_code == 200 and ok.json()["tier"] == "gov"


def test_team_key_list_revoke_flow(make_client):
    c, _ = make_client(master_key=M)
    t = c.post("/admin/teams", headers=AH,
               json={"alias": "Acme", "max_budget": 100}).json()
    k = c.post("/admin/keys", headers=AH,
               json={"team_id": t["id"], "alias": "ci"}).json()
    assert k["key"].startswith("sk-")

    assert len(c.get("/admin/keys", headers=AH).json()["data"]) == 1
    rk = c.delete("/admin/keys/" + k["id"], headers=AH)
    assert rk.status_code == 200 and rk.json()["revoked"] is True


def test_create_key_unknown_team_rejected(make_client):
    c, _ = make_client(master_key=M)
    r = c.post("/admin/keys", headers=AH, json={"team_id": "team_nope"})
    assert r.status_code == 400
