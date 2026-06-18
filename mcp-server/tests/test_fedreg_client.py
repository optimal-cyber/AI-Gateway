import httpx
import pytest

from src import fedreg_client as fc
from src import sam_client as sc


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- pure helpers ----------------------------------------------------------
class TestHelpers:
    def test_normalize_doc_type_friendly(self):
        assert fc.normalize_doc_type("rule") == "RULE"
        assert fc.normalize_doc_type("proposed") == "PRORULE"
        assert fc.normalize_doc_type("Notice") == "NOTICE"
        assert fc.normalize_doc_type("presidential") == "PRESDOCU"

    def test_normalize_doc_type_passthrough_code(self):
        assert fc.normalize_doc_type("PRORULE") == "PRORULE"

    def test_normalize_doc_type_empty_is_none(self):
        assert fc.normalize_doc_type(None) is None
        assert fc.normalize_doc_type("  ") is None

    def test_normalize_doc_type_invalid_raises(self):
        with pytest.raises(ValueError):
            fc.normalize_doc_type("memorandum")

    def test_clamp_per_page(self):
        assert fc.clamp_per_page(0) == 1
        assert fc.clamp_per_page(5) == 5
        assert fc.clamp_per_page(999) == fc.MAX_PER_PAGE
        assert fc.clamp_per_page("oops") == 5

    def test_parse_documents_defensive(self, fedreg_payload):
        docs = fc.parse_documents(fedreg_payload)
        assert len(docs) == 2
        assert docs[0].document_number == "2026-12345"
        assert docs[0].type == "Rule"
        assert docs[0].agencies == ["Defense Department"]
        # second doc has null abstract/pdf — degrade to None, don't raise
        assert docs[1].abstract is None and docs[1].pdf_url is None

    def test_parse_documents_empty(self):
        assert fc.parse_documents({}) == []
        assert fc.parse_documents({"results": None}) == []


# ---- search integration (MockTransport, no network) ------------------------
class TestSearch:
    @pytest.mark.asyncio
    async def test_success_and_query_shape(self, fedreg_payload):
        seen = {}

        def handler(request):
            seen["params"] = request.url.params
            return httpx.Response(200, json=fedreg_payload)

        client = fc.FederalRegisterClient(client=_client(handler))
        docs = await client.search("CMMC", doc_type="rule",
                                   agency="defense-department", per_page=5)
        assert len(docs) == 2
        assert docs[0].title.startswith("Cybersecurity Maturity Model")
        # the bracketed array params reached the wire
        assert seen["params"].get("conditions[term]") == "CMMC"
        assert seen["params"].get("conditions[type][]") == "RULE"
        assert seen["params"].get("conditions[agencies][]") == "defense-department"
        assert client.breaker.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_per_page_clamped_on_wire(self, fedreg_payload):
        seen = {}

        def handler(request):
            seen["pp"] = request.url.params.get("per_page")
            return httpx.Response(200, json=fedreg_payload)

        client = fc.FederalRegisterClient(client=_client(handler))
        await client.search("term", per_page=999)
        assert seen["pp"] == str(fc.MAX_PER_PAGE)

    @pytest.mark.asyncio
    async def test_retry_then_success(self, fedreg_payload):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, json={"error": "busy"})
            return httpx.Response(200, json=fedreg_payload)

        client = fc.FederalRegisterClient(client=_client(handler))
        docs = await client.search("CMMC")
        assert docs and calls["n"] == 2  # retried once on 5xx

    @pytest.mark.asyncio
    async def test_breaker_open_short_circuits(self):
        def handler(request):  # must not be called
            raise AssertionError("transport hit while breaker open")

        b = sc.CircuitBreaker(max_failures=1)
        b.open = True
        client = fc.FederalRegisterClient(breaker=b, client=_client(handler))
        with pytest.raises(fc.CircuitOpenError):
            await client.search("CMMC")

    @pytest.mark.asyncio
    async def test_empty_term_raises(self):
        client = fc.FederalRegisterClient(
            client=_client(lambda r: httpx.Response(200, json={})))
        with pytest.raises(ValueError):
            await client.search("   ")

    @pytest.mark.asyncio
    async def test_bad_doc_type_raises(self):
        client = fc.FederalRegisterClient(
            client=_client(lambda r: httpx.Response(200, json={})))
        with pytest.raises(ValueError):
            await client.search("CMMC", doc_type="memo")
