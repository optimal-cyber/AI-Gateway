# Owning the gateway — reducing the LiteLLM dependency

> Decision record + roadmap for moving from "we run upstream LiteLLM" toward "we
> own the gateway our customers see." Companion to
> [`docs/litellm-supply-chain.md`](litellm-supply-chain.md) (Phase 0) and the
> façade service in [`gateway/`](../gateway) (Phase 1). Could be folded into
> `decisions.md` as ADR-020.

## Why

Four motivations drive this, and they split into two buckets with different fixes:

**Bucket A — risk reduction (solved by pinning, not rewriting):**
- *Supply-chain / CMMC* — running third-party code in the assessment boundary.
- *API instability* — LiteLLM's config keys and proxy import paths move between
  versions (the `nemo_guardrail.py` docstring already warns about this).

**Bucket B — ownership (the only thing that argues for building):**
- *White-label / control* — full name-removal, theming, and custom Swagger
  branding are **LiteLLM Enterprise** (paid) features; `litellm-config.yaml`
  already notes hitting that wall.
- *Own the IP / it's the product* — the gateway is becoming something we want to
  control and offer to approved orgs end-to-end.

## The licensing reality

LiteLLM core + proxy + UI is **MIT-licensed**. We can vendor it, fork it,
rebrand the parts we control, and ship a product on top of it — the only
constraints are (a) keep MIT attribution in the source and (b) don't use the
Enterprise-gated features without a license. **"Own the IP" does not require a
rewrite.** It requires owning our *differentiated* layer and treating LiteLLM as
an embedded engine.

## The strategy: embed, don't expose

Own the parts customers touch and the parts that are our IP; keep — don't
rewrite — the part that is hard and undifferentiated (5-provider normalization,
streaming, SigV4/OAuth provider auth, token/cost tables). After Phase 1, LiteLLM
sits *behind* our branded façade as an internal detail, so brand ownership and
the supply-chain de-risk are already achieved without taking on the riskiest
rewrite. In a CMMC boundary this also matters because **every line we own is
attack surface we must maintain and attest to** — rewriting the commodity engine
*increases* the compliance burden.

## Phases

| Phase | Scope | State |
|---|---|---|
| **0. Pin & vendor** | digest-pin the LiteLLM image, re-pin script, SBOM tooling, evidence record | ✅ shipped — [`docs/litellm-supply-chain.md`](litellm-supply-chain.md) |
| **1. Gateway façade** | our OpenAI-compatible `/v1` front + auth gate + guardrail enforcement point + audit trail; proxies to LiteLLM | ✅ v0 shipped, 15 tests — [`gateway/`](../gateway), in compose on `:4001` |
| **2. Control plane** | move the virtual-key/team/budget store + a branded admin UI into our stack so LiteLLM can be dropped | ✅ v0 shipped, 30 tests — store + admin API + budget metering + minimal UI |
| **3. Provider engine** | replace LiteLLM's provider normalization | 💤 deferred, maybe indefinitely — only if a concrete need forces it |

**Recommendation: do 0 → 1 → 2; do NOT do 3 until forced.** That delivers full
brand/IP ownership of everything customers touch, immediate CMMC/supply-chain
de-risk, and insulation from upstream drift — while LiteLLM keeps doing the
unglamorous provider/streaming/cost work where a rewrite would only add risk.

### Phase 0 — done

Image pinned by digest in `docker/gateway-host/docker-compose.yml`; re-pin with
`scripts/pin-litellm-digest.sh`; SBOM via `scripts/generate-sbom.sh`; evidence
in `docker/gateway-host/litellm.pinned`. Control-mapped to CM-2/CM-3/CM-8/SR-3/
SR-4/SI-2.

### Phase 1 — shipped (v0)

`gateway/` is a FastAPI service (Python 3.11, same conventions as
`compliance-mcp`). It exposes `/v1/chat/completions` (streaming + not),
`/v1/models`, `/health`; enforces a virtual-key bearer gate; runs the same
fail-closed NeMo contract as the LiteLLM shim (gated by
`GATEWAY_GUARDRAIL_ENFORCE`); and writes a `request_id`-joinable audit row per
call with the key fingerprinted, never raw. It runs alongside LiteLLM on `:4001`;
cut over per [`gateway/README.md`](../gateway/README.md). Known gaps documented
there (output rail on token streams; key store still upstream).

### Phase 2 — shipped (v0)

The control plane is where product value + branding concentrate and where the
Enterprise wall bit us. Shipped in `gateway/`, gated by `GATEWAY_CONTROL_PLANE`
(default off — Phase-1 delegation stays the default until you cut over):

- **Store** (`src/store.py`) — SQLite, stdlib-only (mirrors `mcp-server`'s
  sqlite use). Teams, keys, spend; keys stored as a SHA-256 hash, plaintext
  returned once. The façade becomes the source of truth for keys + budgets.
- **Authorize + meter** (`src/control.py`) — validates a presented key against
  the store (active / not expired / model allow-list / budget remaining),
  forwards upstream under a single service credential (`GATEWAY_UPSTREAM_KEY`),
  and records spend per request against key + team (`src/pricing.py`,
  placeholder rates clearly marked "verify before billing"). Streamed responses
  are metered via injected `stream_options.include_usage` (the injected usage
  chunk is stripped back out if the caller didn't ask for it).
- **Admin API** (`src/admin.py`) — master-key-protected CRUD for teams/keys +
  a spend summary. `scripts/provision-org.sh` targets it by default
  (`--backend facade`), including the ADR-018 gov approval gate (a gov team
  requires `approved_by`); `--backend litellm` keeps the legacy path.
- **Branded admin UI** (`static/admin.html`) — dependency-free, served at
  `/admin/ui`; shows Models / Teams / Keys / Spend and brands itself from
  `GATEWAY_NAME` + the Optimal Horizon mark. **This is the answer to the
  white-label motivation:** full name-removal/theming of LiteLLM's own UI is an
  Enterprise (paid) feature, but here the control plane is *our* UI, so it is
  100% Optimal-branded with no LiteLLM wordmark and no license. By fronting with
  the façade, LiteLLM's UI is internal-only (SSM/optional second hostname).

**Deferred from Phase 2 (honest gaps):** the **output guardrail** on *streamed*
responses is still not enforced (tokens are already in flight); budget *alerting*
(Slack) à la LiteLLM's `alerting:`; a polished/SPA admin UI; and a Postgres-backed
store for multi-instance (the stack already runs Postgres — swap the `Store`
class, the rest calls only its interface).

### Phase 3 — deferred

Replacing the 5-provider normalization (OpenAI, Anthropic, Bedrock, Vertex,
Azure), streaming, and accurate token/cost accounting is the hardest, most
bug-prone, least differentiated work. Keep pinned LiteLLM as the internal engine
behind the façade until a concrete requirement (a provider/feature LiteLLM won't
support, or a need to drop it entirely from the boundary) forces the build.

## Control mapping (NIST 800-53 Rev 5 / CMMC L2)

| Control | How this arc satisfies it |
|---|---|
| CM-2 / CM-3 / CM-8 / SR-3 / SR-4 / SI-2 | Phase 0 — see supply-chain doc |
| AC-3 / AC-6 (Access Enforcement / Least Privilege) | Phase 1 auth gate; Phase 2 scoped keys we own |
| AU-2 / AU-3 / AU-12 (Audit events / content / generation) | Phase 1 per-request audit rows joinable with NeMo + MCP logs |
| SC-7 (Boundary Protection) | façade is the single controlled `/v1` ingress; provider egress stays on the engine behind it |
| SI-4 (System Monitoring) | enforcement point + audit trail we own end-to-end |
