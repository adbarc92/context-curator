from __future__ import annotations

from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.learned.feasibility import (
    bm25_ndcg,
    fit_logistic,
    learned_ndcg,
    loso_deltas,
)


def _mixed(name: str, session: str) -> Fixture:
    chunks = [
        FixtureChunk(
            key=f"{name}-k0",
            content="warehouse restock inventory levels",
            producing_tool="Read",
        ),
        FixtureChunk(
            key=f"{name}-k1",
            content="logging configuration setup",
            producing_tool="Bash",
        ),
        FixtureChunk(
            key=f"{name}-k2",
            content="unrelated database migration",
            producing_tool="Grep",
        ),
        FixtureChunk(
            key=f"{name}-k3",
            content="ui button styling notes",
            producing_tool="Glob",
        ),
        FixtureChunk(
            key=f"{name}-k4",
            content="warehouse restock helper method",
            producing_tool="Edit",
        ),
    ]
    return Fixture(
        name=name,
        prompt="warehouse restock",
        gold_keys=[f"{name}-k0"],
        session_id=session,
        chunks=chunks,
    )


def _by_session():
    return {
        "s1": [_mixed("a", "s1"), _mixed("b", "s1")],
        "s2": [_mixed("c", "s2"), _mixed("d", "s2")],
    }


def test_fit_and_score_returns_unit_interval_ndcg():
    by = _by_session()
    flat = [fx for fxs in by.values() for fx in fxs]
    model, means, stds = fit_logistic(flat, C=1.0, seed=0)
    fx = flat[0]
    assert 0.0 <= learned_ndcg(model, means, stds, fx) <= 1.0
    assert 0.0 <= bm25_ndcg(fx) <= 1.0


def test_loso_holds_out_each_session_once():
    by = _by_session()
    deltas, sids, learned, bm = loso_deltas(by, C=1.0, seed=0)
    assert len(deltas) == sum(len(v) for v in by.values())
    assert set(sids) == set(by)
    assert len(learned) == len(bm) == len(deltas)
