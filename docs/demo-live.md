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

## Part 0 — cold start (instances are stopped between demos)

The three lab instances sit **Stopped** between uses. AWS keeps each instance's
**private IPv4 across stop/start** (released only on terminate), so the chat→façade
wiring (`LITELLM_HOST_IP`) and the baked proxy IP stay valid — **no Terraform
re-apply needed** on a normal stop/start. One ordering rule matters: the app hosts
fetch their secrets from Secrets Manager **through the Squid proxy** on boot, so the
**proxy must come up first**.

```bash
# 1) Start the Squid proxy FIRST; let it reach "running":
aws ec2 start-instances --instance-ids i-05781a42730e8c906          # ai-lab-proxy

# 2) Then the app hosts:
aws ec2 start-instances --instance-ids i-01a422690a766bb64 \
                                       i-0d350602ab870aa69           # gateway-host, chat-host
# (IDs drift only if an instance is replaced — otherwise resolve by Name tag:
#  ai-lab-proxy / ai-lab-gateway-host / ai-lab-chat-host.)
```

On boot, `ai-lab-secrets@<role>` regenerates the tmpfs `.env` and `ai-lab-stack@<role>`
brings the compose stack up automatically. **If an app host booted before Squid was
ready** (its secrets fetch would have failed), SSM in and re-kick:

```bash
aws ssm start-session --target i-01a422690a766bb64                 # gateway-host (no SSH — ADR-006)
sudo systemctl restart ai-lab-secrets@gateway ai-lab-stack@gateway
```

**Redeploy current code** — only if you edited the repo since the last run (e.g. the
new `gov/*` OpenAI models). From an SSM shell on gateway-host:

```bash
sudo git -C /opt/ai-lab/repo pull
cd /opt/ai-lab/repo/docker/gateway-host
sudo docker compose up -d --force-recreate litellm                 # config is a bind-mount; no rebuild
```

**Pre-flight the demo-critical secrets** are current — stale provider keys quietly
degrade the live act: `lab/openai_api_key`, `lab/anthropic_api_key` (live calls),
`lab/gateway_master_key` + `lab/gateway_bootstrap_key` (control plane / demo auth).
Reseed any with `AWS_DEFAULT_REGION=us-east-1 ./scripts/seed-secrets.sh` (blank input
leaves a secret unchanged).

**Confirm green before you present** (and not 30 seconds before — give the tunnel a
minute to re-establish):

```bash
cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh
# want: egress invariants, container health, façade auth gates (T-FA-1..3),
#       gpt-4o + claude-opus-4-8 reachable (T-GW-1/2), injection blocked (T-GW-3),
#       gov tier registered (T-GW-5)
```

---

## Part A — the operational demo (`scripts/demo.sh`)

A single narrated script that drives the **live** gateway through five pillars —
**Authorize → Forward → Guard → Prove → Operate** — for a business /
government-stakeholder audience. Run it from an SSM shell on the **gateway-host**
(the façade is on `127.0.0.1:4001`):

```bash
# SSM into gateway-host (no SSH — ADR-006), then:
sudo /opt/ai-lab/repo/scripts/demo.sh
# Live presenting? pause between acts:
DEMO_PAUSE=1 sudo /opt/ai-lab/repo/scripts/demo.sh
```

What the audience sees (all against the live system, ~25s):

1. **AUTHORIZE** — an approved org (`Aegis Defense Corp`) is onboarded and issued a
   **scoped, budgeted** credential. An **unknown key is rejected** (401) and an
   **off-allowlist model is refused** (403) — authorization enforced at the wire.
2. **FORWARD** — that *one* credential reaches frontier models across **multiple
   cloud providers** (Anthropic live; OpenAI registered). Government-ready
   boundaries (GovCloud / Azure Gov / Assured Workloads) are posture-tagged and
   config-ready — including **OpenAI GPT / GPT-OSS on AWS GovCloud (Bedrock)**,
   [authorized at FedRAMP High + DoD IL-4/5 on 2026-06-25](https://aws.amazon.com/about-aws/whats-new/2026/06/addl-bedrock-model-fedramp-il-5-govcloud/),
   registered in the `gov` tier and one creds-and-allowlist flip from live.
3. **GUARD** — a prompt-injection / data-exfiltration attempt is **blocked before
   any model sees it** (fail-closed, `$0` spent), with the finding category +
   severity surfaced and the matched phrase redacted.
4. **PROVE** — the **append-only audit ledger** the run just produced: each
   allow / deny / block with a **non-reversible identity fingerprint**, model,
   tokens, latency, and metered spend — and **no prompt/response content** (data
   minimization).
5. **OPERATE** — the product layer. The org is an **isolated tenant**: suspending it
   **refuses its key at the wire instantly** (HTTP 403), and reactivating restores it.
   Its metered usage rolls into a **plan-based invoice** (unpriced models flagged
   *estimated*, never silently $0). And a **customer portal token** logs the customer
   into a view scoped to *only their tenant*, where they **self-serve their own scoped
   API key** — multi-tenant SaaS, not one shared key.

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
   - **Tenants** — each customer org isolated; **suspend/activate** (refuses its keys
     at the gate), per-tenant plan selector + live usage, and a one-click **invoice**.
   - **Teams** — per-org teams, tier (dev/gov) + budget + live spend.
   - **Keys** — mint scoped/budgeted virtual keys; revoke; per-key spend.
   - **Spend** — real-time metering per org/key.

The **customer portal** is a separate surface at **`/portal/ui`**: a customer pastes
their **portal token** (issued from the Tenants panel) and sees only their own org —
usage, invoice, and self-service key minting. Show it next to the admin console to make
the multi-tenant story concrete.

The **chat client** (`https://chat.optimallabs.io`, the Optimal-branded chat app
behind the same Okta gate) is the "anyone can use it" surface — but the
OpenAI-compatible endpoint + the control plane, not the chat window, are "the gateway."

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
