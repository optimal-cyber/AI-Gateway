H = {"Authorization": "Bearer sk-test"}
MSG = [{"role": "user", "content": "Reply with one word: pong"}]


def test_chat_completion_proxies(client):
    r = client.post("/v1/chat/completions",
                    headers=H, json={"model": "claude-opus-4-8", "messages": MSG})
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "pong"
    assert body["usage"]["total_tokens"] == 2000


def test_audit_row_written_with_tokens_and_redacted_key(make_client):
    c, app = make_client()
    c.post("/v1/chat/completions",
           headers=H, json={"model": "claude-opus-4-8", "messages": MSG})
    rows = app.state.auditor.rows
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "claude-opus-4-8"
    assert row["prompt_tokens"] == 1000 and row["completion_tokens"] == 1000
    assert row["status"] == 200
    # Key is fingerprinted, never raw.
    assert row["key"] is not None and "sk-test" not in row["key"]
    assert "request_id" in row and "duration_ms" in row


def test_models_endpoint_proxies(client):
    r = client.get("/v1/models", headers=H)
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "claude-opus-4-8"


def test_streaming_passthrough(client):
    r = client.post("/v1/chat/completions",
                    headers=H,
                    json={"model": "claude-opus-4-8", "messages": MSG, "stream": True})
    assert r.status_code == 200
    assert "pong" in r.text
    assert "[DONE]" in r.text


def test_invalid_json_body(client):
    r = client.post("/v1/chat/completions", headers=H, content=b"not json{")
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["type"] == "invalid_request_error"
