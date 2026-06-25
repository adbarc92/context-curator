"""Track-B cycle 1: eval-only feasibility of a learned re-onload ranker via leave-one-session-out.
Ships nothing to production. Deterministic given a fixed corpus + seed."""
from __future__ import annotations

from context_curator.eval.fixtures import Fixture
from context_curator.eval.learned.features import apply_norm, candidate_matrix, fit_norm
from context_curator.eval.metrics import ndcg_at_k


def fit_logistic(fixtures: list[Fixture], *, C: float = 1.0, seed: int = 0):
    from sklearn.linear_model import LogisticRegression

    X_all: list[list[float]] = []
    y_all: list[int] = []
    for fx in fixtures:
        X, y, _ = candidate_matrix(fx)
        X_all.extend(X)
        y_all.extend(y)
    means, stds = fit_norm(X_all)
    Z = apply_norm(X_all, means, stds)

    # Handle degenerate case: if all samples have the same label, add a dummy negative example
    unique_labels = set(y_all)
    if len(unique_labels) == 1:
        # Add a synthetic negative sample to allow model fitting
        dummy_row = [0.0] * len(Z[0]) if Z else [0.0]
        Z.append(dummy_row)
        y_all.append(1 - y_all[0])  # opposite of the only class present

    model = LogisticRegression(
        class_weight="balanced", C=C, random_state=seed, max_iter=1000
    )
    model.fit(Z, y_all)
    return model, means, stds


def learned_ndcg(model, means, stds, fx: Fixture, k: int = 10) -> float:
    X, _, keys = candidate_matrix(fx)
    if not X:
        return 0.0
    Z = apply_norm(X, means, stds)
    scores = model.decision_function(Z)
    ranked = [
        key for key, _s in sorted(zip(keys, scores, strict=False), key=lambda t: (-t[1], t[0]))
    ]
    return ndcg_at_k(ranked, set(fx.gold_keys), k)


def bm25_ndcg(fx: Fixture, k: int = 10) -> float:
    from context_curator.eval.bm25 import bm25_scores

    docs = {c.key: c.content for c in fx.chunks}
    sc = bm25_scores(fx.prompt, docs)
    ranked = [key for key, _s in sorted(sc.items(), key=lambda t: (-t[1], t[0]))]
    return ndcg_at_k(ranked, set(fx.gold_keys), k)


def loso_deltas(by_session, *, C: float, seed: int, k: int = 10):
    deltas: list[float] = []
    session_ids: list[str] = []
    learned_ndcgs: list[float] = []
    bm25_ndcgs: list[float] = []
    sessions = sorted(by_session)
    for held in sessions:
        train = [fx for s in sessions if s != held for fx in by_session[s]]
        model, means, stds = fit_logistic(train, C=C, seed=seed)
        for fx in by_session[held]:
            ln = learned_ndcg(model, means, stds, fx, k)
            bn = bm25_ndcg(fx, k)
            learned_ndcgs.append(ln)
            bm25_ndcgs.append(bn)
            deltas.append(ln - bn)
            session_ids.append(held)
    return deltas, session_ids, learned_ndcgs, bm25_ndcgs
