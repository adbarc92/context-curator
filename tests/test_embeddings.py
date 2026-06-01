import math

from context_curator.embeddings import Embedder, HashingEmbedder


def test_hashing_embedder_is_an_embedder():
    assert issubclass(HashingEmbedder, Embedder)


def test_embedding_has_fixed_dim():
    emb = HashingEmbedder(dim=128)
    v = emb.embed("hello world")
    assert len(v) == 128


def test_embedding_is_deterministic():
    emb = HashingEmbedder(dim=64)
    assert emb.embed("auth contract") == emb.embed("auth contract")


def test_embedding_is_unit_normalized_for_nonempty():
    emb = HashingEmbedder(dim=64)
    v = emb.embed("some real tokens here")
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)


def test_empty_text_returns_zero_vector():
    emb = HashingEmbedder(dim=32)
    v = emb.embed("   ")
    assert v == [0.0] * 32
