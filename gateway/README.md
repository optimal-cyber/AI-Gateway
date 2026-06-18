# AI Gateway façade (Phase 1)

Our own **OpenAI-compatible front door**. It owns the `/v1` surface, the
auth gate, the guardrail enforcement point, and the audit trail — and proxies
the actual provider work to the pinned LiteLLM engine behind it. Customers see
the façade; LiteLLM becomes an internal implementation detail.

This is Phase 1 of [`docs/own-gateway.md`](../docs/own-gateway.md) — the
"embed, don't expose" step that gets you brand ownership + your own request
surface without rewriting LiteLLM's hard parts (provider normalization,
streaming, cost tables).

## What it does today

- `POST /v1/chat/completions` — streaming and non-streaming, transparently
  proxied to upstream LiteLLM.
- `GET /v1/models` — proxied.
- `GET /health`, `GET /` — branded; no upstream Swagger exposed (`docs_url=None`).
- **Auth gate** — requires a well-formed `Bearer sk-…` virtual key at the edge,
  forwards it unchanged. LiteLLM stays the source of truth for key validity and
  budgets until Phase 2 moves the key store into the façade (`src/auth.py`).
- **Guardrail enforcement point** — the same fail-closed NeMo DaaS contract as
  the LiteLLM shim, reimplemented with no LiteLLM dependency (`src/guardrail.py`).
  Gated by `GATEWAY_GUARDRAIL_ENFORCE` (default off — see below).
- **Audit trail** — one JSON line per request (`src/audit.py`): request_id, key
  *fingerprint* (never the raw key), model, token counts, latency, decision.
  Same shape as NeMo's `decisions.log` so rows join on `request_id`.

## Run & test

```bash
cd gateway
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q            # 15 tests, mock upstream + fake guardrail
python -m src.app             # serve on :4001 (needs a reachable upstream)
```

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `GATEWAY_PORT` | `4001` | bind port (runs alongside LiteLLM's 4000) |
| `GATEWAY_UPSTREAM_URL` | `http://litellm:4000` | the LiteLLM engine |
| `GATEWAY_GUARDRAIL_URL` | `http://nemo-guardrails:8000` | NeMo DaaS |
| `GATEWAY_GUARDRAIL_ENFORCE` | `false` | enforce rails at the façade |
| `GATEWAY_REQUIRE_KEY` | `true` | reject requests with no bearer key |
| `GATEWAY_AUDIT_LOG` | `/var/log/gateway/requests.log` | rotated JSON-line audit |

## Cutover (lab)

The façade ships in `docker/gateway-host/docker-compose.yml` running **alongside**
LiteLLM. To put it in the request path:

1. **Deploy** the stack; confirm `gateway-facade` is healthy and
   `curl http://gateway-host:4001/health` is ok.
2. **Smoke** it: point a client at `:4001` and run the same checks as
   `scripts/run-smoke-tests.sh` (a virtual key still works unchanged).
3. **Repoint** the consumer — change Open WebUI's base URL (chat-host) and/or
   the Cloudflare Tunnel origin from `litellm:4000` to `gateway-facade:4001`.
4. **Move guardrails** onto the façade: set `GATEWAY_GUARDRAIL_ENFORCE=true` and
   delete the `guardrails:` block from `litellm-config.yaml`, so NeMo runs once,
   at the layer you own.

Rollback is repointing the consumer back to `:4000`.

## Known gaps (honest v0)

- **Output guardrail on streamed responses** is not enforced — tokens are
  proxied through as they arrive, so a post-hoc rail can't un-send them. The
  audit row marks `guardrail_output: skipped_stream`. Non-streaming responses
  are fully output-screened. (LiteLLM has the same fundamental constraint; its
  sequential post_call rail only applies to buffered responses.)
- **Key store / budgets** still live in LiteLLM (Phase 2 moves them).
- The façade trusts the upstream's status codes and bodies; it does not yet
  re-derive cost. Token counts in the audit row come from the upstream `usage`.
