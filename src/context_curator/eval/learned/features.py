"""Cheap, eval-side features for the learned re-onload ranker (track B, cycle 1).
Pure/deterministic. Tool one-hot is lowercased + bucketed so it is Cycle-2 serve-ready."""
from __future__ import annotations

import math

from context_curator.eval.bm25 import bm25_scores
from context_curator.eval.fixtures import Fixture

TOOL_VOCAB = [
    "read", "grep", "glob", "notebookread",
    "edit", "write", "multiedit", "notebookedit",
    "bash", "other",
]
_BASE_FEATURES = ["bm25", "recency_rank", "chunk_log_len"]


def canon_tool(name: str | None) -> str:
    low = (name or "").lower()
    return low if low in TOOL_VOCAB else "other"


def feature_names() -> list[str]:
    return [*_BASE_FEATURES, *(f"tool={t}" for t in TOOL_VOCAB)]


def candidate_matrix(fx: Fixture) -> tuple[list[list[float]], list[int], list[str]]:
    docs = {c.key: c.content for c in fx.chunks}
    bm = bm25_scores(fx.prompt, docs)
    n = len(fx.chunks)
    gold = set(fx.gold_keys)
    X: list[list[float]] = []
    y: list[int] = []
    keys: list[str] = []
    for i, c in enumerate(fx.chunks):
        recency = (i / (n - 1)) if n > 1 else 1.0   # oldest=0.0 .. newest=1.0
        row = [bm.get(c.key, 0.0), recency, math.log1p(len(c.content))]
        tool = canon_tool(c.producing_tool)
        row.extend(1.0 if tool == t else 0.0 for t in TOOL_VOCAB)
        X.append(row)
        y.append(1 if c.key in gold else 0)
        keys.append(c.key)
    return X, y, keys


def fit_norm(X: list[list[float]]) -> tuple[list[float], list[float]]:
    if not X:
        return [], []
    cols = len(X[0])
    n = len(X)
    means = [sum(r[j] for r in X) / n for j in range(cols)]
    stds = []
    for j in range(cols):
        var = sum((r[j] - means[j]) ** 2 for r in X) / n
        s = math.sqrt(var)
        stds.append(s if s > 1e-12 else 1.0)
    return means, stds


def apply_norm(X: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    return [[(r[j] - means[j]) / stds[j] for j in range(len(r))] for r in X]
