def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "upstream" in body and "guardrail_enforce" in body


def test_root_is_branded_not_litellm(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["api"] == "openai-compatible"
    assert "/v1/chat/completions" in body["endpoints"]


def test_no_upstream_swagger_exposed(client):
    # The façade hides docs (docs_url=None) the same way the deploy hides
    # LiteLLM's swagger — verify they 404.
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
