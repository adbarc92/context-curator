"""Eval runner (design §3.3). One embedder populates the store AND backs the policy
(asserted). Metrics over Decision.candidates (the ranking); a production-faithful
precision over Decision.selected too."""
from __future__ import annotations

from dataclasses import dataclass

from context_curator.embeddings import Embedder
from context_curator.eval.fixtures import Fixture
from context_curator.eval.metrics import ndcg_at_k, precision_at_k, recall_at_k
from context_curator.replay.schema import TaskSignal, ToolRef
from context_curator.replay.target import PolicyTarget
from context_curator.store.memory import InMemoryStore


@dataclass(frozen=True)
class ArmMetrics:
    ndcg_at_k: float
    precision_at_k: float
    recall_at_rk: float
    selected_precision: float
    n_fixtures: int


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def evaluate(fixtures: list[Fixture], target, embedder: Embedder,
             k: int = 10, recall_k: int = 3) -> ArmMetrics:
    if isinstance(target, PolicyTarget):
        assert target.embedder is embedder, "store and policy embedders must be identical"
    ndcgs, precs, recs, sel_precs = [], [], [], []
    for fx in fixtures:
        store = InMemoryStore(embedder=embedder)
        for c in fx.chunks:                          # chronological -> seq increases -> recency
            store.store(c.key, c.content, tags=c.tags, ttl_s=None)
        signal = TaskSignal(
            turn_index=0, prompt=fx.prompt, subtask_id=None,
            recent_tool_calls=[ToolRef(name=t, call_id=f"fixture:{i}")
                               for i, t in enumerate(fx.recent_tools)],
        )
        d = target.decide(signal, store)
        gold = set(fx.gold_keys)
        ranked = [c.key for c in d.candidates]
        selected = [c.key for c in d.selected]
        ndcgs.append(ndcg_at_k(ranked, gold, k))
        precs.append(precision_at_k(ranked, gold, k))
        recs.append(recall_at_k(ranked, gold, recall_k))
        sel_precs.append(precision_at_k(selected, gold, k))
    return ArmMetrics(_mean(ndcgs), _mean(precs), _mean(recs), _mean(sel_precs), len(fixtures))
