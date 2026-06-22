# Operate — stand up a reviewable gateway (for Max + devs)

Goal: a running instance reviewers can actually hit — get a virtual key, point an
OpenAI client at it, call a frontier model, and see the control plane. This is the
**full Zero Trust lab** (AWS + Cloudflare + Okta) with the **gateway façade as the
front door** and the **control plane on** (it owns virtual keys, budgets, audit).

> Conceptually: *one secure API / access layer that lets approved organizations
> reach government-ready AI models across multiple clouds.* Dev-tier (OpenAI +
> Anthropic) is **live**; the gov/multi-cloud tier is **configured but inert**
> until its GovCloud/Vertex/Azure-Gov creds are seeded (see §5).

## Topology (what reviewers hit)

```
reviewer ──HTTPS──> Cloudflare Access (Okta SSO / service token)
                       │  cloudflared tunnel
                       ▼
            gateway-facade :4001   ← THE front door (OpenAI /v1 + /admin/ui)
                       │  validates the virtual key, meters spend, audits
                       ▼
                  litellm :4000    ← internal engine (providers, routing)
                       │              + NeMo guardrails + compliance-MCP
                       ▼
              OpenAI / Anthropic (egress via Squid allowlist)
```

`chat.<domain>` (Open WebUI) is one consumer; it calls the façade with the
bootstrap key. `gateway.<domain>` is the façade (`/v1` + `/admin/ui`).

---

## 0. Prerequisites (the credentials this needs — all yours)

| Need | Used for |
|---|---|
| 🔑 **AWS account** + admin creds (`aws configure`) | `terraform apply`, Secrets Manager |
| 🔑 **Cloudflare** account + a zone you control (the repo uses `optimallabs.io`) | Zero Trust tunnels + Access |
| 🔑 **Okta** developer org | SSO for chat + the admin control plane |
| 🔑 **OpenAI** + **Anthropic** API keys | the live dev-tier models |
| A domain on the Cloudflare zone | `gateway.<domain>`, `chat.<domain>` |

> I cannot run any of the 🔑 steps from here (they need your interactive logins).
> Each is a copy-paste command **you** run on your workstation or an SSM shell.
> To capture a command's output in this chat, prefix it with `! ` in the prompt.

---

## 1. Provision infrastructure  🔑 AWS

```bash
cd terraform
terraform init
terraform apply            # creates VPC, 2 EC2 hosts, Squid, Secrets Manager (empty), logging
```
This creates the empty `lab/*` secret containers — including the three façade
secrets (`gateway_master_key`, `gateway_bootstrap_key`, `gateway_upstream_key`).

## 2. Seed secrets  🔑 AWS + provider/Okta keys

```bash
AWS_DEFAULT_REGION=us-east-1 ./scripts/seed-secrets.sh        # prompts incl. the 3 façade keys
AWS_DEFAULT_REGION=us-east-1 ./scripts/seed-okta-secrets.sh   # the okta_* secrets (Phase 1.5)
```
Façade keys to set (invent the sk- values; deliver the bootstrap key to reviewers):
- **`gateway_master_key`** — admin API / `/admin/ui` login. *Required.*
- **`gateway_bootstrap_key`** — the shared first-boot virtual key (Open WebUI + the
  initial reviewer key). *Required.*
- **`gateway_upstream_key`** — leave blank; the façade falls back to the LiteLLM
  master key for the internal hop.

## 3. Cloudflare Access + Okta + DNS  🔑 Cloudflare + Okta

Follow [`okta-setup.md`](okta-setup.md), [`cloudflare-access-policies.md`](cloudflare-access-policies.md),
and [`google-dns-cnames.md`](google-dns-cnames.md). The tunnel **ingress** is already
in Terraform and points `gateway.<domain>` → `gateway-facade:4001`
(`terraform/modules/cloudflare/main.tf`). You wire the Access apps (lab-users /
lab-admins) + DNS records.

## 4. Bring up the stacks  🔑 SSM (no SSH — ADR-006)

On **each** host (gateway-host, then chat-host), via SSM Session Manager:
```bash
sudo git -C /opt/ai-lab/repo pull        # get this branch
sudo systemctl enable --now ai-lab-secrets@gateway   # (or @chat) → tmpfs .env
sudo systemctl enable --now ai-lab-stack@gateway     # (or @chat) → docker compose up -d --build
```
First boot builds `gateway-facade`, brings up LiteLLM + Postgres + NeMo + MCP, and
the façade seeds a `lab` team + the bootstrap key into its control-plane store.

## 5. Verify  🔑 SSM

```bash
cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh     # targets the façade :4001
```
Expect: T-EG egress invariants, container health (incl. `gateway-facade`),
**T-FA-1..3** (façade health, auth gate, admin gate), **T-GW-1..2** (gpt-4o +
claude reachable through the façade with the bootstrap key), **T-GW-3** injection
blocked pre-call, **T-GW-5** gov-tier model registered (live call SKIPs — no
GovCloud creds; see §6).

---

## How a reviewer / dev actually uses it

**A) Quick path — from inside (SSM port-forward, no Cloudflare friction):**
```bash
aws ssm start-session --target <gateway-instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=4001,localPortNumber=4001'
```
Then locally:
```python
from openai import OpenAI
client = OpenAI(api_key="<bootstrap-or-minted-key>", base_url="http://127.0.0.1:4001/v1")
print(client.chat.completions.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": "Reply with one word: pong"}]).choices[0].message.content)
```

**B) External path — through Cloudflare Access:** the `/v1` API is behind Access,
so programmatic clients send a **service token** (`CF-Access-Client-Id` /
`CF-Access-Client-Secret`, issued in the Cloudflare dashboard) *plus* the gateway
virtual key:
```bash
curl https://gateway.<domain>/v1/chat/completions \
  -H "CF-Access-Client-Id: <id>" -H "CF-Access-Client-Secret: <secret>" \
  -H "Authorization: Bearer <gateway-virtual-key>" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"ping"}]}'
```

**C) Chat UI:** `https://chat.<domain>` (Open WebUI) — SSO via Okta, talks to the
façade with the bootstrap key.

**D) Control plane (admins):** `https://gateway.<domain>/admin/ui` — paste the
master key; create per-dev teams + scoped/budgeted keys, watch spend. Or script it:
```bash
GATEWAY_URL=https://gateway.<domain> GATEWAY_MASTER_KEY=<master> \
  ./scripts/provision-org.sh --org "Reviewer Max" --budget 50 --apply
```

---

## 6. Gov / multi-cloud tier — configured, not yet live

The gov-tier models (`gov/claude-opus-4-8` on AWS GovCloud Bedrock + GCP Vertex
Assured Workloads failover; `gov/gpt-4o` on Azure Gov) are registered in
`litellm-config.yaml` with posture tags (ADR-014/015) but have **no creds/egress**
in this deploy — calls SKIP. To light one up: seed its cloud creds, add the
provider endpoint to the Squid allowlist, and a gov-approved tenant key (via
`provision-org.sh --tier gov --approved-by "<name>"`). This is the documented
"multi-cloud, government-ready" path; dev-tier proves the pipe today.

## 7. Reverting the front door

To run the façade as a transparent proxy instead (keys/budgets back in LiteLLM):
set `GATEWAY_CONTROL_PLANE=false` on `gateway-facade` and recreate. To bypass the
façade entirely, point Open WebUI + the tunnel back at `litellm:4000`. See
[`own-gateway.md`](own-gateway.md) and [`../gateway/README.md`](../gateway/README.md).
