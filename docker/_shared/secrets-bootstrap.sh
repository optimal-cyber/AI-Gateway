#!/usr/bin/env bash
# =============================================================================
# secrets-bootstrap.sh — fetch lab/* secrets into a tmpfs .env for docker compose
# =============================================================================
# Runs from a systemd unit BEFORE `docker compose up` (see ai-lab-stack@.service).
# Writes /run/ai-lab/<role>.env (tmpfs, mode 0600) and symlinks it to the compose
# dir as .env, so secrets exist only in RAM and never on disk or in Git
# (requirement #4). Also propagates the host proxy env so containers egress
# through Squid (ADR-009).
#
# Required env:
#   AI_LAB_ROLE        chat | gateway
#   AI_LAB_COMPOSE     path to the compose dir (e.g. /opt/ai-lab/compose)
#   AWS_DEFAULT_REGION e.g. us-east-1
# =============================================================================
set -euo pipefail

: "${AI_LAB_ROLE:?set AI_LAB_ROLE=chat|gateway}"
: "${AI_LAB_COMPOSE:?set AI_LAB_COMPOSE=/path/to/compose/dir}"
: "${AWS_DEFAULT_REGION:?set AWS_DEFAULT_REGION}"

RUN_DIR="/run/ai-lab"
ENV_FILE="${RUN_DIR}/${AI_LAB_ROLE}.env"

install -d -m 0700 "${RUN_DIR}"
umask 077
: > "${ENV_FILE}"

# fetch lab/<name> -> stdout (SecretString)
secret() {
  aws secretsmanager get-secret-value \
    --region "${AWS_DEFAULT_REGION}" \
    --secret-id "lab/$1" \
    --query SecretString --output text
}
# write KEY=VALUE to the env file
put() { printf '%s=%s\n' "$1" "$2" >> "${ENV_FILE}"; }

case "${AI_LAB_ROLE}" in
  chat)
    put LITELLM_HOST_IP             "$(secret gateway_host_private_ip)"
    put LITELLM_VIRTUAL_KEY_WEBUI   "$(secret litellm_virtual_key_webui)"
    put WEBUI_SECRET_KEY            "$(secret webui_secret)"
    put CLOUDFLARE_TUNNEL_TOKEN     "$(secret cloudflare_tunnel_token_chat)"
    ;;
  gateway)
    put OPENAI_API_KEY              "$(secret openai_api_key)"
    put ANTHROPIC_API_KEY           "$(secret anthropic_api_key)"
    put SAM_GOV_API_KEY             "$(secret sam_gov_api_key)"
    put LITELLM_MASTER_KEY          "$(secret litellm_master_key)"
    put LITELLM_SALT_KEY            "$(secret litellm_salt_key)"
    PG_PW="$(secret postgres_password)"
    put POSTGRES_PASSWORD           "${PG_PW}"
    put DATABASE_URL                "postgresql://litellm:${PG_PW}@postgres:5432/litellm"
    put CLOUDFLARE_TUNNEL_TOKEN     "$(secret cloudflare_tunnel_token_gateway)"
    # Okta (LiteLLM admin OIDC, direct — ADR-007)
    OKTA_URL="$(secret okta_tenant_url)"
    put PROXY_BASE_URL              "https://gateway.lab.ironechelon.com"
    put OKTA_TENANT_URL             "${OKTA_URL}"
    put GENERIC_CLIENT_ID           "$(secret okta_litellm_client_id)"
    put GENERIC_CLIENT_SECRET       "$(secret okta_litellm_client_secret)"
    put GENERIC_AUTHORIZATION_ENDPOINT "${OKTA_URL}/oauth2/v1/authorize"
    put GENERIC_TOKEN_ENDPOINT      "${OKTA_URL}/oauth2/v1/token"
    put GENERIC_USERINFO_ENDPOINT   "${OKTA_URL}/oauth2/v1/userinfo"
    put GENERIC_INCLUDE_CLIENT_ID   "true"
    put GENERIC_SCOPE               "openid email profile groups"
    put GENERIC_USER_ROLE_JWT_FIELD "groups"
    ;;
  *)
    echo "unknown AI_LAB_ROLE=${AI_LAB_ROLE}" >&2; exit 1 ;;
esac

# --- proxy env for the containers (ADR-009) ----------------------------------
# Pull HTTP(S)_PROXY / NO_PROXY from /etc/environment (set by the EC2 user-data)
# so compose interpolates them into each egressing service.
if [[ -f /etc/environment ]]; then
  # shellcheck disable=SC1091
  while IFS= read -r line; do
    case "${line%%=*}" in
      http_proxy|https_proxy|no_proxy|HTTP_PROXY|HTTPS_PROXY|NO_PROXY)
        printf '%s\n' "${line}" >> "${ENV_FILE}" ;;
    esac
  done < /etc/environment
fi

chmod 600 "${ENV_FILE}"
ln -sfn "${ENV_FILE}" "${AI_LAB_COMPOSE}/.env"
echo "secrets-bootstrap: wrote ${ENV_FILE} (role=${AI_LAB_ROLE}) and linked ${AI_LAB_COMPOSE}/.env"
