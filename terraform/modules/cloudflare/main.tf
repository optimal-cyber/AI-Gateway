# =============================================================================
# cloudflare — STUB (populated in Phase 1.5 / Phase 4)
# =============================================================================
# Intentionally all comments so root `terraform init` does NOT pull the
# Cloudflare provider until you are ready. To activate:
#   1. export CLOUDFLARE_API_TOKEN=...   (scope: Account.Cloudflare Tunnel,
#      Account.Access: Apps and Policies, Zone.DNS — see
#      docs/cloudflare-access-policies.md)
#   2. uncomment the required_providers + provider in the ROOT module
#   3. uncomment the module "cloudflare" call in terraform/main.tf
#   4. uncomment the resources below
#
# Resources to be defined here (full detail lands with the Phase 1.5/4 runbooks):
#
#   - cloudflare_zero_trust_tunnel_cloudflared.lab_chat / .lab_gateway
#       two named tunnels; their tokens get stored in Secrets Manager
#       (lab/cloudflare_tunnel_token_chat, lab/cloudflare_tunnel_token_gateway)
#   - cloudflare_zero_trust_tunnel_cloudflared_config (ingress rules)
#       lab-chat:    chat.lab.gooptimal.io    -> http://open-webui:8080
#       lab-gateway: gateway.lab.gooptimal.io -> http://litellm:4000
#   - cloudflare_zero_trust_access_identity_provider.okta  (OIDC -> Okta)
#   - cloudflare_zero_trust_access_group.lab_users / .lab_admins
#       keyed off the Okta groups claim (lab-users / lab-admins)
#   - cloudflare_zero_trust_access_application.chat     (permissive, 24h)
#   - cloudflare_zero_trust_access_application.gateway  (strict, 4h,
#       + MFA + WARP posture + US geo)
#   - cloudflare_zero_trust_access_policy.* (allow rules + explicit Block:Everyone)
#
# NOTE on DNS: the public CNAMEs (lab / chat.lab / gateway.lab.gooptimal.io)
# live in Google Cloud DNS, NOT here (ADR-008). This module only declares the
# Cloudflare-side tunnel + Access objects. See docs/google-dns-cnames.md.
#
# -----------------------------------------------------------------------------
# Example skeleton (verify resource/arg names against the current Cloudflare
# provider docs at activation time — the provider renamed many access_* /
# zero_trust_* resources across v4->v5):
#
# resource "cloudflare_zero_trust_tunnel_cloudflared" "lab_chat" {
#   account_id = var.cloudflare_account_id
#   name       = "lab-chat"
#   config_src = "cloudflare"
# }
#
# resource "cloudflare_zero_trust_access_application" "chat" {
#   zone_id          = var.cloudflare_zone_id
#   name             = "AI Lab — Chat"
#   domain           = "chat.lab.gooptimal.io"
#   session_duration = "24h"
#   # ... allow lab-users; Block everyone ...
# }
# =============================================================================
