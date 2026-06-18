"""Federal Register API v1 client — keyless, read-only (compliance MCP growth).

GET https://www.federalregister.gov/api/v1/documents.json
    ?conditions[term]=...&conditions[type][]=RULE&conditions[agencies][]=slug
    &per_page=N&order=newest&fields[]=...

Public record, no API key. Same resilience contract as the SAM.gov client (10s
timeout; retry 429/5xx with exponential backoff; circuit breaker after 5
consecutive failures) — the breaker/retry primitives are shared from sam_client
so there is one source of truth. Parsing is defensive: missing fields degrade to
None rather than raising. No PII redaction — Federal Register documents are
public, so there is nothing to mask (contrast sam_gov_lookup, T-MCP-I).
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from .models import FederalRegisterDocument
from .sam_client import CircuitBreaker, CircuitOpenError, RetryableHTTPError

__all__ = ["FederalRegisterClient", "CircuitOpenError",
           "normalize_doc_type", "clamp_per_page", "parse_documents"]

FED_REG_URL = "https://www.federalregister.gov/api/v1/documents.json"

# friendly input -> Federal Register `conditions[type][]` code
_TYPE_CODES = {
    "rule": "RULE", "final": "RULE", "final rule": "RULE",
    "proposed": "PRORULE", "proposed rule": "PRORULE", "prorule": "PRORULE",
    "notice": "NOTICE",
    "presidential": "PRESDOCU", "presidential document": "PRESDOCU",
    "presdocu": "PRESDOCU",
}
_TYPE_CODE_SET = {"RULE", "PRORULE", "NOTICE", "PRESDOCU"}
_FIELDS = ["document_number", "title", "type", "abstract",
           "publication_date", "html_url", "pdf_url", "agencies"]
MAX_PER_PAGE = 20


def normalize_doc_type(value: Optional[str]) -> Optional[str]:
    """Map a friendly doc-type to a Federal Register type code, or None.

    Returns None for an empty/None value; raises ValueError on an unrecognized
    non-empty value."""
    if value is None or not str(value).strip():
        return None
    key = str(value).strip().lower()
    if key in _TYPE_CODES:
        return _TYPE_CODES[key]
    if str(value).strip().upper() in _TYPE_CODE_SET:
        return str(value).strip().upper()
    raise ValueError(
        "doc_type must be one of: rule, proposed, notice, presidential")


def clamp_per_page(n: int) -> int:
    """Bound page size to [1, MAX_PER_PAGE]; default 5 on bad input."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 5
    return max(1, min(n, MAX_PER_PAGE))


def parse_documents(payload: dict) -> List[FederalRegisterDocument]:
    out: List[FederalRegisterDocument] = []
    for r in (payload or {}).get("results", []) or []:
        if not isinstance(r, dict):
            continue
        agencies: List[str] = []
        for a in (r.get("agencies") or []):
            if isinstance(a, dict):
                name = a.get("name") or a.get("raw_name")
                if name:
                    agencies.append(name)
            elif isinstance(a, str):
                agencies.append(a)
        out.append(FederalRegisterDocument(
            document_number=r.get("document_number"),
            title=r.get("title"),
            type=r.get("type"),
            abstract=r.get("abstract"),
            publication_date=r.get("publication_date"),
            agencies=agencies,
            html_url=r.get("html_url"),
            pdf_url=r.get("pdf_url"),
        ))
    return out


class FederalRegisterClient:
    def __init__(self, timeout: float = 10.0,
                 breaker: Optional[CircuitBreaker] = None,
                 client: Optional[httpx.AsyncClient] = None):
        self.timeout = timeout
        self.breaker = breaker or CircuitBreaker(max_failures=5)
        self._client = client  # injectable for tests

    async def _do_request(self, params: list) -> dict:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._client is None
        try:
            resp = await client.get(FED_REG_URL, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableHTTPError(resp.status_code)
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns:
                await client.aclose()

    @retry(
        retry=retry_if_exception_type(RetryableHTTPError),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _request_with_retry(self, params: list) -> dict:
        return await self._do_request(params)

    async def search(self, term: str, doc_type: Optional[str] = None,
                     agency: Optional[str] = None,
                     per_page: int = 5) -> List[FederalRegisterDocument]:
        term = (term or "").strip()
        if not term:
            raise ValueError("term is required")
        type_code = normalize_doc_type(doc_type)  # may raise ValueError
        # httpx accepts a list of (key, value) tuples, which is how the repeated
        # bracketed array params (conditions[type][], fields[]) are encoded.
        params: list = [
            ("conditions[term]", term),
            ("per_page", str(clamp_per_page(per_page))),
            ("order", "newest"),
        ]
        if type_code:
            params.append(("conditions[type][]", type_code))
        if agency and str(agency).strip():
            params.append(("conditions[agencies][]", str(agency).strip()))
        params.extend(("fields[]", f) for f in _FIELDS)

        self.breaker.before()
        try:
            payload = await self._request_with_retry(params)
        except Exception:
            self.breaker.record_failure()
            raise
        self.breaker.record_success()
        return parse_documents(payload)
