# LiteLLM supply-chain controls (Phase 0 — pin & vendor)

> Part of the **own-the-gateway** arc (see [`docs/own-gateway.md`](own-gateway.md)).
> This page covers the cheapest, do-it-first controls that remove the
> *supply-chain* and *version-drift* risk of running upstream LiteLLM inside the
> boundary — independent of whether we ever build our own gateway.

## The problem

The gateway runs LiteLLM as a container pulled from a **floating tag**:
`ghcr.io/berriai/litellm:main-stable`. Two risks follow from that in a
CMMC L2 / SBIR context:

1. **Non-reproducibility / drift.** `main-stable` moves. The image a 3PAO sees
   today is not the image that ran last week, and a redeploy can silently pull a
   new build that changes behavior (the config already warns that LiteLLM's
   proxy import paths "occasionally move" — see `nemo_guardrail.py`).
2. **Unattested third-party code in the boundary.** We can't point to *what* is
   inside the image we're running.

Neither requires rewriting anything. Both are fixed by **pinning + an SBOM**.

## The controls

### 1. Pin the image by digest

`docker/gateway-host/docker-compose.yml` no longer references `:main-stable`.
It references an **immutable digest**:

```yaml
image: ghcr.io/berriai/litellm@sha256:713e2a03…39c67a
```

A digest is content-addressed: the same `@sha256:` always resolves to the exact
same bytes, or the pull fails. The running artifact is now reproducible and
attestable.

- **Re-pin** (e.g. to adopt a new upstream fix) with
  [`scripts/pin-litellm-digest.sh`](../scripts/pin-litellm-digest.sh). It
  resolves the tag → digest, rewrites the compose line in place, and writes
  `docker/gateway-host/litellm.pinned` as the evidence record. Re-pinning is now
  a **deliberate, reviewed commit** instead of an invisible side effect of a
  redeploy.
- **Evidence:** `docker/gateway-host/litellm.pinned` records the source tag, the
  resolved digest, and the UTC timestamp it was resolved. Commit it.

### 2. Generate and commit an SBOM

[`scripts/generate-sbom.sh`](../scripts/generate-sbom.sh) enumerates everything
inside the pinned image as CycloneDX JSON under `docs/sbom/`. It uses whichever
of `syft` / `trivy` / `docker scout` is installed. Regenerate it every time you
re-pin.

This is the artifact that answers "what third-party code is in your boundary?"
and feeds vulnerability scanning.

### 3. (Optional) rebuild under our own registry

For a stronger posture, rebuild `FROM` the pinned digest into our own registry
(`FROM ghcr.io/berriai/litellm@sha256:…`) so the image identity, scan cadence,
and retention are ours. Not required for Phase 0 — the digest pin already makes
the artifact immutable — so it's left as a documented option, consistent with
how the gov-tier boundaries are "config-ready, not live".

## Control mapping (NIST 800-53 Rev 5 / CMMC L2)

| Control | How this satisfies it |
|---|---|
| **CM-8** (System Component Inventory) | SBOM under `docs/sbom/` enumerates image contents |
| **CM-2 / CM-3** (Baseline / Change Control) | Digest pin + `litellm.pinned`; re-pin is a reviewed commit |
| **SR-3 / SR-4** (Supply Chain Controls / Provenance) | Immutable digest + recorded resolution provenance |
| **SI-2** (Flaw Remediation) | SBOM feeds CVE scanning; re-pin applies fixes deliberately |

## Runbook

```bash
# 1. Pin (or re-pin) the image, with egress to ghcr.io available:
scripts/pin-litellm-digest.sh                 # or: ... v1.55.8-stable

# 2. Inventory it:
scripts/generate-sbom.sh

# 3. Review + commit:
git add docker/gateway-host/docker-compose.yml \
        docker/gateway-host/litellm.pinned \
        docs/sbom/
git commit -m "supply-chain: re-pin LiteLLM digest + refresh SBOM"
```

> **Lab note.** In the lab, container egress is forced through the Squid
> dstdomain allowlist (ADR-009). Resolving a digest or pulling the image needs
> `ghcr.io` (and its token/blob hosts) on the allowlist, **or** run
> `pin-litellm-digest.sh` from an admin workstation and commit the result — the
> gateway host only needs the digest it pulls, not the resolver.
