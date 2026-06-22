"""Control-plane persistence (Phase 2) — teams, keys, spend.

SQLite, stdlib-only (mirrors how mcp-server uses sqlite for poams.db). This is
the store that lets the façade become the source of truth for virtual keys and
budgets, so LiteLLM no longer owns them. For multi-instance deployment, swap
this class for a Postgres-backed one (the existing stack already runs Postgres);
the rest of the façade calls only this interface.

Keys are stored as a SHA-256 HASH, never plaintext — the plaintext is returned
exactly once at creation (Stripe/LiteLLM style) and is unrecoverable after.

Data model mirrors scripts/provision-org.sh semantics: org == team, tier
dev|gov (ADR-014), per-team + per-key budgets, model allow-lists, and the
ADR-018 gov approval gate (approved_by) enforced in the admin layer.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id              TEXT PRIMARY KEY,
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
    team_id     TEXT REFERENCES teams(id),
    models_json TEXT NOT NULL DEFAULT '[]',
    max_budget  REAL,
    spend       REAL NOT NULL DEFAULT 0,
    expires_at  TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend_log (
    request_id        TEXT,
    key_id            TEXT,
    team_id           TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost              REAL,
    ts                TEXT
);
CREATE INDEX IF NOT EXISTS ix_keys_team ON keys(team_id);
CREATE INDEX IF NOT EXISTS ix_spend_team ON spend_log(team_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


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

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- teams -------------------------------------------------------------
    def create_team(self, *, alias: str, tier: str = "dev",
                    max_budget: Optional[float] = None,
                    soft_budget: Optional[float] = None,
                    budget_duration: Optional[str] = None,
                    models: Optional[List[str]] = None,
                    approved_by: Optional[str] = None) -> Dict[str, Any]:
        tid = "team_" + secrets.token_hex(8)
        with self._lock:
            self._db.execute(
                "INSERT INTO teams(id,alias,tier,max_budget,soft_budget,"
                "budget_duration,models_json,approved_by,spend,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,0,?)",
                (tid, alias, tier, max_budget, soft_budget, budget_duration,
                 json.dumps(models or []), approved_by, _now()))
            self._db.commit()
        return self.get_team(tid)  # type: ignore[return-value]

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        return _team_row(row) if row else None

    def list_teams(self) -> List[Dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM teams ORDER BY created_at").fetchall()
        return [_team_row(r) for r in rows]

    # -- keys --------------------------------------------------------------
    def create_key(self, *, team_id: Optional[str] = None, alias: Optional[str] = None,
                   models: Optional[List[str]] = None,
                   max_budget: Optional[float] = None,
                   expires_at: Optional[str] = None) -> Dict[str, Any]:
        return self._insert_key("sk-" + secrets.token_urlsafe(32), team_id=team_id,
                                alias=alias, models=models, max_budget=max_budget,
                                expires_at=expires_at)

    def create_key_with_plaintext(self, plaintext: str, *, team_id: Optional[str] = None,
                                  alias: Optional[str] = None,
                                  models: Optional[List[str]] = None,
                                  max_budget: Optional[float] = None,
                                  expires_at: Optional[str] = None) -> Dict[str, Any]:
        """Idempotently store a key with a KNOWN plaintext (bootstrap path). If a
        key with this hash already exists, return it unchanged (re-running boot is
        safe). The plaintext is the caller's to keep — we still only store its hash."""
        existing = self.get_key_by_plaintext(plaintext)
        if existing is not None:
            existing["key"] = plaintext
            return existing
        return self._insert_key(plaintext, team_id=team_id, alias=alias,
                                models=models, max_budget=max_budget, expires_at=expires_at)

    def _insert_key(self, plaintext: str, *, team_id, alias, models,
                    max_budget, expires_at) -> Dict[str, Any]:
        kid = "key_" + secrets.token_hex(8)
        with self._lock:
            self._db.execute(
                "INSERT INTO keys(id,key_hash,alias,team_id,models_json,"
                "max_budget,spend,expires_at,active,created_at) "
                "VALUES(?,?,?,?,?,?,0,?,1,?)",
                (kid, hash_key(plaintext), alias, team_id,
                 json.dumps(models or []), max_budget, expires_at, _now()))
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

    def list_keys(self, team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if team_id:
            rows = self._db.execute(
                "SELECT * FROM keys WHERE team_id=? ORDER BY created_at", (team_id,)).fetchall()
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
                     prompt_tokens: int, completion_tokens: int, cost: float) -> None:
        with self._lock:
            if key_id:
                self._db.execute("UPDATE keys SET spend=spend+? WHERE id=?", (cost, key_id))
            if team_id:
                self._db.execute("UPDATE teams SET spend=spend+? WHERE id=?", (cost, team_id))
            self._db.execute(
                "INSERT INTO spend_log(request_id,key_id,team_id,model,"
                "prompt_tokens,completion_tokens,cost,ts) VALUES(?,?,?,?,?,?,?,?)",
                (request_id, key_id, team_id, model, prompt_tokens,
                 completion_tokens, cost, _now()))
            self._db.commit()

    def spend_summary(self) -> Dict[str, Any]:
        total = self._db.execute(
            "SELECT COALESCE(SUM(cost),0) c, COUNT(*) n FROM spend_log").fetchone()
        by_team = self._db.execute(
            "SELECT team_id, COALESCE(SUM(cost),0) c, COUNT(*) n "
            "FROM spend_log GROUP BY team_id").fetchall()
        return {
            "total_cost": round(total["c"], 6),
            "total_requests": total["n"],
            "by_team": [{"team_id": r["team_id"], "cost": round(r["c"], 6),
                         "requests": r["n"]} for r in by_team],
        }


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
