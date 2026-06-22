#!/usr/bin/env bash
# =============================================================================
# provision-org.sh — onboard an approved organization as a gateway tenant
# =============================================================================
# An org = one team with a budget and a model allow-list scoped to the
# compliance tier it's approved for (ADR-014):
#   --tier dev  -> commercial models only
#   --tier gov  -> gov/* models only (a gov tenant never reaches a commercial
#                  endpoint)
#
# BACKEND (--backend, default: facade):
#   facade  -> the AI Gateway façade admin API (/admin/teams, /admin/keys).
#              Auth: GATEWAY_MASTER_KEY. The façade owns keys/budgets when
#              GATEWAY_CONTROL_PLANE=true — keys minted here are only ENFORCED
#              once the control plane is on (see gateway/README.md, docs/own-gateway.md).
#   litellm -> the legacy LiteLLM admin API (/team/new, /key/generate).
#              Auth: LITELLM_MASTER_KEY. Kept for the transition / rollback.
#
# DRY-RUN BY DEFAULT: prints the team + key payloads and sends nothing. Pass
# --apply to POST against a live gateway with the master key.
#
# Mutating action — review the dry-run, then re-run with --apply. See
# docs/org-onboarding.md for the full runbook (Okta group mapping, verification).
# =============================================================================
set -uo pipefail

ORG="" ; TIER="dev" ; BUDGET="" ; APPROVED_BY="" ; APPLY=0 ; BACKEND="facade"

usage() {
  cat <<USAGE
Usage: provision-org.sh --org "<name>" [--tier dev|gov] [--budget <usd>]
                        [--approved-by "<name>"] [--backend facade|litellm] [--apply]
  --org          Organization name (required)
  --tier         Approved compliance tier: dev (default) | gov
  --budget       Team max budget, whole USD (default: dev 100, gov 250)
  --approved-by  Approver name — REQUIRED for --tier gov (ADR-018 gate)
  --backend      Target API: facade (default) | litellm
  --apply        Actually POST to the gateway (default: dry-run only)
Env: GATEWAY_URL (default: facade http://127.0.0.1:4001, litellm http://127.0.0.1:4000)
     GATEWAY_MASTER_KEY (facade --apply) | LITELLM_MASTER_KEY (litellm --apply)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)         ORG="${2:-}"; shift 2 ;;
    --tier)        TIER="${2:-}"; shift 2 ;;
    --budget)      BUDGET="${2:-}"; shift 2 ;;
    --approved-by) APPROVED_BY="${2:-}"; shift 2 ;;
    --backend)     BACKEND="${2:-}"; shift 2 ;;
    --apply)       APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "${ORG}" ]] && { echo "error: --org is required" >&2; usage; exit 2; }
case "${TIER}" in dev|gov) ;; *) echo "error: --tier must be dev|gov" >&2; exit 2 ;; esac
case "${BACKEND}" in facade|litellm) ;; *) echo "error: --backend must be facade|litellm" >&2; exit 2 ;; esac

# Per-tier budget default; whole-USD only (bash integer arithmetic for soft_budget).
[[ -z "${BUDGET}" ]] && { [[ "${TIER}" == "gov" ]] && BUDGET=250 || BUDGET=100; }
[[ "${BUDGET}" =~ ^[0-9]+$ ]] || { echo "error: --budget must be a whole number of USD" >&2; exit 2; }

# ADR-018 approval gate: the sensitive (gov) tier requires a named approver.
# (The façade admin API enforces this server-side too; we fail fast in dry-run.)
if [[ "${TIER}" == "gov" && -z "${APPROVED_BY}" ]]; then
  echo "error: --tier gov requires --approved-by \"<name>\" (ADR-018 approval gate)" >&2
  exit 2
fi
SOFT=$(( BUDGET * 80 / 100 ))   # soft_budget alert threshold = 80% of max

# Tier model allow-lists — keep in sync with docker/gateway-host/litellm-config.yaml.
if [[ "${TIER}" == "gov" ]]; then
  MODELS='["gov/claude-opus-4-8","gov/gpt-4o"]'
else
  MODELS='["gpt-4o","claude-fable-5","claude-opus-4-8","claude-sonnet-4-6","claude-haiku-4-5"]'
fi

# slug for aliases: lower, non-alnum -> '-'
SLUG=$(printf '%s' "${ORG}" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')

# --- backend-specific wiring -------------------------------------------------
if [[ "${BACKEND}" == "facade" ]]; then
  GW_URL="${GATEWAY_URL:-http://127.0.0.1:4001}"
  MASTER="${GATEWAY_MASTER_KEY:-}" ; MASTER_ENV="GATEWAY_MASTER_KEY"
  TEAM_PATH="/admin/teams" ; KEY_PATH="/admin/keys" ; TEAM_ID_FIELD="id"
  # approved_by as JSON: a string for gov, null for dev.
  if [[ -n "${APPROVED_BY}" ]]; then APPROVED_JSON=$(printf '"%s"' "${APPROVED_BY}"); else APPROVED_JSON="null"; fi
  TEAM_PAYLOAD=$(printf '{"alias":"%s","tier":"%s","max_budget":%s,"soft_budget":%s,"budget_duration":"30d","models":%s,"approved_by":%s}' \
    "${ORG}" "${TIER}" "${BUDGET}" "${SOFT}" "${MODELS}" "${APPROVED_JSON}")
  KEY_PAYLOAD_TMPL='{"team_id":"%s","alias":"%s-key","max_budget":%s}'
else
  GW_URL="${GATEWAY_URL:-http://127.0.0.1:4000}"
  MASTER="${LITELLM_MASTER_KEY:-}" ; MASTER_ENV="LITELLM_MASTER_KEY"
  TEAM_PATH="/team/new" ; KEY_PATH="/key/generate" ; TEAM_ID_FIELD="team_id"
  TEAM_PAYLOAD=$(printf '{"team_alias":"%s","max_budget":%s,"soft_budget":%s,"budget_duration":"30d","models":%s,"metadata":{"tier":"%s","approved_by":"%s"}}' \
    "${ORG}" "${BUDGET}" "${SOFT}" "${MODELS}" "${TIER}" "${APPROVED_BY:-n/a}")
  KEY_PAYLOAD_TMPL='{"team_id":"%s","key_alias":"%s-key","max_budget":%s}'
fi

echo "== provision-org: ${ORG} (tier=${TIER}, budget=\$${BUDGET}, soft=\$${SOFT}/30d) =="
echo "backend:  ${BACKEND}"
echo "gateway:  ${GW_URL}"
echo "approver: ${APPROVED_BY:-n/a (dev tier — no approval gate)}"
echo
echo "[1] POST ${TEAM_PATH}"
echo "    ${TEAM_PAYLOAD}"
echo "[2] POST ${KEY_PATH}  (team_id from step 1; alias=${SLUG}-key)"
echo

if [[ "${APPLY}" -ne 1 ]]; then
  echo "DRY-RUN — nothing sent. Re-run with --apply to provision."
  [[ "${BACKEND}" == "facade" ]] && echo "Note: keys are ENFORCED only when GATEWAY_CONTROL_PLANE=true on the façade."
  exit 0
fi

if [[ -z "${MASTER}" ]]; then
  echo "error: --apply requires ${MASTER_ENV} in the environment" >&2
  exit 2
fi
if ! command -v curl >/dev/null || ! command -v python3 >/dev/null; then
  echo "error: --apply needs curl and python3" >&2
  exit 2
fi

auth=(-H "Authorization: Bearer ${MASTER}" -H 'Content-Type: application/json')

team_resp=$(curl -sS --max-time 20 "${auth[@]}" -d "${TEAM_PAYLOAD}" "${GW_URL}${TEAM_PATH}")
team_id=$(printf '%s' "${team_resp}" | TIF="${TEAM_ID_FIELD}" python3 -c 'import sys,os,json; print(json.load(sys.stdin).get(os.environ["TIF"],""))' 2>/dev/null)
if [[ -z "${team_id}" ]]; then
  echo "error: could not create team / parse ${TEAM_ID_FIELD}. Response:" >&2
  echo "${team_resp}" >&2
  exit 1
fi
echo "team_id: ${team_id}"

key_payload=$(printf "${KEY_PAYLOAD_TMPL}" "${team_id}" "${SLUG}" "${BUDGET}")
key_resp=$(curl -sS --max-time 20 "${auth[@]}" -d "${key_payload}" "${GW_URL}${KEY_PATH}")
vkey=$(printf '%s' "${key_resp}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("key",""))' 2>/dev/null)
if [[ -z "${vkey}" ]]; then
  echo "error: could not generate key. Response:" >&2
  echo "${key_resp}" >&2
  exit 1
fi

echo
echo "✓ provisioned ${ORG}"
echo "  backend:     ${BACKEND}"
echo "  team_id:     ${team_id}"
echo "  virtual key: ${vkey}"
echo "  tier:        ${TIER}  (models: ${MODELS})"
echo "  budget:      \$${BUDGET} hard / \$${SOFT} soft-alert per 30d"
echo "  approved_by: ${APPROVED_BY:-n/a}"
echo
echo "Deliver the virtual key to the org over a secure channel — it is shown once."
echo "Next: map the org's Okta group -> team_id and add it to Cloudflare Access"
echo "(docs/org-onboarding.md step 3)."
[[ "${BACKEND}" == "facade" ]] && echo "Reminder: enforced only when GATEWAY_CONTROL_PLANE=true on the façade."
