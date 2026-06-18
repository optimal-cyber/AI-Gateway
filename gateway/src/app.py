"""AI Gateway façade — an OpenAI-compatible front door we own.

Phase 1 of docs/own-gateway.md. Owns the `/v1` surface, the auth gate, the
guardrail enforcement point, and the audit trail; proxies provider work to the
pinned LiteLLM engine behind it. Run alongside LiteLLM (default port 4001) and
cut traffic over once smoke tests pass — see gateway/README.md.

    python -m src.app            # serve
    python -m src.app --health   # container HEALTHCHECK probe (no HTTP needed)
"""

from __future__ import annotations

import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import auth as auth_mod
from .audit import Auditor, key_fingerprint
from .config import Settings, load
from .guardrail import Guardrail
from .upstream import Upstream

CHAT_PATH = "/v1/chat/completions"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s: Settings = app.state.settings
    app.state.upstream = getattr(app.state, "upstream", None) or Upstream(
        s.upstream_url, s.upstream_timeout)
    app.state.guardrail = getattr(app.state, "guardrail", None) or Guardrail(
        s.guardrail_url, s.guardrail_timeout)
    app.state.auditor = getattr(app.state, "auditor", None) or Auditor(s.audit_log)
    try:
        yield
    finally:
        for c in (app.state.upstream, app.state.guardrail):
            try:
                await c.aclose()
            except Exception:  # noqa: BLE001
                pass


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    s = settings or load()
    app = FastAPI(title=s.name, version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)  # own surface; no upstream swagger
    app.state.settings = s

    # -- dependencies (overridable in tests) -------------------------------
    def get_settings() -> Settings:
        return app.state.settings

    def get_upstream(request: Request) -> Upstream:
        return request.app.state.upstream

    def get_guardrail(request: Request) -> Guardrail:
        return request.app.state.guardrail

    def get_auditor(request: Request) -> Auditor:
        return request.app.state.auditor

    app.dependency_overrides = {}  # tests populate this

    # -- branding / health -------------------------------------------------
    @app.get("/")
    async def root(cfg: Settings = Depends(get_settings)):
        return {"service": cfg.name, "api": "openai-compatible",
                "endpoints": ["/v1/chat/completions", "/v1/models", "/health"]}

    @app.get("/health")
    async def health(cfg: Settings = Depends(get_settings)):
        return {"status": "ok", "service": cfg.name,
                "upstream": cfg.upstream_url,
                "guardrail_enforce": cfg.guardrail_enforce}

    @app.get("/v1/models")
    async def models(request: Request,
                     cfg: Settings = Depends(get_settings),
                     up: Upstream = Depends(get_upstream)):
        auth_mod.authenticate(request, require_key=cfg.require_key,
                              key_prefix=cfg.key_prefix)
        resp = await up.get("/v1/models", dict(request.headers))
        return JSONResponse(_safe_json(resp), status_code=resp.status_code)

    # -- the endpoint ------------------------------------------------------
    @app.post(CHAT_PATH)
    async def chat_completions(request: Request,
                               cfg: Settings = Depends(get_settings),
                               up: Upstream = Depends(get_upstream),
                               gr: Guardrail = Depends(get_guardrail),
                               audit: Auditor = Depends(get_auditor)):
        t0 = time.perf_counter()
        rid = uuid.uuid4().hex
        principal = auth_mod.authenticate(
            request, require_key=cfg.require_key, key_prefix=cfg.key_prefix)
        fp = key_fingerprint(principal.key if principal else None)

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={
                "error": {"message": "Invalid JSON body.", "type": "invalid_request_error"}})

        model = body.get("model")
        stream = bool(body.get("stream"))

        def _row(**extra):
            return dict(request_id=rid, key=fp, model=model, stream=stream,
                        duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                        guardrail_enforce=cfg.guardrail_enforce, **extra)

        # --- pre-call guardrail (input) -----------------------------------
        if cfg.guardrail_enforce:
            res = await gr.check("user", gr.prompt_text(body), request_id=rid)
            if res.blocked:
                audit.emit(**_row(status="blocked", phase="input",
                                  findings=res.get("findings"),
                                  activated_rails=res.get("activated_rails")))
                raise HTTPException(status_code=400, detail={
                    "error": {"message": res.get("message", "Blocked by guardrail."),
                              "type": "blocked_by_guardrail", "guardrail": "nemo",
                              "findings": res.get("findings", [])}})

        headers = dict(request.headers)
        headers["x-gateway-request-id"] = rid

        # --- streaming passthrough ----------------------------------------
        if stream:
            resp = await up.stream(CHAT_PATH, body, headers)

            async def body_iter():
                try:
                    async for chunk in resp.aiter_raw():
                        yield chunk
                finally:
                    await resp.aclose()
                    audit.emit(**_row(status=resp.status_code, phase="stream",
                                      # output rail on token streams is a known
                                      # gap — see gateway/README.md.
                                      guardrail_output="skipped_stream"))

            return StreamingResponse(
                body_iter(), status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "text/event-stream"))

        # --- non-streaming ------------------------------------------------
        resp = await up.post_json(CHAT_PATH, body, headers)
        data = _safe_json(resp)

        if resp.status_code >= 400:
            audit.emit(**_row(status=resp.status_code, phase="upstream_error"))
            return JSONResponse(data, status_code=resp.status_code)

        # --- post-call guardrail (output) ---------------------------------
        if cfg.guardrail_enforce:
            res = await gr.check("assistant", gr.response_text(data), request_id=rid)
            if res.blocked:
                audit.emit(**_row(status="blocked", phase="output",
                                  findings=res.get("findings")))
                raise HTTPException(status_code=400, detail={
                    "error": {"message": res.get("message", "Response blocked by guardrail."),
                              "type": "blocked_by_guardrail", "guardrail": "nemo"}})

        usage = data.get("usage") if isinstance(data, dict) else None
        audit.emit(**_row(status=resp.status_code,
                          prompt_tokens=(usage or {}).get("prompt_tokens"),
                          completion_tokens=(usage or {}).get("completion_tokens")))
        return JSONResponse(data, status_code=resp.status_code)

    return app


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"error": {"message": "Non-JSON upstream response.",
                          "type": "upstream_error"},
                "raw": resp.text[:2000]}


# Module-level app for `uvicorn src.app:app` and tests.
app = create_app()


def _cli_health() -> int:
    print('{"status": "ok"}')
    return 0


def main() -> None:
    if "--health" in sys.argv:
        sys.exit(_cli_health())
    import uvicorn
    s = app.state.settings
    uvicorn.run(app, host=s.host, port=s.port)


if __name__ == "__main__":
    main()
