"""Stickiness ranker (Track A): rank candidates by how many times their file entity was already
fetched, BM25 as tie-breaker, newer first. The SAME function runs in the live hook and in the eval
(`eval/stickiness.py`), so a measured number is a shipped number.

`refetch_counts` is exactly the eval's `prior_refetch_scores` (count of EARLIER candidates whose
entities match: equal, or one path contains the other), computed through an entity index instead
of the O(n^2) pairwise scan, which is too slow for a ~900-chunk store inside the hook budget.
Equivalence is proven by randomized tests against the pairwise definition."""
from __future__ import annotations

import os
from bisect import bisect_left
from collections.abc import Iterable, Sequence

from context_curator.bm25 import bm25_scores

Candidate = tuple[str, str, Iterable[str]]          # (key, content, entities), oldest -> newest


def _strip(p: str) -> str:
    return p.rstrip(os.sep)


def refetch_counts(entity_sets: Sequence[Iterable[str]]) -> list[int]:
    """For each position i: the number of j < i whose entity set matches set i. Empty sets score 0
    and never count toward others."""
    sets = [frozenset(s) for s in entity_sets]
    distinct = sorted({e for s in sets for e in s})
    by_strip: dict[str, list[str]] = {}
    for e in distinct:
        by_strip.setdefault(_strip(e), []).append(e)
    sets_with: dict[str, set[frozenset[str]]] = {}
    for s in sets:
        for e in s:
            sets_with.setdefault(e, set()).add(s)

    def matching_entities(e: str) -> set[str]:
        out = {e}
        prefix = _strip(e) + os.sep                     # descendants: y startswith strip(e)+sep
        i = bisect_left(distinct, prefix)
        while i < len(distinct) and distinct[i].startswith(prefix):
            out.add(distinct[i])
            i += 1
        for k, ch in enumerate(e):                      # ancestors: e startswith strip(y)+sep
            if ch == os.sep:
                out.update(by_strip.get(e[:k], ()))
        return out

    related: dict[frozenset[str], set[frozenset[str]]] = {}
    seen: dict[frozenset[str], int] = {}
    counts: list[int] = []
    for s in sets:
        if not s:
            counts.append(0)
            continue
        rel = related.get(s)
        if rel is None:
            rel = {t for e in s for y in matching_entities(e) for t in sets_with.get(y, ())}
            related[s] = rel
        counts.append(sum(seen.get(t, 0) for t in rel))
        seen[s] = seen.get(s, 0) + 1
    return counts


def stickiness_rank(prompt: str, candidates: Sequence[Candidate]) -> list[str]:
    """Keys best-first: refetch count desc, then BM25 vs `prompt` desc, then newer first.
    `candidates` are oldest -> newest; each key is returned once (a repeated key keeps its last,
    i.e. newest, candidate) so nDCG can never count a key twice."""
    last = {k: i for i, (k, _c, _e) in enumerate(candidates)}
    cands = [c for i, c in enumerate(candidates) if last[c[0]] == i]
    counts = refetch_counts([ents for _k, _c, ents in cands])
    bm = bm25_scores(prompt, {k: c for k, c, _e in cands})
    order = sorted(range(len(cands)), key=lambda i: (-counts[i], -bm[cands[i][0]], -i))
    return [cands[i][0] for i in order]
