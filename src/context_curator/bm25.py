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
# A token is a maximal [a-z0-9]+ run of the lowercased text. Mapping every other byte of the UTF-8
# encoding to a space (non-ASCII bytes are all >= 0x80) makes bytes.split() yield exactly those
# tokens, at C speed and without building a str per token.
_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"
_KEEP = bytes(i if chr(i) in _ALNUM else 0x20 for i in range(256))
# Same split, but every token byte becomes "a": the token count is the number of " a" boundaries,
# with no per-token allocation. Used for docs that contain no query term (the common case).
_MARK = bytes(0x61 if chr(i) in _ALNUM else 0x20 for i in range(256))


def _tok(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


# Above this many distinct query terms, one substring scan per term per doc costs more than
# counting every token of the doc once, so long prompts (pasted logs) switch to a Counter.
_MAX_SCAN_TERMS = 10


def _doc_stats(text: str, qterms: list[tuple[str, bytes]],
               qmap: dict[bytes, str]) -> tuple[int, dict[str, int]]:
    """(token count, term frequency of each query term present) for one doc. `qmap` is `qterms`
    keyed by bytes."""
    low = text.lower().encode("utf-8", "surrogatepass")
    if len(qterms) > _MAX_SCAN_TERMS:
        toks = low.translate(_KEEP).split()
        counts = Counter(toks)
        if len(counts) < len(qterms):                   # walk whichever side is smaller
            return len(toks), {qmap[tb]: n for tb, n in counts.items() if tb in qmap}
        return len(toks), {term: counts[tb] for term, tb in qterms if tb in counts}
    present = [(term, tb) for term, tb in qterms if tb in low]      # substring: cheap prefilter
    if not present:
        marks = low.translate(_MARK)
        return marks.count(b" a") + (1 if marks[:1] == b"a" else 0), {}
    toks = low.translate(_KEEP).split()
    tf: dict[str, int] = {}
    for term, tb in present:
        n = toks.count(tb)
        if n:
            tf[term] = n
    return len(toks), tf


def bm25_scores(
    prompt: str, docs: dict[str, str], *, k1: float = _K1, b: float = _B
) -> dict[str, float]:
    """BM25 score of each doc in `docs` against `prompt`. IDF is smoothed and computed over THIS
    doc set (M = len(docs)); returns {key: score}. Only query-term statistics are gathered (BM25
    needs nothing else), which keeps the live hook inside its latency budget on a ~4 M-char store.
    Bit-identical to the original implementation (tests/eval/test_bm25_equivalence.py)."""
    m = len(docs)
    q = _tok(prompt)
    qterms = [(t, t.encode("ascii")) for t in sorted(set(q))]
    qmap = {tb: t for t, tb in qterms}
    lens: dict[str, int] = {}
    tfs: dict[str, dict[str, int]] = {}
    for key, text in docs.items():
        lens[key], tfs[key] = _doc_stats(text, qterms, qmap)
    df: dict[str, int] = {}
    for tf in tfs.values():
        for term in tf:
            df[term] = df.get(term, 0) + 1
    idf = {t: math.log((m - n + 0.5) / (n + 0.5) + 1.0) for t, n in df.items()}
    avgdl = (sum(lens.values()) / m) if m else 0.0
    qpos: dict[str, list[int]] = {}
    for i, term in enumerate(q):
        qpos.setdefault(term, []).append(i)
    out: dict[str, float] = {}
    for key in docs:
        tf = tfs[key]
        dl = lens[key]
        s = 0.0
        if tf:
            norm = k1 * (1 - b + b * (dl / avgdl if avgdl else 0.0))
            contrib = {}
            for term, f in tf.items():
                denom = f + norm
                contrib[term] = idf.get(term, 0.0) * (f * (k1 + 1) / denom if denom else 0.0)
            # Add one contribution per prompt position whose term is in this doc, in prompt
            # order: the float sum then runs in exactly the reference order (bit-identical).
            for i in sorted(i for term in tf for i in qpos[term]):
                s += contrib[q[i]]
        out[key] = s
    return out
