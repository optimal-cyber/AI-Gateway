"""Smoke tests for the server module. Tool logic is covered by the data_store /
sam_client / redaction suites; here we just confirm the module imports and the
admin-role + vkey-hash helpers behave."""

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed in this environment")

from src import server  # noqa: E402


def test_build_mcp():
    mcp = server.build_mcp()
    assert mcp is not None


def test_is_admin_default_false():
    assert server._is_admin() is False


def test_is_admin_true_for_proxy_admin():
    tok = server._caller_role.set("proxy_admin")
    try:
        assert server._is_admin() is True
    finally:
        server._caller_role.reset(tok)


def test_vkey_hash_anonymous():
    assert server._vkey_hash() == "anonymous"


def test_vkey_hash_stable():
    tok = server._caller_auth.set("sk-abc123")
    try:
        h = server._vkey_hash()
        assert h != "anonymous" and len(h) == 12
    finally:
        server._caller_auth.reset(tok)


# ---- tool logic helpers ----------------------------------------------------
import httpx  # noqa: E402

from src import data_store, fedreg_client, sam_client  # noqa: E402


async def test_do_nist():
    r = await server._do_nist_control_lookup("SC-7")
    assert r["found"] and r["control"]["id"] == "SC-7"


async def test_do_nist_unknown():
    r = await server._do_nist_control_lookup("ZZ-1")
    assert r["found"] is False and "available" in r


async def test_do_poam_list(monkeypatch, poam_db):
    monkeypatch.setattr(data_store, "POAM_DB", poam_db)
    r = await server._do_poam_list()
    assert r["count"] == 5


async def test_do_poam_list_bad_status(monkeypatch, poam_db):
    monkeypatch.setattr(data_store, "POAM_DB", poam_db)
    r = await server._do_poam_list(status_filter="bogus")
    assert "error" in r


async def test_do_poam_summary(monkeypatch, poam_db):
    monkeypatch.setattr(data_store, "POAM_DB", poam_db)
    r = await server._do_poam_summary()
    assert r["total"] == 5


async def test_do_cmmc():
    r = await server._do_cmmc_status()
    assert r["total_practices"] == 110


def _sam_with(payload):
    handler = lambda req: httpx.Response(200, json=payload)  # noqa: E731
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return sam_client.SAMClient(api_key="k", client=client)


async def test_do_sam_lookup_redacted_for_non_admin(monkeypatch, sam_payload):
    monkeypatch.setattr(server, "_sam", _sam_with(sam_payload))
    r = await server._do_sam_gov_lookup("ZQGGHJH74DW7", include_pii=True)  # not admin
    assert r["found"]
    assert r["entity"]["points_of_contact"][0]["email"] == "[REDACTED]"
    assert r["entity"]["pii_included"] is False


async def test_do_sam_lookup_unmasked_for_admin(monkeypatch, sam_payload):
    monkeypatch.setattr(server, "_sam", _sam_with(sam_payload))
    tok = server._caller_role.set("proxy_admin")
    try:
        r = await server._do_sam_gov_lookup("ZQGGHJH74DW7", include_pii=True)
    finally:
        server._caller_role.reset(tok)
    assert r["entity"]["points_of_contact"][0]["email"] == "poc@example.com"
    assert r["entity"]["pii_included"] is True


async def test_do_sam_lookup_invalid_id(monkeypatch, sam_payload):
    monkeypatch.setattr(server, "_sam", _sam_with(sam_payload))
    r = await server._do_sam_gov_lookup("nope")
    assert r["found"] is False and "error" in r


def _fedreg_with(payload):
    handler = lambda req: httpx.Response(200, json=payload)  # noqa: E731
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return fedreg_client.FederalRegisterClient(client=client)


async def test_do_fedreg_search_ok(monkeypatch, fedreg_payload):
    monkeypatch.setattr(server, "_fedreg", _fedreg_with(fedreg_payload))
    r = await server._do_federal_register_search("CMMC", doc_type="rule")
    assert r["count"] == 2
    assert r["documents"][0]["document_number"] == "2026-12345"
    assert r["documents"][0]["agencies"] == ["Defense Department"]


async def test_do_fedreg_search_empty_term(monkeypatch, fedreg_payload):
    monkeypatch.setattr(server, "_fedreg", _fedreg_with(fedreg_payload))
    r = await server._do_federal_register_search("   ")
    assert r["count"] == 0 and "error" in r


async def test_do_fedreg_search_bad_doc_type(monkeypatch, fedreg_payload):
    monkeypatch.setattr(server, "_fedreg", _fedreg_with(fedreg_payload))
    r = await server._do_federal_register_search("CMMC", doc_type="memo")
    assert r["count"] == 0 and "error" in r
