# =============================================================================
# cloudflare — Zero Trust wiring (Phase 1.5 definitions / Phase 4 activation)
# =============================================================================
# Everything below is INTENTIONALLY commented so root `terraform init` does not
# pull the Cloudflare provider until you are ready. To activate:
#
#   1. export CLOUDFLARE_API_TOKEN=...
#        token scopes: Account > Cloudflare Tunnel:Edit,
#                      Account > Access: Apps and Policies:Edit,
#                      Account > Access: Organizations, Identity Providers:Edit
#        (DNS is NOT needed — the public CNAMEs live in Google DNS, ADR-008.)
#   2. add the provider to the ROOT module (terraform/main.tf):
#        terraform { required_providers { cloudflare = {
#          source = "cloudflare/cloudflare", version = "~> 5.0" } } }
#        provider "cloudflare" {}   # reads CLOUDFLARE_API_TOKEN from env
#   3. uncomment the module "cloudflare" call in terraform/main.tf and pass vars
#   4. uncomment everything in this file, then: terraform init && plan
#
# SCHEMA NOTE (verified 2026-05 against cloudflare/cloudflare v5 docs):
#   - access applications reference REUSABLE policies via `policies = [{id,precedence}]`
#   - access policies are standalone resources with include/require/exclude as
#     LISTS OF OBJECTS (not nested blocks).
#   The provider's zero_trust_* resources move fast (see provider issues #5491,
#   #5499 on policy precedence). RE-VERIFY the condition sub-keys (okta/geo/
#   auth_method/device_posture) on the registry at activation time.
# =============================================================================

/*  ------------------------- BEGIN COMMENTED MODULE -------------------------

# ---- IdP: Okta (OIDC) -------------------------------------------------------
# config.okta_account is the tenant URL; client id/secret come from Secrets
# Manager (lab/okta_cf_client_id, lab/okta_cf_client_secret).
resource "cloudflare_zero_trust_access_identity_provider" "okta" {
  account_id = var.cloudflare_account_id
  name       = "Okta"
  type       = "okta"
  config = {
    client_id     = var.okta_cf_client_id
    client_secret = var.okta_cf_client_secret
    okta_account  = var.okta_tenant_url   # e.g. https://dev-12345678.okta.com
    claims        = ["groups"]            # request the groups claim
  }
}

# ---- Access groups keyed off the Okta `groups` claim ------------------------
resource "cloudflare_zero_trust_access_group" "lab_users" {
  account_id = var.cloudflare_account_id
  name       = "lab-users"
  include = [{
    okta = {
      identity_provider_id = cloudflare_zero_trust_access_identity_provider.okta.id
      name                 = ["lab-users"]
    }
  }]
}

resource "cloudflare_zero_trust_access_group" "lab_admins" {
  account_id = var.cloudflare_account_id
  name       = "lab-admins"
  include = [{
    okta = {
      identity_provider_id = cloudflare_zero_trust_access_identity_provider.okta.id
      name                 = ["lab-admins"]
    }
  }]
}

# ---- Policies (reusable, referenced by the applications) --------------------
# Chat (permissive): allow lab-users. Access default-denies anything that does
# not match an allow policy, so the "Block Everyone" tail is implicit; an
# explicit deny policy can be added with higher precedence if you want it shown.
resource "cloudflare_zero_trust_access_policy" "chat_allow" {
  account_id = var.cloudflare_account_id
  name       = "chat-allow-lab-users"
  decision   = "allow"
  include = [{
    group = { id = cloudflare_zero_trust_access_group.lab_users.id }
  }]
}

# Gateway admin (strict): lab-admins AND mfa AND WARP posture AND US geo.
# require = AND across the listed conditions.
resource "cloudflare_zero_trust_access_policy" "gateway_allow" {
  account_id = var.cloudflare_account_id
  name       = "gateway-allow-lab-admins-strict"
  decision   = "allow"
  include = [{
    group = { id = cloudflare_zero_trust_access_group.lab_admins.id }
  }]
  require = [
    { auth_method    = { auth_method = "mfa" } },
    { geo            = { geo = ["US"] } },                       # remove for travel
    { device_posture = { integration_uid = var.warp_posture_id } }, # WARP healthy
  ]
}

# ---- Applications -----------------------------------------------------------
resource "cloudflare_zero_trust_access_application" "chat" {
  account_id       = var.cloudflare_account_id
  name             = "AI Lab — Chat"
  domain           = "chat.optimallabs.io"
  type             = "self_hosted"
  session_duration = "24h"
  policies = [{
    id         = cloudflare_zero_trust_access_policy.chat_allow.id
    precedence = 1
  }]
  # Confirm these headers reach origin (trusted-header SSO, ADR-007):
  # Cf-Access-Authenticated-User-Email / -Name / Cf-Access-Jwt-Assertion
}

resource "cloudflare_zero_trust_access_application" "gateway" {
  account_id       = var.cloudflare_account_id
  name             = "AI Lab — Gateway Admin"
  domain           = "gateway.optimallabs.io"
  type             = "self_hosted"
  session_duration = "4h"
  policies = [{
    id         = cloudflare_zero_trust_access_policy.gateway_allow.id
    precedence = 1
  }]
}

# ---- Tunnels ----------------------------------------------------------------
# config_src = "cloudflare" => ingress is managed here (dashboard/TF), not on
# the EC2 host; cloudflared just runs with its token. Tokens are read and pushed
# to Secrets Manager (lab/cloudflare_tunnel_token_chat / _gateway) — see the
# token data source below.
resource "cloudflare_zero_trust_tunnel_cloudflared" "lab_chat" {
  account_id = var.cloudflare_account_id
  name       = "lab-chat"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "lab_gateway" {
  account_id = var.cloudflare_account_id
  name       = "lab-gateway"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "lab_chat" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab_chat.id
  config = {
    ingress = [
      { hostname = "chat.optimallabs.io", service = "http://open-webui:8080" },
      { service = "http_status:404" },
    ]
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "lab_gateway" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab_gateway.id
  config = {
    ingress = [
      # Front door is the gateway façade (gateway/): it serves the
      # OpenAI-compatible /v1 endpoint AND the branded control plane at /admin/ui.
      # LiteLLM (litellm:4000) stays internal behind it. To also expose LiteLLM's
      # own /ui for debugging, add a separate hostname -> http://litellm:4000.
      { hostname = "gateway.optimallabs.io", service = "http://gateway-facade:4001" },
      { service = "http_status:404" },
    ]
  }
}

# Tunnel tokens (sensitive) — feed these into Secrets Manager so cloudflared on
# the hosts can fetch them via the secrets-bootstrap script.
data "cloudflare_zero_trust_tunnel_cloudflared_token" "lab_chat" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab_chat.id
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "lab_gateway" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab_gateway.id
}

# Optionally write the tokens straight into the lab/* secrets created by the
# secrets module (pass the secret IDs in as variables):
# resource "aws_secretsmanager_secret_version" "tunnel_token_chat" {
#   secret_id     = var.secret_id_tunnel_token_chat
#   secret_string = data.cloudflare_zero_trust_tunnel_cloudflared_token.lab_chat.token
# }
# resource "aws_secretsmanager_secret_version" "tunnel_token_gateway" {
#   secret_id     = var.secret_id_tunnel_token_gateway
#   secret_string = data.cloudflare_zero_trust_tunnel_cloudflared_token.lab_gateway.token
# }

----------------------------- END COMMENTED MODULE ----------------------------- */
