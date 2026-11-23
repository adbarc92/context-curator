"""BM25 lexical scoring — pure, dependency-free, shared by the live onload hook and the eval
harness (`context_curator.eval.bm25` re-exports it). Per-doc-set SMOOTHED IDF over the given docs
(task-local, round-3 I4). Deterministic."""
from __future__ import annotations

import math
import re
from collections import Counter

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
