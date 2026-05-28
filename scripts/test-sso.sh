#!/usr/bin/env bash
# =============================================================================
# test-sso.sh — structural SSO checks (no browser, no MFA)
# =============================================================================
# Verifies the identity plane is wired correctly at the protocol level. The full
# interactive MFA flow is exercised in the Phase 5 test plan; this is the fast
# pre-flight. Checks degrade to SKIP when a host/var isn't available yet, so it
# is safe to run at any phase.
#
# Inputs (env, all optional — checks that need a missing one are SKIPPED):
#   OKTA_TENANT_URL          e.g. https://dev-12345678.okta.com
#   CF_TEAM                  Cloudflare Zero Trust team name, e.g. optimallabs
#   CHAT_HOST                default chat.lab.ironechelon.com
#   GATEWAY_HOST             default gateway.lab.ironechelon.com
#   OKTA_LITELLM_CLIENT_ID   to assert the gateway OIDC redirect carries it
#
# Usage:
#   OKTA_TENANT_URL=https://dev-123.okta.com CF_TEAM=optimallabs ./scripts/test-sso.sh
# =============================================================================
set -uo pipefail

CHAT_HOST="${CHAT_HOST:-chat.lab.ironechelon.com}"
GATEWAY_HOST="${GATEWAY_HOST:-gateway.lab.ironechelon.com}"
OKTA_TENANT_URL="${OKTA_TENANT_URL:-}"
CF_TEAM="${CF_TEAM:-}"
OKTA_LITELLM_CLIENT_ID="${OKTA_LITELLM_CLIENT_ID:-}"

pass=0 fail=0 skip=0
ok()   { echo "  ✓ PASS  $*"; pass=$((pass+1)); }
no()   { echo "  ✗ FAIL  $*"; fail=$((fail+1)); }
sk()   { echo "  - SKIP  $*"; skip=$((skip+1)); }

# tiny JSON field check without requiring jq
has_json_field() { # <body> <field>
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -e --arg f "$2" 'has($f)' >/dev/null 2>&1
  else
    printf '%s' "$1" | grep -q "\"$2\""
  fi
}

echo "== SSO structural checks =="

# 1. Okta OIDC discovery doc well-formed
echo "[1] Okta OIDC discovery"
if [[ -n "$OKTA_TENANT_URL" ]]; then
  disc="$(curl -fsS "${OKTA_TENANT_URL%/}/.well-known/openid-configuration" 2>/dev/null)"
  if [[ -n "$disc" ]] && has_json_field "$disc" "authorization_endpoint" \
       && has_json_field "$disc" "token_endpoint" && has_json_field "$disc" "jwks_uri"; then
    ok "discovery doc has authorization_endpoint, token_endpoint, jwks_uri"
  else
    no "discovery doc missing/malformed at $OKTA_TENANT_URL"
  fi
else
  sk "set OKTA_TENANT_URL to check Okta discovery"
fi

# 2. Cloudflare Access JWKS reachable (origins verify Cf-Access-Jwt-Assertion against this)
echo "[2] Cloudflare Access JWKS"
if [[ -n "$CF_TEAM" ]]; then
  certs="$(curl -fsS "https://${CF_TEAM}.cloudflareaccess.com/cdn-cgi/access/certs" 2>/dev/null)"
  if [[ -n "$certs" ]] && has_json_field "$certs" "keys"; then
    ok "Access JWKS present at ${CF_TEAM}.cloudflareaccess.com"
  else
    no "Access JWKS not reachable for team '${CF_TEAM}'"
  fi
else
  sk "set CF_TEAM to check the Access JWKS"
fi

# 3. Chat app redirects unauthenticated requests to Cloudflare Access
echo "[3] chat app -> Cloudflare Access redirect"
loc="$(curl -sS -o /dev/null -D - "https://${CHAT_HOST}" 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1)=="location"{print $2}')"
code="$(curl -sS -o /dev/null -w '%{http_code}' "https://${CHAT_HOST}" 2>/dev/null)"
if [[ "$code" == "302" || "$code" == "301" ]] && printf '%s' "$loc" | grep -q "cloudflareaccess.com"; then
  ok "${CHAT_HOST} ${code} -> ${loc%%\?*} (cloudflareaccess.com)"
elif [[ -z "$code" || "$code" == "000" ]]; then
  sk "${CHAT_HOST} not resolvable/up yet (Phase 4 DNS + tunnel)"
else
  no "${CHAT_HOST} did not redirect to cloudflareaccess.com (got HTTP ${code})"
fi

# 4. Gateway LiteLLM OIDC redirect targets the Okta authorize URL with the client_id
echo "[4] gateway LiteLLM OIDC redirect"
gloc="$(curl -sS -o /dev/null -D - "https://${GATEWAY_HOST}/sso/key/generate" 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1)=="location"{print $2}')"
if [[ -z "$gloc" ]]; then
  sk "${GATEWAY_HOST} not up yet, or LiteLLM SSO path differs (verify in Phase 2)"
else
  authok=true
  [[ -n "$OKTA_TENANT_URL" ]] && { printf '%s' "$gloc" | grep -q "$(printf '%s' "${OKTA_TENANT_URL#https://}")" || authok=false; }
  printf '%s' "$gloc" | grep -q "oauth2/v1/authorize" || authok=false
  if [[ -n "$OKTA_LITELLM_CLIENT_ID" ]]; then
    printf '%s' "$gloc" | grep -q "client_id=${OKTA_LITELLM_CLIENT_ID}" || authok=false
  fi
  if $authok; then ok "gateway redirects to Okta authorize endpoint"; else
    no "gateway OIDC redirect did not match expected Okta authorize/client_id: $gloc"; fi
fi

echo
echo "== summary: ${pass} pass, ${fail} fail, ${skip} skip =="
[[ "$fail" -eq 0 ]]
