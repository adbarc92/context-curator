"""Paired bootstrap CI (design §3.6). NOTE: meaningful only once n is adequate (n≳30);
at the M3b starter corpus size it is a width-of-ignorance display, not a verdict.
stdlib only (random.Random(seed) -> reproducible RESAMPLING; not the underlying values)."""
from __future__ import annotations

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
