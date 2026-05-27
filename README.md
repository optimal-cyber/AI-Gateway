# Zero Trust AI Lab

A reference design for SBIR/CMMC-bound teams who need defensible AI tooling without
enterprise Zero Trust vendor pricing. Cloudflare Zero Trust + AWS + Okta Developer +
open-source components, assembled as a credible counterpart to a Zscaler-based
reference architecture.

Built by **Optimal, LLC (CAGE 14HQ0)** as a reference architecture for SBIR/STTR
awardees pursuing **CMMC Level 2 self-assessment readiness**.

> **This is a reference design, not a product, and not a CMMC assessment boundary.**
> It holds no client data and is not itself a compliance attestation. See the
> disclaimers section (added in Phase 6) and [docs/decisions.md](docs/decisions.md).

---

## Status

🚧 **Under construction.** This README is a stub; the full write-up (architecture
diagram, stack-comparison table, SSO model, prerequisites, cost estimate, security
notes) lands in **Phase 6**.

### Build phases

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffolding, ADRs, threat model, `.gitignore` | ✅ done |
| 1 | Terraform AWS baseline (VPC, NFW, EC2, secrets, logging) | ⬜ |
| 1.5 | Identity plane — Okta + Cloudflare Access SSO | ⬜ |
| 2 | Docker Compose stacks (Open WebUI, LiteLLM, NeMo) | ⬜ |
| 3 | Compliance MCP server (the differentiator) | ⬜ |
| 4 | Cloudflare config (tunnels, Access apps, Gateway, DNS) | ⬜ |
| 4.5 | Landing page at `lab.gooptimal.io` | ⬜ |
| 5 | Test plan + demonstration evidence | ⬜ |
| 6 | Full README + LinkedIn talking points | ⬜ |

Deploy order follows the phases: 0 → 1 → 1.5 → 2 → 3 → 4 → 4.5 → 5.

### Start here

- [docs/decisions.md](docs/decisions.md) — ADR log (the "why")
- [docs/threat-model.md](docs/threat-model.md) — STRIDE + AI-specific threats
- [prompts/CLAUDE_CODE_PROMPT.md](prompts/CLAUDE_CODE_PROMPT.md) — the full build spec

---

## Architecture (target)

```
User (browser, any network)
   │
   ▼
Cloudflare Access  ───► OIDC to Okta (MFA, group claims)
   │
   ▼
Cloudflare Tunnel (cloudflared on AWS EC2 — no public ingress)
   ├──► lab.gooptimal.io       ──► static landing page (Cloudflare Pages)
   ├──► chat.lab.gooptimal.io  ──► Open WebUI (trusted-header SSO)
   └──► gateway.lab...         ──► LiteLLM admin (direct OIDC to Okta)
                                       └──► NeMo Guardrails ──► OpenAI / Anthropic
                                       └──► compliance-mcp (read-only)
   │ (workload egress)
   ▼
AWS Network Firewall (domain allowlist) ──► internet
```

DNS: `gooptimal.io` stays on Google Cloud DNS; three CNAMEs under `lab.` point at
Cloudflare. Production records (`outpost.gooptimal.io`, MX, SPF/DKIM/DMARC) are never
touched. See [ADR-008](docs/decisions.md).

## Prerequisites

Populated in Phase 6. Tooling used so far: Terraform 1.6+, AWS provider 5.x, AWS CLI,
Docker + Compose, Python 3.11+, `wrangler` (Pages), `dig`.

## Cost

Target: **~$80–100/month at idle**, single-AZ, t3.small, gp3. AWS Network Firewall is
the largest line item (see [ADR-004](docs/decisions.md)). Full breakdown in Phase 6.
