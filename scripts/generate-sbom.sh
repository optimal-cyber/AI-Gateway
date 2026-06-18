#!/usr/bin/env bash
# =============================================================================
# generate-sbom.sh — produce a CycloneDX SBOM for the pinned LiteLLM image.
# =============================================================================
# Why: a Software Bill of Materials is the supply-chain evidence a 3PAO expects
# for third-party code running inside the boundary (CMMC L2 / NIST 800-53
# SR-3, SR-4, CM-8). We pin the image by digest (scripts/pin-litellm-digest.sh)
# and then enumerate what's inside it. Output is committed under docs/sbom/ so
# the artifact and its inventory live together in version control.
#
# Tries syft, then trivy, then `docker scout sbom` — whichever is installed.
# All three emit CycloneDX JSON. None network-call the image once it's pulled.
#
# Usage:
#   scripts/generate-sbom.sh                       # SBOM of the pinned litellm image
#   scripts/generate-sbom.sh <image-ref>           # SBOM of an arbitrary ref
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIN_RECORD="${REPO_ROOT}/docker/gateway-host/litellm.pinned"
OUT_DIR="${REPO_ROOT}/docs/sbom"

# Resolve the image: explicit arg wins, else read the committed pin record.
if [ "${1:-}" != "" ]; then
  IMAGE_REF="$1"
elif [ -f "$PIN_RECORD" ]; then
  IMAGE_REF="$(awk -F': +' '/^pinned_image:/{print $2}' "$PIN_RECORD")"
else
  echo "no image ref given and no pin record at $PIN_RECORD" >&2
  echo "run scripts/pin-litellm-digest.sh first, or pass an image ref" >&2
  exit 1
fi

[ -n "$IMAGE_REF" ] || { echo "could not determine image ref" >&2; exit 1; }
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR}/litellm.cyclonedx.json"

echo "Generating SBOM for: ${IMAGE_REF}" >&2

if command -v syft >/dev/null 2>&1; then
  echo "using syft" >&2
  syft "$IMAGE_REF" -o cyclonedx-json="$OUT_FILE"
elif command -v trivy >/dev/null 2>&1; then
  echo "using trivy" >&2
  trivy image --quiet --format cyclonedx --output "$OUT_FILE" "$IMAGE_REF"
elif command -v docker >/dev/null 2>&1 && docker scout version >/dev/null 2>&1; then
  echo "using docker scout" >&2
  docker scout sbom --format cyclonedx --output "$OUT_FILE" "$IMAGE_REF"
else
  cat >&2 <<'EOF'
No SBOM tool found. Install one of:
  syft   — https://github.com/anchore/syft   (brew install syft)
  trivy  — https://github.com/aquasecurity/trivy
  docker scout (ships with recent Docker Desktop)
Then re-run this script.
EOF
  exit 1
fi

echo "wrote ${OUT_FILE}" >&2
echo "Commit docs/sbom/ alongside docker/gateway-host/litellm.pinned." >&2
