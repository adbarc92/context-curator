
from context_curator.models import Chunk


def test_store_returns_key_and_retrieve_roundtrips(store):
    key = store.store("shared:contracts:auth", "POST /login -> {token}", tags=["auth"])
    assert key == "shared:contracts:auth"
    got = store.retrieve(key)
    assert isinstance(got, Chunk)
    assert got.content == "POST /login -> {token}"
    assert got.tags == ["auth"]


def test_store_computes_embedding_at_write_time(store):
    store.store("k1", "some content tokens", tags=[])
    got = store.retrieve("k1")
    assert got.embedding is not None
    assert len(got.embedding) > 0


def test_retrieve_missing_returns_none(store):
    assert store.retrieve("does:not:exist") is None


def test_overwrite_updates_content(store):
    store.store("k1", "v1")
    store.store("k1", "v2")
    assert store.retrieve("k1").content == "v2"


def test_evict_removes(store):
    store.store("k1", "v1")
    assert store.evict("k1") is True
    assert store.retrieve("k1") is None
    assert store.evict("k1") is False  # already gone


def test_pin_sets_pin_flag(store):
    store.store("k1", "v1", pin=False)
    assert store.pin("k1") is True
    assert store.retrieve("k1").pin is True
    assert store.pin("missing") is False


def test_list_returns_keys_under_prefix(store):
    store.store("shared:contracts:a", "x")
    store.store("shared:contracts:b", "y")
    store.store("session:s1:turn_log", "z")
    keys = set(store.list("shared:contracts"))
    assert keys == {"shared:contracts:a", "shared:contracts:b"}


def test_query_returns_chunks_with_content(store):
    store.store("k1", "alpha", tags=["x"])
    store.store("k2", "beta", tags=["x"])
    results = store.query("anything", tags=["x"], k=10)
    assert all(isinstance(r, Chunk) for r in results)
    assert {r.content for r in results} == {"alpha", "beta"}


def test_query_tag_filter(store):
    store.store("k1", "alpha", tags=["keep"])
    store.store("k2", "beta", tags=["drop"])
    results = store.query("anything", tags=["keep"], k=10)
    assert [r.key for r in results] == ["k1"]


def test_query_respects_k(store):
    for i in range(5):
        store.store(f"k{i}", f"content {i}", tags=["t"])
    assert len(store.query("anything", tags=["t"], k=3)) == 3


def test_query_token_budget_trims(store):
    # each content is ~25 chars => ~6 tokens via len//4
    for i in range(5):
        store.store(f"k{i}", "x" * 100, tags=["t"])
    # budget of 30 tokens => 100-char (25-token) chunks: only 1 fits
    results = store.query("anything", tags=["t"], k=10, token_budget=30)
    assert len(results) == 1
