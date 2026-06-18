# Approved-organization onboarding runbook (G3 / ADR-016)

How to onboard an **approved organization** as a tenant of the gateway. An org =
one LiteLLM team with scoped keys, a budget, and a model allow-list gated by the
compliance tier it's approved for ([ADR-014](decisions.md)). See
[ADR-016](decisions.md) for the model.

> Reference design — **not** an attestation. "Approved" means the lab operator
> vetted and provisioned the org; it is not a government authorization, and the
> lab holds no client data.

## 0. Prerequisites

- A running gateway and its **master key** (`LITELLM_MASTER_KEY`, from Secrets
  Manager / the tmpfs `.env`).
- **Remote Terraform state migrated** ([ADR-017](decisions.md)) before
  provisioning real orgs.
- The org's approved **tier**: `dev` (commercial boundary) or `gov`
  (government-ready boundaries only).

## 1. Vet

Confirm the org and the tier it's approved for. A `gov`-approved org is
*constrained* to `gov/*` models (its prompts never reach a commercial endpoint); a
`dev`-approved org gets the commercial models. Record the decision.

## 2. Provision the tenant (team + key)

From an SSM shell on gateway-host (master key in env):

```bash
export LITELLM_MASTER_KEY=...                 # from the tmpfs .env / Secrets Manager
export GATEWAY_URL=http://127.0.0.1:4000

# dry-run first — prints the team + key payloads, sends nothing:
./scripts/provision-org.sh --org "Acme Defense" --tier gov --budget 250

# then apply:
./scripts/provision-org.sh --org "Acme Defense" --tier gov --budget 250 --apply
```

The script creates the team (`/team/new`) with the tier-scoped model allow-list +
budget, then mints a virtual key (`/key/generate`). It prints the new `team_id`
and the virtual key — **deliver the key to the org over a secure channel; it is
shown once.**

## 3. Map identity (Okta → team)

So the org's *users* (not just an API key) can reach their tenant:

- Create an Okta group for the org (e.g. `org-acme-defense`).
- Map it to the org's LiteLLM team (LiteLLM SSO `team_id` mapping).
- Add the group to the relevant Cloudflare Access policy — same pattern as the
  lab's `lab-users` / `lab-admins` ([ADR-007](decisions.md)).

**Upgrade path:** for an org that brings its own IdP, use Okta org-to-org / OIDC
federation instead of a lab-tenant group ([ADR-016](decisions.md); not built here).

## 4. Verify (T-TEN-1)

- The org's key reaches **only** its allowed tier: a `gov`-org key to a `gov/*`
  model is accepted; the same key to a `dev` model is **rejected** (not in the
  team allow-list).
- Spend/logs attribute to the org's team in the Admin UI.
- Two orgs' keys see only their own budgets — no cross-tenant visibility.

## 5. Offboard

Revoke the org's keys (`/key/delete`), optionally delete the team
(`/team/delete`), and remove the Okta group from Access. Spend history stays in
the audit log.
