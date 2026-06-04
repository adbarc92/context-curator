"""Independent gold-judge (design §4) — drops ONLY fixtures whose planted gold is flat-out wrong
(a yes/no relevance call per gold key), NEVER adjudicates ranking (round-3 C1). The circularity
guard is a drop-rate-by-BM25-recall-tercile statistic (round-3 C1), NOT a judge ranking."""
from __future__ import annotations

from typing import Protocol

from context_curator.eval.fixtures import Fixture


class GoldJudge(Protocol):
    def is_relevant(self, prompt: str, chunk_content: str) -> bool: ...


def judge_corpus(fixtures: list[Fixture], judge: GoldJudge) -> tuple[list[Fixture], list[Fixture]]:
    """Keep a fixture iff EVERY gold key's chunk is judged relevant to the prompt. Returns
    (kept, dropped)."""
    kept, dropped = [], []
    for fx in fixtures:
        by_key = {c.key: c.content for c in fx.chunks}
        ok = all(judge.is_relevant(fx.prompt, by_key.get(g, "")) for g in fx.gold_keys)
        (kept if ok else dropped).append(fx)
    return kept, dropped


def drop_rate_by_bm25_tercile(kept_bm25_recall: list[float],
                              dropped_bm25_recall: list[float]) -> dict[str, float]:
    """Circularity guard (round-3 C1): if the judge dropped disproportionately many HIGH-BM25-recall
    fixtures, the survivor set is stripped of BM25-favorable cases -> bge-aligned -> RIGGED.
    Splits ALL fixtures' BM25 recall at the 1/3 and 2/3 quantiles and reports the drop-rate in the
    high vs low tercile."""
    allr = sorted(kept_bm25_recall + dropped_bm25_recall)
    if not allr:
        return {"high_tercile_drop_rate": 0.0, "low_tercile_drop_rate": 0.0}
    lo_q = allr[len(allr) // 3]
    hi_q = allr[2 * len(allr) // 3]

    def rate(band_lo: float, band_hi: float) -> float:
        in_band_kept = [r for r in kept_bm25_recall if band_lo <= r <= band_hi]
        in_band_drop = [r for r in dropped_bm25_recall if band_lo <= r <= band_hi]
        total = len(in_band_kept) + len(in_band_drop)
        return (len(in_band_drop) / total) if total else 0.0

    return {"high_tercile_drop_rate": rate(hi_q, 1.0),
            "low_tercile_drop_rate": rate(0.0, lo_q)}
