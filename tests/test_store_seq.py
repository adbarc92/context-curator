from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


def _sqlite(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))


def test_recency_is_write_order_not_walltime(tmp_path):
    s = _sqlite(tmp_path)
    s.store("a", "x")
    s.store("b", "x")
    s.store("c", "x")
    assert [c.key for c in s.query("q", k=10)] == ["c", "b", "a"]


def test_overwrite_moves_key_to_front(tmp_path):
    s = _sqlite(tmp_path)
    s.store("a", "x")
    s.store("b", "x")
    s.store("a", "x2")  # re-store: a is now newest
    assert [c.key for c in s.query("q", k=10)] == ["a", "b"]


def test_seq_survives_reopen(tmp_path):
    db = str(tmp_path / "cc.db")
    s1 = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=32))
    s1.store("a", "x")
    s1.store("b", "x")
    s1.close()
    s2 = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=32))
    s2.store("c", "x")  # must seed seq from MAX(seq) of existing rows -> newest
    assert [c.key for c in s2.query("q", k=10)] == ["c", "b", "a"]
