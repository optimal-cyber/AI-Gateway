"""Test harness for the façade.

We stand the real FastAPI app up with a MOCK LiteLLM upstream (httpx
MockTransport) and a fake guardrail/auditor injected via app.state, so the
proxy, auth gate, guardrail enforcement, control plane, and audit rows are all
exercised without a real LiteLLM, NeMo, or database file (SQLite :memory:).
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings
from src.guardrail import Guardrail, GuardrailResult
from src.store import Store
from src.upstream import Upstream


def _make_handler(captured):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        captured.append({"path": request.url.path, "auth": auth})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "claude-opus-4-8"}]})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content or b"{}")
            if "sk-bad" in auth:
                return httpx.Response(401, json={"error": {"message": "invalid key"}})
            if body.get("stream"):
                include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

                async def _sse():
                    yield b'data: {"choices":[{"delta":{"content":"pong"}}]}\n\n'
                    if include_usage:  # terminal usage-only chunk, OpenAI-style
                        yield (b'data: {"choices":[],"usage":{"prompt_tokens":1000,'
                               b'"completion_tokens":1000,"total_tokens":2000}}\n\n')
                    yield b"data: [DONE]\n\n"
                return httpx.Response(200, content=_sse(),
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={
                "id": "chatcmpl-x", "model": body.get("model", "unknown"),
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000,
                          "total_tokens": 2000}})
        return httpx.Response(404, json={"error": {"message": "not found"}})
    return handler


class FakeGuardrail:
    def __init__(self, blocked=False, message="Blocked by the AI-lab guardrail."):
        self.blocked = blocked
        self.message = message
        self.calls = []

    async def check(self, role, content, request_id=None, caller_role=None):
        self.calls.append((role, content))
        return GuardrailResult(
            blocked=self.blocked,
            findings=[{"category": "test"}] if self.blocked else [],
            activated_rails=[], message=self.message)

    prompt_text = staticmethod(Guardrail.prompt_text)
    response_text = staticmethod(Guardrail.response_text)

    async def aclose(self):
        pass


class FakeAuditor:
    def __init__(self):
        self.rows = []

    def emit(self, **fields):
        self.rows.append(fields)


def build(enforce=False, require_key=True, blocked=False,
          control_plane=False, master_key="", upstream_key="", bootstrap_key=""):
    settings = Settings(guardrail_enforce=enforce, require_key=require_key,
                        audit_log="/tmp/gateway-test.log", control_plane=control_plane,
                        master_key=master_key, upstream_key=upstream_key,
                        bootstrap_key=bootstrap_key, db_path=":memory:")
    app = create_app(settings)
    captured = []
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_make_handler(captured)),
        base_url="http://litellm-mock", trust_env=False)
    app.state.upstream = Upstream("http://litellm-mock", 30, client=mock_client)
    app.state.guardrail = FakeGuardrail(blocked=blocked)
    app.state.auditor = FakeAuditor()
    app.state.captured = captured
    if control_plane or master_key:
        app.state.store = Store(":memory:")
    return app


@pytest.fixture
def client():
    app = build()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_client():
    """Factory so a test can choose enforce/require_key/blocked/control_plane."""
    created = []

    def _make(**kw):
        app = build(**kw)
        c = TestClient(app)
        c.__enter__()
        created.append((c, app))
        return c, app

    yield _make
    for c, _ in created:
        c.__exit__(None, None, None)


@pytest.fixture
def store():
    return Store(":memory:")
