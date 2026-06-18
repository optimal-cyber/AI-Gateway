"""Structured request audit log for the gateway façade.

One JSON line per request, to stdout AND a rotated file — the same shape and
philosophy as NeMo's decisions.log and the compliance-mcp audit rows, so the
three join on `request_id` to reconstruct a full life-of-a-prompt for a 3PAO.

PRIVACY: we never log prompt or completion CONTENT here. Only metadata
(identity fingerprint, model, token counts, latency, decision). The virtual key
is never logged raw — only a non-reversible fingerprint (prefix + sha256 head).
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import sys
from typing import Any, Optional


def key_fingerprint(key: Optional[str]) -> Optional[str]:
    """Non-reversible, low-cardinality id for a virtual key (audit only).

    Returns e.g. "sk-…a1b2:9f86d081" — enough to correlate requests from the
    same key without ever storing the secret.
    """
    if not key:
        return None
    tail = key[-4:] if len(key) >= 4 else key
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"…{tail}:{digest}"


class Auditor:
    def __init__(self, log_path: str) -> None:
        self._log = logging.getLogger("gateway.audit")
        self._log.setLevel(logging.INFO)
        self._log.propagate = False
        if not self._log.handlers:
            stdout = logging.StreamHandler(sys.stdout)
            stdout.setFormatter(logging.Formatter("%(message)s"))
            self._log.addHandler(stdout)
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                fileh = logging.handlers.RotatingFileHandler(
                    log_path, maxBytes=10_000_000, backupCount=5)
                fileh.setFormatter(logging.Formatter("%(message)s"))
                self._log.addHandler(fileh)
            except OSError:
                pass  # stdout is enough if the volume isn't mounted yet

    def emit(self, **fields: Any) -> None:
        # Drop None values for a compact row; callers pass only what they have.
        row = {"event": "gateway_request", **{k: v for k, v in fields.items() if v is not None}}
        self._log.info(json.dumps(row, separators=(",", ":")))
