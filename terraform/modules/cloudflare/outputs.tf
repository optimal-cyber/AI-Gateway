# cloudflare module outputs — uncomment with the module (Phase 4).
# These are the exact CNAME target values to paste into Google Cloud DNS
# (docs/google-dns-cnames.md). DNS itself stays in Google (ADR-008).
#
# output "chat_tunnel_cname" {
#   description = "chat.lab.gooptimal.io  CNAME ->"
#   value       = "${cloudflare_zero_trust_tunnel_cloudflared.lab_chat.id}.cfargotunnel.com"
# }
#
# output "gateway_tunnel_cname" {
#   description = "gateway.lab.gooptimal.io  CNAME ->"
#   value       = "${cloudflare_zero_trust_tunnel_cloudflared.lab_gateway.id}.cfargotunnel.com"
# }
#
# output "okta_idp_id" {
#   value = cloudflare_zero_trust_access_identity_provider.okta.id
# }
