#!/usr/bin/env bash
# =============================================================================
# seed-okta-secrets.sh — interactively seed the five Okta lab/* secrets
# =============================================================================
# Run AFTER `terraform apply` and after creating the Okta apps
# (docs/okta-setup.md). Values are read with `read -rs` so they never echo or
# land in shell history. Empty input leaves that secret unchanged.
#
# Usage:
#   AWS_PROFILE=ai-lab AWS_DEFAULT_REGION=us-east-1 ./scripts/seed-okta-secrets.sh
# =============================================================================
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"

SECRETS=(
  "okta_tenant_url|Okta tenant URL (https://dev-XXXXXXXX.okta.com, no trailing slash)"
  "okta_cf_client_id|Okta 'Cloudflare Access' app — Client ID"
  "okta_cf_client_secret|Okta 'Cloudflare Access' app — Client secret"
  "okta_litellm_client_id|Okta 'LiteLLM Admin' app — Client ID"
  "okta_litellm_client_secret|Okta 'LiteLLM Admin' app — Client secret"
)

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
aws sts get-caller-identity >/dev/null || { echo "AWS creds invalid — configure first." >&2; exit 1; }

echo "Seeding Okta lab/* secrets in region ${REGION}."
echo "Press ENTER with no input to skip (leaves a secret unchanged)."
echo

for entry in "${SECRETS[@]}"; do
  name="${entry%%|*}"
  prompt="${entry#*|}"
  printf '  %-30s %s\n  > ' "lab/${name}" "${prompt}"
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
echo "Done. Verify structurally with:  OKTA_TENANT_URL=<url> ./scripts/test-sso.sh"
