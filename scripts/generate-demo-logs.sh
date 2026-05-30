#!/usr/bin/env bash
# =============================================================================
# generate-demo-logs.sh — populate LiteLLM Logs view with realistic traffic
# =============================================================================
# Reusable wrapper for scripts/generate-demo-logs.py. Fetches the Open WebUI
# virtual key from AWS Secrets Manager and runs the generator either:
#
#   (a) directly against a reachable LiteLLM (--local — runs the Python here);
#   (b) inside the gateway-host container network via SSM + docker cp + docker
#       exec (--remote — default, since the script targets http://localhost:4000
#       from the litellm container's perspective and there's no public LiteLLM
#       endpoint to hit from outside Cloudflare Access).
#
# Examples:
#   ./scripts/generate-demo-logs.sh                       # default: --count 80, no MCP
#   ./scripts/generate-demo-logs.sh -- --with-mcp         # include MCP tool calls
#   ./scripts/generate-demo-logs.sh -- --count 30 --dry-run
#
# Cost: ~80 chat calls @ ~$0.005 avg ≈ $0.40 in real Anthropic/OpenAI spend.
# Blocked calls and MCP tool calls cost $0.
# =============================================================================
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-ai-lab}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GW_NAME_TAG="${GW_NAME_TAG:-ai-lab-gateway-host}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  sed -n '2,/^# ===/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage

# split args at the first `--`: anything after is passed to the Python script
PY_ARGS=()
if [[ " $* " == *" -- "* ]]; then
  for ((i = 1; i <= $#; i++)); do
    if [[ "${!i}" == "--" ]]; then
      PY_ARGS=("${@:i+1}")
      break
    fi
  done
fi

echo "→ fetching virtual key from Secrets Manager (lab/litellm_virtual_key_webui)"
KEY="$(aws secretsmanager get-secret-value \
       --profile "$AWS_PROFILE" --region "$AWS_REGION" \
       --secret-id lab/litellm_virtual_key_webui \
       --query SecretString --output text)"

echo "→ locating $GW_NAME_TAG"
GW_IID="$(aws ec2 describe-instances --profile "$AWS_PROFILE" --region "$AWS_REGION" \
          --filters "Name=tag:Name,Values=${GW_NAME_TAG}" \
                    "Name=instance-state-name,Values=running" \
          --query 'Reservations[0].Instances[0].InstanceId' --output text)"

if [[ -z "$GW_IID" || "$GW_IID" == "None" ]]; then
  echo "❌ gateway-host instance not found"
  exit 1
fi
echo "  $GW_IID"

# Build the SSM command that:
#  1. copies the generator into the litellm container
#  2. runs it with the virtual key in env
echo "→ shipping generator to gateway-host and running inside litellm container"

# Encode the Python source as base64 to avoid SSM quote-hell.
GEN_B64="$(base64 -i "$SCRIPT_DIR/generate-demo-logs.py" | tr -d '\n')"
ARGS_STR="${PY_ARGS[*]:-}"

CMD_JSON="$(cat <<JSON
{
  "InstanceIds": ["$GW_IID"],
  "DocumentName": "AWS-RunShellScript",
  "Parameters": {
    "commands": [
      "set -euo pipefail",
      "echo $GEN_B64 | base64 -d > /tmp/generate-demo-logs.py",
      "docker cp /tmp/generate-demo-logs.py gateway-host-litellm-1:/tmp/generate-demo-logs.py",
      "docker exec -e LITELLM_VIRTUAL_KEY='${KEY}' -e LITELLM_BASE='http://localhost:4000' gateway-host-litellm-1 python /tmp/generate-demo-logs.py $ARGS_STR"
    ]
  }
}
JSON
)"

CID="$(aws ssm send-command --profile "$AWS_PROFILE" --region "$AWS_REGION" \
       --comment "generate demo logs" --cli-input-json "$CMD_JSON" \
       --query 'Command.CommandId' --output text)"
echo "  CommandId=$CID"

echo "→ waiting for completion (polling every 5s)"
while :; do
  STATUS="$(aws ssm get-command-invocation --profile "$AWS_PROFILE" --region "$AWS_REGION" \
            --command-id "$CID" --instance-id "$GW_IID" \
            --query 'Status' --output text 2>/dev/null || echo Pending)"
  echo "  status=$STATUS"
  [[ "$STATUS" != "Pending" && "$STATUS" != "InProgress" ]] && break
  sleep 5
done

echo
echo "===== STDOUT ====="
aws ssm get-command-invocation --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --command-id "$CID" --instance-id "$GW_IID" \
  --query 'StandardOutputContent' --output text

echo
echo "===== STDERR (tail) ====="
aws ssm get-command-invocation --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --command-id "$CID" --instance-id "$GW_IID" \
  --query 'StandardErrorContent' --output text | tail -20

echo
echo "✓ done. open https://gateway.optimallabs.io → Logs to see the new rows."
