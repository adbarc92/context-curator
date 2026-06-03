"""BM25 lexical IR baseline (design §3.3, §5) — the strong comparator bge must beat. Per-fixture
SMOOTHED IDF over the fixture's own chunks (task-local; avoids both per-fixture IDF noise and
whole-corpus cross-task contamination — round-3 I4). Pure-Python, deterministic."""
from __future__ import annotations

import math
import re

from context_curator.replay.schema import Decision, SelectedChunk, TaskSignal
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens

_K1 = 1.5
_B = 0.75


def _tok(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def bm25_scores(
    prompt: str, docs: dict[str, str], *, k1: float = _K1, b: float = _B
) -> dict[str, float]:
    """BM25 score of each doc in `docs` against `prompt`. IDF is smoothed and computed over THIS
    doc set (M = len(docs)); returns {key: score}."""
    m = len(docs)
    toks = {key: _tok(text) for key, text in docs.items()}
    df: dict[str, int] = {}
    for tl in toks.values():
        for term in set(tl):
            df[term] = df.get(term, 0) + 1
    idf = {t: math.log((m - n + 0.5) / (n + 0.5) + 1.0) for t, n in df.items()}
    avgdl = (sum(len(tl) for tl in toks.values()) / m) if m else 0.0
    q = _tok(prompt)
    out: dict[str, float] = {}
    for key, tl in toks.items():
        dl = len(tl)
        tf: dict[str, int] = {}
        for term in tl:
            tf[term] = tf.get(term, 0) + 1
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            f = tf[term]
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
