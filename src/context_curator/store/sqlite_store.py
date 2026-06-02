"""SQLite-backed Store (DESIGN.md §4.1, §5). Embedded, no daemon. Tenant scope
is enforced in SQL; tag filtering happens in Python (adequate at single-machine
scale). Embeddings serialize as JSON text (cosine ranking is M3)."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from context_curator.embeddings import Embedder
from context_curator.keys import is_within_scope
from context_curator.models import Chunk, utcnow_iso
from context_curator.store.expiry import is_expired
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
    expires_at       TEXT,                   -- precomputed, nullable; NULL when pinned/ttl NULL
    seq              INTEGER NOT NULL        -- monotonic write order; recency ranks on this
);
CREATE TABLE IF NOT EXISTS cc_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _compute_expires_at(created_at: str, ttl_s: int | None, pin: bool) -> str | None:
    if pin or ttl_s is None:
        return None
    base = datetime.fromisoformat(created_at)
    return (base + timedelta(seconds=ttl_s)).isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


class SqliteStore(Store):
    def __init__(self, db_path: str, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        parent = Path(db_path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None (autocommit): store() uses an explicit, version-proof
        # BEGIN IMMEDIATE for race-free seq; busy_timeout handles writer contention; WAL
        # allows concurrent readers. check_same_thread=False for the FastMCP thread case.
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_DDL)      # multi-statement DDL needs executescript
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix

    def close(self) -> None:
        self._conn.close()

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
        return is_expired(row["created_at"], row["ttl_s"], bool(row["pin"]))

    # --- interface ---------------------------------------------------------
    def store(self, key: str, content: str, tags: list[str] | None = None,
              ttl_s: int | None = 86400, pin: bool = False,
              source: str = "tool:read", provenance: str | None = None) -> str:
        created_at = utcnow_iso()
        # Embed BEFORE the transaction so the write lock is not held during embedding
        # (round-3 fix; matters once the M3 embedder replaces the cheap HashingEmbedder).
        embedding = self._embedder.embed(content)
        chunk = Chunk(
            key=key, content=content, tags=list(tags or []), ttl_s=ttl_s, pin=pin,
            source=source, provenance=provenance, created_at=created_at, embedding=embedding,
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                """INSERT INTO chunks
                   (key, content, tags, source, created_at, last_onloaded_at, pin,
                    ttl_s, provenance, embedding, expires_at, seq)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,
                           (SELECT COALESCE(MAX(seq), 0) + 1 FROM chunks))
                   ON CONFLICT(key) DO UPDATE SET
                     content=excluded.content, tags=excluded.tags, source=excluded.source,
                     created_at=excluded.created_at, pin=excluded.pin, ttl_s=excluded.ttl_s,
                     provenance=excluded.provenance, embedding=excluded.embedding,
                     expires_at=excluded.expires_at, seq=excluded.seq""",
                (
                    key, content, json.dumps(chunk.tags), source, created_at, None,
                    1 if pin else 0, ttl_s, provenance, json.dumps(chunk.embedding),
                    _compute_expires_at(created_at, ttl_s, pin),
                ),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.rollback()   # no-op if no txn; frees the write lock so the
            raise                   # long-lived MCP server connection isn't wedged
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
                "SELECT * FROM chunks ORDER BY seq DESC"
            ).fetchall()
        else:
            p = self._allowed_prefix
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE key = ? OR key LIKE ? ORDER BY seq DESC",
                (p, p + ":%"),
            ).fetchall()
        out: list[Chunk] = []
        used = 0
        for row in rows:
            if self._is_expired(row):
                continue
            # Defence-in-depth: SQL LIKE treats `_`/`%` as wildcards, so a prefix
            # containing them can over-match. Re-check scope in Python (as list/retrieve do).
            if not is_within_scope(row["key"], self._allowed_prefix):
                continue
            c = self._row_to_chunk(row)
            if tags is not None and not set(tags).issubset(set(c.tags)):
                continue
            if token_budget is not None:
                # first-fit: stop at the first chunk that would exceed the budget (intentional;
                # M3 may revisit). Recency order means newest-first wins the budget.
                t = estimate_tokens(c.content)
                if used + t > token_budget:
                    break
                used += t
            out.append(c)
            if len(out) >= k:
                break
        return out

    def all_live_chunks(self) -> list[Chunk]:
        if self._allowed_prefix is None:
            rows = self._conn.execute("SELECT * FROM chunks ORDER BY seq DESC").fetchall()
        else:
            p = self._allowed_prefix
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE key = ? OR key LIKE ? ORDER BY seq DESC",
                (p, p + ":%"),
            ).fetchall()
        out: list[Chunk] = []
        for row in rows:
            if self._is_expired(row):
                continue
            # defence-in-depth: SQL LIKE treats `_`/`%` as wildcards (same guard as query)
            if not is_within_scope(row["key"], self._allowed_prefix):
                continue
            out.append(self._row_to_chunk(row))
        return out

    def list(self, prefix: str) -> list[str]:
        # boundary-aware: `prefix + ":%"` so `shared:contracts` never matches `shared:contractsX`
        rows = self._conn.execute(
            "SELECT key FROM chunks WHERE key = ? OR key LIKE ?", (prefix, prefix + ":%")
        ).fetchall()
        return [r["key"] for r in rows if is_within_scope(r["key"], self._allowed_prefix)]

    def evict(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM chunks WHERE key = ?", (key,))
        return cur.rowcount > 0

    def pin(self, key: str) -> bool:
        cur = self._conn.execute(
            "UPDATE chunks SET pin = 1, expires_at = NULL WHERE key = ?", (key,)
        )
        return cur.rowcount > 0


SWEEP_INTERVAL_S = 300


def sweep_expired(store: SqliteStore) -> int:
    """Rate-limited deletion of expired (non-pinned) chunks. At most one sweep per
    SWEEP_INTERVAL_S across processes (gated by cc_meta.last_sweep). Returns rows deleted."""
    conn = store._conn
    now = _now()
    row = conn.execute("SELECT value FROM cc_meta WHERE key = 'last_sweep'").fetchone()
    if row is not None:
        last = datetime.fromisoformat(row["value"])
        if (now - last).total_seconds() < SWEEP_INTERVAL_S:
            return 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "DELETE FROM chunks WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now.isoformat(),),
        )
        conn.execute(
            "INSERT INTO cc_meta(key, value) VALUES ('last_sweep', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now.isoformat(),),
        )
        conn.execute("COMMIT")
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise
