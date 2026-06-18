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

**Status:** **Superseded by ADR-010** (2026-05-28). The original reasoning is
preserved below for the audit trail; what was actually deployed is in ADR-010.

**Context.** `gooptimal.io` is a live domain serving production GoOptimal services —
`outpost.gooptimal.io` (newsletter), MX records (email), and SPF/DKIM/DMARC TXT
records. Cloudflare's normal onboarding wants the whole zone (nameserver delegation
to Cloudflare). Delegating the apex would move *all* of those production records'
authority to Cloudflare, expanding blast radius far beyond this sandbox for zero
lab benefit.

**Decision (original).** Keep `gooptimal.io` authoritative on **Google Cloud DNS**.
Add exactly three CNAME records under the `lab.` namespace pointing at Cloudflare
targets:

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

**Consequences (as originally intended).**
- Production records (`outpost`, MX, SPF/DKIM/DMARC, apex) are **never touched**.
- CNAME-to-tunnel (vs. Cloudflare-proxied A records) means no orange-cloud
  Cloudflare features on these hostnames beyond what Tunnel/Access provide — which
  is exactly what we wanted.
- Tunnel UUIDs only known after the tunnels exist (Phase 4), so CNAMEs were filled
  in at that point.
- A DNS misconfiguration would have been contained to `*.lab.gooptimal.io`.

**Why this didn't actually deploy.** Cloudflare Access requires the application
domain to live in a Cloudflare-managed zone. Tunnel ingress works for any hostname,
but the Access policies (which are the entire point of the identity-aware proxy)
cannot bind to a domain Cloudflare doesn't host. Free-plan subdomain zones (e.g.
adding only `lab.gooptimal.io` as its own zone) are rejected — that's an Enterprise
feature. See ADR-010 for the actual deployed approach.

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

- **No direct HTTP/HTTPS egress from the app hosts.** The app security group permits
  outbound only: all intra-VPC traffic (proxy 3128, gateway 4000, VPC DNS) plus
  **cloudflared's tunnel port 7844** straight to the Cloudflare edge. Ports 80/443
  are **not** opened to the internet, so every HTTP/HTTPS call is forced through the
  Squid allowlist — the allowlist cannot be bypassed by ignoring a proxy env var,
  because the SG drops the packets.
  *(Amendment 2026-05-27: the first cut gave the app subnet no default route at all,
  but cloudflared connects to the edge over QUIC/HTTP2 on udp/tcp 7844 and cannot use
  an HTTP CONNECT proxy — the tunnel could never establish. The app subnet now has a
  NAT default route in proxy mode; the SG, not the absence of a route, is what forces
  HTTP/HTTPS through the proxy. The 7844 rule is `0.0.0.0/0` today; hardening TODO is
  to scope it to Cloudflare's edge IP ranges.)*
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

---

## ADR-010 — Use `ironechelon.com` for the lab subdomain; landing intentionally on Cloudflare DNS

**Status:** Accepted (2026-05-28). Supersedes ADR-008. **Further refined by ADR-011**
on the same day: the lab actually deployed at `chat.ironechelon.com` /
`gateway.ironechelon.com` (apex-level) rather than `*.lab.ironechelon.com`
because Cloudflare's free Universal SSL only covers one wildcard level. The
`ironechelon.com`-as-host zone decision below is correct and stands; only the
hostname level changed.

**Context.** ADR-008 wanted the lab apps on `*.lab.gooptimal.io` with DNS still on
Google so production records stayed isolated. When we hit Phase 4 (Cloudflare
Access wiring), the design failed on a Cloudflare product limit:

1. **Tunnel ingress** is account-scoped and accepts any hostname (set via API for
   `chat.lab.gooptimal.io` and `gateway.lab.gooptimal.io` — that worked).
2. **Access apps** are zone-scoped and **only accept domains in a Cloudflare-managed
   zone** (API error `12130: domain does not belong to zone`). Without an Access
   app on the hostname, the whole identity-aware proxy story collapses — that's
   the *point* of the lab.
3. **Free-plan subdomain zones** (e.g., adding only `lab.gooptimal.io` as its own
   zone, with NS delegation from Google) are **rejected by Cloudflare Free** — the
   onboarding requires a registrable apex. Subdomain zones are an Enterprise
   feature.
4. **Full nameserver migration of `gooptimal.io` to Cloudflare** would re-create
   the original blast-radius problem (MX, SPF, DKIM, `outpost`, `ai-security`,
   `compliance`, `api/app/auth`, and the apex wildcard would all move to a single
   new vendor). ADR-008's original concern still applies.

**Decision.** Move the lab subdomain to **`ironechelon.com`** (already an active
Cloudflare zone on the same account). Use:

| Name | → | Where DNS lives |
|---|---|---|
| `lab.ironechelon.com` | Cloudflare Pages (landing) | **Cloudflare DNS** |
| `chat.lab.ironechelon.com` | `<chat-tunnel-uuid>.cfargotunnel.com` (proxied) | **Cloudflare DNS** |
| `gateway.lab.ironechelon.com` | `<gateway-tunnel-uuid>.cfargotunnel.com` (proxied) | **Cloudflare DNS** |

The two app CNAMEs are **proxied (orange cloud)** — that's what activates Access
enforcement on the request path.

**Alternatives reconsidered.**
- *Keep gooptimal.io path with no Access* — defeats the entire purpose of the lab.
- *Pay for Cloudflare Enterprise* — ~$2k/month minimum; not viable for a personal
  reference design budgeted at ~$80/mo.
- *Throwaway domain* — same brand-cost trade we considered originally; we already
  have `ironechelon.com` paid for and active.

**Consequences.**
- `gooptimal.io` is **completely untouched** — same protection as ADR-008 intended.
  No production records (MX, SPF, DKIM, `outpost`, `ai-security`, etc.) were ever
  modified. Two CNAMEs were briefly added under `lab.gooptimal.io` during the
  attempt and have been removed.
- The Okta LiteLLM Admin app's redirect URI shifted to
  `https://gateway.lab.ironechelon.com/sso/callback` (the Cloudflare Access Okta
  app's redirect URI is tied to the team name `optimallabs`, so it didn't change).
- The portfolio brand on the landing page splits slightly: lab apps under
  `*.lab.ironechelon.com`, but the larger `Optimal, LLC / Optimal Labs` framing on
  the landing page is unchanged. The trade is honest in `docs/linkedin-talking-points.md`.
- Going forward the `lab.` namespace lives on Cloudflare DNS, so future labs
  (`rag.lab`, `mcp-write.lab`, etc.) can be added there directly without DNS work
  elsewhere.
- `docs/google-dns-cnames.md` is now misleading-by-filename but useful as a record
  of the original plan — kept (rather than deleted) so the git history remains
  readable.

---

## ADR-011 — Drop the `lab.` namespace; deploy at apex-level subdomains

**Status:** Accepted (2026-05-28). Refines ADR-010.

**Context.** With ADR-010 the lab moved to `*.lab.ironechelon.com`. When we hit
Phase 4 end-to-end testing, browsers got `SSLV3_ALERT_HANDSHAKE_FAILURE` against
both `chat.lab.ironechelon.com` and `gateway.lab.ironechelon.com`. The Cloudflare
edge is reachable, the tunnel is healthy with 4 connections, the cert chain just
fails the TLS handshake.

Diagnosis: **Cloudflare's free Universal SSL covers the apex domain and one
wildcard level only** (i.e., `ironechelon.com` and `*.ironechelon.com`). Two-level
deep subdomains like `chat.lab.ironechelon.com` need either:

1. An **Advanced Certificate** (~$10/month per zone) explicitly listing
   `*.lab.ironechelon.com` in the SAN, or
2. **Cloudflare for SaaS / Custom Hostnames** (more complex configuration,
   per-hostname certs).

Verified by inspecting the cert Cloudflare presents at the apex (`SAN=ironechelon.com`)
vs. empty/handshake-failure at the two-level hostnames.

**Decision.** Drop the `lab.` level. Deploy the lab apps at **apex-level
subdomains** that Universal SSL covers:

| Original (ADR-010) | Actually deployed |
|---|---|
| `chat.lab.ironechelon.com` | `chat.ironechelon.com` |
| `gateway.lab.ironechelon.com` | `gateway.ironechelon.com` |

Tunnel UUIDs, Access policies (chat permissive 24h, gateway strict 4h with MFA
+ US geo), Okta IdP wiring, and the AWS infrastructure are all unchanged — only
the published hostnames and DNS records change.

**Alternatives.**
- *Pay for Advanced Certificate* — kept the `lab.` prefix at ~$10/mo. Rejected
  because the lab is budgeted at ~$80/mo and the prefix is aesthetic, not
  functional. The cost line is real money over the life of a portfolio piece.
- *Cloudflare for SaaS* — works for free with custom hostnames, but the config
  is more involved and less typical for what the lab demonstrates. Rejected on
  complexity grounds.
- *Move to a 1-level-deep subdomain on a Cloudflare zone that allowed it* —
  same effort as just dropping `.lab` here.

**Consequences.**
- **Visible URL change:** users (and the LinkedIn post) hit
  `https://chat.ironechelon.com` and `https://gateway.ironechelon.com`. The
  `lab.` prefix that signaled "sandbox" is gone — the architecture/policy
  signaling lives in the Cloudflare Access setup and the apps themselves now.
- **Okta** LiteLLM admin redirect URI updated to `https://gateway.ironechelon.com/sso/callback`.
- **DNS in Cloudflare** for `ironechelon.com`: `chat.lab` / `gateway.lab` records
  removed; `chat` and `gateway` records added, both **proxied (orange cloud)**.
- **Cost:** **$0 incremental** (Universal SSL is free; no Advanced Cert needed).
- **AWS infrastructure, Okta groups/policies, tunnel UUIDs, Access policies,
  repo code** — all unchanged at the design level. A repo find/replace covered
  the hostname references in docs, compose, scripts, and Terraform comments.
- **Future labs** (`rag`, `mcp-write`, etc.) — same pattern: each gets its own
  apex-level subdomain on `ironechelon.com` rather than a sub-subdomain.
- **`gooptimal.io` still untouched** — same protection ADR-008 originally wanted.

**Lessons captured for `docs/linkedin-talking-points.md`:** "Universal SSL only
covers one wildcard level" is the kind of vendor detail that bites at deploy
time. The fix took 5 minutes but the diagnosis took 30. Worth surfacing as a
gotcha when describing the lab.


## ADR-012 — Per-host instance type: gateway-host on t3.medium, chat-host on t3.small

**Date:** 2026-05-29
**Status:** Accepted
**Supersedes / amends:** none (refines the cost model in `docs/cost.md`)

### Context

The lab originally sized both app hosts (`chat-host`, `gateway-host`) as
**t3.small** (2 vCPU, 2 GB RAM) to stay under the ~$80/mo ceiling that drove
ADR-009. That sizing was fine in the first few deploys because NeMo Guardrails'
LLM rails weren't actually initializing — the SDK was failing on a missing
`dataclasses-json` transitive dep at startup, falling back to deterministic
detectors only. The deterministic path was always the authoritative block per
ADR-003, so the lab worked, but `nemo_enabled` was `false` and the
`activated_rails` audit field was empty.

After fixing the import (`dataclasses-json>=0.6` pinned in
`docker/gateway-host/nemo/Dockerfile`), NeMo's `LLMRails(config)` now loads
langchain + tokenizer footprint at boot — measured RSS spike of ~600–1000 MB on
top of the existing LiteLLM (~400 MB), Postgres (~50 MB), compliance-mcp
(~80 MB), and cloudflared (~30 MB). Two simultaneous container recreates
(NeMo + LiteLLM during a deploy) pushed total resident over 2 GB; the kernel
OOM-killed the largest tractable victim, which turned out to be the SSM agent.
The instance kept running, cloudflared kept the tunnel up, but commands came
back `Undeliverable`, and we had to stop+resize+start to recover.

We considered two options:
1. **Bump both hosts to t3.medium** (4 GB RAM). +$30.40/mo total. Total lab
   ~$95/mo — over ceiling.
2. **Bump only gateway-host to t3.medium**. +$15.20/mo. Total lab ~$80–85/mo —
   at ceiling. chat-host runs Open WebUI + cloudflared only (~400 MB resident);
   t3.small fits with margin.

### Decision

Per-role instance type via `instance_type_overrides` map on the compute module:

```hcl
variable "instance_type_overrides" {
  type    = map(string)
  default = { gateway = "t3.medium" }
  # chat: inherits var.instance_type default (t3.small)
}
```

The resource picks via `lookup(var.instance_type_overrides, each.key, var.instance_type)`.

### Consequences

- **Gateway-host: t3.medium (2 vCPU, 4 GB).** Headroom for NeMo SDK warmup +
  one or two concurrent provider streams from LiteLLM without OOM thrash.
- **Chat-host: t3.small (2 vCPU, 2 GB).** Unchanged. Open WebUI is the only
  Python service of consequence here.
- **Cost:** +$15.20/mo (one instance step up). Total lab cost stays at the
  ~$80/mo ceiling target, with room for the t3.small line item to absorb the
  delta.
- **Recovery procedure:** for future OOM events, stop → modify instance type →
  start is the cleanest recovery (it lets systemd re-spawn everything cleanly
  via `ai-lab-secrets@<role>` and the docker compose restart policies). Avoid
  in-place reboot when the host is already over-budget on memory.
- **Detection capability uplift:** with NeMo SDK loaded, T-GR-* tests now
  produce both deterministic findings AND populated `activated_rails` audit
  records — the LinkedIn / 3PAO walkthrough story gets concretely better.


## ADR-013 — Move the lab to `optimallabs.io`; deprecate `ironechelon.com`

**Date:** 2026-05-29
**Status:** Accepted
**Supersedes / amends:** ADR-010 (operationally — `ironechelon.com` was always
the placeholder), ADR-011 (cert constraint reasoning still applies, now to
`*.optimallabs.io`).

### Context

`ironechelon.com` was registered as a workaround when `gooptimal.io` couldn't
host the lab subdomain on Cloudflare Free tier without paying for Advanced
Certificate Manager (ADR-010). The build proceeded under that placeholder, but
the user's actual brand for AI lab / product work is **Optimal Labs** (LLC =
Optimal, LLC). Shipping a portfolio artifact under `chat.ironechelon.com` reads
as "who is this?" to anyone landing from LinkedIn. After T-SSO-1 passed
end-to-end (see commit dc45d0b path), the user purchased `optimallabs.io` from
Cloudflare Registrar ($50/yr at-cost) for the cleaner brand match. The zone
landed in the same Cloudflare account, so no nameserver gymnastics — the swap is
just plumbing.

### Decision

Move all lab hostnames to `optimallabs.io`:

| Old (placeholder) | New (live) |
|---|---|
| `chat.ironechelon.com` | `chat.optimallabs.io` |
| `gateway.ironechelon.com` | `gateway.optimallabs.io` |
| `lab.ironechelon.com` (landing, never deployed) | n/a — landing TBD on `optimallabs.io` |

**Strategy:** add-then-deprecate, zero downtime. We added the new hostnames as
additional destinations on the existing Cloudflare Access apps (the newer Access
UI supports up to 5 destinations per app — we did NOT need parallel apps with
duplicated policies, which was our first attempted approach before catching the
multi-destination support). The existing reusable policies (`allow-lab-users-or-admins`
for chat, `allow-lab-admins-strict` for gateway) enforce both hostnames
automatically. The existing Cloudflare Tunnel `lab-chat` and `lab-gateway` got
the new public-hostname routes alongside the ironechelon ones, so the tunnel
tokens in Secrets Manager (`lab/cloudflare_tunnel_token_chat`,
`lab/cloudflare_tunnel_token_gateway`) needed no change.

**Okta:** added `https://gateway.optimallabs.io/sso/callback` to the LiteLLM
admin OIDC app's Sign-in redirect URIs alongside the existing ironechelon
entry. (Note: the initial Phase 7 walkthrough mistakenly named
`/sso/key/generate` as the new redirect URI; that path is the LiteLLM admin's
create-virtual-key page, not an OIDC callback target. LiteLLM's actual OIDC
callback is `PROXY_BASE_URL + /sso/callback`. The error surfaced as an Okta
400 Bad Request — "redirect_uri parameter must be a Login redirect URI in the
client app settings" — on the first attempt to sign into the optimallabs
admin URL. Fix was to add the `/sso/callback` URI and delete the wrong
`/sso/key/generate` entry.) The `[CF Access] Cloudflare Access` OIDC app (used
for chat SSO) has no host-bound URI to change — Access handles the redirect at
the Cloudflare layer.

**Repo:** blind search-and-replace `ironechelon.com → optimallabs.io` and
`ironechelon → optimallabs` across 14 files (compose, secrets-bootstrap,
README, scripts, terraform module comments, docs other than this file). This
file (`docs/decisions.md`) was intentionally left alone so ADR-010 and ADR-011
remain accurate history.

**LiteLLM:** `PROXY_BASE_URL` in `secrets-bootstrap.sh` flipped from
`https://gateway.ironechelon.com` to `https://gateway.optimallabs.io`; the
gateway-host's `ai-lab-secrets@gateway` unit regenerates the tmpfs `.env` and a
LiteLLM container recreate picks up the new value (LiteLLM uses this as the
allowed redirect target after Okta OIDC).

### Consequences

- **Effective immediately:** `https://chat.optimallabs.io` and
  `https://gateway.optimallabs.io` serve the lab end-to-end with the same Okta
  IdP and policies.
- **Fallback window:** `chat.ironechelon.com` and `gateway.ironechelon.com`
  keep working through the deprecation period (same tunnels, same Access apps,
  same policies) so an in-progress LinkedIn draft or shared screenshot doesn't
  404 the moment we cut over.
- **`gooptimal.io` still untouched.** Same protection ADR-008 originally wanted
  — email/marketing zone stays clean.
- **Cleanup TODO:** once we ship the LinkedIn post with the optimallabs URLs:
  - Remove `chat.ironechelon.com` / `gateway.ironechelon.com` from the two
    tunnels' Published application routes.
  - Remove the ironechelon hostnames from the two Access apps' Destinations
    (leaving each app with only its optimallabs hostname).
  - Remove the ironechelon redirect URI from the Okta LiteLLM app.
  - Delete the orphaned reusable policy `AI Lab — Chat (optimallabs)` that
    was created during the failed parallel-app attempt before we caught the
    multi-destination support.
  - Optionally: let `ironechelon.com` lapse at next renewal (no specific value
    in keeping it once unused).

**Lesson captured for `docs/linkedin-talking-points.md`:** the cost of pivoting
DNS late in a build is much lower than you fear when (a) both old and new zones
live in the same Cloudflare account, and (b) the Access app destination model
supports multi-hostname so policies can be shared. The whole swap was ~30 min
of dashboard work and one 60-second SSM redeploy.

---

## ADR-014 — Government-ready model tiers and per-model compliance posture

**Date:** 2026-06-18
**Status:** Accepted
**Implements:** [`docs/roadmap.md`](roadmap.md) Phase G1.

### Context

The gateway started as one team in front of commercial OpenAI/Anthropic
endpoints. The north star ([roadmap](roadmap.md)) is an access layer that lets
approved organizations reach *government-ready* AI across multiple clouds. Before
adding clouds (G2) or gating tenants by what they may reach (G3), the gateway
needs a machine-readable answer to one question per model: **"is this deployment
served from a compliant boundary, and what is its posture?"** Without that,
"government-ready" is a marketing word, not a routing/authorization input.

A model's compliance posture is a property of *where and how it is served*, not
of the weights. `gpt-4o` on commercial OpenAI and the same family on Azure
Government are the same model with very different postures. So the unit that
carries the tag is the gateway's `model_list` entry (the deployment), not the
bare model name.

### Decision

1. **Two tiers, tagged on every `model_list` entry** via LiteLLM `model_info`:
   - `tier: dev` — commercial / non-government boundary. Default for the existing
     OpenAI-direct and Anthropic-direct entries. Fine for development and demos;
     **never** presented as government-ready.
   - `tier: gov` — served from a documented government-ready boundary (below).

2. **Posture schema** (`model_info`), on every entry:

   | field | values |
   |---|---|
   | `tier` | `gov` \| `dev` |
   | `boundary` | human-readable boundary (e.g. `AWS GovCloud (Bedrock)`) |
   | `cloud` | `aws` \| `azure` \| `gcp` \| `anthropic` \| `openai` |
   | `region` | serving region (e.g. `us-gov-west-1`) |
   | `fedramp` | `high` \| `moderate` \| `in-process` \| `none` |
   | `residency` | `us` \| other |
   | `retention` | `none` \| `30d` \| `provider-default` |
   | `il` | DoD Impact Level eligibility, or `n/a` |

3. **What counts as a `gov` boundary.** A boundary qualifies for `tier: gov` only
   when its operator publishes the authorization the tag claims (FedRAMP / IL /
   residency). At G1 the recognized targets are:
   - **Claude Platform on AWS** — Anthropic-operated via AWS (SigV4 + AWS IAM,
     US). Chosen as the first government-ready *Claude* path because it has **full
     API parity** with the first-party API (server-side tools, Managed Agents),
     unlike the partner-operated boundaries.
   - **Amazon Bedrock in AWS GovCloud** — FedRAMP High / IL4–IL5 boundary,
     `anthropic.`-prefixed model IDs; partner-operated, so a feature subset (no
     Anthropic server-side tools / Managed Agents).
   - (G2 extends the same tagging discipline to Azure Government / Microsoft
     Foundry and Vertex AI Assured Workloads.)

4. **The tag is a claim about the deployment boundary, evidenced by the boundary
   operator — not an attestation by this lab.** A `gov` tag asserts "served from a
   boundary its operator has authorized to that posture," nothing more. The lab
   inherits no authorization and stays a reference architecture.

### Consequences

- `litellm-config.yaml` carries `model_info` posture on every entry; commercial
  endpoints are explicitly `tier: dev`, and gov-tier entries exist for the
  recognized boundaries.
- **Gov-tier entries are config-ready but not live in this lab.** The lab runs in
  a commercial AWS account (`317839577064`, us-east-1) with no GovCloud /
  Claude-Platform-on-AWS credentials. The entries provision routing + posture;
  they go live once the gov boundary's credentials (Secrets Manager) and egress
  (Squid allowlist) are added. Model strings carry the same "verify against your
  deployed LiteLLM version" caveat as the existing entries.
- The `tier` field becomes the input G3 uses for per-org model allow-lists (an
  org may be approved for `dev` only, or for `gov`).
- The smoke test asserts a `gov`-tagged model is *registered* at the gateway
  (`/v1/models`); the live call SKIPs until gov credentials exist (T-GW-5).
- Honesty guardrail unchanged: README/landing keep "government-ready
  architecture," never "this lab is authorized."

---

## ADR-015 — Multi-cloud government-ready boundaries + posture-aware routing/failover

**Date:** 2026-06-18
**Status:** Accepted
**Implements:** [`docs/roadmap.md`](roadmap.md) Phase G2. **Builds on:** ADR-014.

### Context

ADR-014 established the gov/dev tier and tagged one gov boundary (AWS GovCloud via
Bedrock). The north star is *multiple* clouds, so an approved org isn't locked to
one provider's authorization, region, or availability. G2 wires additional
government-ready boundaries under the same posture-tagging discipline and makes a
single logical gov model resolve across them with failover.

The provider-parity caveat from the claude-api reference matters here: Anthropic
server-side tools and Managed Agents run only on Anthropic-direct and Claude
Platform on AWS — *not* Bedrock / Vertex / Azure Foundry. The gateway's MCP /
government-service tool-routing is LiteLLM-side, so the *services* (SAM.gov,
Federal Register, NIST, CMMC) stay available cross-cloud even where provider-native
agent features don't.

### Decision

1. **G2 government-ready boundaries** (each tagged per ADR-014):
   - **AWS GovCloud (Bedrock)** — from G1.
   - **GCP Vertex AI (Assured Workloads)** — `vertex_ai/claude-*`; FedRAMP boundary
     via Assured Workloads, US residency.
   - **Azure Government (Azure OpenAI)** — `azure/*` against the `*.openai.azure.us`
     Government endpoint; FedRAMP High boundary.

2. **Posture-aware routing/failover.** A logical gov model name with ≥2 same-named
   `model_list` deployments forms a LiteLLM routing group; the proxy load-balances
   and retries the next deployment on failure. `gov/claude-opus-4-8` now has two
   deployments (AWS GovCloud + GCP Assured Workloads) → cross-cloud failover for
   one logical model. `router_settings` sets the strategy + retry count.

3. **Per-cloud egress, scoped.** Each boundary's endpoint is added to the Squid
   allowlist scoped to the exact host family — `.aiplatform.googleapis.com`,
   `.openai.azure.us` — never a broad `.googleapis.com` / `.azure.us`, to keep
   default-deny intact.

4. **Same honesty + liveness rule as G1.** All G2 gov entries are CONFIG-READY,
   NOT LIVE — the lab holds no Vertex / Azure-Gov credentials. They register,
   carry posture, and form the routing group; they go live when each boundary's
   creds and egress are provisioned.

### Consequences

- The gov tier now spans **three clouds** (AWS GovCloud, GCP Assured Workloads,
  Azure Government), and `gov/claude-opus-4-8` is a cross-cloud failover group.
- **Acceptance (G2 done-when):** one logical gov model resolves across ≥2 clouds
  with failover. Verifiable end-to-end only with provisioned gov creds; the host
  smoke test verifies the gov catalog is *registered* (T-GW-5), not live failover.
- Server-side-tool / Managed-Agents parity differs by boundary (full on
  Anthropic-direct + Claude Platform on AWS; subset on Bedrock / Vertex / Azure).
  The gateway's MCP government-services are unaffected.
- Sets up G3: per-org allow-lists can now select a tier *and* constrain which
  clouds/regions an org may use.

---

## ADR-016 — Approved-organization tenancy: one org = one LiteLLM team

**Date:** 2026-06-18
**Status:** Accepted
**Implements:** [`docs/roadmap.md`](roadmap.md) Phase G3. **Builds on:** ADR-014/015.

### Context

The lab has been single-operator (one team `AI-Lab`, one key `open-webui`). The
north star is an access layer for *approved organizations*. Each org needs its own
scoped credentials, budget, and a model allow-list deciding which posture tiers
(ADR-014) it may reach — with isolation between orgs in spend and audit.

LiteLLM already models this primitive: a **team** owns virtual keys, a
`max_budget`, rate limits, and a `models` allow-list. So the tenancy unit is a
LiteLLM team; "approved organization" = a provisioned team plus the identity
mapping that lets its users authenticate to it.

### Decision

1. **Org = LiteLLM team.** Each approved org is one team with its own virtual
   key(s), a `max_budget` (+ tpm/rpm limits), and a **model allow-list scoped by
   approved tier**:
   - a `dev`-approved org gets the `dev` models only;
   - a `gov`-approved org gets the `gov/*` models only — **not** the commercial
     `dev` endpoints, so a gov tenant's prompts never leave a gov boundary;
   - (an org explicitly approved for both can be granted both lists.)

2. **Identity → tenant.** Users reach their org via Okta group → team mapping: an
   Okta group per org, mapped to the org's LiteLLM team, gated at Cloudflare
   Access the same way the lab's groups are (ADR-007). **Upgrade path (documented,
   not built):** B2B federation — an org brings its own IdP via Okta org-to-org /
   OIDC federation — for orgs that won't live in the lab's tenant.

3. **Provisioning is a runbook + script, not click-ops.** Onboarding an approved
   org is [`docs/org-onboarding.md`](org-onboarding.md) +
   `scripts/provision-org.sh` (LiteLLM admin API: `/team/new` → `/key/generate`),
   so a new tenant is a repeatable, reviewable action. The script is **dry-run by
   default**; `--apply` performs the mutation against a live gateway.

4. **Tenant isolation in audit.** Every LiteLLM request row carries the key +
   end-user; the team/org dimension makes spend and logs segregable per tenant.
   The compliance-MCP `caller_virtual_key_hash` line is per-key, so tool calls
   attribute to the org too.

### Consequences

- Multi-tenancy is provisioning + identity mapping over primitives LiteLLM already
  has — **no gateway code change**. The lab ships the runbook + script; the
  existing operator stays one team among (eventually) many.
- Tier-scoped allow-lists turn ADR-014's posture tags into an access-control
  input: a gov tenant is *constrained* to gov boundaries (the compliance-correct
  default), not merely *offered* them.
- Real multi-tenant state must be durable and shared — see ADR-017 (remote TF
  state), a hard prerequisite before provisioning real orgs.
- **Acceptance (G3 done-when):** two distinct orgs reach the gateway with isolated
  keys/budgets, each reaching only its allowed tiers, audit segregating by org
  (test-plan T-TEN-1; requires a live gateway + master key).

---

## ADR-017 — Remote Terraform state (S3 + DynamoDB); supersedes ADR-001

**Date:** 2026-06-18
**Status:** Accepted
**Supersedes:** ADR-001 (local state was acceptable only while the lab held no
real data and ran single-operator).
**Implements:** [`docs/roadmap.md`](roadmap.md) Phase G3 prerequisite.

### Context

ADR-001 chose local Terraform state because the lab held no real data and ran
single-operator — and named this as "the first thing to revisit if that ever
changes." G3 introduces approved organizations: multiple tenants, eventually more
than one operator provisioning them. Local state cannot support that — no locking
(concurrent applies corrupt state), no shared source of truth, no durable history.

### Decision

Adopt **remote state on S3 with DynamoDB state locking** before any real org is
provisioned:
- S3 bucket (versioned, SSE, public-access-blocked) for state.
- DynamoDB table (`LockID` hash key) for state locks.
- Backend config kept in `terraform/backend.tf.example` (a `.example` so the
  current local-state workflow still `init`s until you migrate).

**Bootstrap (chicken-and-egg):** the bucket + table are created once out-of-band
(throwaway local-state config or by hand), then the main config migrates with
`terraform init -migrate-state`. The commands are in the backend example.

### Consequences

- **Not yet migrated** — the lab is still single-operator with no real org data,
  so the backend is config-ready (`.example`) and local state keeps working today.
  Migration is the gate that opens before the first real tenant.
- State locking makes concurrent org-provisioning safe; versioning gives rollback;
  SSE + public-access-block protects state (which can carry sensitive outputs).
- ADR-001 is superseded but left in place as history.
