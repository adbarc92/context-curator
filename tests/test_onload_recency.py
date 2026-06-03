import context_curator.embeddings as emb_mod
from context_curator.models import Chunk
from context_curator.onload.select import recency_select


def _c(key, content, *, pin=False):
    return Chunk(key=key, content=content, pin=pin)        # newest-first = list order


def test_recency_newest_first_excludes_pins_and_conventions(monkeypatch):
    # guard: recency_select must construct NO embedder
    monkeypatch.setattr(emb_mod.HashingEmbedder, "__init__",
                        lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("embedder built")))
    cands = [_c("session:x:tool:c2", "newer"),
             _c("pinnedkey", "p", pin=True),
             _c("proj:app:conventions", "conv"),
             _c("session:x:tool:c1", "older")]
    out = recency_select(cands, k=10, token_budget=None)
    assert [c.key for c in out] == ["session:x:tool:c2", "session:x:tool:c1"]


def test_recency_respects_k_and_budget():
    cands = [_c(f"k{i}", "x" * 80) for i in range(5)]      # ~20 tokens each
    assert len(recency_select(cands, k=2, token_budget=None)) == 2
    assert len(recency_select(cands, k=10, token_budget=30)) == 1   # first-fit break
