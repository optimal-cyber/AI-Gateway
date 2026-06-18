"""Gateway façade configuration (env-driven).

Phase 1 of the own-the-gateway arc (docs/own-gateway.md). The façade is OUR
OpenAI-compatible front door: it owns the `/v1` surface, the auth gate, the
guardrail enforcement point, and the audit trail — and proxies the actual
provider work to the (pinned, vendored) LiteLLM engine behind it. Customers see
the façade; LiteLLM is an internal implementation detail.

All settings come from the environment so the same image runs in the lab
(secrets injected by docker/_shared/secrets-bootstrap.sh) and in tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Branding — the façade owns `/` and the product name (the thing white-label
    # was gated behind upstream). See docs/own-gateway.md.
    name: str = os.environ.get("GATEWAY_NAME", "AI Gateway")

    # Bind.
    host: str = os.environ.get("GATEWAY_HOST", "0.0.0.0")
    port: int = int(os.environ.get("GATEWAY_PORT", "4001"))

    # Upstream LiteLLM engine (intra-docker; never goes through the Squid proxy).
    upstream_url: str = os.environ.get("GATEWAY_UPSTREAM_URL", "http://litellm:4000")
    upstream_timeout: float = float(os.environ.get("GATEWAY_UPSTREAM_TIMEOUT", "600"))

    # Guardrail (the same NeMo DaaS the LiteLLM shim calls — nemo_guardrail.py).
    guardrail_url: str = os.environ.get("GATEWAY_GUARDRAIL_URL", "http://nemo-guardrails:8000")
    guardrail_timeout: float = float(os.environ.get("GATEWAY_GUARDRAIL_TIMEOUT", "10"))
    # Enforce at the façade layer? Default OFF: in the current topology LiteLLM
    # still runs the NeMo rails, so enforcing here too would double every NeMo
    # call. Flip to true as part of migrating guardrails OFF LiteLLM and ONTO
    # the façade (then remove the `guardrails:` block from litellm-config.yaml).
    # The audit trail below is written regardless of this flag.
    guardrail_enforce: bool = _flag("GATEWAY_GUARDRAIL_ENFORCE", False)

    # Auth. v0 enforces presence + shape of the virtual key at the edge and
    # forwards it; LiteLLM remains the source of truth for key validity/budgets
    # until Phase 2 moves the key store into the façade.
    require_key: bool = _flag("GATEWAY_REQUIRE_KEY", True)
    key_prefix: str = os.environ.get("GATEWAY_KEY_PREFIX", "sk-")

    # Structured request audit log (JSON lines, rotated) — mirrors the shape of
    # NeMo's decisions.log so rows join on request_id across the stack.
    audit_log: str = os.environ.get("GATEWAY_AUDIT_LOG", "/var/log/gateway/requests.log")


def load() -> "Settings":
    return Settings()
