# Cloudflare Zero Trust + Access policies runbook (Phase 1.5 / Phase 4)

Two Access applications, two postures: **chat = permissive**, **gateway =
strict**. Default-deny everywhere else. This runbook is the dashboard path; the
equivalent Terraform lives (commented) in `terraform/modules/cloudflare/`.

Prereq: Okta apps from `docs/okta-setup.md` exist (you need the Cloudflare-app
client id/secret).

---

## 0. Terraform alternative (if you prefer IaC)

```bash
export CLOUDFLARE_API_TOKEN=...    # scopes below
```
Token scopes (My Profile → API Tokens → Create Token → custom):
- **Account → Cloudflare Tunnel → Edit**
- **Account → Access: Apps and Policies → Edit**
- **Account → Access: Organizations, Identity Providers and Groups → Edit**

DNS scope is **not** required — the public CNAMEs live in Google DNS (ADR-008).
Then uncomment the provider + module per `terraform/modules/cloudflare/main.tf`.

The rest of this doc is the click-path equivalent.

---

## 1. Zero Trust organization + team name

1. Cloudflare dashboard → **Zero Trust**. If first time, it provisions an org and
   asks for a **team name** — pick `optimallabs` (this yields
   `https://optimallabs.cloudflareaccess.com`).
2. This team name is `<cf-team-name>`. **Go back to Okta** (`docs/okta-setup.md`
   step 3.2) and set the Cloudflare app's redirect URI to:
   `https://optimallabs.cloudflareaccess.com/cdn-cgi/access/callback`

## 2. Add Okta as a login method

1. **Settings → Authentication → Login methods → Add new → Okta**.
2. Fill:
   - App ID = `lab/okta_cf_client_id`
   - Client secret = `lab/okta_cf_client_secret`
   - Okta account URL = `lab/okta_tenant_url` (e.g. `https://dev-12345678.okta.com`)
3. **Enable "Read group memberships" / OIDC `groups` claim** so Access groups can
   match Okta groups.
4. **Test** the login method (it round-trips to Okta) and **Save**.
5. (Recommended) **Settings → Authentication** → remove the default "One-time
   PIN" method so Okta is the *only* IdP, matching the spec ("IdP: Okta only").

## 3. Access groups (keyed off the Okta `groups` claim)

**Access → Access Groups → Add a group** (create two):

| Group name | Include rule |
|---|---|
| `lab-users` | Okta groups **is** `lab-users` (selector: the Okta login method → Groups) |
| `lab-admins` | Okta groups **is** `lab-admins` |

## 4. WARP device posture (for the strict gateway app)

1. Enroll your device in WARP (`docs` link in dashboard) and join the team
   `optimallabs`.
2. **Settings → WARP Client → Device posture** → ensure a posture attribute
   exists you can require (e.g. **"WARP" / Gateway** check, or "WARP is healthy").
   Note its name — you'll require it in the gateway policy.

## 5. Application 1 — Chat (permissive)

**Access → Applications → Add an application → Self-hosted**:

| Field | Value |
|---|---|
| Application name | `AI Lab — Chat` |
| Session duration | **24h** |
| Application domain | `chat.lab.ironechelon.com` |
| Identity providers | **Okta only** (untick others) |
| Instant Auth / accept all available IdPs | off (Okta only) |

**Policies:**
1. **Allow** — name `allow-lab-users`, Action **Allow**, Include: **Access group
   = `lab-users`**.
2. **Block** — name `block-everyone`, Action **Block**, Include: **Everyone**.
   (Order matters: Allow is evaluated first. The explicit Block is belt-and-
   suspenders; Access default-denies unmatched requests anyway.)

**Settings → make sure these reach the origin** (trusted-header SSO, ADR-007):
- `Cf-Access-Authenticated-User-Email`
- `Cf-Access-Authenticated-User-Name`
- `Cf-Access-Jwt-Assertion`

These are forwarded by Access by default for self-hosted apps; confirm in the
app's settings and verify with `scripts/test-sso.sh` / curling the origin behind
the tunnel.

## 6. Application 2 — Gateway admin (strict)

**Add application → Self-hosted**:

| Field | Value |
|---|---|
| Application name | `AI Lab — Gateway Admin` |
| Session duration | **4h** |
| Application domain | `gateway.lab.ironechelon.com` |
| Identity providers | **Okta only** |

**Policies:**
1. **Allow** — name `allow-lab-admins-strict`, Action **Allow**, all of
   (require = AND):
   - Include: **Access group = `lab-admins`**
   - Require: **Login method / Authentication method = `mfa`** (Selector:
     "Authentication method" → Multifactor)
   - Require: **Device posture = WARP** (the posture attribute from step 4)
   - Require: **Country = United States**
     *(Travel: temporarily remove this Require, or add your current country, then
     re-add US. Documented removal so a trip doesn't lock you out.)*
2. **Block** — name `block-everyone`, Action **Block**, Include: **Everyone**.

## 7. Cloudflare Gateway DNS / DLP (light touch — Phase 4)

- **Gateway → Firewall policies → DNS**: block **Security categories**
  (Malware, Phishing, Command & Control).
- **DLP / Gateway HTTP** (optional, requires WARP HTTP filtering): a custom
  detection list with paste patterns for credentials — `ghp_*`, `AKIA*`,
  `sk_live_*`, and a generic 40+ char high-entropy regex. This complements the
  NeMo secret-pattern guardrail (defense in depth, AI-5 in the threat model).

---

## Verify

```bash
CF_TEAM=optimallabs OKTA_TENANT_URL=https://dev-12345678.okta.com \
  ./scripts/test-sso.sh
```
Structural checks: chat hostname 302s to `*.cloudflareaccess.com`, Access JWKS
reachable, Okta discovery doc well-formed, LiteLLM OIDC redirect resolves to the
Okta authorize URL with the right client_id. Full browser+MFA flow is Phase 5.

## What feeds the next phases

- Tunnel UUIDs (Phase 4) → the CNAME values in `docs/google-dns-cnames.md`.
- This split (chat permissive / gateway strict) is the basis for the SSO
  role-mapping matrix in `docs/sso-role-mapping.md`.
