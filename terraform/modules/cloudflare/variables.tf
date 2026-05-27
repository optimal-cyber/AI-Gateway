# cloudflare module variables — uncomment with the module (Phase 4).
#
# variable "cloudflare_account_id" {
#   description = "Cloudflare account ID (Zero Trust org)."
#   type        = string
# }
#
# variable "okta_tenant_url" {
#   description = "Okta tenant URL, e.g. https://dev-12345678.okta.com (lab/okta_tenant_url)."
#   type        = string
# }
#
# variable "okta_cf_client_id" {
#   description = "Okta OIDC app (Cloudflare Access) client id (lab/okta_cf_client_id)."
#   type        = string
#   sensitive   = true
# }
#
# variable "okta_cf_client_secret" {
#   description = "Okta OIDC app (Cloudflare Access) client secret (lab/okta_cf_client_secret)."
#   type        = string
#   sensitive   = true
# }
#
# variable "warp_posture_id" {
#   description = "Device-posture integration UID for the WARP-healthy check (gateway app)."
#   type        = string
# }
#
# # Optional: pass the lab/* secret IDs to auto-store tunnel tokens.
# variable "secret_id_tunnel_token_chat"    { type = string }
# variable "secret_id_tunnel_token_gateway" { type = string }
