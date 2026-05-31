from context_curator.embeddings import HashingEmbedder
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore
from context_curator.tokens import estimate_tokens


def _signal():
    return TaskSignal(turn_index=0, prompt="do the thing", subtask_id=None, recent_tool_calls=[])


def _store_with(*contents):
    s = InMemoryStore(embedder=HashingEmbedder(dim=32))
    for i, c in enumerate(contents):
        s.store(f"k{i}", c, tags=["t"], ttl_s=None)
    return s


def test_onloads_most_recent_first():
    store = _store_with("oldest", "newest")
    d = RecencyOnlyTarget(tags=["t"]).decide(_signal(), store)
    assert [s.key for s in d.selected] == ["k1", "k0"]
    assert all(s.score is None for s in d.selected)


def test_total_tokens_matches_and_respects_budget():
    store = _store_with("x" * 100, "y" * 100, "z" * 100)  # ~25 tokens each
    target = RecencyOnlyTarget(tags=["t"], token_budget=30)
    d = target.decide(_signal(), store)
    assert len(d.selected) == 1
    assert d.total_tokens <= 30
    # cross-check against the store's own token accounting (single-tokenizer invariant, §3.4)
    for s in d.selected:
        assert s.tokens == estimate_tokens(store.retrieve(s.key).content)
    assert d.total_tokens == sum(s.tokens for s in d.selected)


def test_k_and_budget_bind_simultaneously():
    store = _store_with("x" * 40, "y" * 40, "z" * 40)  # ~10 tokens each
    # k=2 and budget=15: budget allows 1 (10, next 10 -> 20>15), k allows 2 -> first-fit wins -> 1
    d = RecencyOnlyTarget(tags=["t"], k=2, token_budget=15).decide(_signal(), store)
    assert len(d.selected) == 1


def test_no_budget_sums_all_selected():
    store = _store_with("aa", "bb")
    d = RecencyOnlyTarget(tags=["t"]).decide(_signal(), store)
    assert d.total_tokens == sum(s.tokens for s in d.selected)


def test_empty_store_empty_decision():
    store = InMemoryStore(embedder=HashingEmbedder(dim=32))
    d = RecencyOnlyTarget(tags=["t"]).decide(_signal(), store)
    assert d.selected == [] and d.total_tokens == 0


def test_recency_target_satisfies_replay_target_protocol():
    from context_curator.replay.target import ReplayTarget
    target: ReplayTarget = RecencyOnlyTarget()  # structural conformance
    assert target.name == "recency-only"
