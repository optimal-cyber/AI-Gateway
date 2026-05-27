"""LiteLLM custom guardrail -> NeMo Guardrails DaaS shim (ADR-003).

Registered in litellm-config.yaml as:

    guardrails:
      - guardrail_name: "nemo-input"
        litellm_params:
          guardrail: nemo_guardrail.NemoDaaSGuardrail
          mode: "pre_call"
      - guardrail_name: "nemo-output"
        litellm_params:
          guardrail: nemo_guardrail.NemoDaaSGuardrail
          mode: "post_call"

Mounted into the LiteLLM container at /app/nemo_guardrail.py and on PYTHONPATH.

FAIL-CLOSED: if the NeMo service is unreachable, the request is BLOCKED rather
than allowed through unscreened. Killing the guardrail must not be a bypass
(threat model T-NEMO-T). Verify the CustomGuardrail import path against the
LiteLLM version you deploy — the base class is stable but the proxy type
imports occasionally move.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

try:
    from litellm.integrations.custom_guardrail import CustomGuardrail
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.caching.caching import DualCache
    from litellm._logging import verbose_proxy_logger as _log
except Exception:  # pragma: no cover - lets the file import for local linting
    CustomGuardrail = object  # type: ignore
    UserAPIKeyAuth = Any       # type: ignore
    DualCache = Any            # type: ignore
    import logging
    _log = logging.getLogger("nemo_guardrail")

from fastapi import HTTPException

NEMO_URL = os.environ.get("NEMO_GUARDRAIL_URL", "http://nemo-guardrails:8000")
TIMEOUT = float(os.environ.get("NEMO_GUARDRAIL_TIMEOUT", "10"))


class NemoDaaSGuardrail(CustomGuardrail):  # type: ignore[misc]
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = httpx.AsyncClient(timeout=TIMEOUT)

    # -- helpers -----------------------------------------------------------
    async def _check(self, role: str, content: str,
                     request_id: Optional[str] = None,
                     caller_role: Optional[str] = None) -> dict:
        if not content:
            return {"blocked": False, "findings": [], "activated_rails": []}
        try:
            resp = await self._client.post(
                f"{NEMO_URL}/v1/guardrail/check",
                json={"role": role, "content": content,
                      "request_id": request_id, "caller_role": caller_role},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            _log.error("NeMo guardrail unreachable (%s) — failing closed", exc)
            return {"blocked": True, "findings": [],
                    "activated_rails": [{"type": "error", "name": "guardrail_unreachable"}],
                    "message": "Guardrail unavailable — request blocked (fail-closed)."}

    @staticmethod
    def _prompt_text(data: dict) -> str:
        parts = []
        for m in data.get("messages", []) or []:
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):  # multimodal content blocks
                parts.extend(b.get("text", "") for b in c if isinstance(b, dict))
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _response_text(response: Any) -> str:
        try:
            choices = getattr(response, "choices", None) or response.get("choices", [])
            out = []
            for ch in choices:
                msg = getattr(ch, "message", None) or ch.get("message", {})
                content = getattr(msg, "content", None) if msg is not None else None
                if content is None and isinstance(msg, dict):
                    content = msg.get("content")
                if isinstance(content, str):
                    out.append(content)
            return "\n".join(out)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _raise(result: dict) -> None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "blocked_by_guardrail",
                "guardrail": "nemo",
                "message": result.get("message", "Blocked by the AI-lab guardrail."),
                "findings": result.get("findings", []),
                "activated_rails": result.get("activated_rails", []),
            },
        )

    # -- LiteLLM hooks -----------------------------------------------------
    async def async_pre_call_hook(self, user_api_key_dict: "UserAPIKeyAuth",
                                  cache: "DualCache", data: dict,
                                  call_type: Optional[str]) -> dict:
        text = self._prompt_text(data)
        caller_role = (data.get("metadata") or {}).get("user_api_key_user_role")
        result = await self._check("user", text,
                                   request_id=data.get("litellm_call_id"),
                                   caller_role=caller_role)
        if result.get("blocked"):
            self._raise(result)
        return data

    async def async_post_call_success_hook(self, data: dict,
                                           user_api_key_dict: "UserAPIKeyAuth",
                                           response: Any) -> Any:
        text = self._response_text(response)
        result = await self._check("assistant", text,
                                   request_id=data.get("litellm_call_id"))
        if result.get("blocked"):
            self._raise(result)
        return response
