"""Keystone command (design §3.6, §5-§6). Three arms — recency-only, BM25-lexical, and the
semantic policy — scored on the held-out test split. The headline is the semantic arm vs the
STRONGEST cheap baseline per fixture, judged against a pre-registered +0.10 MEI with a 90% CI."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from context_curator.embeddings import Embedder
from context_curator.eval.bm25 import Bm25Target
from context_curator.eval.fixtures import load_fixtures
from context_curator.eval.metrics import ndcg_at_k
from context_curator.eval.runner import ArmMetrics, evaluate
from context_curator.eval.stats import bootstrap_ci
from context_curator.eval.sweep import grid_sweep
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights
from context_curator.replay.schema import TaskSignal, ToolRef
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore

_MEI = 0.10  # pre-registered minimum effect of interest (design §6)


@dataclass
class KeystoneReport:
    best_weights: PolicyWeights
    arm3: ArmMetrics
    arm2: ArmMetrics
    arm_bm25: ArmMetrics
    n_test: int
    per_fixture_ndcg_delta: list[float]
    delta_ci90: tuple[float, float]
    verdict: str


def _cos(emb: Embedder, a: str, b: str) -> float:
    va, vb = emb.embed(a), emb.embed(b)
    dot = sum(x * y for x, y in zip(va, vb, strict=True))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    return dot / (na * nb) if na and nb else 0.0


def _ndcg_per_fixture(fixtures, target, embedder, k):
    out = []
    for fx in fixtures:
        store = InMemoryStore(embedder=embedder)
        for c in fx.chunks:
            store.store(c.key, c.content, tags=c.tags, ttl_s=None)
        d = target.decide(
            TaskSignal(
                turn_index=0, prompt=fx.prompt, subtask_id=None,
                recent_tool_calls=[ToolRef(name=t, call_id=f"fixture:{i}")
                                   for i, t in enumerate(fx.recent_tools)],
            ),
            store,
        )
        out.append(ndcg_at_k([c.key for c in d.candidates], set(fx.gold_keys), k))
    return out


def run_keystone(
    corpus_dir: str, embedder: Embedder, k: int = 10, seed: int = 0
) -> KeystoneReport:
    fixtures = load_fixtures(corpus_dir)
    train = [f for f in fixtures if f.split == "train"]
    test = [f for f in fixtures if f.split == "test"]
    assert train and test, "corpus needs both train and test fixtures"
    best = grid_sweep(train, embedder, k=k).best
    arm3_target = PolicyTarget(RelevancePolicy(embedder, best))
    arm3 = evaluate(test, arm3_target, embedder, k=k)
    arm2 = evaluate(test, RecencyOnlyTarget(), embedder, k=k)
    arm_bm25 = evaluate(test, Bm25Target(), embedder, k=k)
    d3 = _ndcg_per_fixture(test, arm3_target, embedder, k)
    d2 = _ndcg_per_fixture(test, RecencyOnlyTarget(), embedder, k)
    dbm = _ndcg_per_fixture(test, Bm25Target(), embedder, k)
    # headline: semantic vs the STRONGEST cheap baseline per fixture (round-2 C1/C3)
    base = [max(b, m) for b, m in zip(d2, dbm, strict=True)]
    deltas = [a - s for a, s in zip(d3, base, strict=True)]
    lo, hi = bootstrap_ci(deltas, seed=seed)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    if lo <= 0 <= hi:
        verdict = (
            f"INCONCLUSIVE (underpowered): semantic vs strongest baseline {mean_delta:+.3f} "
            f"nDCG, n={len(test)}, 90% CI [{lo:+.3f},{hi:+.3f}] includes 0"
        )
    elif lo > 0 and mean_delta >= _MEI:
        verdict = (
            f"GREEN: semantic beats the strongest baseline by {mean_delta:+.3f} nDCG "
            f"(CI excludes 0, >= MEI {_MEI})"
        )
    elif lo > 0:
        verdict = (
            f"NEGATIVE (powered): effect {mean_delta:+.3f} < MEI {_MEI} "
            f"-> no practically-meaningful advantage"
        )
    else:
        verdict = f"baseline wins (CI excludes 0): {mean_delta:+.3f}"
    return KeystoneReport(
        best, arm3, arm2, arm_bm25, len(test), deltas, (lo, hi), verdict
    )


def main() -> None:
    import hashlib
    import json
    import os

    from context_curator.embeddings import FastEmbedEmbedder

    corpus = os.environ.get(
        "KEYSTONE_CORPUS", str(Path(__file__).parent / "fixtures" / "realistic")
    )
    embedder = FastEmbedEmbedder()
    rpt = run_keystone(corpus, embedder)
    lines = [
        "# Keystone (3-arm: recency / BM25 / semantic)",
        "",
        "> bge floats are machine-sensitive — REGENERATE, do not diff."
        " `seed` fixes resampling only.",
        f"> n_test={rpt.n_test}; verdict uses the pre-registered +{_MEI} MEI and a 90% CI.",
        "",
        f"verdict: {rpt.verdict}",
        f"best_weights: w_similarity={rpt.best_weights.w_similarity}",
        f"arm-semantic: nDCG@10={rpt.arm3.ndcg_at_k:.3f} P@10={rpt.arm3.precision_at_k:.3f} "
        f"R@3={rpt.arm3.recall_at_rk:.3f} sel-P={rpt.arm3.selected_precision:.3f}",
        f"arm-bm25: nDCG@10={rpt.arm_bm25.ndcg_at_k:.3f} P@10={rpt.arm_bm25.precision_at_k:.3f} "
        f"R@3={rpt.arm_bm25.recall_at_rk:.3f} sel-P={rpt.arm_bm25.selected_precision:.3f}",
        f"arm-recency: nDCG@10={rpt.arm2.ndcg_at_k:.3f} P@10={rpt.arm2.precision_at_k:.3f} "
        f"R@3={rpt.arm2.recall_at_rk:.3f} sel-P={rpt.arm2.selected_precision:.3f}",
        f"per-fixture nDCG deltas (semantic - strongest baseline): "
        f"{[round(x, 3) for x in rpt.per_fixture_ndcg_delta]}",
    ]
    text = "\n".join(lines)
    print(text)
    out = Path("results")
    out.mkdir(exist_ok=True)
    (out / "keystone-10.md").write_text(text + "\n", encoding="utf-8")

    # frozen per-fixture bge cosine matrix for reviewer repro (round-1 I4)
    cos = {
        fx.name: {c.key: round(_cos(embedder, fx.prompt, c.content), 6) for c in fx.chunks}
        for fx in load_fixtures(corpus)
    }
    raw = json.dumps(cos, sort_keys=True)
    Path(corpus, "_bge_cosines.json").write_text(raw, encoding="utf-8")
    print("cosine-matrix sha256:", hashlib.sha256(raw.encode()).hexdigest()[:16])


if __name__ == "__main__":
    main()
