# =============================================================================
# secrets — empty lab/* Secrets Manager placeholders (requirement #4)
# =============================================================================
# These are created EMPTY. Real values are seeded post-apply by:
#   scripts/seed-secrets.sh       (provider/app secrets)
#   scripts/seed-okta-secrets.sh  (the five okta_* secrets, Phase 1.5)
# Exception: lab/gateway_host_private_ip is populated automatically by the root
# module from the compute output (a derived value, not a user secret).
#
# Nothing here ever holds a real value in state at apply time — only the empty
# container resources. The instance role is scoped to GetSecretValue on lab/*.
# =============================================================================

locals {
  # Short name (map key) -> these become lab/<short_name>
  secret_names = toset([
    "openai_api_key",
    "anthropic_api_key",
    "cloudflare_tunnel_token_chat",
    "cloudflare_tunnel_token_gateway",
    "sam_gov_api_key",
    "litellm_master_key",
    "litellm_salt_key",
    "webui_secret",
    "postgres_password",
    "okta_tenant_url",
    "okta_cf_client_id",
    "okta_cf_client_secret",
    "okta_litellm_client_id",
    "okta_litellm_client_secret",
    "litellm_virtual_key_webui",
    # AI Gateway façade (control plane — gateway/, docs/own-gateway.md):
    "gateway_master_key",      # façade admin API credential
    "gateway_upstream_key",    # OPTIONAL — LiteLLM key for the upstream hop (falls back to litellm_master_key)
    "gateway_bootstrap_key",   # first-boot key: seeds a default team+key; Open WebUI uses it
    "gateway_host_private_ip", # populated by root from compute output
  ])
}

resource "aws_secretsmanager_secret" "this" {
  for_each = local.secret_names

  name                    = "lab/${each.value}"
  description             = "Zero Trust AI Lab secret: ${each.value} (seeded post-apply)"
  recovery_window_in_days = var.secret_recovery_days

  tags = { Name = "lab/${each.value}" }
}
