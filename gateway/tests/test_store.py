def test_team_and_key_lifecycle(store):
    t = store.create_team(alias="Acme", tier="dev", max_budget=100)
    assert t["id"].startswith("team_") and t["spend"] == 0 and t["models"] == []

    k = store.create_key(team_id=t["id"], alias="ci", max_budget=10)
    assert k["key"].startswith("sk-")        # plaintext returned exactly once
    assert "key_hash" not in k               # hash is never exposed

    got = store.get_key_by_plaintext(k["key"])
    assert got and got["id"] == k["id"] and got["active"] is True

    assert store.revoke_key(k["id"]) is True
    assert store.get_key(k["id"])["active"] is False


def test_record_spend_increments_key_and_team(store):
    t = store.create_team(alias="Acme")
    k = store.create_key(team_id=t["id"])
    store.record_spend(request_id="r1", key_id=k["id"], team_id=t["id"],
                       model="claude-opus-4-8", prompt_tokens=1000,
                       completion_tokens=0, cost=1.5)
    assert store.get_key(k["id"])["spend"] == 1.5
    assert store.get_team(t["id"])["spend"] == 1.5
    s = store.spend_summary()
    assert s["total_cost"] == 1.5 and s["total_requests"] == 1


def test_unknown_plaintext_returns_none(store):
    assert store.get_key_by_plaintext("sk-nope") is None


def test_two_keys_get_distinct_secrets(store):
    a = store.create_key(alias="a")
    b = store.create_key(alias="b")
    assert a["key"] != b["key"]
