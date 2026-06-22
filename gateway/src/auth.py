"""Virtual-key auth gate for the façade (Phase 1).

v0 enforces that a well-formed virtual key is PRESENT at the edge and forwards
it unchanged to LiteLLM, which remains the source of truth for key validity,
scopes, and budgets. Phase 2 (docs/own-gateway.md) moves the key store into the
façade so LiteLLM can be dropped entirely; the route code calls only this
module, so that swap is local to here.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request


class Principal:
    """The authenticated caller, as far as the façade knows in v0."""

    def __init__(self, key: str) -> None:
        self.key = key


def extract_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def authenticate(request: Request, *, require_key: bool, key_prefix: str) -> Optional[Principal]:
    key = extract_key(request)
    if key is None:
        if require_key:
            raise HTTPException(
                status_code=401,
                detail={"error": {"message": "Missing bearer virtual key.",
                                  "type": "authentication_error", "code": "missing_key"}})
        return None
    if key_prefix and not key.startswith(key_prefix):
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Malformed virtual key.",
                              "type": "authentication_error", "code": "bad_key"}})
    return Principal(key)
