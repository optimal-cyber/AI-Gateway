# Demo the live gateway

How to demo the **live secure access layer** end-to-end. There is **no public demo
URL** by design — the gateway has zero public ingress (ADR-002); it is reached only
through Cloudflare Access + Okta (browser), or from an SSM shell on the host.

> Reference posture: no client data; the **government-ready (gov) boundaries are
> config-ready, not live** — `gov/*` models carry posture but a live gov call needs
> gov-cloud credentials (roadmap go-live).

The fastest, most credible demo is the **operational script** (Part A): one command
runs against the live gateway and narrates the product story. Parts B–D are the
visual control plane and the manual proofs for a more technical room.

---

## Part A — the operational demo (`scripts/demo.sh`)

A single narrated script that drives the **live** gateway through the four pillars —
**Authorize → Forward → Guard → Prove** — for a business / government-stakeholder
audience. Run it from an SSM shell on the **gateway-host** (the façade is on
`127.0.0.1:4001`):

```bash
# SSM into gateway-host (no SSH — ADR-006), then:
sudo /opt/ai-lab/repo/scripts/demo.sh
# Live presenting? pause between acts:
DEMO_PAUSE=1 sudo /opt/ai-lab/repo/scripts/demo.sh
```

What the audience sees (all against the live system, ~15s):

1. **AUTHORIZE** — an approved org (`Aegis Defense Corp`) is onboarded and issued a
   **scoped, budgeted** credential. An **unknown key is rejected** (401) and an
   **off-allowlist model is refused** (403) — authorization enforced at the wire.
2. **FORWARD** — that *one* credential reaches frontier models across **multiple
   cloud providers** (Anthropic live; OpenAI registered). Government-ready
   boundaries (GovCloud / Azure Gov / Assured Workloads) are posture-tagged and
   config-ready.
3. **GUARD** — a prompt-injection / data-exfiltration attempt is **blocked before
   any model sees it** (fail-closed, `$0` spent), with the finding category +
   severity surfaced and the matched phrase redacted.
4. **PROVE** — the **append-only audit ledger** the run just produced: each
   allow / deny / block with a **non-reversible identity fingerprint**, model,
   tokens, latency, and metered spend — and **no prompt/response content** (data
   minimization).

The script mints a fresh demo credential each run and **revokes it on exit** (the
demo org team is retained for re-runs). Override `GATEWAY_URL` / `ORG_NAME` /
`GATEWAY_MASTER_KEY` to point it elsewhere.

> **For a flawless multi-cloud act**, ensure `lab/openai_api_key` in Secrets Manager
> is a **valid** key — if it's stale, `gpt-4o` shows as *"registered · provider
> credential refresh pending"* (honest, but Anthropic carries the live calls). Reseed
> with `scripts/seed-secrets.sh` (or update the secret) and the act auto-greens.

**Health-check first** (containers + egress green):

```bash
cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh
```

---

## Part B — the visual control plane (the screen that *is* the gateway)

The front door is the **façade** (`gateway.optimallabs.io` → `gateway-facade:4001`),
which owns the control plane (virtual keys, budgets, audit). It serves a branded `/`
and no Swagger; its admin UI is at **`/admin/ui`**. To land the bare root there, add a
Cloudflare Single Redirect on `gateway.optimallabs.io`: path `/` → 302 `/admin/ui`.

1. Browser → **`https://gateway.optimallabs.io/admin/ui`**.
2. Cloudflare Access → Okta + MFA (must be `lab-admins`, US geo, WARP).
3. Paste the **master key** (`gateway_master_key`) and walk the control plane:
   - **Teams** — per-org teams, tier (dev/gov) + budget + live spend.
   - **Keys** — mint scoped/budgeted virtual keys; revoke; per-key spend.
   - **Spend** — real-time metering per org/key.

The **chat client** (`https://chat.optimallabs.io`, Open WebUI behind the same Okta
gate) is the "anyone can use it" surface — but the OpenAI-compatible endpoint + the
control plane, not the chat window, are "the gateway."

---

## Part C — the OpenAI-compatible endpoint, by hand (technical depth)

From an SSM shell on **gateway-host**. The front door is the **façade on `:4001`**;
with the control plane on, callers use a **façade** key (the bootstrap key or one
minted in `/admin/ui`), not the LiteLLM master key:

```bash
export LITELLM_KEY=$(sudo grep -oP 'GATEWAY_BOOTSTRAP_KEY=\K.*' /run/ai-lab/gateway.env)
cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh   # T-FA-* + T-GW-* live proofs
```

`T-GW-1..2` prove one key reaches `gpt-4o` **and** `claude-opus-4-8`; `T-GW-3` proves
an injection is **blocked pre-call** (no spend). The bare "only the base_url changed"
curl is in the README gateway section.

---

## Part D — (optional) demo tenancy

```bash
export GATEWAY_MASTER_KEY=$(sudo grep -oP 'GATEWAY_MASTER_KEY=\K.*' /run/ai-lab/gateway.env)

# dry-run, then apply — a dev tenant and a gov tenant (gov needs an approver):
./scripts/provision-org.sh --org "Demo Co" --tier dev --budget 50 --apply
./scripts/provision-org.sh --org "Acme Defense" --tier gov --approved-by "ryan" --apply
```

Show the two teams + keys in the Admin UI, then prove tier gating: a `dev` key reaches
commercial models; the gov approval gate (ADR-018) requires a named approver.

---

## What you will NOT see (by design)

- **No public URL.** The gateway is private; only an approved Okta user (Admin UI /
  chat) or an in-VPC/SSM caller (endpoint) reaches it.
- **No gov-tier completions.** The gov boundaries are config-ready, not live — `gov/*`
  models register and carry posture, but a live call needs gov-cloud credentials
  (roadmap go-live).
