#!/usr/bin/env bash
# =============================================================================
# seed-secrets.sh — interactively seed the NON-Okta lab/* secrets
# =============================================================================
# Run AFTER `terraform apply` (the empty lab/* secrets must already exist).
# Okta secrets are seeded separately by scripts/seed-okta-secrets.sh (Phase 1.5).
# lab/gateway_host_private_ip is populated automatically by Terraform — skip it.
#
# Values are read with `read -rs` (no echo, no shell history). Empty input
# leaves that secret unchanged so you can re-run to fill in just a few.
#
# Usage:
#   AWS_PROFILE=ai-lab AWS_DEFAULT_REGION=us-east-1 ./scripts/seed-secrets.sh
# =============================================================================
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# short name -> human prompt
SECRETS=(
  "openai_api_key|OpenAI API key (sk-...)"
  "anthropic_api_key|Anthropic API key (sk-ant-...)"
  "sam_gov_api_key|SAM.gov API key"
  "cloudflare_tunnel_token_chat|Cloudflare tunnel token — lab-chat (Phase 4)"
  "cloudflare_tunnel_token_gateway|Cloudflare tunnel token — lab-gateway (Phase 4)"
  "litellm_master_key|LiteLLM master key (invent one, e.g. sk-$(openssl rand -hex 16))"
  "litellm_salt_key|LiteLLM salt key (invent one; do NOT change after first use)"
  "litellm_virtual_key_webui|LiteLLM virtual key (legacy / optional — Open WebUI now uses the bootstrap key)"
  "webui_secret|Open WebUI secret key (invent one, e.g. openssl rand -hex 32)"
  "postgres_password|Postgres password for LiteLLM backing store (invent one)"
  # --- AI Gateway façade control plane (gateway/, docs/own-gateway.md) ---
  "gateway_master_key|Façade ADMIN key — REQUIRED for /admin (invent one, e.g. sk-$(openssl rand -hex 16))"
  "gateway_bootstrap_key|Façade FIRST-BOOT key — Open WebUI + devs use it; REQUIRED (invent one, e.g. sk-$(openssl rand -hex 16))"
  "gateway_upstream_key|Façade UPSTREAM key — OPTIONAL; blank = use LiteLLM master key (or paste a LiteLLM virtual key)"
)

echo "Seeding non-Okta lab/* secrets in region ${REGION}."
echo "Press ENTER with no input to skip a secret (leaves it unchanged)."
echo

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
aws sts get-caller-identity >/dev/null || { echo "AWS creds invalid — configure first." >&2; exit 1; }

for entry in "${SECRETS[@]}"; do
  name="${entry%%|*}"
  prompt="${entry#*|}"
  printf '  %-34s %s\n  > ' "lab/${name}" "${prompt}"
  read -rs value
  echo
  if [[ -z "${value}" ]]; then
    echo "    (skipped)"
    continue
  fi
  aws secretsmanager put-secret-value \
    --region "${REGION}" \
    --secret-id "lab/${name}" \
    --secret-string "${value}" \
    --output text --query 'VersionId' >/dev/null
  echo "    ✓ stored"
  unset value
done

echo
echo "Done. Okta secrets (okta_*) are seeded by scripts/seed-okta-secrets.sh (Phase 1.5)."
echo "lab/gateway_host_private_ip is managed by Terraform — do not set it here."
