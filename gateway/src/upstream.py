"""Upstream client — proxies to the LiteLLM engine behind the façade.

The façade → LiteLLM hop is intra-docker (service name `litellm:4000`), so like
the guardrail client it sets trust_env=False: provider egress through the Squid
allowlist happens FROM LiteLLM, not from the façade. Routing this hop through
Squid would 403 on the internal hostname.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

import httpx


class Upstream:
    def __init__(self, base_url: str, timeout: float,
                 client: Optional[httpx.AsyncClient] = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=self._base, timeout=timeout, trust_env=False)

    @staticmethod
    def _forward_headers(headers: dict) -> dict:
        """Pass the caller's auth through; drop hop-by-hop / host headers."""
        drop = {"host", "content-length", "connection", "accept-encoding"}
        return {k: v for k, v in headers.items() if k.lower() not in drop}

    async def post_json(self, path: str, body: dict, headers: dict) -> httpx.Response:
        return await self._client.post(
            path, json=body, headers=self._forward_headers(headers))

    async def get(self, path: str, headers: dict) -> httpx.Response:
        return await self._client.get(path, headers=self._forward_headers(headers))

    async def stream(self, path: str, body: dict, headers: dict) -> httpx.Response:
        """Open a streaming upstream response. Caller must `await resp.aclose()`."""
        req = self._client.build_request(
            "POST", path, json=body, headers=self._forward_headers(headers))
        return await self._client.send(req, stream=True)

    async def aclose(self) -> None:
        await self._client.aclose()
