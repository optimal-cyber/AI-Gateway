"""Control-plane persistence (Phase 2) — tenants, teams, keys, spend.

SQLite, stdlib-only (mirrors how mcp-server uses sqlite for poams.db). This is
the store that lets the façade become the source of truth for virtual keys and
budgets, so LiteLLM no longer owns them. For multi-instance deployment, swap
this class for a Postgres-backed one (the existing stack already runs Postgres);
the rest of the façade calls only this interface.

Keys are stored as a SHA-256 HASH, never plaintext — the plaintext is returned
exactly once at creation (Stripe/LiteLLM style) and is unrecoverable after.

Data model — the TENANT is the first-class customer isolation boundary:
    tenant (the approved organization)
      └── team(s)        one or more, tier dev|gov (ADR-014)
            └── key(s)   scoped, budgeted virtual credentials
Every team/key/spend row carries its tenant_id, so reads scope cleanly per
customer and the audit/usage evidence segregates by tenant. A tenant has a
lifecycle status (active|suspended) enforced at the auth gate (control.authorize),
so suspension is a real lever, not a flag. The gov approval gate (approved_by,
ADR-018) is enforced in the admin layer. Legacy teams created before the tenant
spine are backfilled a tenant on first open (idempotent).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Tenant table + the tenant_id columns/indexes are created here for FRESH dbs.
# For dbs created before the tenant spine, _migrate() ALTERs in the columns and
# creates the tenant indexes (which is why those indexes are NOT in this block —
# they would reference a column that doesn't exist yet on an old db).
SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'active',
    tier          TEXT NOT NULL DEFAULT 'dev',
    plan          TEXT,
    contact_email TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT REFERENCES tenants(id),
    alias           TEXT NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'dev',
    max_budget      REAL,
    soft_budget     REAL,
    budget_duration TEXT,
    models_json     TEXT NOT NULL DEFAULT '[]',
    approved_by     TEXT,
    spend           REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keys (
    id          TEXT PRIMARY KEY,
    key_hash    TEXT NOT NULL UNIQUE,
    alias       TEXT,
    tenant_id   TEXT REFERENCES tenants(id),
    team_id     TEXT REFERENCES teams(id),
    models_json TEXT NOT NULL DEFAULT '[]',
    max_budget  REAL,
    rpm_limit   INTEGER,
    spend       REAL NOT NULL DEFAULT 0,
    expires_at  TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend_log (
    request_id        TEXT,
    tenant_id         TEXT,
    key_id            TEXT,
    team_id           TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost              REAL,
    ts                TEXT
);
CREATE TABLE IF NOT EXISTS access_requests (
    id          TEXT PRIMARY KEY,
    org         TEXT NOT NULL,
    email       TEXT,
    use_case    TEXT,
    tier        TEXT NOT NULL DEFAULT 'dev',
    boundary    TEXT,
    max_budget  REAL,
    rpm_limit   INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending',
    decided_by  TEXT,
    decided_at  TEXT,
    tenant_id   TEXT,
    team_id     TEXT,
    key_id      TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_keys_team ON keys(team_id);
CREATE INDEX IF NOT EXISTS ix_spend_team ON spend_log(team_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "tenant"


class Store:
    def __init__(self, db_path: str) -> None:
        # ":memory:" supported for tests. file:...?mode... not needed here.
        if db_path != ":memory:":
            import os
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns/indexes introduced after a db was first created, then
        backfill tenants for legacy teams. Idempotent — safe to run every open."""
        with self._lock:
            kcols = {r["name"] for r in self._db.execute("PRAGMA table_info(keys)").fetchall()}
            if "rpm_limit" not in kcols:
                self._db.execute("ALTER TABLE keys ADD COLUMN rpm_limit INTEGER")
            # Tenant spine: thread tenant_id through the rows that hang off a tenant.
            for table in ("teams", "keys", "spend_log", "access_requests"):
                cols = {r["name"] for r in
                        self._db.execute(f"PRAGMA table_info({table})").fetchall()}
                if "tenant_id" not in cols:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT")
            # Indexes that reference tenant_id — created here (after the column is
            # guaranteed to exist) rather than in SCHEMA, which runs before this.
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_teams_tenant ON teams(tenant_id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_keys_tenant ON keys(tenant_id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_spend_tenant ON spend_log(tenant_id)")
            self._db.commit()
        self._backfill_tenants()

    def _backfill_tenants(self) -> None:
        """Give every pre-spine team (tenant_id IS NULL) its own tenant and stamp
        that tenant_id onto the team's keys/spend/requests. Pre-spine: org == team,
        so one team -> one tenant preserves the old 1:1 mapping without data loss."""
        with self._lock:
            orphans = self._db.execute(
                "SELECT id, alias, tier FROM teams WHERE tenant_id IS NULL").fetchall()
            for t in orphans:
                tid = "tenant_" + secrets.token_hex(8)
                slug = self._unique_slug_locked(t["alias"])
                self._db.execute(
                    "INSERT INTO tenants(id,name,slug,status,tier,created_at) "
                    "VALUES(?,?,?,'active',?,?)",
                    (tid, t["alias"], slug, t["tier"] or "dev", _now()))
                self._db.execute("UPDATE teams SET tenant_id=? WHERE id=?", (tid, t["id"]))
                for tbl in ("keys", "spend_log", "access_requests"):
                    self._db.execute(
                        f"UPDATE {tbl} SET tenant_id=? WHERE team_id=? AND tenant_id IS NULL",
                        (tid, t["id"]))
            self._db.commit()

    def _unique_slug_locked(self, name: str) -> str:
        """Return a slug not already taken in tenants. Caller must hold _lock."""
        base = _slugify(name)
        slug, i = base, 2
        while self._db.execute("SELECT 1 FROM tenants WHERE slug=?", (slug,)).fetchone():
            slug, i = f"{base}-{i}", i + 1
        return slug

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- tenants (the customer isolation boundary) -------------------------
    def create_tenant(self, *, name: str, slug: Optional[str] = None,
                      tier: str = "dev", plan: Optional[str] = None,
                      contact_email: Optional[str] = None,
                      status: str = "active") -> Dict[str, Any]:
        tid = "tenant_" + secrets.token_hex(8)
        with self._lock:
            s = self._unique_slug_locked(slug or name)
            self._db.execute(
                "INSERT INTO tenants(id,name,slug,status,tier,plan,contact_email,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (tid, name, s, status, tier, plan, contact_email, _now()))
            self._db.commit()
        return self.get_tenant(tid)  # type: ignore[return-value]

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        return dict(row) if row else None

    def get_tenant_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM tenants WHERE slug=?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_tenants(self) -> List[Dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def set_tenant_status(self, tenant_id: str, status: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tenants SET status=? WHERE id=?", (status, tenant_id))
            self._db.commit()
            return cur.rowcount > 0

    def set_tenant_plan(self, tenant_id: str, plan: Optional[str]) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tenants SET plan=? WHERE id=?", (plan, tenant_id))
            self._db.commit()
            return cur.rowcount > 0

    # -- teams -------------------------------------------------------------
    def create_team(self, *, alias: str, tier: str = "dev",
                    max_budget: Optional[float] = None,
                    soft_budget: Optional[float] = None,
                    budget_duration: Optional[str] = None,
                    models: Optional[List[str]] = None,
                    approved_by: Optional[str] = None,
                    tenant_id: Optional[str] = None) -> Dict[str, Any]:
        # A team always belongs to a tenant. If the caller didn't supply one,
        # auto-create it from the alias (preserves the simple org == tenant == team
        # path while keeping the tenant a first-class, isolatable entity).
        if tenant_id is None:
            tenant_id = self.create_tenant(name=alias, tier=tier)["id"]
        tid = "team_" + secrets.token_hex(8)
        with self._lock:
            self._db.execute(
                "INSERT INTO teams(id,tenant_id,alias,tier,max_budget,soft_budget,"
                "budget_duration,models_json,approved_by,spend,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                (tid, tenant_id, alias, tier, max_budget, soft_budget, budget_duration,
                 json.dumps(models or []), approved_by, _now()))
            self._db.commit()
        return self.get_team(tid)  # type: ignore[return-value]

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        return _team_row(row) if row else None

    def list_teams(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if tenant_id:
            rows = self._db.execute(
                "SELECT * FROM teams WHERE tenant_id=? ORDER BY created_at",
                (tenant_id,)).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM teams ORDER BY created_at").fetchall()
        return [_team_row(r) for r in rows]

    # -- keys --------------------------------------------------------------
    def create_key(self, *, team_id: Optional[str] = None, alias: Optional[str] = None,
                   models: Optional[List[str]] = None,
                   max_budget: Optional[float] = None,
                   expires_at: Optional[str] = None,
                   rpm_limit: Optional[int] = None,
                   tenant_id: Optional[str] = None) -> Dict[str, Any]:
        return self._insert_key("sk-" + secrets.token_urlsafe(32), team_id=team_id,
                                alias=alias, models=models, max_budget=max_budget,
                                expires_at=expires_at, rpm_limit=rpm_limit,
                                tenant_id=tenant_id)

    def create_key_with_plaintext(self, plaintext: str, *, team_id: Optional[str] = None,
                                  alias: Optional[str] = None,
                                  models: Optional[List[str]] = None,
                                  max_budget: Optional[float] = None,
                                  expires_at: Optional[str] = None,
                                  tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Idempotently store a key with a KNOWN plaintext (bootstrap path). If a
        key with this hash already exists, return it unchanged (re-running boot is
        safe). The plaintext is the caller's to keep — we still only store its hash."""
        existing = self.get_key_by_plaintext(plaintext)
        if existing is not None:
            existing["key"] = plaintext
            return existing
        return self._insert_key(plaintext, team_id=team_id, alias=alias, models=models,
                                max_budget=max_budget, expires_at=expires_at,
                                tenant_id=tenant_id)

    def _insert_key(self, plaintext: str, *, team_id, alias, models,
                    max_budget, expires_at, rpm_limit=None, tenant_id=None) -> Dict[str, Any]:
        # A key inherits its team's tenant unless one is given explicitly, so every
        # key carries tenant_id directly — the auth gate resolves the tenant in one
        # lookup, and tenant-scoped reads need no join.
        if tenant_id is None and team_id is not None:
            team = self.get_team(team_id)
            tenant_id = team["tenant_id"] if team else None
        kid = "key_" + secrets.token_hex(8)
        with self._lock:
            self._db.execute(
                "INSERT INTO keys(id,key_hash,alias,tenant_id,team_id,models_json,"
                "max_budget,rpm_limit,spend,expires_at,active,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,0,?,1,?)",
                (kid, hash_key(plaintext), alias, tenant_id, team_id,
                 json.dumps(models or []), max_budget, rpm_limit, expires_at, _now()))
            self._db.commit()
        out = self.get_key(kid)
        assert out is not None
        out["key"] = plaintext  # returned ONCE, never stored
        return out

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
        return _key_row(row) if row else None

    def get_key_by_plaintext(self, plaintext: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM keys WHERE key_hash=?", (hash_key(plaintext),)).fetchone()
        return _key_row(row) if row else None

    def list_keys(self, team_id: Optional[str] = None,
                  tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if tenant_id:
            rows = self._db.execute(
                "SELECT * FROM keys WHERE tenant_id=? ORDER BY created_at",
                (tenant_id,)).fetchall()
        elif team_id:
            rows = self._db.execute(
                "SELECT * FROM keys WHERE team_id=? ORDER BY created_at",
                (team_id,)).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM keys ORDER BY created_at").fetchall()
        return [_key_row(r) for r in rows]

    def revoke_key(self, key_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE keys SET active=0 WHERE id=?", (key_id,))
            self._db.commit()
            return cur.rowcount > 0

    # -- spend -------------------------------------------------------------
    def record_spend(self, *, request_id: Optional[str], key_id: Optional[str],
                     team_id: Optional[str], model: Optional[str],
                     prompt_tokens: int, completion_tokens: int, cost: float,
                     tenant_id: Optional[str] = None) -> None:
        with self._lock:
            if key_id:
                self._db.execute("UPDATE keys SET spend=spend+? WHERE id=?", (cost, key_id))
            if team_id:
                self._db.execute("UPDATE teams SET spend=spend+? WHERE id=?", (cost, team_id))
            self._db.execute(
                "INSERT INTO spend_log(request_id,tenant_id,key_id,team_id,model,"
                "prompt_tokens,completion_tokens,cost,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (request_id, tenant_id, key_id, team_id, model, prompt_tokens,
                 completion_tokens, cost, _now()))
            self._db.commit()

    def spend_summary(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        if tenant_id:
            t = self._db.execute(
                "SELECT COALESCE(SUM(cost),0) c, COUNT(*) n FROM spend_log WHERE tenant_id=?",
                (tenant_id,)).fetchone()
            return {"tenant_id": tenant_id, "total_cost": round(t["c"], 6),
                    "total_requests": t["n"]}
        total = self._db.execute(
            "SELECT COALESCE(SUM(cost),0) c, COUNT(*) n FROM spend_log").fetchone()
        by_team = self._db.execute(
            "SELECT team_id, COALESCE(SUM(cost),0) c, COUNT(*) n "
            "FROM spend_log GROUP BY team_id").fetchall()
        by_tenant = self._db.execute(
            "SELECT tenant_id, COALESCE(SUM(cost),0) c, COUNT(*) n "
            "FROM spend_log GROUP BY tenant_id").fetchall()
        return {
            "total_cost": round(total["c"], 6),
            "total_requests": total["n"],
            "by_team": [{"team_id": r["team_id"], "cost": round(r["c"], 6),
                         "requests": r["n"]} for r in by_team],
            "by_tenant": [{"tenant_id": r["tenant_id"], "cost": round(r["c"], 6),
                           "requests": r["n"]} for r in by_tenant],
        }

    def tenant_usage(self, tenant_id: str) -> Dict[str, Any]:
        """Per-tenant metered usage — the read the billing layer aggregates from
        and the tenant-facing 'my usage' view renders."""
        row = self._db.execute(
            "SELECT COALESCE(SUM(cost),0) c, COUNT(*) n, "
            "COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct "
            "FROM spend_log WHERE tenant_id=?", (tenant_id,)).fetchone()
        by_model = self._db.execute(
            "SELECT model, COALESCE(SUM(cost),0) c, COUNT(*) n, "
            "COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct "
            "FROM spend_log WHERE tenant_id=? GROUP BY model ORDER BY c DESC",
            (tenant_id,)).fetchall()
        return {
            "tenant_id": tenant_id,
            "total_cost": round(row["c"], 6),
            "total_requests": row["n"],
            "prompt_tokens": row["pt"],
            "completion_tokens": row["ct"],
            "by_model": [{"model": r["model"], "cost": round(r["c"], 6),
                          "requests": r["n"], "prompt_tokens": r["pt"],
                          "completion_tokens": r["ct"]} for r in by_model],
        }

    # -- access requests (onboarding: request -> approve -> provision) -----
    def create_request(self, *, org: str, email: Optional[str] = None,
                       use_case: Optional[str] = None, tier: str = "dev",
                       boundary: Optional[str] = None,
                       max_budget: Optional[float] = None,
                       rpm_limit: Optional[int] = None) -> Dict[str, Any]:
        rid = "req_" + secrets.token_hex(8)
        with self._lock:
            self._db.execute(
                "INSERT INTO access_requests(id,org,email,use_case,tier,boundary,"
                "max_budget,rpm_limit,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,'pending',?)",
                (rid, org, email, use_case, tier, boundary, max_budget, rpm_limit, _now()))
            self._db.commit()
        return self.get_request(rid)  # type: ignore[return-value]

    def get_request(self, rid: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def list_requests(self) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM access_requests ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def mark_request(self, rid: str, *, status: str, decided_by: Optional[str],
                     tenant_id: Optional[str] = None, team_id: Optional[str] = None,
                     key_id: Optional[str] = None) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE access_requests SET status=?,decided_by=?,decided_at=?,"
                "tenant_id=?,team_id=?,key_id=? WHERE id=?",
                (status, decided_by, _now(), tenant_id, team_id, key_id, rid))
            self._db.commit()
            return cur.rowcount > 0


def _team_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["models"] = json.loads(d.pop("models_json") or "[]")
    return d


def _key_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["models"] = json.loads(d.pop("models_json") or "[]")
    d["active"] = bool(d["active"])
    d.pop("key_hash", None)  # never expose the hash
    return d
