"""BM25 lexical IR baseline (design §3.3, §5) — the strong comparator bge must beat. Per-fixture
SMOOTHED IDF over the fixture's own chunks (task-local; avoids both per-fixture IDF noise and
whole-corpus cross-task contamination — round-3 I4). Pure-Python, deterministic."""
from __future__ import annotations

from context_curator.bm25 import _B, _K1, bm25_scores  # noqa: F401  (re-export)
from context_curator.replay.schema import Decision, SelectedChunk, TaskSignal
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens


class Bm25Target:
    """Arm — ranks the store's live chunks by BM25 against the prompt (design §5). Same
    Decision shape as PolicyTarget/RecencyOnlyTarget so the keystone scores it identically."""

    name = "bm25"

    def __init__(self, k: int = 10, token_budget: int | None = None) -> None:
        self.k = k
        self.token_budget = token_budget

    def decide(self, signal: TaskSignal, store: Store) -> Decision:
        chunks = store.all_live_chunks()                       # full live set
        scores = bm25_scores(signal.prompt, {c.key: c.content for c in chunks})
        ranked = sorted(chunks, key=lambda c: (-scores.get(c.key, 0.0), c.key))
        cand = [
            SelectedChunk(key=c.key, score=round(scores.get(c.key, 0.0), 6),
                          tokens=estimate_tokens(c.content))
            for c in ranked
        ]
        selected = cand[: self.k]
        return Decision(
            turn_index=signal.turn_index,
            subtask_id=signal.subtask_id,
            prompt_preview=signal.prompt[:80],
            selected=selected,
            total_tokens=sum(s.tokens for s in selected),
            candidates=cand,
        )
