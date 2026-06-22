"""NeMo DaaS guardrail client for the façade.

This is the same fail-closed contract as the LiteLLM shim
(docker/gateway-host/nemo_guardrail.py), reimplemented as a plain async client
the façade owns — no dependency on any LiteLLM base class. When the façade
takes over enforcement (GATEWAY_GUARDRAIL_ENFORCE=true), this is the single
enforcement point and the LiteLLM `guardrails:` block can be removed.

FAIL-CLOSED: if the NeMo service is unreachable, the request is BLOCKED rather
than allowed through unscreened (threat model T-NEMO-T). Killing the guardrail
must not be a bypass.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx


class GuardrailResult(dict):
    @property
    def blocked(self) -> bool:
        return bool(self.get("blocked"))


class Guardrail:
    def __init__(self, url: str, timeout: float,
                 client: Optional[httpx.AsyncClient] = None) -> None:
        self._url = url.rstrip("/")
        # trust_env=False: the NeMo URL is always an intra-docker service name,
        # never an internet host. Same reasoning as nemo_guardrail.py — with
        # HTTP_PROXY set for provider egress, httpx would otherwise route this
        # internal call through Squid and trip the fail-closed branch.
        self._client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)

    async def check(self, role: str, content: str,
                    request_id: Optional[str] = None,
                    caller_role: Optional[str] = None) -> GuardrailResult:
        if not content:
            return GuardrailResult(blocked=False, findings=[], activated_rails=[])
        try:
            resp = await self._client.post(
                f"{self._url}/v1/guardrail/check",
                json={"role": role, "content": content,
                      "request_id": request_id, "caller_role": caller_role},
            )
            resp.raise_for_status()
            return GuardrailResult(resp.json())
        except Exception:  # noqa: BLE001 — fail closed on ANY error
            return GuardrailResult(
                blocked=True, findings=[],
                activated_rails=[{"type": "error", "name": "guardrail_unreachable"}],
                message="Guardrail unavailable — request blocked (fail-closed).")

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- content extraction (OpenAI chat shape) ----------------------------
    @staticmethod
    def prompt_text(body: dict) -> str:
        parts = []
        for m in body.get("messages", []) or []:
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):  # multimodal content blocks
                parts.extend(b.get("text", "") for b in c if isinstance(b, dict))
        return "\n".join(p for p in parts if p)

    @staticmethod
    def response_text(body: Any) -> str:
        try:
            choices = body.get("choices", []) if isinstance(body, dict) else []
            out = []
            for ch in choices:
                msg = ch.get("message", {}) if isinstance(ch, dict) else {}
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, str):
                    out.append(content)
            return "\n".join(out)
        except Exception:  # noqa: BLE001
            return ""
