#!/usr/bin/env bash
# =============================================================================
# provision-org.sh — onboard an approved organization as a gateway tenant
# =============================================================================
# An org = one LiteLLM team (ADR-016) with a budget and a model allow-list scoped
# to the compliance tier it's approved for (ADR-014):
#   --tier dev  -> commercial models only
#   --tier gov  -> gov/* models only (a gov tenant never reaches a commercial
#                  endpoint)
#
# DRY-RUN BY DEFAULT: prints the /team/new and /key/generate payloads and sends
# nothing. Pass --apply to POST against a live gateway with the master key.
#
# Mutating action — review the dry-run, then re-run with --apply. See
# docs/org-onboarding.md for the full runbook (Okta group mapping, verification).
# =============================================================================
set -uo pipefail

GW_URL="${GATEWAY_URL:-http://127.0.0.1:4000}"
ORG="" ; TIER="dev" ; BUDGET="100" ; APPLY=0

usage() {
  cat <<USAGE
Usage: provision-org.sh --org "<name>" [--tier dev|gov] [--budget <usd>] [--apply]
  --org      Organization name (required)
  --tier     Approved compliance tier: dev (default) | gov
  --budget   Team max budget in USD (default: 100)
  --apply    Actually POST to the gateway (default: dry-run only)
Env: GATEWAY_URL (default http://127.0.0.1:4000), LITELLM_MASTER_KEY (for --apply)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)    ORG="${2:-}"; shift 2 ;;
    --tier)   TIER="${2:-}"; shift 2 ;;
    --budget) BUDGET="${2:-}"; shift 2 ;;
    --apply)  APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "${ORG}" ]] && { echo "error: --org is required" >&2; usage; exit 2; }
case "${TIER}" in dev|gov) ;; *) echo "error: --tier must be dev|gov" >&2; exit 2 ;; esac

# Tier model allow-lists — keep in sync with docker/gateway-host/litellm-config.yaml.
if [[ "${TIER}" == "gov" ]]; then
  MODELS='["gov/claude-opus-4-8","gov/gpt-4o"]'
else
  MODELS='["gpt-4o","claude-fable-5","claude-opus-4-8","claude-sonnet-4-6","claude-haiku-4-5"]'
fi

# slug for aliases: lower, non-alnum -> '-'
SLUG=$(printf '%s' "${ORG}" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')

TEAM_PAYLOAD=$(printf '{"team_alias":"%s","max_budget":%s,"models":%s}' "${ORG}" "${BUDGET}" "${MODELS}")

echo "== provision-org: ${ORG} (tier=${TIER}, budget=\$${BUDGET}) =="
echo "gateway: ${GW_URL}"
echo
echo "[1] POST /team/new"
echo "    ${TEAM_PAYLOAD}"
echo "[2] POST /key/generate  (team_id from step 1; key_alias=${SLUG}-key)"
echo

if [[ "${APPLY}" -ne 1 ]]; then
  echo "DRY-RUN — nothing sent. Re-run with --apply to provision."
  exit 0
fi

if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
  echo "error: --apply requires LITELLM_MASTER_KEY in the environment" >&2
  exit 2
fi
if ! command -v curl >/dev/null || ! command -v python3 >/dev/null; then
  echo "error: --apply needs curl and python3" >&2
  exit 2
fi

auth=(-H "Authorization: Bearer ${LITELLM_MASTER_KEY}" -H 'Content-Type: application/json')

team_resp=$(curl -sS --max-time 20 "${auth[@]}" -d "${TEAM_PAYLOAD}" "${GW_URL}/team/new")
team_id=$(printf '%s' "${team_resp}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("team_id",""))' 2>/dev/null)
if [[ -z "${team_id}" ]]; then
  echo "error: could not create team / parse team_id. Response:" >&2
  echo "${team_resp}" >&2
  exit 1
fi
echo "team_id: ${team_id}"

key_payload=$(printf '{"team_id":"%s","key_alias":"%s-key","max_budget":%s}' "${team_id}" "${SLUG}" "${BUDGET}")
key_resp=$(curl -sS --max-time 20 "${auth[@]}" -d "${key_payload}" "${GW_URL}/key/generate")
vkey=$(printf '%s' "${key_resp}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("key",""))' 2>/dev/null)
if [[ -z "${vkey}" ]]; then
  echo "error: could not generate key. Response:" >&2
  echo "${key_resp}" >&2
  exit 1
fi

echo
echo "✓ provisioned ${ORG}"
echo "  team_id:     ${team_id}"
echo "  virtual key: ${vkey}"
echo "  tier:        ${TIER}  (models: ${MODELS})"
echo
echo "Deliver the virtual key to the org over a secure channel — it is shown once."
echo "Next: map the org's Okta group -> team_id and add it to Cloudflare Access"
echo "(docs/org-onboarding.md step 3)."
