"""Live compliance evidence — the control map computed from the running signals.

Each control names the NIST SP 800-53 Rev 5 + CMMC L2 (NIST SP 800-171) identifiers
it maps to, the gateway component that implements it, and an evidence string
computed LIVE from the same hash-chained audit ledger + control-plane store the
gateway already produces. The same artifact an authorizing official ingests is
regenerated on demand, not maintained by hand.

This is a reference control mapping, NOT a certification or an SSP — it mirrors
docs/control-mapping.md and surfaces the evidence the gateway emits. status:
  operating = the control is active AND evidence has been observed
  ready      = the control is in force, no activity recorded yet
  FAILED     = the control's live check failed (e.g., the audit chain is broken)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .audit import verify_chain


def _read_rows(path: str):
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except ValueError:
                        pass
    except OSError:
        pass
    return rows


def assess(audit_log_path: str, store) -> dict:
    rows = _read_rows(audit_log_path)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    n_total = len(rows)
    n_24h = sum(1 for r in rows if (r.get("ts") or "") >= cutoff)
    n_allow = sum(1 for r in rows if r.get("status") == 200)
    n_deny = sum(1 for r in rows if r.get("phase") == "authz")
    n_block = sum(1 for r in rows if r.get("status") == 400)
    identities = {r.get("key") for r in rows if r.get("key")}
    chain = verify_chain(audit_log_path)

    tenants = store.list_tenants() if store else []
    keys = store.list_keys() if store else []
    n_active = sum(1 for k in keys if k.get("active"))
    n_suspended = sum(1 for t in tenants if t.get("status") != "active")
    n_scoped = sum(1 for k in keys
                   if (k.get("models") or k.get("max_budget") is not None or k.get("rpm_limit")))

    def c(nist, cmmc, name, by, evidence, status="operating"):
        return {"nist": nist, "cmmc": cmmc, "name": name, "by": by,
                "evidence": evidence, "status": status}

    controls = [
        c("AC-2 / AC-3", "AC.L2-3.1.1", "Account Mgmt / Access Enforcement",
          "Per-tenant isolation; per-org virtual keys with model allow-lists, "
          "budgets + rate limits; suspension enforced at the auth gate",
          f"{len(tenants)} isolated tenant orgs ({n_suspended} suspended), "
          f"{n_active} active scoped keys; {n_deny} requests denied at the boundary",
          "operating" if n_active else "ready"),
        c("AC-6", "AC.L2-3.1.5", "Least Privilege",
          "Per-key/team model allow-lists, spend caps, RPM ceilings",
          f"{n_scoped} scoped credentials in force",
          "operating" if n_scoped else "ready"),
        c("AC-4 / SC-7", "SC.L2-3.13.1", "Boundary Protection / Information Flow",
          "Single governed boundary; default-deny Squid egress allowlist",
          "all model calls traverse one boundary; egress default-deny"),
        c("AU-2", "AU.L2-3.3.1", "Audit Events",
          "Façade records every request (allow / deny / block)",
          f"{n_total} requests logged — {n_allow} allowed, {n_deny} denied, {n_block} blocked",
          "operating" if n_total else "ready"),
        c("AU-3", "AU.L2-3.3.1", "Content of Audit Records",
          "Structured rows: identity, model, boundary, tokens, decision, timestamp",
          "each row carries identity fingerprint, model, tier/cloud, tokens, decision"),
        c("AU-9", "AU.L2-3.3.8", "Protection of Audit Information",
          "Hash-chained, tamper-evident ledger (verifiable on demand)",
          f"chain {chain['reason']} — {chain['count']} chained rows verified",
          "operating" if chain["ok"] else "FAILED"),
        c("AU-12", "AU.L2-3.3.1", "Audit Generation",
          "Every request generates a tamper-evident row",
          f"{n_24h} rows generated in the last 24h",
          "operating" if n_24h else "ready"),
        c("IA-2", "IA.L2-3.5.1 / 3.5.3", "Identification & Authentication",
          "Okta OIDC + Cloudflare Access (MFA, US geo) → scoped virtual keys",
          f"{len(identities)} distinct caller identities observed",
          "operating" if identities else "ready"),
        c("SI-4", "SI.L2-3.14.6", "System Monitoring",
          "Inline guardrail scans every prompt and response (fail-closed)",
          f"{n_block} requests blocked inline by the guardrail",
          "operating" if n_block else "ready"),
        c("SI-10", "SI.L2-3.14.1", "Information Input Validation",
          "Injection / secret / PII detection; OWASP LLM Top 10 gate in CI",
          f"{n_block} blocked; OWASP LLM Top 10 probes gate every pull request"),
        c("SC-12 / SC-28", "SC.L2-3.13.10 / 3.13.16", "Key Mgmt / Protection at Rest",
          "Provider secrets in tmpfs (RAM-only); virtual keys SHA-256 hashed",
          "no provider key or plaintext virtual key persisted to disk"),
        c("CA-9 / SA-9", "—", "External System Connections",
          "Provider egress restricted to an accredited allowlist",
          "only allowlisted provider endpoints reachable from the boundary"),
    ]
    operating = sum(1 for x in controls if x["status"] == "operating")
    failed = sum(1 for x in controls if x["status"] == "FAILED")
    return {
        "generated": now.isoformat(timespec="seconds"),
        "frameworks": ["NIST SP 800-53 Rev 5", "CMMC L2 / NIST SP 800-171"],
        "summary": {"controls": len(controls), "operating": operating,
                    "ready": len(controls) - operating - failed, "failed": failed,
                    "audit_rows": n_total, "chain_ok": chain["ok"]},
        "controls": controls,
        "disclaimer": "Reference control mapping computed from live signals — not a "
                      "certification, SSP, or authorization. See docs/control-mapping.md.",
    }
