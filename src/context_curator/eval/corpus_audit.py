"""Corpus FAIRNESS audit (design §3.2) — characterizes the assembled corpus and FAILS only a
DEGENERATE one. It drops NOTHING to make a method win (that would be the round-1 circularity).
Pinned predicate so the committed-corpus test is deterministic (round-3 I3)."""
from __future__ import annotations

from dataclasses import dataclass

from context_curator.eval.fixtures import Fixture

# Pinned audit thresholds (round-3 I3 — exact, not "roughly")
_THIRD_MIN_FRAC = 0.20      # each recency third must hold >= 20% of fixtures' gold
_HARD_NEG_MIN = 2           # every fixture needs >= 2 hard negatives (tagged "hard_neg")


@dataclass
class AuditReport:
    ok: bool
    reason: str
    third_counts: tuple[int, int, int]   # (oldest, middle, newest) gold counts
    n: int


def _gold_third(fx: Fixture) -> int:
    """0=oldest third, 1=middle, 2=newest, by the FIRST gold key's chronological position
    (chunks are oldest-first)."""
    keys = [c.key for c in fx.chunks]
    gpos = min(keys.index(g) for g in fx.gold_keys if g in keys)
    frac = gpos / max(1, len(keys) - 1)
    return 0 if frac < 1 / 3 else (1 if frac < 2 / 3 else 2)


def audit_corpus(fixtures: list[Fixture], *, n_chunks_min: int = 12) -> AuditReport:
    n = len(fixtures)
    counts = [0, 0, 0]
    for fx in fixtures:
        if len(fx.chunks) < n_chunks_min:
            return AuditReport(False, f"fixture {fx.name} has <{n_chunks_min} chunks", (0, 0, 0), n)
        n_hard = sum(1 for c in fx.chunks if "hard_neg" in c.tags)
        if n_hard < _HARD_NEG_MIN:
            return AuditReport(False, f"fixture {fx.name} has <{_HARD_NEG_MIN} hard negatives",
                               (0, 0, 0), n)
        counts[_gold_third(fx)] += 1
    if n == 0:
        return AuditReport(False, "empty corpus", (0, 0, 0), 0)
    for label, c in zip(("oldest", "middle", "newest"), counts, strict=True):
        if c < _THIRD_MIN_FRAC * n:
            return AuditReport(False, f"recency-{label} third under-represented ({c}/{n})",
                               tuple(counts), n)  # type: ignore[arg-type]
    return AuditReport(True, "fair", tuple(counts), n)  # type: ignore[arg-type]
