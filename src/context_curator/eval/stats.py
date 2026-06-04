"""Paired bootstrap CI (design §3.6). NOTE: meaningful only once n meets the power target for the
pre-registered effect (design §6); below that it is a width-of-ignorance display, not a verdict.
stdlib only (random.Random(seed) -> reproducible RESAMPLING; not the underlying values)."""
from __future__ import annotations

import math
import random


def bootstrap_ci(deltas: list[float], *, seed: int, alpha: float = 0.1,
                 iters: int = 2000) -> tuple[float, float]:
    """Percentile [alpha/2, 1-alpha/2] interval of the resampled mean of `deltas`."""
    if not deltas:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(iters)
    )
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)


def cluster_bootstrap_ci(deltas: list[float], cluster_ids: list[str], *, seed: int,
                         alpha: float = 0.1, iters: int = 2000) -> tuple[float, float]:
    """Session-clustered percentile CI (design §5.3): resample whole sessions with replacement, then
    take all of a resampled session's deltas. Respects intra-session correlation (the true N is the
    number of sessions). Contract: len mismatch -> ValueError; 0 sessions -> (0,0); 1 session ->
    (-inf,+inf) width-of-ignorance (a single cluster carries no between-session information)."""
    if len(deltas) != len(cluster_ids):
        raise ValueError("deltas and cluster_ids must have equal length")
    by_cluster: dict[str, list[float]] = {}
    for d, c in zip(deltas, cluster_ids, strict=True):
        by_cluster.setdefault(c, []).append(d)
    clusters = list(by_cluster)
    if not clusters:
        return (0.0, 0.0)
    if len(clusters) == 1:
        return (-math.inf, math.inf)
    rng = random.Random(seed)
    n = len(clusters)
    means: list[float] = []
    for _ in range(iters):
        pool: list[float] = []
        for _ in range(n):
            pool.extend(by_cluster[clusters[rng.randrange(n)]])
        means.append(sum(pool) / len(pool))
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)
