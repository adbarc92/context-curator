from pathlib import Path

import context_curator.eval as e
from context_curator.eval.learned.feasibility import (
    bm25_ndcg,
    fit_logistic,
    learned_ndcg,
    loso_deltas,
)
from context_curator.eval.real_corpus import harvest_trace
from context_curator.replay.capture.transcript import parse_transcript


def _by_session():
    base = Path(e.__file__).parent.parent.parent.parent / "tests" / "eval" / "_traces"
    out: dict[str, list] = {}
    for f in ("sample_a.jsonl", "sample_b.jsonl"):
        for fx in harvest_trace(parse_transcript(str(base / f)), w=5, min_candidates=5):
            out.setdefault(fx.session_id or "?", []).append(fx)
    return out


def test_fit_and_score_returns_unit_interval_ndcg():
    by = _by_session()
    flat = [fx for fxs in by.values() for fx in fxs]
    assert flat, "sample traces must yield fixtures"
    model, means, stds = fit_logistic(flat, C=1.0, seed=0)
    fx = flat[0]
    assert 0.0 <= learned_ndcg(model, means, stds, fx) <= 1.0
    assert 0.0 <= bm25_ndcg(fx) <= 1.0


def test_loso_holds_out_each_session_once():
    by = _by_session()
    deltas, sids, learned, bm = loso_deltas(by, C=1.0, seed=0)
    assert len(deltas) == sum(len(v) for v in by.values())
    assert set(sids) == set(by)            # every session appears as held-out
    assert len(learned) == len(bm) == len(deltas)
