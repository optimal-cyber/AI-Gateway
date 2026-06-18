H = {"Authorization": "Bearer sk-test"}


def test_missing_key_rejected(client):
    r = client.post("/v1/chat/completions",
                    json={"model": "claude-opus-4-8", "messages": []})
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "missing_key"


def test_malformed_key_rejected(client):
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer nope-not-a-key"},
                    json={"model": "claude-opus-4-8", "messages": []})
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "bad_key"


def test_require_key_false_allows_anonymous(make_client):
    c, _ = make_client(require_key=False)
    r = c.post("/v1/chat/completions",
               json={"model": "claude-opus-4-8",
                     "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200


def test_upstream_key_rejection_passes_through(client):
    # Façade shape-check passes (sk-…), but the mock upstream (LiteLLM, source
    # of truth) rejects it — status must pass through unchanged.
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-bad"},
                    json={"model": "claude-opus-4-8",
                          "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401
    assert "invalid key" in r.text
