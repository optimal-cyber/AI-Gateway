# Demo the live gateway

How to demo the **live private gateway** end-to-end with the current config
(through G1–G5). There is **no public demo URL** by design — the gateway has zero
public ingress (ADR-002); it is reached only through Cloudflare Access + Okta, or
from an SSM shell on the host. This runbook covers pushing the latest config to
the running stack, then the two demo surfaces: the **Admin control plane** and the
**OpenAI-compatible endpoint**.

> Assumes the stack is already deployed (README Deploy order, Phases 0–6). This is
> a redeploy-of-config + demo, not a from-scratch deploy. Reference design — no
> client data; the gov tiers are config-ready, not live (no gov-cloud creds).

## Prerequisites

- AWS CLI + SSM access to the lab account (no SSH — ADR-006).
- Provider keys seeded in Secrets Manager (`lab/*`): `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY` (for live completions), `SAM_GOV_API_KEY` (for the SAM.gov
  demo). Seeded into the tmpfs `.env` by `secrets-bootstrap.sh`.
- An admin Okta user in `lab-admins` (for the Admin UI), with MFA + WARP.

## Part A — push the G1–G5 config to the running stack

The repo lives on the host at `/opt/ai-lab/repo`. `litellm` is an image with the
config **mounted** (recreate to reload); `compliance-mcp` and `nemo` are **built**
on the host (the Federal Register tool is a code change → rebuild).

```bash
# SSM into gateway-host, then:
sudo git -C /opt/ai-lab/repo pull           # GitHub redirects the old AI-Lab URL
cd /opt/ai-lab/repo/docker/gateway-host

# Rebuild the MCP image (new federal_register_search tool) + recreate it:
sudo docker compose up -d --build compliance-mcp
# Recreate LiteLLM so it re-reads the mounted litellm-config.yaml (new model_list
# posture tiers, gov entries, router_settings, alerting):
sudo docker compose up -d --force-recreate litellm
# (nemo-guardrails unchanged in G1–G5; no rebuild needed.)
```

**Egress for `federal_register_search`** (optional — only if you'll demo it): add
`.federalregister.gov` to the live Squid allowlist. Quick path (no instance
replace) — SSM into the **proxy** host:

```bash
echo ".federalregister.gov" | sudo tee -a /etc/squid/allowlist.txt
sudo squid -k reconfigure          # hot-reload the ACL, no restart
```

The durable path is `terraform apply` (the allowlist is rendered into the proxy
user-data; note an apply may replace the proxy instance). The G2 gov endpoints
(`.aiplatform.googleapis.com`, `.openai.azure.us`, GovCloud Bedrock) are inert
without creds, so skip them for the demo.

**Verify health** before demoing:

```bash
cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh   # containers + egress all green
```

## Part B — demo the control plane (the screen that *is* the gateway)

> The front door is the **façade** (`gateway.optimallabs.io` → `gateway-facade:4001`),
> which owns the control plane (virtual keys, budgets, audit). It serves a branded
> `/` and no Swagger; its admin UI is at **`/admin/ui`** (Optimal Horizon mark). To
> land the bare root there, add a **Cloudflare Single Redirect** on
> `gateway.optimallabs.io`: path `/` → 302 `/admin/ui`.

1. Browser → **`https://gateway.optimallabs.io/admin/ui`**.
2. Cloudflare Access → Okta + MFA (must be `lab-admins`, US geo, WARP).
3. Paste the **master key** (`gateway_master_key`) and walk the control plane:
   - **Teams** — per-org teams, tier (dev/gov) + budget + live spend.
   - **Keys** — mint scoped/budgeted virtual keys; revoke; per-key spend.
   - Create a reviewer team + key live, then call `/v1` with it (Part C).
4. **Model registration + per-call logs** (incl. `$0`/0-token guardrail-blocked
   rows) live in the **LiteLLM** admin behind the façade — reach it internally via
   an SSM port-forward to `:4000/ui`, or publish it on a second hostname (see the
   tunnel-ingress note in `terraform/modules/cloudflare/main.tf`).

This — the OpenAI endpoint + the control plane — not the chat window, is "the gateway."

## Part C — demo the OpenAI-compatible endpoint (the API *is* the gateway)

From an SSM shell on **gateway-host**. The front door is the **façade on `:4001`**
(the smoke-test default); with the control plane on, callers use a **façade** key
(the bootstrap key), not the LiteLLM master key:

```bash
# Use the façade bootstrap key from the tmpfs .env (a valid control-plane key):
export LITELLM_KEY=$(sudo grep -oP 'GATEWAY_BOOTSTRAP_KEY=\K.*' /run/ai-lab/gateway.env)
# GATEWAY_URL defaults to the façade :4001; set :4000 to test LiteLLM directly.

cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh
```

Full reviewer/operator runbook (incl. how a dev gets a key and calls it from
outside): [`operate.md`](operate.md).

`T-GW-1..5` are the live proof: one key reaches `gpt-4o` **and**
`claude-opus-4-8` (T-GW-1/2), an injection is **blocked pre-call** (T-GW-3, no
spend), a **SAM.gov** lookup routes through `/v1` (T-GW-4), and a `gov`-tier model
is **registered** (T-GW-5; the gov *call* SKIPs — no gov-cloud creds, as expected).
For the bare "only the base_url changed" curl, see the README gateway section.

## Part D — (optional) demo tenancy

```bash
export LITELLM_MASTER_KEY=$(sudo grep -oP 'LITELLM_MASTER_KEY=\K.*' /run/ai-lab/gateway.env)

# dry-run, then apply — a dev tenant and a gov tenant (gov needs an approver):
./scripts/provision-org.sh --org "Demo Co" --tier dev --budget 50 --apply
./scripts/provision-org.sh --org "Acme Defense" --tier gov --approved-by "ryan" --apply
```

Show the two teams + keys in the Admin UI, then prove tier gating (T-TEN-1 /
T-GOV-1): the gov key reaches `gov/*` only; a `dev` model call with it is rejected.

## Part E — cleanup

Revoke the demo keys/teams in the Admin UI (or `POST /key/delete`,
`/team/delete`). Spend history stays in the audit log.

## What you will NOT see (by design)

- **No public URL.** The gateway is private; only an approved Okta user (Admin UI)
  or an in-VPC/SSM caller (endpoint) reaches it.
- **No gov-tier completions.** The gov boundaries are config-ready, not live —
  `gov/*` models register and carry posture, but a live call needs gov-cloud
  credentials (roadmap go-live). T-GW-5's call SKIPs; that's the expected result.
