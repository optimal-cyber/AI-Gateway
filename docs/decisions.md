# Architecture Decision Records — Zero Trust AI Lab

This is the ADR log for the Optimal Labs Zero Trust AI lab. Each record captures a
decision, the context that forced it, the alternatives considered, and the
consequences a fellow assessor would want to interrogate. Style-only choices are
decided silently in code; anything affecting **cost, blast radius, or compliance
posture** lands here.

Status legend: `Accepted` · `Superseded` · `Proposed`

---

## ADR-001 — Local Terraform state for the lab

**Status:** Accepted (2026-05-27)

**Context.** This is a single-operator personal lab. There is no team concurrently
applying, no CI pipeline running `terraform apply`, and the blast radius is one
personal AWS account holding no client data. Remote state (S3 + DynamoDB lock)
adds a bootstrap chicken-and-egg problem (you need TF to create the backend, or a
separate bootstrap) and a few dollars/month, in exchange for locking and
durability benefits that a solo operator does not need yet.

**Decision.** Use local Terraform state (`terraform.tfstate` on the operator
workstation), git-ignored. State is never committed.

**Alternatives.** (a) S3 backend with DynamoDB lock table — correct for any team or
production boundary, rejected as premature here. (b) Terraform Cloud free tier —
introduces a third-party dependency and account for no benefit at this scale.

**Consequences.**
- State lives on one machine; back it up manually before destructive applies.
- Migrating to S3 later is a documented one-time `terraform init -migrate-state`.
- If this lab ever holds anything resembling real data, **this ADR must be
  superseded** before that happens — local state is the first thing an assessor
  would flag if the boundary changed. Tracked as a Phase 2 consideration.

---

## ADR-002 — Cloudflare Access + Tunnel as the ZPA substitute

**Status:** Accepted (2026-05-27)

**Context.** The reference architecture this lab answers uses Zscaler Private Access
(ZPA) to broker identity-aware, network-invisible access to internal apps. The
goal is a *credible, defensible* equivalent built from components an SBIR-stage
team can actually afford. The control being demonstrated — "no public ingress;
every session is authenticated, authorized, and logged at the edge before a packet
reaches the workload" — is vendor-independent.

**Decision.** Cloudflare Access (identity-aware proxy, OIDC to Okta) in front of
Cloudflare Tunnel (`cloudflared` dialing out from the EC2 hosts). The EC2 security
groups open **zero** inbound ports to the internet; reachability is exclusively
through the tunnel's outbound connection. This is functionally the ZPA pattern:
the application is dark to the public internet, and the broker enforces identity.

**Alternatives.** (a) Zscaler ZPA — the thing being substituted; enterprise
pricing, not viable for the target audience. (b) AWS-native (ALB + Cognito + WAF)
— still requires public ingress to the ALB and does not give the
"workload-is-dark" property without more plumbing. (c) Tailscale/Headscale —
excellent mesh VPN but a different access model (device-centric, not a
browser-based IdP-fronted reverse proxy) and weaker per-app authz story for a
browser SaaS-style app like Open WebUI.

**Consequences.**
- Maps cleanly to NIST 800-53 **AC-3, AC-4, SC-7, IA-2** and the CMMC L2 access-
  control / boundary-protection practices — the same controls ZPA would be
  evidencing.
- Hard dependency on Cloudflare availability for the lab to be reachable.
- Free/Pay-as-you-go Zero Trust tier covers a single operator at no cost.
- Egress filtering is a *separate* problem (see ADR-004) — Access/Tunnel govern
  inbound identity, not outbound workload traffic.

---

## ADR-003 — NeMo Guardrails as the AI Guard substitute

**Status:** Accepted (2026-05-27)

**Context.** Commercial Zero Trust AI stacks bundle an "AI guard" / prompt-firewall
that inspects prompts and completions for injection, data loss, and policy
violations. The lab needs an open-source analogue that sits inline on the
LLM request path and produces an auditable allow/block decision.

**Decision.** NVIDIA **NeMo Guardrails**, run as a standalone service and wired into
LiteLLM as a Detection-as-a-Service (DaaS) guardrail on both the pre-call (prompt)
and post-call (completion) hooks. Colang policies cover prompt injection, jailbreak
attempts, secret exposure (PAT / AWS key / high-entropy regex), and PII (SSN, credit
card with Luhn). Every decision is logged.

**Alternatives.** (a) Llama Guard / Prompt Guard — strong models but narrower
(classification, not a policy engine with dialog flows); could be added *behind*
NeMo later. (b) Rebuff / commercial prompt-firewalls — either immature or paid.
(c) Pure regex in a LiteLLM custom callback — brittle, no dialog reasoning, harder
to defend as a "guardrail system."

**Consequences.**
- Inline latency on every LLM call; acceptable for a demo, flagged for tuning.
- Colang is its own DSL with a learning curve and version drift — **verify Colang
  syntax against current NeMo docs at implementation time** (Phase 2), do not trust
  recall.
- Demonstrates the AI-specific control surface (prompt injection, data exfil) that
  generic Zero Trust labs omit — a deliberate differentiator.

---

## ADR-004 — AWS Network Firewall first, Cloudflare Gateway later

**Status:** Superseded by ADR-009 (2026-05-27) — egress control is now a Squid
allowlist proxy by default; Network Firewall is retained as an optional module.
Original record kept below for the reasoning trail.

**Context.** Two distinct ways to constrain workload egress: (1) filter at the AWS
network boundary (Network Firewall on the VPC egress path), or (2) filter at the
identity/SASE layer (Cloudflare Gateway via a WARP Connector on the hosts). The
lab's strongest teaching point is that **workload egress allowlisting is the part
most cloud teams skip** — so it must be demonstrably enforced from day one.

**Decision.** Ship AWS **Network Firewall** with a stateful domain-allowlist rule
group as the Phase 1 egress control (api.openai.com, api.anthropic.com, api.sam.gov,
container registries, OS/package mirrors, and the AWS service + Cloudflare endpoints
the hosts genuinely need). Private-subnet default route goes to the firewall
endpoint, then to NAT. Cloudflare Gateway via WARP Connector is left as a **stubbed
Terraform module + `docs/phase2.md` TODO** — a documented upgrade path, not a day-one
dependency.

**Alternatives.** (a) Cloudflare Gateway first — couples egress policy to the same
vendor as ingress (concentration risk) and is harder to point at as an *AWS-native*
control during an assessment. (b) Both at once — more cost and moving parts than a
demo needs. (c) Security-group/NACL egress only — cannot do domain-based
allowlisting (SNI/HTTP host), only IP/port, which is the wrong granularity for
SaaS API endpoints behind rotating IPs.

**Consequences.**
- Network Firewall has a real hourly + per-GB cost — the single largest line item
  in the lab (see README cost table); flagged against the ~$80/mo ceiling.
- Domain allowlist must be maintained as upstreams change IPs/hostnames; the list
  is a Terraform variable so it is reviewable and diff-able.
- Maps to **SC-7 (boundary protection), AC-4 (information flow), SI-4 (monitoring)**.
- Phase 2 swap to Cloudflare Gateway is additive, not a rewrite.

---

## ADR-005 — Read-only MCP only on the first iteration

**Status:** Accepted (2026-05-27)

**Context.** The compliance MCP server is the lab's showcase: an LLM gateway that can
query *your own* compliance evidence (POA&Ms, NIST catalog, CMMC status) and a real
.gov API (SAM.gov). MCP tools that can **write** to a POA&M store or any upstream
system convert a prompt-injection bug into a data-integrity incident.

**Decision.** First deploy exposes **read-only tools exclusively**. No tool mutates
state. The POA&M SQLite store is opened read-only by the server. Write-mode is
treated as a separate security project with its own prerequisites, documented in
`docs/mcp-write-mode.md` (per-call approval, scoped IAM, Splunk audit, rollback,
prompt-injection regression suite, a dedicated `lab-mcp-write-approvers` Okta group).

**Alternatives.** (a) Ship read+write behind a feature flag — too easy to flip
without the surrounding controls; rejected. (b) No MCP at all — discards the
differentiator.

**Consequences.**
- A prompt-injection success can at worst *read* seeded, non-sensitive lab data —
  bounded blast radius, which is itself the point being demonstrated.
- The "what it takes to safely enable writes" doc becomes a portfolio artifact in
  its own right.

---

## ADR-006 — SSM Session Manager instead of SSH

**Status:** Accepted (2026-05-27)

**Context.** Operators need shell access to two EC2 hosts in private subnets with no
inbound internet exposure. SSH requires either a public IP + open port 22 (violates
the no-public-ingress requirement), a bastion (more cost/attack surface), or
key management.

**Decision.** **No SSH key material on instances and no port 22 anywhere.** All
operator access via AWS **SSM Session Manager** over the instance role + SSM agent,
with session logging to CloudWatch. Instances reach SSM through VPC service
endpoints / the egress allowlist, not the public internet for the API calls.

**Alternatives.** (a) EC2 Instance Connect — still SSH-based, needs port 22.
(b) Bastion host — extra instance, extra cost, extra thing to patch. (c) SSH over
the Cloudflare Tunnel — possible but over-engineered for admin shell.

**Consequences.**
- No SSH keys to rotate, lose, or leak — removes an entire credential class.
- Session Manager activity is itself auditable (who connected, when) → CloudWatch.
- Maps to **AC-2, AC-17 (remote access), AU-2**.
- Requires the SSM agent healthy and the instance role's SSM permissions correct —
  a `terraform plan` and a post-apply `aws ssm start-session` smoke test confirm it.

---

## ADR-007 — Trusted-header SSO for Open WebUI, direct OIDC for LiteLLM admin

**Status:** Accepted (2026-05-27)

**Context.** Two apps, two sensitivities. Open WebUI is the end-user chat surface
(low blast radius per user). The LiteLLM admin panel controls provider API keys,
issues virtual keys, and sets budgets (high blast radius — a compromise there is a
billing and data-egress event). Collapsing both onto one auth mechanism would
either over-trust the chat app or under-protect the admin panel.

**Decision.** Two independent auth surfaces:

- **Open WebUI → trusted-header SSO.** Cloudflare Access performs the full Okta OIDC
  handshake at the edge, then injects `Cf-Access-Authenticated-User-Email` /
  `Cf-Access-Authenticated-User-Name`. Open WebUI runs with `WEBUI_AUTH=true` and
  `WEBUI_AUTH_TRUSTED_EMAIL_HEADER=Cf-Access-Authenticated-User-Email`; the user's
  email becomes their identity automatically. **Load-bearing invariant:** Open WebUI
  binds to `127.0.0.1:8080` only. Anything that can reach the port *bypassing
  cloudflared* could forge the trusted header. The localhost bind is the entire
  security boundary for this app and is commented as such in the compose file.

- **LiteLLM admin → direct OIDC to Okta.** LiteLLM registers as its own Okta OIDC
  application and performs its own handshake, consuming Okta's `groups` claim for
  role mapping (`lab-admins` → `proxy_admin`, everyone else → viewer). Cloudflare
  Access *still* gates network reachability in front of it (defense in depth), but
  the app does not trust an injected header for privileged actions — it
  independently verifies the token.

**Alternatives.** (a) Trusted-header for both — under-protects the admin panel; a
header-forgery or misconfig becomes admin compromise. (b) Direct OIDC for both —
Open WebUI's OIDC is serviceable but the trusted-header path is simpler, gives a
cleaner edge audit trail, and is the more interesting pattern to demonstrate. (c)
Cloudflare Access service tokens — wrong tool for interactive human admin login.

**Consequences.**
- Two Okta OIDC apps to maintain (documented in `docs/okta-setup.md`).
- The two patterns side-by-side are a teaching artifact: *match the auth mechanism
  to the blast radius.*
- The localhost-bind invariant must survive every future compose edit — it is
  called out in the compose file and the threat model (T-CHAT-spoofing).
- Maps to **IA-2, IA-5, AC-6 (least privilege), AC-3**.

---

## ADR-008 — CNAMEs from Google DNS instead of full nameserver migration

**Status:** Accepted (2026-05-27)

**Context.** `gooptimal.io` is a live domain serving production GoOptimal services —
`outpost.gooptimal.io` (newsletter), MX records (email), and SPF/DKIM/DMARC TXT
records. Cloudflare's normal onboarding wants the whole zone (nameserver delegation
to Cloudflare). Delegating the apex would move *all* of those production records'
authority to Cloudflare, expanding blast radius far beyond this sandbox for zero
lab benefit.

**Decision.** Keep `gooptimal.io` authoritative on **Google Cloud DNS**. Add exactly
three CNAME records under the `lab.` namespace pointing at Cloudflare targets:

| Name | → |
|---|---|
| `lab.gooptimal.io` | Cloudflare Pages `*.pages.dev` (landing) |
| `chat.lab.gooptimal.io` | `<chat-tunnel-uuid>.cfargotunnel.com` |
| `gateway.lab.gooptimal.io` | `<gateway-tunnel-uuid>.cfargotunnel.com` |

The `lab.` prefix sandboxes the experiment and leaves headroom for future labs
(`rag.lab`, `mcp-write.lab`) without touching production DNS again.

**Alternatives.** (a) Full nameserver migration to Cloudflare — unacceptable blast
radius to production email and the newsletter for a personal sandbox. (b) A separate
throwaway domain — loses the portfolio value of demonstrating on the real
`gooptimal.io` brand and complicates the LinkedIn story.

**Consequences.**
- Production records (`outpost`, MX, SPF/DKIM/DMARC, apex) are **never touched** —
  this constraint is restated in bold at the top of `docs/google-dns-cnames.md`.
- CNAME-to-tunnel (vs. Cloudflare-proxied A records) means no orange-cloud
  Cloudflare features on these hostnames beyond what Tunnel/Access provide — which
  is exactly what we want here.
- Tunnel UUIDs are only known after the tunnels exist (Phase 4), so the CNAME values
  are filled in at that point.
- A DNS misconfiguration is contained to `*.lab.gooptimal.io`.

---

## ADR-009 — Squid allowlist proxy as default egress control; Network Firewall optional

**Status:** Accepted (2026-05-27). Supersedes the default-control decision in ADR-004.

**Context.** ADR-004 chose AWS Network Firewall (NFW) as the day-one egress control.
On costing it for `terraform plan`, a single NFW firewall endpoint bills at
**~$0.395/endpoint-hour ≈ $288/month** before per-GB data processing — by itself
~3.6× the ~$80/month ceiling in requirement #8 and well over the spec's own
"~$100/month at idle" README target. An always-on lab with NFW lands around
$350–380/month. The operator (cost discipline is priority #2) elected a cheaper
control that preserves the *demonstrated capability* — domain-allowlisted workload
egress — without the managed-service price.

**Decision.** Default egress control is a **hardened Squid forward proxy** enforcing
a `dstdomain` allowlist, behind a **NAT Gateway**. Enforcement is structural, not
opt-in:

- **App subnets have no `0.0.0.0/0` route** (route table is `local`-only). The sole
  path to the internet is the Squid proxy's private IP (reached via the in-VPC local
  route). Anything that is not proxy-aware simply has no egress — the allowlist
  cannot be bypassed by ignoring an env var.
- **Squid hardening:** explicit `dstdomain` allowlist ACL; `http_access deny all`
  tail rule (default-deny); `CONNECT` permitted only to 443; HTTP (80) only to the
  package/OS mirrors that genuinely need it; caching disabled (`cache deny all` — no
  traffic data at rest); runs as the unprivileged `squid` user; **no public IP**;
  security group accepts 3128 only from the app subnet CIDR; `access.log` shipped to
  CloudWatch.
- **NAT Gateway** sits behind the proxy (proxy subnet → NAT → IGW), so even the proxy
  has no public IP. The operator explicitly asked for NAT (managed, no public IP on
  any instance) over a cheaper NAT-instance.
- **AWS Network Firewall is retained as an optional module**, gated by
  `enable_network_firewall` (default `false`). When enabled, the network module
  reroutes app subnets through the firewall endpoint. This keeps the AWS-native
  artifact available on demand for a client demo without billing 24/7.

**Why the proxy destination check is sound.** Squid dials the `CONNECT` target host
itself and resolves it via VPC DNS, so a client cannot reach `evil.com` by lying in
an SNI field — the destination is the CONNECT host, allowlisted by `dstdomain`. The
residual is the same class NFW carries (a permitted domain that is itself malicious),
plus reliance on VPC DNS integrity. Documented in the threat model (TB4).

**AWS service reachability.** The app instances reach SSM, Secrets Manager, and
CloudWatch Logs *through the proxy*, so the allowlist includes the regional AWS
service endpoints (`ssm`, `ssmmessages`, `ec2messages`, `secretsmanager`, `logs`,
`s3`) alongside the provider/registry/mirror domains. `NO_PROXY` covers IMDS
(`169.254.169.254`), localhost, and the VPC CIDR so instance-role creds and intra-VPC
traffic never traverse the proxy. (VPC interface endpoints were considered but add
~$7/mo each × ~5 ≈ $35/mo — rejected on cost vs. routing those few domains through
the proxy we are already running.)

**Cost.** Egress infra ≈ NAT (~$33/mo) + Squid t3.micro (~$8/mo) ≈ **$41/mo** vs.
~$288/mo for NFW. Total lab ≈ **$75–85/month at idle**, inside the ceiling.

**Alternatives.** (a) NFW default-on — rejected on cost (this ADR). (b) NFW
default-off toggle — leaves egress unfiltered when off, a documented gap; rejected in
favor of an always-on control. (c) Squid in a public subnet with a public IP and no
NAT (~$8/mo total) — cheaper, but the operator asked for NAT and no-public-IP is the
stronger posture; rejected on the security-vs-$33 tradeoff. (d) NAT instance instead
of NAT Gateway — ~$4/mo but operator-managed; rejected for the managed service.

**Consequences.**
- Maps to the same controls ADR-004 cited — **SC-7, AC-4, SI-4** — via a host-based
  allowlisting proxy rather than a managed boundary appliance. The assessor-facing
  evidence shifts from "AWS-managed firewall logs" to "proxy access logs + a
  routing topology that forces all egress through the allowlist." Both are
  defensible; the routing topology is the load-bearing part.
- Docker daemon, the SSM agent, and the AWS CLI on the app hosts must be
  proxy-configured (user-data sets `/etc/environment`, a Docker systemd drop-in, and
  the SSM agent proxy). **Phase 2 compose files must propagate `HTTP(S)_PROXY` /
  `NO_PROXY` into the containers** that make outbound calls (LiteLLM → providers, MCP
  → SAM.gov). Tracked as a Phase 2 dependency.
- The allowlist is a Terraform variable (`egress_allowlist_domains`) — reviewable and
  diff-able, same property ADR-004 wanted.
- Phase 2 Cloudflare Gateway upgrade path (docs/phase2.md) is unchanged.
