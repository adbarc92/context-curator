from context_curator.embeddings import NullEmbedder
from context_curator.store.sqlite_store import SqliteStore


def test_null_embedder_dim_384_embed_none():
    e = NullEmbedder()
    assert e.dim == 384
    assert e.embed("anything") is None


def test_store_with_null_embedder_persists_sql_null(tmp_path):
    s = SqliteStore(db_path=str(tmp_path / "n.db"), embedder=NullEmbedder())
    s.store("session:x:tool:c", "some captured content")
    got = s.retrieve("session:x:tool:c")
    assert got is not None
    assert got.embedding is None          # NULL round-trips as None
