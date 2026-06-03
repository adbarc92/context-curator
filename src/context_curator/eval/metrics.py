"""IR metrics (design §3.1). Binary relevance; pure, deterministic. The authority on
metric correctness is this module's golden tests, not the keystone proxy."""
from __future__ import annotations

import math


def precision_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    topk = ranked[:k]
    if not topk:
        return 0.0
    hits = sum(1 for key in topk if key in gold)
    return hits / min(k, len(topk))


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    hits = sum(1 for key in ranked[:k] if key in gold)
    return hits / len(gold)


def ndcg_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, key in enumerate(ranked[:k]) if key in gold)
    n_ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(n_ideal))
    return dcg / idcg if idcg > 0 else 0.0
