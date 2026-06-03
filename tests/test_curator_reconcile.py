import json

from context_curator.curator import reconcile
from context_curator.embeddings import Embedder, NullEmbedder
from context_curator.store.sqlite_store import SqliteStore


class _Emb(Embedder):
    @property
    def dim(self):
        return 3

    def embed(self, text):
        return [float(len(text)), 0.0, 0.0]


def test_backfill_fills_null_rows(tmp_path):
    s = SqliteStore(db_path=str(tmp_path / "r.db"), embedder=NullEmbedder())
    s.store("session:x:tool:a", "alpha")               # embedding NULL
    n = reconcile.reconcile_once(s, _Emb(), batch=16)
    assert n == 1
    got = s.retrieve("session:x:tool:a")
    assert got.embedding is not None and len(got.embedding) == 3


def test_reconcile_preserves_seq_created_expires(tmp_path):
    s = SqliteStore(db_path=str(tmp_path / "r.db"), embedder=NullEmbedder())
    s.store("session:x:tool:a", "alpha", ttl_s=3600)
    before = s._conn.execute(
        "SELECT seq, created_at, expires_at FROM chunks WHERE key=?",
        ("session:x:tool:a",),
    ).fetchone()
    reconcile.reconcile_once(s, _Emb(), batch=16)
    after = s._conn.execute(
        "SELECT seq, created_at, expires_at FROM chunks WHERE key=?",
        ("session:x:tool:a",),
    ).fetchone()
    assert tuple(after) == tuple(before)               # ONLY embedding changed (round-2-C2 lesson)


def test_one_time_wrong_dim_migration(tmp_path):
    s = SqliteStore(db_path=str(tmp_path / "r.db"), embedder=NullEmbedder())
    s.store("session:x:tool:old", "legacy")
    s._conn.execute("UPDATE chunks SET embedding=? WHERE key=?",
                    (json.dumps([0.1] * 256), "session:x:tool:old"))
    migrated = reconcile.migrate_wrong_dim_once(s, dim=3)
    assert migrated == 1                               # NULLed the 256-dim row
    assert s.retrieve("session:x:tool:old").embedding is None
    assert reconcile.migrate_wrong_dim_once(s, dim=3) == 0     # flag set -> no-op second run
