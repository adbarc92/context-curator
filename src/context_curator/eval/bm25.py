"""BM25 lexical IR baseline (design §3.3, §5) — the strong comparator bge must beat. Per-fixture
SMOOTHED IDF over the fixture's own chunks (task-local; avoids both per-fixture IDF noise and
whole-corpus cross-task contamination — round-3 I4). Pure-Python, deterministic."""
from __future__ import annotations

import math
import re
from collections import Counter

from context_curator.replay.schema import Decision, SelectedChunk, TaskSignal
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens

_K1 = 1.5
_B = 0.75


_TOKEN = re.compile(r"[a-z0-9]+")


def _tok(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def bm25_scores(
    prompt: str, docs: dict[str, str], *, k1: float = _K1, b: float = _B
) -> dict[str, float]:
    """BM25 score of each doc in `docs` against `prompt`. IDF is smoothed and computed over THIS
    doc set (M = len(docs)); returns {key: score}. Counter-based for the live hook's latency
    budget; bit-identical to the original loop version (tests/eval/test_bm25_equivalence.py)."""
    m = len(docs)
    tfs = {key: Counter(_tok(text)) for key, text in docs.items()}
    lens = {key: sum(tf.values()) for key, tf in tfs.items()}
    df: Counter[str] = Counter()
    for tf in tfs.values():
        df.update(tf.keys())
    avgdl = (sum(lens.values()) / m) if m else 0.0
    q = _tok(prompt)
    idf = {t: math.log((m - df[t] + 0.5) / (df[t] + 0.5) + 1.0) for t in set(q) if t in df}
    out: dict[str, float] = {}
    for key, tf in tfs.items():
        dl = lens[key]
        s = 0.0
        for term in q:
            f = tf.get(term)
            if not f:
                continue
            denom = f + k1 * (1 - b + b * (dl / avgdl if avgdl else 0.0))
            s += idf.get(term, 0.0) * (f * (k1 + 1) / denom if denom else 0.0)
        out[key] = s
    return out


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
