import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


def _scoped_store(tmp_path, prefix):
    return SqliteStore(
        db_path=str(tmp_path / "cc.db"),
        embedder=HashingEmbedder(dim=32),
        allowed_prefix=prefix,
    )


def test_query_never_crosses_tenant_boundary(tmp_path):
    # Seed many tenants via an unscoped writer sharing the same db file.
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    for t in range(20):
        for i in range(5):
            seed.store(f"proj:acme:tenant:t{t}:offloaded:{i}", f"secret-{t}-{i}", tags=["s"])
    # Also a prefix-collision tenant: t1 vs t10/t11...
    target = "proj:acme:tenant:t1"
    scoped = _scoped_store(tmp_path, target)

    results = scoped.query("anything", tags=["s"], k=1000)
    assert results, "scoped query should see its own tenant's chunks"
    for c in results:
        assert c.key.startswith(target + ":"), f"LEAK: {c.key} escaped scope {target}"


def test_retrieve_blocks_out_of_scope_key(tmp_path):
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store("proj:acme:tenant:t99:secret", "nope", tags=["s"])
    scoped = _scoped_store(tmp_path, "proj:acme:tenant:t1")
    assert scoped.retrieve("proj:acme:tenant:t99:secret") is None


def test_list_blocks_out_of_scope_keys(tmp_path):
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store("proj:acme:tenant:t1:a", "x")
    seed.store("proj:acme:tenant:t10:a", "y")  # prefix collision
    seed.store("proj:acme:tenant:t99:a", "z")
    scoped = _scoped_store(tmp_path, "proj:acme:tenant:t1")
    keys = scoped.list("proj:acme:tenant:t1")
    assert keys == ["proj:acme:tenant:t1:a"]


@pytest.mark.parametrize("attacker", [
    "proj:acme:tenant:t10",
    "proj:acme:tenant:t100",
    "proj:acme:tenant:t1x",
])
def test_prefix_collision_does_not_leak(tmp_path, attacker):
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store(f"{attacker}:data", "leak?", tags=["s"])
    seed.store("proj:acme:tenant:t1:data", "mine", tags=["s"])
    scoped = _scoped_store(tmp_path, "proj:acme:tenant:t1")
    for c in scoped.query("x", tags=["s"], k=1000):
        assert c.key.startswith("proj:acme:tenant:t1:")


def test_query_scope_with_like_wildcard_in_prefix(tmp_path):
    # An allowed_prefix containing `_` (a SQL LIKE wildcard) must not leak via LIKE
    # over-matching. Also exercises the token_budget code path under scoping.
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store("proj:my_app:tenant:t1:doc", "mine", tags=["s"])
    seed.store("proj:myXapp:tenant:t1:doc", "leak?", tags=["s"])  # 'X' matches '_' in LIKE
    scoped = _scoped_store(tmp_path, "proj:my_app:tenant:t1")
    keys = {c.key for c in scoped.query("x", tags=["s"], k=1000, token_budget=99999)}
    assert keys == {"proj:my_app:tenant:t1:doc"}
