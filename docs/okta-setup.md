# Okta setup runbook (Phase 1.5)

Okta is the identity source of truth for the lab. You build **two** OIDC apps —
one for Cloudflare Access (fronts both hostnames) and one for the LiteLLM admin
panel's own OIDC handshake (ADR-007). Budget **~20–30 minutes**.

> All client IDs/secrets captured here are stored in AWS Secrets Manager via
> `scripts/seed-okta-secrets.sh` (uses `read -rs` — values never hit shell
> history or this repo). Nothing secret is written to Git.

Some placeholders are not known until later phases — fill them in then:
- `<cf-team-name>` — your Cloudflare Zero Trust team name (Phase 4,
  `docs/cloudflare-access-policies.md` step 1). The Okta redirect URI for the
  Cloudflare app uses it; you can edit the app's redirect URI after the fact.

---

## 1. Create the Okta Developer tenant

1. Go to <https://developer.okta.com/signup/> and create a free **Okta
   Integrator / Developer** account (personal email is fine).
2. After activation, note your tenant URL, e.g. `https://dev-12345678.okta.com`.
   This is `lab/okta_tenant_url`.
3. Sign in to the **Admin Console** (`https://dev-12345678-admin.okta.com`).

## 2. Create groups and assign yourself

1. **Directory → Groups → Add group**:
   - `lab-users`
   - `lab-admins`
2. **Directory → People** → your user → **Groups** → add to **both** groups.
   (Admins are a strict subset conceptually, but you belong to both so you can
   exercise the chat app and the gateway app.)

## 3. OIDC app #1 — Cloudflare Access

1. **Applications → Applications → Create App Integration**
   - Sign-in method: **OIDC - OpenID Connect**
   - Application type: **Web Application** → Next
2. Settings:
   - Name: `AI Lab — Cloudflare Access`
   - Grant type: **Authorization Code**
   - **Sign-in redirect URIs:**
     `https://<cf-team-name>.cloudflareaccess.com/cdn-cgi/access/callback`
     *(placeholder until Phase 4; edit once the team name exists)*
   - Sign-out redirect URIs: leave default
   - **Assignments:** Limit access to selected groups → `lab-users`, `lab-admins`
3. Save. From the app's **General** tab capture:
   - **Client ID** → `lab/okta_cf_client_id`
   - **Client secret** → `lab/okta_cf_client_secret`
4. **Enable the groups claim** (so Cloudflare Access groups can match):
   - **Sign On** tab → **OpenID Connect ID Token** → Edit
   - Groups claim type: **Filter**
   - Groups claim filter: `groups`  matches regex  `^lab-.*`
   - Save. (This emits only `lab-*` groups in the token — least disclosure.)

## 4. OIDC app #2 — LiteLLM admin

1. **Create App Integration** → **OIDC - Web Application** → Next
2. Settings:
   - Name: `AI Lab — LiteLLM Admin`
   - Grant type: **Authorization Code**
   - **Sign-in redirect URI:** `https://gateway.ironechelon.com/sso/callback`
   - **Assignments:** Limit access to selected groups → **`lab-admins` only**
     (the chat-only `lab-users` must not reach the gateway admin app)
3. Save and capture:
   - **Client ID** → `lab/okta_litellm_client_id`
   - **Client secret** → `lab/okta_litellm_client_secret`
4. **Enable the groups claim** exactly as in step 3.4 (Filter, `groups` matches
   regex `^lab-.*`). LiteLLM maps the `groups` claim to roles (Phase 2):
   `lab-admins → proxy_admin`, everyone else → `internal_user_viewer`.

> Why two apps? Two independent auth surfaces for two blast radii (ADR-007).
> The Cloudflare app gates *network reachability* for both hostnames; the
> LiteLLM app independently re-verifies the token for *privileged admin actions*
> so a forged trusted header can never grant gateway admin.

## 5. Enforce MFA (recommended)

1. **Security → Authenticators** → ensure **Okta Verify** (and/or WebAuthn) is
   active alongside Password.
2. **Security → Authentication Policies** → the policy applied to both apps:
   require **Password + Another factor** (Possession).
   This is what makes the gateway app's "Authentication method includes `mfa`"
   Cloudflare rule meaningful (Phase 4).

## 6. Capture the tenant URL

- `lab/okta_tenant_url` = `https://dev-12345678.okta.com` (no trailing slash).

---

## Seed the secrets

After `terraform apply` has created the empty `lab/*` secrets:

```bash
AWS_PROFILE=ai-lab AWS_DEFAULT_REGION=us-east-1 ./scripts/seed-okta-secrets.sh
```

It prompts (hidden input) for the five Okta values:
`okta_tenant_url`, `okta_cf_client_id`, `okta_cf_client_secret`,
`okta_litellm_client_id`, `okta_litellm_client_secret`.

## Verify (structural, no browser)

```bash
OKTA_TENANT_URL=https://dev-12345678.okta.com ./scripts/test-sso.sh
```
Checks the Okta OIDC discovery doc is well-formed and the authorize/token
endpoints resolve. Full browser-based MFA flow is exercised in the Phase 5 test
plan.

## Summary of what you captured

| Okta artifact | Secrets Manager key |
|---|---|
| Tenant URL | `lab/okta_tenant_url` |
| Cloudflare Access app — Client ID | `lab/okta_cf_client_id` |
| Cloudflare Access app — Client secret | `lab/okta_cf_client_secret` |
| LiteLLM Admin app — Client ID | `lab/okta_litellm_client_id` |
| LiteLLM Admin app — Client secret | `lab/okta_litellm_client_secret` |
| Groups: `lab-users`, `lab-admins` | (membership, not a secret) |
