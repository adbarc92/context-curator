import math

import pytest


@pytest.fixture(scope="module")
def embedder():
    """Skip the whole module unless the bge model can actually be constructed —
    probing the MODEL, not just the package (a 130MB download must not block CI)."""
    pytest.importorskip("fastembed")
    from context_curator.embeddings import FastEmbedEmbedder
    emb = FastEmbedEmbedder()
    try:
        emb.embed("warmup")        # forces model construction/download
    except Exception as e:         # noqa: BLE001
        pytest.skip(f"bge model unavailable: {e}")
    return emb


def test_dim_and_unit_norm(embedder):
    v = embedder.embed("hello world")
    assert len(v) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-5)


def test_deterministic(embedder):
    assert embedder.embed("a query about authentication") == \
           embedder.embed("a query about authentication")


def test_cos_distribution_validates_sim_floor(embedder):
    # related vs unrelated short-text pairs; sim_floor=0.5 must sit between the bands
    def cos(a, b):
        va, vb = embedder.embed(a), embedder.embed(b)
        return sum(x * y for x, y in zip(va, vb, strict=False))
    related = [
        cos("how do I log in a user", "user authentication and login flow"),
        cos("parse a CSV file in python", "read csv rows with the python csv module"),
        cos("fix a failing unit test", "the pytest assertion is failing"),
        cos("deploy the app to production", "production deployment pipeline"),
    ]
    unrelated = [
        cos("how do I log in a user", "the weather in Tokyo tomorrow"),
        cos("parse a CSV file in python", "best recipe for banana bread"),
        cos("fix a failing unit test", "history of the Roman empire"),
        cos("deploy the app to production", "how tall is Mount Everest"),
    ]
    assert max(unrelated) < 0.5 < min(related)
    assert min(related) - max(unrelated) > 0.1
