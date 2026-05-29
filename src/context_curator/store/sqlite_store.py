"""SQLite-backed Store (DESIGN.md §4.1, §5). Embedded, no daemon. Tenant scope
is enforced in SQL; tag filtering happens in Python (adequate at single-machine
scale). Embeddings serialize as JSON text (cosine ranking is M3)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from context_curator.embeddings import Embedder
from context_curator.keys import is_within_scope
from context_curator.models import Chunk, utcnow_iso
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens

_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    key              TEXT PRIMARY KEY,
    content          TEXT NOT NULL,
    tags             TEXT NOT NULL,          -- JSON array
    source           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_onloaded_at TEXT,
    pin              INTEGER NOT NULL,       -- 0/1
    ttl_s            INTEGER,                -- nullable
    provenance       TEXT,
    embedding        TEXT,                   -- JSON array
    expires_at       TEXT                    -- precomputed, nullable; NULL when pinned or ttl_s NULL
);
"""


def _compute_expires_at(created_at: str, ttl_s: int | None, pin: bool) -> str | None:
    if pin or ttl_s is None:
        return None
    base = datetime.fromisoformat(created_at)
    return (base + timedelta(seconds=ttl_s)).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SqliteStore(Store):
    def __init__(self, db_path: str, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_DDL)
        self._conn.commit()
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix

    # --- helpers -----------------------------------------------------------
    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            key=row["key"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            source=row["source"],
            created_at=row["created_at"],
            last_onloaded_at=row["last_onloaded_at"],
            pin=bool(row["pin"]),
            ttl_s=row["ttl_s"],
            provenance=row["provenance"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
        )

    def _is_expired(self, row: sqlite3.Row) -> bool:
        if row["expires_at"] is None:
            return False
        return datetime.fromisoformat(row["expires_at"]) <= _now()

    # --- interface ---------------------------------------------------------
    def store(self, key: str, content: str, tags: list[str] | None = None,
              ttl_s: int | None = 86400, pin: bool = False,
              source: str = "tool:read", provenance: str | None = None) -> str:
        created_at = utcnow_iso()
        chunk = Chunk(
            key=key, content=content, tags=list(tags or []), ttl_s=ttl_s, pin=pin,
            source=source, provenance=provenance, created_at=created_at,
            embedding=self._embedder.embed(content),
        )
        self._conn.execute(
            """INSERT INTO chunks
               (key, content, tags, source, created_at, last_onloaded_at, pin,
                ttl_s, provenance, embedding, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 content=excluded.content, tags=excluded.tags, source=excluded.source,
                 created_at=excluded.created_at, pin=excluded.pin, ttl_s=excluded.ttl_s,
                 provenance=excluded.provenance, embedding=excluded.embedding,
                 expires_at=excluded.expires_at""",
            (
                key, content, json.dumps(chunk.tags), source, created_at, None,
                1 if pin else 0, ttl_s, provenance, json.dumps(chunk.embedding),
                _compute_expires_at(created_at, ttl_s, pin),
            ),
        )
        self._conn.commit()
        return key

    def retrieve(self, key: str) -> Chunk | None:
        if not is_within_scope(key, self._allowed_prefix):
            return None
        row = self._conn.execute("SELECT * FROM chunks WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        if self._is_expired(row):
            self.evict(key)  # lazy delete
            return None
        return self._row_to_chunk(row)

    def query(self, task_context: str, tags: list[str] | None = None,
              k: int = 10, token_budget: int | None = None) -> list[Chunk]:
        # tenant scope enforced in SQL
        if self._allowed_prefix is None:
            rows = self._conn.execute(
                "SELECT * FROM chunks ORDER BY created_at DESC"
            ).fetchall()
        else:
            p = self._allowed_prefix
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE key = ? OR key LIKE ? ORDER BY created_at DESC",
                (p, p + ":%"),
            ).fetchall()
        out: list[Chunk] = []
        used = 0
        for row in rows:
            if self._is_expired(row):
                continue
            c = self._row_to_chunk(row)
            if tags is not None and not set(tags).issubset(set(c.tags)):
                continue
            if token_budget is not None:
                t = estimate_tokens(c.content)
                if used + t > token_budget:
                    break
                used += t
            out.append(c)
            if len(out) >= k:
                break
        return out

    def list(self, prefix: str) -> list[str]:
        # boundary-aware: `prefix + ":%"` so `shared:contracts` never matches `shared:contractsX`
        rows = self._conn.execute(
            "SELECT key FROM chunks WHERE key = ? OR key LIKE ?", (prefix, prefix + ":%")
        ).fetchall()
        return [r["key"] for r in rows if is_within_scope(r["key"], self._allowed_prefix)]

    def evict(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM chunks WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    def pin(self, key: str) -> bool:
        cur = self._conn.execute(
            "UPDATE chunks SET pin = 1, expires_at = NULL WHERE key = ?", (key,)
        )
        self._conn.commit()
        return cur.rowcount > 0
