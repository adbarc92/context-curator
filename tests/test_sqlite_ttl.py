import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))


def test_expired_chunk_reads_as_absent(sqlite_store):
    sqlite_store.store("k1", "transient", ttl_s=0)  # expires immediately
    assert sqlite_store.retrieve("k1") is None


def test_unexpired_chunk_is_returned(sqlite_store):
    sqlite_store.store("k1", "durable", ttl_s=3600)
    assert sqlite_store.retrieve("k1").content == "durable"


def test_pinned_chunk_never_expires(sqlite_store):
    sqlite_store.store("k1", "pinned", ttl_s=0, pin=True)
    assert sqlite_store.retrieve("k1").content == "pinned"


def test_pinning_after_store_clears_expiry(sqlite_store):
    sqlite_store.store("k1", "becomes pinned", ttl_s=0)
    # would be expired, but pin() clears expires_at
    assert sqlite_store.pin("k1") is True
    assert sqlite_store.retrieve("k1").content == "becomes pinned"


def test_none_ttl_means_no_expiry(sqlite_store):
    sqlite_store.store("k1", "forever", ttl_s=None)
    assert sqlite_store.retrieve("k1").content == "forever"


def test_expired_chunk_excluded_from_query(sqlite_store):
    sqlite_store.store("live", "a", ttl_s=3600, tags=["t"])
    sqlite_store.store("dead", "b", ttl_s=0, tags=["t"])
    keys = {c.key for c in sqlite_store.query("x", tags=["t"], k=10)}
    assert keys == {"live"}
