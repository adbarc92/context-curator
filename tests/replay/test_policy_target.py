from context_curator.embeddings import Embedder
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.capture.synthetic import TraceBuilder
from context_curator.replay.engine import ReplayEngine
from context_curator.replay.schema import Decision, TaskSignal, ToolRef
from context_curator.replay.target import PolicyTarget, ReplayTarget
from context_curator.store.memory import InMemoryStore


class FakeEmbedder(Embedder):
    _VECS = {"auth": [1.0, 0.0], "other": [0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 2

    def embed(self, text: str) -> list[float]:
        return self._VECS.get(text.split()[0], [0.0, 0.0])


def _target():
    return PolicyTarget(RelevancePolicy(FakeEmbedder()))


def test_satisfies_protocol_and_populates_decision():
    t: ReplayTarget = _target()                         # structural conformance
    assert t.name == "semantic-policy"
    store = InMemoryStore(embedder=FakeEmbedder())
    store.store("session:s:tool:0", "auth thing", ttl_s=None)
    sig = TaskSignal(turn_index=0, prompt="auth please", subtask_id=None,
                     recent_tool_calls=[ToolRef(name="Read", call_id="c0")])
    d = t.decide(sig, store)
    assert isinstance(d, Decision)
    assert d.candidates and d.candidates[0].score is not None
    assert d.offloaded == []                            # offload deferred to M4


def test_uses_all_live_chunks_not_truncated_query():
    # 30 chunks: a k=10 query would hide older ones; the policy must see all 30
    store = InMemoryStore(embedder=FakeEmbedder())
    for i in range(30):
        store.store(f"session:s:tool:{i}", "other", ttl_s=None)
    store.store("session:s:tool:target", "auth content", ttl_s=None)  # newest, relevant
    sig = TaskSignal(turn_index=0, prompt="auth please", subtask_id=None, recent_tool_calls=[])
    d = _target().decide(sig, store)
    assert len(d.candidates) == 31                       # saw the full live set
    assert d.selected[0].key == "session:s:tool:target"  # relevant one ranked first


def test_byte_identical_across_runs():
    trace = (TraceBuilder("s")
             .user("auth please")
             .tool("Read", {}).result("auth content")
             .user("auth again")
             .build())
    a = ReplayEngine(target=_target()).run(trace).model_dump()
    b = ReplayEngine(target=_target()).run(trace).model_dump()
    assert a == b                                        # single-machine determinism
