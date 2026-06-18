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
| `GATEWAY_CONTROL_PLANE` | `false` | own keys/budgets locally (Phase 2) |
| `GATEWAY_DB_PATH` | `/var/lib/gateway/control.db` | SQLite control-plane store |
| `GATEWAY_MASTER_KEY` | _(unset)_ | admin API credential; admin closed if unset |
| `GATEWAY_UPSTREAM_KEY` | _(unset)_ | service key used on the LiteLLM hop when control plane is on |
| `GATEWAY_PRICING` | _(unset)_ | JSON per-model rate overrides for spend metering |

## Control plane (Phase 2)

When `GATEWAY_CONTROL_PLANE=true`, the façade stops delegating to LiteLLM for key
validity/budgets and becomes the **source of truth**:

- presented keys are validated against the local store (active? expired? model
  allow-listed? budget remaining?),
- the request is forwarded to LiteLLM under a single `GATEWAY_UPSTREAM_KEY`
  service credential (the caller's gateway key never reaches LiteLLM),
- spend is metered per request against the key and its team (`src/pricing.py` —
  **placeholder rates, verify before billing**). **Streamed** responses are
  metered too: the façade injects `stream_options.include_usage`, reads the
  terminal usage chunk, and strips that injected chunk back out if the caller
  didn't request it (so the client sees an unmodified stream).

Manage it with the **admin API** (Bearer `GATEWAY_MASTER_KEY`) or the branded UI
at **`/admin/ui`**. Semantics mirror `scripts/provision-org.sh` (org == team,
tier dev|gov, the ADR-018 gov approval gate):

```bash
ADMIN=(-H "Authorization: Bearer $GATEWAY_MASTER_KEY" -H 'Content-Type: application/json')
# create a dev team with a $100 budget
curl -sS "${ADMIN[@]}" -d '{"alias":"Acme","tier":"dev","max_budget":100}' \
  http://gateway-host:4001/admin/teams
# mint a key under it (the plaintext is returned ONCE)
curl -sS "${ADMIN[@]}" -d '{"team_id":"team_…","alias":"acme-ci"}' \
  http://gateway-host:4001/admin/keys
curl -sS "${ADMIN[@]}" http://gateway-host:4001/admin/spend   # usage summary
```

Endpoints: `POST/GET /admin/teams`, `GET /admin/teams/{id}`, `POST/GET /admin/keys`,
`GET /admin/keys/{id}`, `DELETE /admin/keys/{id}` (revoke), `GET /admin/spend`.

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
5. **Move key management** onto the façade (Phase 2): set
   `GATEWAY_CONTROL_PLANE=true`, a `GATEWAY_MASTER_KEY`, and a
   `GATEWAY_UPSTREAM_KEY` (one LiteLLM service key the façade uses upstream).
   Provision teams/keys via `/admin/ui` or the admin API and reissue keys to
   tenants. LiteLLM then only does provider routing under the one service key.

Rollback is repointing the consumer back to `:4000`.

## Known gaps (honest v0)

- **Output guardrail on streamed responses** is not enforced — tokens are
  proxied through as they arrive, so a post-hoc rail can't un-send them. The
  audit row marks `guardrail_output: skipped_stream`. Non-streaming responses
  are fully output-screened. (LiteLLM has the same fundamental constraint; its
  sequential post_call rail only applies to buffered responses.)
- **Pricing** (`src/pricing.py`) ships placeholder rates so budget enforcement is
  exercised — verify against real provider pricing before relying on spend.
- **Store** is SQLite (single-instance). For multi-instance, swap the `Store`
  class for a Postgres-backed one (the stack already runs Postgres); the rest of
  the façade calls only its interface.
- **Budget alerting** (Slack, like LiteLLM's `alerting:`) and a polished SPA
  admin UI are deferred.
