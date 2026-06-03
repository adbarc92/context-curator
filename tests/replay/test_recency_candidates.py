from context_curator.embeddings import HashingEmbedder
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore


def test_recency_target_populates_full_recency_candidates():
    store = InMemoryStore(embedder=HashingEmbedder(dim=8))
    for key in ("a", "b", "c"):           # a oldest, c newest
        store.store(key, "x", ttl_s=None)
    d = RecencyOnlyTarget().decide(
        TaskSignal(turn_index=0, prompt="p", subtask_id=None, recent_tool_calls=[]), store)
    assert [c.key for c in d.candidates] == ["c", "b", "a"]   # full pool, newest-first
    assert all(c.score is None for c in d.candidates)
