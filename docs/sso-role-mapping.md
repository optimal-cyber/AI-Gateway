# SSO role mapping & identity propagation (Phase 1.5)

How an Okta group becomes an authorization decision in each system, and how a
single user's prompt produces correlated, attributable log entries across all
three planes. This is the "explain it to a fellow assessor" doc.

---

## Role mapping matrix

| Okta group | Cloudflare Access — Chat (`chat`) | Cloudflare Access — Gateway (`gateway`) | Open WebUI role | LiteLLM admin role |
|---|---|---|---|---|
| `lab-users` | ✅ Allow (24h session) | ⛔ Blocked (not in `lab-admins`) | `user` (from trusted email header) | n/a (can't reach the admin UI) |
| `lab-admins` | ✅ Allow | ✅ Allow **iff** also MFA + WARP + US geo | `user` | `proxy_admin` |
| *(neither / unauthenticated)* | ⛔ default-deny | ⛔ default-deny | no identity injected → no access | no token → no access |
| *authed but not `lab-admins`, reaching LiteLLM* | — | — | — | `internal_user_viewer` (viewer-only) |

Mapping owners:
- **Cloudflare**: Access groups `lab-users` / `lab-admins` keyed off the Okta
  `groups` claim (`docs/cloudflare-access-policies.md`).
- **Open WebUI**: identity = `Cf-Access-Authenticated-User-Email` (trusted
  header); role defaults to `user`, signup disabled (ADR-007). It does **not**
  read groups — network reachability is the gate, role is flat.
- **LiteLLM**: its own Okta OIDC handshake; `GENERIC_USER_ROLE_JWT_FIELD=groups`
  → `lab-admins` maps to `proxy_admin`, all else to `internal_user_viewer`
  (Phase 2 config).

The two sensitivities, two mechanisms split is ADR-007: chat trusts the edge
header; the gateway admin independently verifies the Okta token.

---

## Identity propagation

```
                         ┌─────────────────────────────────────────┐
                         │  OKTA (source of truth)                  │
   user ──login+MFA────▶ │  user.email + groups[] claim (lab-*)     │
                         └───────────────┬─────────────────────────┘
                                         │ OIDC id_token (groups: [lab-users,lab-admins])
                                         ▼
                         ┌─────────────────────────────────────────┐
                         │  CLOUDFLARE ACCESS (edge IdP proxy)      │
                         │  validates token, evaluates policy,      │
                         │  mints a signed Access JWT               │
                         └───────┬───────────────────────┬─────────┘
              chat           │                       │   gateway
        (group lab-users)        │                       │   (lab-admins+MFA+WARP+US)
                                 ▼                       ▼
        ┌──────────────────────────────┐   ┌──────────────────────────────────┐
        │ Cloudflare Tunnel ─▶ EC2#1    │   │ Cloudflare Tunnel ─▶ EC2#2        │
        │ injects headers:              │   │ (network gate only; no header     │
        │  Cf-Access-Authenticated-     │   │  trust for privilege)             │
        │   User-Email   ───────────┐   │   │                                   │
        │  Cf-Access-Jwt-Assertion  │   │   │  LiteLLM does its OWN OIDC to     │
        │                           ▼   │   │  Okta ─▶ groups claim ─▶ role     │
        │  OPEN WEBUI               │   │   │  lab-admins → proxy_admin          │
        │  identity = that email    │   │   │  else       → internal_viewer      │
        │  (role: user)             │   │   └───────────────┬───────────────────┘
        └───────────┬───────────────┘                      │
                    │ ENABLE_FORWARD_USER_INFO_HEADERS=true │
                    │ (X-OpenWebUI-User-Email ...)          │
                    ▼                                       ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  LiteLLM gateway  (virtual key per caller + forwarded email)   │
        │   ─▶ NeMo guardrail decision   ─▶ provider (OpenAI/Anthropic)   │
        │   ─▶ compliance-mcp (x-caller-role from group)                  │
        └──────────────────────────────────────────────────────────────┘
```

Key point: **the same `user.email` and `groups` claim** issued once by Okta is
the join key that threads through every downstream system.

---

## Audit-trail story (one prompt, four correlated records)

A user in `lab-users` opens `chat.optimallabs.io`, completes Okta MFA, and
sends one prompt. That single action leaves a correlated trail you can stitch
together by **email + timestamp**:

1. **Okta System Log** — `user.authentication.sso` for `ryan@…`, including the
   **MFA factor** used (Okta Verify) and the target app (`AI Lab — Cloudflare
   Access`). *Proves who authenticated and how.*
2. **Cloudflare Access log** (Zero Trust → Logs → Access) — an `Allow` event for
   `ryan@…` on `chat.optimallabs.io`, **policy matched = `allow-lab-users`**,
   IdP = Okta, session id. *Proves the authorization decision at the edge.*
3. **Open WebUI** — the chat/session is attributed to `ryan@…` (identity came
   from `Cf-Access-Authenticated-User-Email`). *Proves app-level attribution.*
4. **LiteLLM request log** (Postgres) — a request row tagged with the Open
   WebUI virtual key and the forwarded user email, model used, token counts,
   spend, and the **NeMo guardrail decision** for that call. *Proves what the
   prompt did and whether a guardrail fired.*

Stitching: `Okta.actor.alternateId == CFAccess.user_email ==
OpenWebUI.user == LiteLLM.end_user`. One email, four systems, one timeline.
This correlation is the strongest single artifact for the Phase 5 demo
(see `docs/test-plan.md` → audit-trail test) and the LinkedIn post.

For the strict path, swap step 2 for an `Allow` on `gateway.optimallabs.io`
with **policy `allow-lab-admins-strict`** (showing the MFA + WARP + US
requirements all matched), and add a fifth record: the **LiteLLM admin UI**
sign-in mapping the `lab-admins` group to `proxy_admin`.

---

## Failure modes (what a denial looks like, for the test plan)

| Scenario | Where it's denied | Visible evidence |
|---|---|---|
| `lab-users` hits `gateway` | Cloudflare Access (not in `lab-admins`) | Access block page naming the policy |
| `lab-admins` from non-US IP | Cloudflare Access (geo Require) | Block page; Access log `geo` fail |
| `lab-admins` without WARP | Cloudflare Access (posture Require) | Block page; posture not satisfied |
| Forged `Cf-Access-...-Email` to Open WebUI | Can't reach the port (127.0.0.1 bind, ADR-007 / T-CHAT-S) | nothing reaches origin |
| Authed non-admin in LiteLLM UI | LiteLLM (groups → viewer) | viewer-only UI, no key/budget controls |
