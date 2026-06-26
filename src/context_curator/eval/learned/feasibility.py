"""Track-B cycle 1: eval-only feasibility of a learned re-onload ranker via leave-one-session-out.
Ships nothing to production. Deterministic given a fixed corpus + seed."""
from __future__ import annotations

import glob
import math
import os
import random
from pathlib import Path

from context_curator.eval.fixtures import Fixture
from context_curator.eval.learned.features import apply_norm, candidate_matrix, fit_norm
from context_curator.eval.metrics import ndcg_at_k
from context_curator.eval.precision_gate import precision_gate
from context_curator.eval.real_corpus import entities_match, harvest_trace, lexical_bias
from context_curator.eval.stats import cluster_bootstrap_ci
from context_curator.replay.capture.transcript import parse_transcript


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

    if len(set(y_all)) < 2:
        raise ValueError("fit_logistic requires both gold and non-gold training candidates")

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
    deltas, session_ids, learned_ndcgs, bm25_ndcgs = [], [], [], []
    sessions = sorted(by_session)
    for held in sessions:
        train = [fx for s in sessions if s != held for fx in by_session[s]]
        train_labels = {yy for fx in train for yy in candidate_matrix(fx)[1]}
        if len(train_labels) < 2:
            continue  # degenerate fold (all-gold/all-nongold training data) — skip
        model, means, stds = fit_logistic(train, C=C, seed=seed)
        for fx in by_session[held]:
            ln = learned_ndcg(model, means, stds, fx, k)
            bn = bm25_ndcg(fx, k)
            learned_ndcgs.append(ln)
            bm25_ndcgs.append(bn)
            deltas.append(ln - bn)
            session_ids.append(held)
    return deltas, session_ids, learned_ndcgs, bm25_ndcgs


def prior_refetch_scores(fx: Fixture) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, c in enumerate(fx.chunks):
        ents = set(c.entities)
        count = 0
        if ents:
            for j in range(i):
                if entities_match(ents, set(fx.chunks[j].entities)):
                    count += 1
        out[c.key] = float(count)
    return out


def _dirs(entities: list[str]) -> set[str]:
    return {os.path.dirname(e) for e in entities if e}


def same_dir_scores(fx: Fixture, *, w_loc: int = 5) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, c in enumerate(fx.chunks):
        dirs = _dirs(c.entities)
        flag = 0.0
        if dirs:
            for j in range(max(0, i - w_loc), i):
                if dirs & _dirs(fx.chunks[j].entities):
                    flag = 1.0
                    break
        out[c.key] = flag
    return out


def solo_ndcg(by_session, score_fn, *, k: int = 10) -> float:
    vals: list[float] = []
    for fxs in by_session.values():
        for fx in fxs:
            sc = score_fn(fx)
            ranked = [key for key, _s in sorted(sc.items(), key=lambda t: (-t[1], t[0]))]
            vals.append(ndcg_at_k(ranked, set(fx.gold_keys), k))
    return sum(vals) / len(vals) if vals else 0.0


def _harvest_by_session(paths: list[str], *, w: int = 5, min_candidates: int = 5):
    by: dict[str, list[Fixture]] = {}
    for p in paths:
        for fx in harvest_trace(parse_transcript(p), w=w, min_candidates=min_candidates):
            by.setdefault(fx.session_id or "?", []).append(fx)
    return by


def needed_n_range(deltas, session_ids, *, mei, seed, iters=200):
    by: dict[str, list[float]] = {}
    for d, s in zip(deltas, session_ids, strict=False):
        by.setdefault(s, []).append(d)
    clusters = list(by)
    n = len(clusters)
    if n < 2:
        return (None, None)
    rng = random.Random(seed)
    ests: list[int] = []
    for _ in range(iters):
        chosen = [clusters[rng.randrange(n)] for _ in range(n)]
        d2, s2 = [], []
        for idx, c in enumerate(chosen):
            d2.extend(by[c])
            s2.extend([f"{c}#{idx}"] * len(by[c]))
        lo, hi = cluster_bootstrap_ci(d2, s2, seed=seed)
        width = hi - lo
        if math.isfinite(width) and width > 0:
            ests.append(math.ceil(n * (width / mei) ** 2))
    return (min(ests), max(ests)) if ests else (None, None)


def run_feasibility(paths, *, mei=0.10, seed=0, C=1.0, w_loc=5, k=10) -> dict:
    by = _harvest_by_session(paths)
    deltas, sids, learned, bm = loso_deltas(by, C=C, seed=seed, k=k)
    lo, hi = cluster_bootstrap_ci(deltas, sids, seed=seed)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    gate = precision_gate(lo=lo, hi=hi, n_sessions=len(by), mei=mei)
    nmin, nmax = needed_n_range(deltas, sids, mei=mei, seed=seed)
    learned_mean = sum(learned) / len(learned) if learned else 0.0
    bm_mean = sum(bm) / len(bm) if bm else 0.0
    circ = {
        "prior_refetch_solo_ndcg": solo_ndcg(by, prior_refetch_scores, k=k),
        "same_dir_solo_ndcg": solo_ndcg(by, lambda fx: same_dir_scores(fx, w_loc=w_loc), k=k),
    }
    flat = [fx for fxs in by.values() for fx in fxs]
    lb = lexical_bias(flat, k=3, margin=0.15, seed=seed)
    return {
        "mean_delta": mean_delta, "ci": [lo, hi], "n_sessions": len(by),
        "n_fixtures": len(deltas), "gate_status": gate.status, "needed_n": gate.needed_n,
        "needed_n_range": [nmin, nmax], "learned_mean_ndcg": learned_mean,
        "bm25_mean_ndcg": bm_mean, "circularity": circ,
        "lexical_gold_r3": lb.gold_recall, "lexical_control_r3": lb.control_recall,
        "lexical_degenerate": lb.degenerate, "mei": mei,
        "per_session": {s: len(v) for s, v in by.items()},
    }


def format_report(rep: dict) -> str:
    lo, hi = rep["ci"]
    md = rep["mean_delta"]
    mei = rep["mei"]
    if rep["n_sessions"] < 3:
        verdict = f"FEASIBILITY-ONLY (n_sessions={rep['n_sessions']} < 3)"
    elif md <= 0:
        verdict = f"NO-GO: learned does not beat BM25 (mean {md:+.3f} <= 0)"
    elif lo > 0 and md >= mei:
        verdict = (
            f"STRONG SIGNAL: learned − BM25 {md:+.3f} >= MEI, CI>0 → GO to cycle 2"
        )
    elif md > 0:
        verdict = (
            f"WEAK POSITIVE: learned − BM25 {md:+.3f} (< MEI or CI includes 0) "
            "→ judgement call"
        )
    else:
        verdict = f"UNCLEAR: {md:+.3f}"
    lines = [
        "# Learned ranker — Cycle 1 feasibility (eval-only)",
        "",
        (
            "> Deterministic on a fixed corpus + seed; gold labels are CWD-dependent"
            " (os.path). No bge."
        ),
        "",
        f"verdict: {verdict}",
        (
            f"mean(learned − BM25) nDCG@10: {md:+.4f}; clustered 90% CI "
            f"[{lo:+.4f}, {hi:+.4f}]"
        ),
        (
            f"n_sessions={rep['n_sessions']} n_fixtures={rep['n_fixtures']} "
            f"per_session={rep['per_session']}"
        ),
        (
            f"arms: learned nDCG@10={rep['learned_mean_ndcg']:.3f} "
            f"bm25 nDCG@10={rep['bm25_mean_ndcg']:.3f}"
        ),
        (
            f"precision gate: {rep['gate_status']} (needed_n={rep['needed_n']}, "
            f"range~{rep['needed_n_range']})"
        ),
        (
            f"circularity audit (solo nDCG@10): {rep['circularity']} "
            f"— flag any within MEI of learned ({rep['learned_mean_ndcg']:.3f})"
        ),
        (
            f"lexical-bias: gold_R@3={rep['lexical_gold_r3']:.3f} "
            f"control={rep['lexical_control_r3']:.3f} degenerate={rep['lexical_degenerate']}"
        ),
    ]
    return "\n".join(lines)


def main() -> None:
    paths = sorted(glob.glob("src/context_curator/eval/fixtures/_real_local/*.jsonl"))
    rep = run_feasibility(paths)
    text = format_report(rep)
    print(text)
    Path("docs/superpowers/keystone-learned.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
