"""Threshold sweep for ONLOAD_BGE_COSINE_THRESHOLD (design §5). Gated precision is monotone in
the threshold (round-1 C3), so we choose the HIGHEST threshold whose MICRO-AVERAGED recall stays
>= recall_floor (round-2/3 I5). `gold_cosines` = the raw bge cosine of each gold key to its prompt,
pooled across fixtures (micro-average = fraction of all gold clearing the threshold). Per-cell
bootstrap CIs are reported as context; eligibility is decided on point recall (deterministic)."""
from __future__ import annotations

from dataclasses import dataclass

from context_curator.eval.stats import bootstrap_ci


@dataclass
class ThresholdSweepResult:
    chosen: float
    curve: dict[float, float]                  # threshold -> micro recall (point)
    ci: dict[float, tuple[float, float]]       # threshold -> recall 90% CI (context)


def sweep_threshold(gold_cosines: list[float], *, grid: list[float], recall_floor: float,
                    seed: int = 0) -> ThresholdSweepResult:
    curve: dict[float, float] = {}
    ci: dict[float, tuple[float, float]] = {}
    for t in grid:
        # >= mirrors the production onload gate (a chunk is kept iff cosine >= threshold)
        hits = [1.0 if c >= t else 0.0 for c in gold_cosines]
        curve[t] = sum(hits) / len(hits) if hits else 0.0
        ci[t] = bootstrap_ci(hits, seed=seed) if hits else (0.0, 0.0)
    # highest threshold whose POINT micro-recall >= floor; fall back to the lowest grid point
    eligible = [t for t in grid if curve[t] >= recall_floor]
    chosen = max(eligible) if eligible else min(grid)
    return ThresholdSweepResult(chosen=chosen, curve=curve, ci=ci)
