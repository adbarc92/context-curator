"""Background backfill + one-time wrong-dim migration (design §5.2). Embeds OUTSIDE the write
transaction; touches ONLY `embedding` (never seq/created_at/expires_at). Subsumes cc-reembed."""
from __future__ import annotations

import json

from context_curator.embeddings import Embedder
from context_curator.store.sqlite_store import SqliteStore


def migrate_wrong_dim_once(store: SqliteStore, *, dim: int) -> int:
    """One-time: NULL out any non-NULL embedding whose length != dim (old 256-dim rows), so the
    normal backfill re-embeds them. Gated by a cc_meta flag so it runs once per store."""
    conn = store._conn
    flag = conn.execute("SELECT value FROM cc_meta WHERE key='curator_dim_migrated'").fetchone()
    if flag is not None and flag["value"] == str(dim):
        return 0
    rows = conn.execute("SELECT key, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
    bad = [r["key"] for r in rows if len(json.loads(r["embedding"])) != dim]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for key in bad:
            conn.execute("UPDATE chunks SET embedding=NULL WHERE key=?", (key,))
        conn.execute(
            "INSERT INTO cc_meta(key, value) VALUES('curator_dim_migrated', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(dim),),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise
    return len(bad)


def reconcile_once(store: SqliteStore, embedder: Embedder, *, batch: int) -> int:
    """One tick: SELECT up to `batch` NULL rows, embed them WITH NO LOCK HELD, then a single
    short BEGIN IMMEDIATE of per-row UPDATEs (embedding only). Returns rows updated."""
    conn = store._conn
    rows = conn.execute(
        "SELECT key, content FROM chunks WHERE embedding IS NULL LIMIT ?", (batch,)
    ).fetchall()
    if not rows:
        return 0
    vecs = [(r["key"], embedder.embed(r["content"])) for r in rows]   # OUTSIDE the transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        for key, vec in vecs:
            if vec is not None:
                conn.execute(
                    "UPDATE chunks SET embedding=? WHERE key=?", (json.dumps(vec), key)
                )
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise
    return sum(1 for _, v in vecs if v is not None)


def reconcile_loop(
    store: SqliteStore,
    embedder: Embedder,
    *,
    batch: int,
    interval_s: float,
    stop,
) -> None:
    """Runs on the curator's reconcile thread until `stop` (threading.Event) is set. Backs off on
    transient SQLITE_BUSY rather than letting capture eat the busy_timeout (round-2 C4b)."""
    while not stop.is_set():
        try:
            reconcile_once(store, embedder, batch=batch)
        except Exception:                               # incl. transient busy -> retry next tick
            pass
        stop.wait(interval_s)
