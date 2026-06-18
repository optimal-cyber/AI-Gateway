# MCP wiring — compliance server ↔ LiteLLM

How the read-only compliance MCP server (Phase 3) plugs into the gateway, and how
a chat prompt reaches a tool.

## Topology

```
Open WebUI ──/v1/chat──▶ LiteLLM ──MCP (streamable HTTP)──▶ compliance-mcp
 (chat host)   (vkey)     (gateway host, same docker network)   :8000/mcp
                              │
                              └─ injects x-caller-role + x-mcp-auth
```

All three of LiteLLM, NeMo, and compliance-mcp run in the **gateway-host** compose
network; the MCP server is never published to a host port or the subnet — only
LiteLLM reaches it at `http://compliance-mcp:8000/mcp`.

## LiteLLM config

`docker/gateway-host/litellm-config.yaml`:

```yaml
mcp_servers:
  compliance:
    url: "http://compliance-mcp:8000/mcp"
    transport: "http"
```

The server name (`compliance`) is how clients address it (`/mcp/compliance` paths
in some clients). The MCP server mounts streamable HTTP at `/mcp` by default
(FastMCP `stateless_http=True, json_response=True`), which matches the URL above.

## Tools exposed (all read-only — ADR-005)

| Tool | Args | Source |
|---|---|---|
| `sam_gov_lookup` | `uei_or_cage`, `include_pii=false` | SAM.gov Entity API v3 (live) |
| `federal_register_search` | `term`, `doc_type?`, `agency?`, `per_page=5` | federalregister.gov API v1 (live, keyless) |
| `nist_control_lookup` | `control_id` | `data/nist_800_53_subset.json` (10 controls) |
| `poam_list` | `status_filter?` (enum) | `data/poams.db` (read-only SQLite) |
| `poam_summary` | — | `data/poams.db` |
| `cmmc_level2_self_assess_status` | — | `data/cmmc_l2_status.json` |

## Identity & the admin-gated PII path

LiteLLM forwards two headers the MCP server reads via an ASGI middleware into
contextvars:

- **`x-caller-role`** → drives `sam_gov_lookup`'s PII unmask. POC email/phone are
  returned only when `include_pii=true` **and** the caller's role is admin
  (`proxy_admin` / `lab-admins`). Otherwise `[REDACTED]` (threat T-MCP-I).
- **`x-mcp-auth`** → hashed (sha256, first 12 hex) into every audit log line as
  `caller_virtual_key_hash`. The raw value is never logged.

> The role is asserted by LiteLLM, not the end user, and the MCP server is only
> reachable inside the gateway docker network — a user cannot set `x-caller-role`
> themselves (T-MCP-S).

## Resilience & logging

- Live `.gov` calls (`sam_gov_lookup`, `federal_register_search`): `httpx` 10s
  timeout, `tenacity` exp-backoff retry on 429/5xx, a circuit breaker that opens
  after 5 consecutive failures (then returns "temporarily unavailable" instead of
  hammering the API). Both egress through the Squid allowlist (`.sam.gov`,
  `.federalregister.gov`); `federal_register_search` needs no API key.
- Every tool call emits one `structlog` JSON line to stdout (→ CloudWatch via the
  container log driver): `tool_name`, `status`, `caller_virtual_key_hash`,
  `caller_role`, `duration_ms`, `redacted_args`. Matched secret/PII values are
  pre-redacted before they could reach a log.

## Testing

Unit suite (no network, no MCP runtime needed):

```bash
cd mcp-server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest --cov=src --cov-report=term-missing -q
# 63 passed, 87% coverage (data_store 97%, models 100%, sam_client 95%, fedreg_client 90%)
```

End-to-end (Phase 5 test plan): from Open WebUI ask
"List current POA&Ms by severity" and "Look up Optimal, LLC by CAGE 14HQ0" —
verify the tool call in the LiteLLM request log and the MCP structlog line.

## Egress note

`compliance-mcp` reaches `api.sam.gov` through the Squid allowlist proxy
(HTTP(S)_PROXY set on the container; `.sam.gov` is allowlisted). It has no other
internet path (ADR-009).
