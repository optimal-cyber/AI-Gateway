"""Per-model pricing for budget enforcement (Phase 2).

Cost = prompt_tokens/1k * input + completion_tokens/1k * output (USD).

⚠️ PLACEHOLDER RATES. These are order-of-magnitude defaults so budget
enforcement is *exercised*, NOT authoritative prices. Verify against current
provider pricing before relying on spend figures for billing — same "verify
against the version you deploy" discipline as litellm-config.yaml. Override any
rate at runtime via GATEWAY_PRICING (JSON: {"model": [in_per_1k, out_per_1k]}).
A model absent from the table costs 0 and logs a warning row downstream.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Tuple

_log = logging.getLogger("gateway.pricing")

# (input_per_1k, output_per_1k) USD — PLACEHOLDERS, verify before billing.
_DEFAULT: Dict[str, Tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.010),
    "claude-fable-5": (0.015, 0.075),
    "claude-opus-4-8": (0.015, 0.075),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-haiku-4-5": (0.0008, 0.004),
    # gov/* mirror their commercial counterparts until real gov pricing is wired.
    "gov/gpt-4o": (0.0025, 0.010),
    "gov/claude-opus-4-8": (0.015, 0.075),
}


def _load_overrides() -> Dict[str, Tuple[float, float]]:
    raw = os.environ.get("GATEWAY_PRICING")
    if not raw:
        return {}
    try:
        return {k: (float(v[0]), float(v[1])) for k, v in json.loads(raw).items()}
    except Exception:  # noqa: BLE001
        _log.warning("GATEWAY_PRICING is not valid JSON; ignoring overrides")
        return {}


_RATES = {**_DEFAULT, **_load_overrides()}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = _RATES.get(model)
    if rate is None:
        return 0.0
    return round((prompt_tokens / 1000.0) * rate[0]
                 + (completion_tokens / 1000.0) * rate[1], 6)


def known(model: str) -> bool:
    return model in _RATES
