import pytest

from context_curator.eval.bm25 import Bm25Target
from context_curator.eval.eviction_regret import (
    RegretReport,
    Session,
    SessionTurn,
    eviction_regret,
)
from context_curator.eval.fixtures import FixtureChunk
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore
from tests.eval.conftest import KeywordEmbedder


def _stale_auth() -> Session:
    # turn 0 introduces `gold` (vocab "A B C"); turns 1-2 introduce 6 fillers (disjoint vocab
    # "D E F"); turn 3's prompt "A B C" needs `gold` (age 3 >= lag 2). 7 chunks available at turn 3.
    return Session(name="stale-auth", turns=[
        SessionTurn(prompt="A B C", new_chunks=[FixtureChunk(key="gold", content="A B C")]),
        SessionTurn(prompt="D E F",
                    new_chunks=[FixtureChunk(key=f"f{i}", content="D E F") for i in (1, 2, 3)]),
        SessionTurn(prompt="D E F",
                    new_chunks=[FixtureChunk(key=f"f{i}", content="D E F") for i in (4, 5, 6)]),
        SessionTurn(prompt="A B C", needed_keys=["gold"]),
    ])


def _no_old_needs() -> Session:
    # `x` introduced and needed in the SAME turn (age 0 < lag) -> zero old need-events.
    return Session(name="no-old-needs", turns=[
        SessionTurn(prompt="A", new_chunks=[FixtureChunk(key="x", content="A")], needed_keys=["x"]),
    ])


def test_recency_arm_buries_old_gold_high_regret():
    r = eviction_regret([_stale_auth()], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=2)
    assert r.rate == 1.0
    assert r.arm == "recency-only"
    assert r.old_need_events == 1 and r.regret_events == 1


def test_semantic_arm_resurfaces_old_gold_zero_regret():
    emb = KeywordEmbedder()
    target = PolicyTarget(RelevancePolicy(emb))
    r = eviction_regret([_stale_auth()], target, emb, k=5, lag=2)
    assert r.rate == 0.0
    assert r.arm == "semantic-policy"
    assert r.old_need_events == 1 and r.regret_events == 0


def test_no_old_needs_rate_is_none():
    r = eviction_regret([_no_old_needs()], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=2)
    assert r.rate is None and r.old_need_events == 0


def test_validation_rejects_unintroduced_needed_key():
    s = Session(name="bad", turns=[SessionTurn(prompt="p", needed_keys=["ghost"])])
    with pytest.raises(ValueError):
        eviction_regret([s], RecencyOnlyTarget(), KeywordEmbedder())


def test_validation_rejects_duplicate_key():
    s = Session(name="dup", turns=[
        SessionTurn(prompt="p", new_chunks=[FixtureChunk(key="x", content="A")]),
        SessionTurn(prompt="p", new_chunks=[FixtureChunk(key="x", content="A")]),
    ])
    with pytest.raises(ValueError):
        eviction_regret([s], RecencyOnlyTarget(), KeywordEmbedder())


def test_lag_too_large_excludes_the_need():
    # age(gold)=3 < lag 4 -> no old need-events -> rate None
    r = eviction_regret([_stale_auth()], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=4)
    assert r.old_need_events == 0 and r.rate is None


def test_larger_k_admits_the_buried_chunk():
    # k=7 over the 7-chunk pool admits gold (recency position 7) -> recency regret 0.0
    r = eviction_regret([_stale_auth()], RecencyOnlyTarget(), KeywordEmbedder(), k=7, lag=2)
    assert r.rate == 0.0


def test_candidates_is_full_pool_for_all_arms():
    emb = KeywordEmbedder()
    chunks = [c for turn in _stale_auth().turns for c in turn.new_chunks]   # the 7 chunks
    store = InMemoryStore(embedder=emb)
    for c in chunks:
        store.store(c.key, c.content, tags=c.tags, ttl_s=None)
    sig = TaskSignal(turn_index=3, prompt="A B C", subtask_id=None, recent_tool_calls=[])
    for target in (RecencyOnlyTarget(), Bm25Target(), PolicyTarget(RelevancePolicy(emb))):
        assert len(target.decide(sig, store).candidates) == len(chunks)   # full pool, not truncated


def test_bm25_arm_runs_and_reports():
    r = eviction_regret([_stale_auth()], Bm25Target(), KeywordEmbedder(), k=5, lag=2)
    assert isinstance(r, RegretReport) and r.arm == "bm25" and r.old_need_events == 1


def test_empty_session_does_not_throw():
    s = Session(name="empty", turns=[SessionTurn(prompt="p")])
    r = eviction_regret([s], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=2)
    assert r.rate is None and r.old_need_events == 0
