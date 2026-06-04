from context_curator.eval.corpus_audit import audit_corpus
from context_curator.eval.fixtures import Fixture, FixtureChunk


def _fx(name, gold_pos, n=12, hard_negs=2):
    # chunks chronological oldest..newest; gold at index gold_pos; hard_negs flagged via tag.
    # pick the first `hard_negs` indices that are not the gold position
    neg_idx = [i for i in range(n) if i != gold_pos][:hard_negs]
    chunks = []
    for i in range(n):
        tag = ["hard_neg"] if i in neg_idx else []
        chunks.append(FixtureChunk(key=f"{name}:c{i}", content=f"content {i}", tags=tag))
    return Fixture(name=name, chunks=chunks, prompt="p",
                   gold_keys=[f"{name}:c{gold_pos}"], split="test")


def test_audit_fails_recency_trivial_corpus():
    # all gold newest -> recency-trivial -> FAIL
    corpus = [_fx(f"f{i}", gold_pos=11) for i in range(9)]
    rep = audit_corpus(corpus, n_chunks_min=8)
    assert rep.ok is False
    assert "recency" in rep.reason.lower()


def test_audit_passes_mixed_recency_with_hard_negs():
    # gold spread across oldest/middle/newest thirds (n=12 -> thirds 0-3,4-7,8-11)
    positions = [1, 2, 5, 6, 9, 10, 1, 6, 10]      # covers all three thirds
    corpus = [_fx(f"f{i}", gold_pos=p) for i, p in enumerate(positions)]
    rep = audit_corpus(corpus, n_chunks_min=8)
    assert rep.ok is True


def test_audit_fails_missing_hard_negatives():
    corpus = [_fx(f"f{i}", gold_pos=5, hard_negs=0) for i in range(9)]
    rep = audit_corpus(corpus, n_chunks_min=8)
    assert rep.ok is False
    assert "hard negative" in rep.reason.lower()


def _fx_real(name, gold_pos, n=12):
    chunks = [FixtureChunk(key=f"{name}:c{i}", content=f"content {i}") for i in range(n)]  # no tags
    return Fixture(name=name, chunks=chunks, prompt="p", gold_keys=[f"{name}:c{gold_pos}"],
                   split="test")


def test_audit_real_mode_ignores_missing_hard_negs():
    positions = [1, 5, 9, 1, 6, 10, 2, 7, 11]   # gold spread across all three recency thirds
    corpus = [_fx_real(f"f{i}", p) for i, p in enumerate(positions)]
    rep = audit_corpus(corpus, n_chunks_min=8, require_hard_neg=False)
    assert rep.ok is True


def test_audit_real_mode_flags_degenerate_recency():
    corpus = [_fx_real(f"f{i}", 11) for i in range(9)]   # all gold newest -> recency-trivial
    rep = audit_corpus(corpus, n_chunks_min=8, require_hard_neg=False)
    assert rep.ok is False and "recency" in rep.reason.lower()
