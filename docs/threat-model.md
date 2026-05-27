# Threat Model — Zero Trust AI Lab

**Method:** STRIDE per component, plus an AI-specific section (prompt injection, RAG
poisoning, model exfiltration, tool misuse, secret leakage in prompts).
**Scope:** the lab as defined in [decisions.md](decisions.md) and the architecture
diagram in the master prompt. Personal AWS / Cloudflare / Okta; **no client data, no
FedRAMP boundary, no production GoOptimal services** are in scope.
**Risk rating:** Low / Med / High = likelihood × impact *within this sandbox*. Ratings
are deliberately conservative for a lab; an equivalent production system would rate
several of these higher.

Threat IDs follow `T-<COMPONENT>-<STRIDE-letter>`.

---

## Trust boundaries

```
                       Internet (untrusted)
  ────────────────────────────┬───────────────────────────────────
                              │  TB1: Cloudflare edge (Access + Okta OIDC)
                       Cloudflare global network
  ────────────────────────────┬───────────────────────────────────
                              │  TB2: Cloudflare Tunnel (outbound-only from VPC)
   AWS VPC 10.50.0.0/16   ┌────┴─────────────────────────────┐
                          │  private subnets (no IGW)         │
     chat-host  ──127.0.0.1 bind──  Open WebUI                │
        │  TB3: localhost-only trusted-header boundary        │
     gateway-host  LiteLLM ── NeMo ── compliance-mcp          │
        │  TB4: workload egress                               │
  ────────────────────────┴───────────────────────────────────────
            AWS Network Firewall (domain allowlist) → NAT → Internet
                              │  TB5: third-party APIs (OpenAI/Anthropic/SAM.gov)
```

- **TB1** — public → authenticated. Cloudflare Access + Okta. Default-deny.
- **TB2** — authenticated → VPC. Tunnel is outbound-only; no inbound ports.
- **TB3** — edge identity → app identity. The Open WebUI localhost bind (ADR-007).
- **TB4** — workload → internet. Network Firewall allowlist (ADR-004).
- **TB5** — lab → external SaaS/.gov. Provider keys + SAM.gov key.

---

## C1 — Cloudflare Access / Tunnel (the broker, TB1/TB2)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-CF-S | Spoofing | Attacker impersonates a valid user to reach an app | Okta OIDC + MFA enforced at Access; no app is reachable without a valid Access JWT | Low |
| T-CF-T | Tampering | Tamper with the Access JWT injected to origin | JWT is signed by Cloudflare; origin can verify via `Cf-Access-Jwt-Assertion` against the team JWKS | Low |
| T-CF-R | Repudiation | User denies an action | Access logs every authn event (user, policy matched, time) → correlated in audit-trail test | Low |
| T-CF-I | Info disclosure | App exposed publicly bypassing Access | No public ingress (SG opens 0 ports); tunnel is outbound-only; **verify SG has no 0.0.0.0/0 80/443** | Med |
| T-CF-D | DoS | Flood the public hostname | Cloudflare edge absorbs L3/4; Access challenges pre-origin | Low |
| T-CF-E | Elevation | `lab-users` user reaches the strict gateway app | Two separate Access apps; gateway requires `lab-admins` + MFA + WARP + US geo; explicit Block:Everyone tail rule | Low |

**Key invariant to verify each apply:** the EC2 security groups have **no** inbound
rule for 80/443 from `0.0.0.0/0`. This is the single most important check (T-CF-I).

---

## C2 — Open WebUI / chat-host (TB3)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-CHAT-S | Spoofing | **Forge `Cf-Access-Authenticated-User-Email`** to impersonate any user | Open WebUI binds `127.0.0.1:8080` only; only cloudflared (same host) can reach it; nothing else on the network path can inject the header. This bind is the entire boundary (ADR-007). | Med |
| T-CHAT-T | Tampering | Modify chat history / settings | Per-user identity from trusted header; volume on the host only; no multi-tenant write path exposed | Low |
| T-CHAT-R | Repudiation | User denies a prompt | `ENABLE_FORWARD_USER_INFO_HEADERS=true` propagates identity to LiteLLM → request log ties prompt to email | Low |
| T-CHAT-I | Info disclosure | One user reads another's chats | Open WebUI per-user separation; `ENABLE_SIGNUP=false`, `DEFAULT_USER_ROLE=user` | Low |
| T-CHAT-E | Elevation | Self-register as admin | Signup disabled; default role `user`; admin promotion is manual | Low |

**Standing risk:** any future change that binds Open WebUI to `0.0.0.0` or adds a
host port mapping **breaks T-CHAT-S** and must be rejected in review.

---

## C3 — LiteLLM gateway / gateway-host (TB3/TB4/TB5)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-LLM-S | Spoofing | Impersonate an admin in the LiteLLM UI | Direct Okta OIDC handshake (not a trusted header); `groups` claim → role; UI access `admin_only` | Low |
| T-LLM-T | Tampering | Alter virtual keys / budgets / model list | Admin-only UI; master key from Secrets Manager; `store_model_in_db` changes are audited in Postgres | Med |
| T-LLM-R | Repudiation | Deny issuing a key or making a call | Postgres request audit log; per-request virtual-key attribution | Low |
| T-LLM-I | Info disclosure | **Provider API keys leak** | Keys only on gateway-host, only in tmpfs `/run/ai-lab/gateway.env` (0600), pulled from Secrets Manager at boot; never in image/git; egress allowlist limits where a leaked key could be used from | Med |
| T-LLM-D | DoS | Runaway spend via the gateway | Per-key `max_budget` (default 0.0 for new internal users); only the webui virtual key has spend; provider-side limits | Med |
| T-LLM-E | Elevation | Viewer escalates to `proxy_admin` | Role derived from Okta group claim verified by LiteLLM, not user-supplied; `lab-admins` membership is the gate | Low |

---

## C4 — NeMo Guardrails (inline AI guard)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-NEMO-T | Tampering | Bypass/disable the guardrail to reach the model unfiltered | Guardrail is a separate service on the request path; LiteLLM calls it pre- and post-call; failure mode should **fail-closed** (verify at impl) | Med |
| T-NEMO-R | Repudiation | Deny that a block happened | Decision log (JSON) to file with logrotate; correlated to the request | Low |
| T-NEMO-D | DoS | Slow/expensive prompts stall the guard | Timeouts on the guardrail call; sized for demo load | Low |
| T-NEMO-bypass | (AI) | Novel jailbreak evades Colang policies | Defense-in-depth: guard is one layer; MCP is read-only; egress allowlisted; treat as detective not perfect-preventive | Med |

---

## C5 — compliance-mcp (the differentiator, TB5)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-MCP-S | Spoofing | Forge `x-caller-role: admin` to unlock PII in `sam_gov_lookup` | Header is injected by LiteLLM, not user-controllable; MCP only reachable inside the gateway-host docker network | Med |
| T-MCP-T | Tampering | **Mutate compliance data** via a tool | **No write tools exist** (ADR-005); SQLite opened read-only; write-mode gated behind `docs/mcp-write-mode.md` prerequisites | Low |
| T-MCP-R | Repudiation | Deny a lookup | structlog JSON: tool_name, caller_virtual_key_hash, duration, status, redacted_args | Low |
| T-MCP-I | Info disclosure | Leak SAM.gov POC PII | Email/phone redacted unless `include_pii=true` **and** caller is admin; data shipped is non-sensitive sample/seed | Low |
| T-MCP-D | DoS | SAM.gov upstream hangs/ratelimits | httpx 10s timeout; tenacity backoff on 429/5xx; circuit breaker opens after 5 consecutive failures | Low |
| T-MCP-E | Elevation | SQL/argument injection via tool args (e.g., `status_filter`) | Parameterized queries; `poam_list` filters on an enum-validated status; **no arbitrary string search** (`poam_summary` has no free-text filter); Pydantic-validated inputs | Med |

---

## C6 — AWS substrate (network / firewall / secrets / compute, TB4)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-AWS-S | Spoofing | Assume the instance role from elsewhere | Role trust scoped to EC2 service; IMDSv2 should be enforced (verify in compute module) | Med |
| T-AWS-T | Tampering | Modify firewall rules / route tables to widen egress | IaC-managed; change is a visible `terraform plan` diff; local state backed up | Med |
| T-AWS-R | Repudiation | Deny an admin shell session | SSM Session Manager logs sessions → CloudWatch (ADR-006) | Low |
| T-AWS-I | Info disclosure | Read `lab/*` secrets | Instance role scoped to `secretsmanager:GetSecretValue` on `arn:...:secret:lab/*` only; no `*` resource; no SSH key class to steal | Med |
| T-AWS-D | DoS | NAT/firewall saturation | Single-AZ, demo-scale; not a target | Low |
| T-AWS-E | Elevation | Over-broad instance role enables lateral movement | Least-privilege role; no `iam:*`, no broad `secretsmanager:*`; egress allowlist limits exfil destinations | Med |

**Verify each apply:** IMDSv2 required; instance role resource ARN is `lab/*` not `*`;
no SSH key in instance metadata; private subnets have no IGW route.

---

## C7 — Identity plane (Okta, TB1)

| ID | STRIDE | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T-OKTA-S | Spoofing | Phish the single operator account | MFA (password + Okta Verify) enforced in the Okta auth policy | Med |
| T-OKTA-T | Tampering | Alter group membership to gain `lab-admins` | Okta admin is the operator only; group changes are in the Okta system log | Low |
| T-OKTA-I | Info disclosure | Leak OIDC client secrets | Stored in Secrets Manager (`lab/okta_*`); never in git; `read -rs` in seed script keeps them out of shell history | Med |
| T-OKTA-E | Elevation | A `lab-users` member reaches admin surfaces | Gateway app + LiteLLM both require `lab-admins`; the two apps' assigned-group sets differ | Low |

---

## AI-specific threats (cross-component)

These are the threats a generic Zero Trust lab omits and this one deliberately
addresses. Each maps back to a component mitigation above.

| ID | Threat | Vector | Mitigation in this lab | Residual |
|---|---|---|---|---|
| AI-1 | **Prompt injection** | User prompt instructs the model to ignore policy, dump system prompt, or misuse a tool | NeMo pre-call detection (T-NEMO); MCP tools are read-only (T-MCP-T); tool args Pydantic/enum-validated (T-MCP-E) | Med |
| AI-2 | **RAG / data poisoning** | Malicious content in a retrieved/seeded source steers the model | Out of scope on first deploy (no live RAG); seed data is authored and reviewed; **flagged for the future `rag.lab` namespace** | Low |
| AI-3 | **Model / data exfiltration** | Coax the model to emit secrets or exfil via tool calls to attacker domains | Secret-pattern guardrail (PAT/AWS key/high-entropy) in NeMo; egress allowlist (ADR-004) blocks arbitrary exfil destinations; provider keys never in prompts | Med |
| AI-4 | **Tool misuse** | Injection chains the model into harmful tool calls | Read-only MCP; per-tool input validation; admin-gated PII; no write surface (ADR-005) | Med |
| AI-5 | **Secret leakage in prompts** | User pastes a real credential into chat; it lands in logs/provider | NeMo secret-pattern block on the prompt path; Cloudflare Gateway DLP paste-detection (Phase 4); request logs store hashed key refs, not raw secrets | Med |

---

## Top risks to watch (the short list an assessor would press on)

1. **T-CHAT-S / TB3** — the Open WebUI `127.0.0.1` bind is load-bearing. Any compose
   change that exposes the port forges-the-header-trivially. Guard in review.
2. **T-CF-I** — no-public-ingress depends on the security group never opening
   80/443 to the world. Verify on every `terraform plan`.
3. **T-LLM-I / T-AWS-I** — provider keys and `lab/*` secrets. tmpfs + scoped role +
   egress allowlist are the three layers; none alone is sufficient.
4. **T-NEMO-bypass / AI-1** — guardrails are detective, not perfect. The read-only
   MCP and egress allowlist are what bound the damage when a jailbreak succeeds.
5. **ADR-001 local state** — acceptable only while this holds no real data. The first
   thing to revisit if the boundary ever changes.
