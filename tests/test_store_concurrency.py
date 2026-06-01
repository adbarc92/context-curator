import threading

import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore, sweep_expired


def _store(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=16))


def test_wal_enabled(tmp_path):
    s = _store(tmp_path)
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_concurrent_writers_no_duplicate_seq(tmp_path):
    db = str(tmp_path / "cc.db")
    _store(tmp_path)  # create schema

    def writer(n):
        s = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=16))
        for i in range(50):
            s.store(f"k{n}-{i}", "x")
        s.close()

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    chk = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=16))
    seqs = [r[0] for r in chk._conn.execute("SELECT seq FROM chunks").fetchall()]
    assert len(seqs) == len(set(seqs)) == 200  # 4*50 rows, all-distinct seq


def test_store_rollback_leaves_connection_usable(tmp_path):
    class Boom(HashingEmbedder):
        def embed(self, text):
            raise RuntimeError("boom")

    # embed raises -> store() raises, but no lingering write lock
    s = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=Boom(dim=16))
    with pytest.raises(RuntimeError):
        s.store("k1", "x")
    s2 = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=16))
    s2.store("k2", "y")  # must not hang/lock
    assert s2.retrieve("k2") is not None


def test_sweep_deletes_expired_spares_pinned_and_rate_limits(tmp_path, monkeypatch):
    s = _store(tmp_path)
    s.store("dead", "x", ttl_s=0)
    s.store("live", "y", ttl_s=3600)
    s.store("pinned", "z", ttl_s=0, pin=True)
    assert sweep_expired(s) == 1  # only 'dead'
    assert s.retrieve("live") is not None
    assert s.retrieve("pinned") is not None
    s.store("dead2", "x", ttl_s=0)
    assert sweep_expired(s) == 0  # rate-limited within SWEEP_INTERVAL_S
