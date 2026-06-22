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

# Load HTTP(S)_PROXY/NO_PROXY from /etc/environment BEFORE the aws CLI runs.
# The systemd unit launches us with a clean env, so without this the aws CLI
# tries direct egress to secretsmanager.<region>.amazonaws.com and the app SG
# drops the connection (no direct 443 to the internet — ADR-009).
if [[ -f /etc/environment ]]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/environment
  set +a
fi

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
# like secret(), but tolerate an unseeded/empty secret (prints empty, no abort).
# Used for OPTIONAL secrets so a missing one never crashes the whole stack.
secret_opt() {
  aws secretsmanager get-secret-value \
    --region "${AWS_DEFAULT_REGION}" \
    --secret-id "lab/$1" \
    --query SecretString --output text 2>/dev/null || true
}
# write KEY=VALUE to the env file
put() { printf '%s=%s\n' "$1" "$2" >> "${ENV_FILE}"; }

case "${AI_LAB_ROLE}" in
  chat)
    put LITELLM_HOST_IP             "$(secret gateway_host_private_ip)"
    put LITELLM_VIRTUAL_KEY_WEBUI   "$(secret_opt litellm_virtual_key_webui)"
    # Open WebUI now calls the façade (:4001) using the façade bootstrap key.
    put GATEWAY_BOOTSTRAP_KEY       "$(secret_opt gateway_bootstrap_key)"
    put WEBUI_SECRET_KEY            "$(secret webui_secret)"
    put CLOUDFLARE_TUNNEL_TOKEN     "$(secret cloudflare_tunnel_token_chat)"
    ;;
  gateway)
    put OPENAI_API_KEY              "$(secret openai_api_key)"
    put ANTHROPIC_API_KEY           "$(secret anthropic_api_key)"
    # Optional: unseeded -> SAM.gov lookups degrade (the MCP still runs). Tolerant
    # so a missing SAM key never aborts the whole gateway stack.
    put SAM_GOV_API_KEY             "$(secret_opt sam_gov_api_key)"
    put LITELLM_MASTER_KEY          "$(secret litellm_master_key)"
    put LITELLM_SALT_KEY            "$(secret litellm_salt_key)"
    PG_PW="$(secret postgres_password)"
    put POSTGRES_PASSWORD           "${PG_PW}"
    put DATABASE_URL                "postgresql://litellm:${PG_PW}@postgres:5432/litellm"
    put CLOUDFLARE_TUNNEL_TOKEN     "$(secret cloudflare_tunnel_token_gateway)"
    # AI Gateway façade control plane (gateway/, docs/own-gateway.md). All three
    # are OPTIONAL at bootstrap so a missing one never aborts the stack; but the
    # façade front door needs master + bootstrap seeded to authenticate callers.
    # upstream_key is blank-by-default — compose falls back to LITELLM_MASTER_KEY.
    put GATEWAY_MASTER_KEY          "$(secret_opt gateway_master_key)"
    put GATEWAY_UPSTREAM_KEY        "$(secret_opt gateway_upstream_key)"
    put GATEWAY_BOOTSTRAP_KEY       "$(secret_opt gateway_bootstrap_key)"
    # Okta (LiteLLM admin OIDC, direct — ADR-007). OPTIONAL now that the façade is
    # the front door and LiteLLM's /ui is internal-only: unseeded -> LiteLLM SSO is
    # simply disabled (the container still starts and /v1 works), so a missing Okta
    # config never blocks the stack. Seed these for full LiteLLM-/ui SSO.
    # OKTA_URL is a shell-only intermediate used to build the GENERIC_* URLs.
    # Do NOT put OKTA_TENANT_URL into the env — in LiteLLM >= 1.86 it triggers
    # the Okta-specific SSO path which prepends it to GENERIC_AUTHORIZATION_ENDPOINT,
    # producing a malformed URL like https://x.comhttps://x.com/oauth2/v1/authorize.
    # GENERIC_* alone is the documented setup; the URL already contains the tenant.
    OKTA_URL="$(secret_opt okta_tenant_url)"
    put PROXY_BASE_URL              "https://gateway.optimallabs.io"
    put GENERIC_CLIENT_ID           "$(secret_opt okta_litellm_client_id)"
    put GENERIC_CLIENT_SECRET       "$(secret_opt okta_litellm_client_secret)"
    put GENERIC_AUTHORIZATION_ENDPOINT "${OKTA_URL}/oauth2/v1/authorize"
    put GENERIC_TOKEN_ENDPOINT      "${OKTA_URL}/oauth2/v1/token"
    put GENERIC_USERINFO_ENDPOINT   "${OKTA_URL}/oauth2/v1/userinfo"
    # GENERIC_INCLUDE_CLIENT_ID=false: don't ALSO include the client_id/secret in the
    # POST body — fastapi_sso always sends them via the HTTP Basic Authorization header.
    # Setting it to true triggers both, which Okta rejects with RFC 6749
    # "Cannot supply multiple client credentials" (invalid_request).
    put GENERIC_INCLUDE_CLIENT_ID   "false"
    put GENERIC_SCOPE               "openid email profile groups"
    # Point at a derived single-string claim (configured in Okta as a custom
    # token claim via expression: isMemberOfGroupName("lab-admins") ?
    #   "proxy_admin" : "internal_user_viewer").
    # The raw 'groups' claim is a list, and LiteLLM doesn't iterate lists for
    # role matching — it treats the field value as a single string. Without the
    # derived claim, LiteLLM always falls back to internal_user_viewer and
    # admin_only mode blocks the user out of /ui.
    put GENERIC_USER_ROLE_JWT_FIELD "litellm_role"
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
