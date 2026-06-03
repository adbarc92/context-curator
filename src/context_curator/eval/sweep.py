"""Weight sweep (design §3.5). w_similarity only (selection bias control at small n);
LOO-CV per cell. A COARSE DIRECTIONAL SCAN — the chosen cell is not statistically
distinguishable from its neighbors at this corpus size; see top_cells."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from context_curator.embeddings import Embedder
from context_curator.eval.fixtures import Fixture
from context_curator.eval.metrics import ndcg_at_k
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import PolicyTarget
from context_curator.store.memory import InMemoryStore

DEFAULT_GRID = [
    {"w_similarity": s, "w_recency": round(1 - s, 2)} for s in (0.4, 0.5, 0.65, 0.8, 1.0)
]

_BASE_WEIGHTS = PolicyWeights()


@dataclass
class SweepCell:
    weights: PolicyWeights
    loo_ndcg: float
    fold_std: float


@dataclass
class SweepResult:
    best: PolicyWeights
    top_cells: list[SweepCell]


def _ndcg_one(fx: Fixture, weights: PolicyWeights, embedder: Embedder, k: int) -> float:
    store = InMemoryStore(embedder=embedder)
    for c in fx.chunks:
        store.store(c.key, c.content, tags=c.tags, ttl_s=None)
    d = PolicyTarget(RelevancePolicy(embedder, weights)).decide(
        TaskSignal(turn_index=0, prompt=fx.prompt, subtask_id=None, recent_tool_calls=[]),
        store,
    )
    return ndcg_at_k([c.key for c in d.candidates], set(fx.gold_keys), k)


def grid_sweep(
    train_fixtures: list[Fixture],
    embedder: Embedder,
    grid: list[dict] = DEFAULT_GRID,
    k: int = 10,
    base: PolicyWeights = _BASE_WEIGHTS,
) -> SweepResult:
    cells: list[SweepCell] = []
    for combo in grid:                                   # fixed order -> deterministic
        w = replace(base, **combo)
        per_fixture = [_ndcg_one(fx, w, embedder, k) for fx in train_fixtures]
        mean = sum(per_fixture) / len(per_fixture) if per_fixture else 0.0
        var = (
            sum((x - mean) ** 2 for x in per_fixture) / len(per_fixture)
            if per_fixture else 0.0
        )
        cells.append(SweepCell(weights=w, loo_ndcg=mean, fold_std=math.sqrt(var)))
    # ties: prefer higher w_similarity (pro-semantic tiebreak)
    cells.sort(key=lambda c: (c.loo_ndcg, c.weights.w_similarity), reverse=True)
    return SweepResult(best=cells[0].weights, top_cells=cells)
