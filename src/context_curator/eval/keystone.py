"""Keystone command (design §3.6). Reports the arm-2-vs-arm-3 slice as a DIRECTIONAL,
explicitly-underpowered first-look — NOT a significant verdict (n too small)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from context_curator.embeddings import Embedder
from context_curator.eval.fixtures import load_fixtures
from context_curator.eval.metrics import ndcg_at_k
from context_curator.eval.runner import ArmMetrics, evaluate
from context_curator.eval.stats import bootstrap_ci
from context_curator.eval.sweep import grid_sweep
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore


@dataclass
class KeystoneReport:
    best_weights: PolicyWeights
    arm3: ArmMetrics
    arm2: ArmMetrics
    n_test: int
    per_fixture_ndcg_delta: list[float]
    delta_ci90: tuple[float, float]
    verdict: str


def _ndcg_per_fixture(fixtures, target, embedder, k):
    out = []
    for fx in fixtures:
        store = InMemoryStore(embedder=embedder)
        for c in fx.chunks:
            store.store(c.key, c.content, tags=c.tags, ttl_s=None)
        d = target.decide(
            TaskSignal(
                turn_index=0, prompt=fx.prompt, subtask_id=None, recent_tool_calls=[]
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
    d3 = _ndcg_per_fixture(test, arm3_target, embedder, k)
    d2 = _ndcg_per_fixture(test, RecencyOnlyTarget(), embedder, k)
    deltas = [a - b for a, b in zip(d3, d2, strict=True)]
    lo, hi = bootstrap_ci(deltas, seed=seed)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    verdict = (
        f"directional: arm-3 ahead by {mean_delta:+.3f} nDCG "
        f"(UNDERPOWERED, n={len(test)}, 90% CI [{lo:+.3f},{hi:+.3f}] includes 0 "
        f"-> INCONCLUSIVE; grow corpus to n>=~30)"
        if lo <= 0 <= hi
        else (
            f"arm-3 beats arm-2 (CI excludes 0): +{mean_delta:.3f}"
            if lo > 0
            else f"arm-2 wins (CI excludes 0): {mean_delta:.3f}"
        )
    )
    return KeystoneReport(best, arm3, arm2, len(test), deltas, (lo, hi), verdict)


def main() -> None:
    from context_curator.embeddings import FastEmbedEmbedder

    corpus = str(Path(__file__).parent / "fixtures" / "realistic")
    rpt = run_keystone(corpus, FastEmbedEmbedder())
    lines = [
        "# Keystone (DIRECTIONAL, not conclusive)",
        "",
        "> bge floats are machine-sensitive — REGENERATE, do not diff."
        " `seed` fixes resampling only.",
        f"> n_test={rpt.n_test}; not powered to detect small deltas. Grow corpus to n>=~30.",
        "",
        f"verdict: {rpt.verdict}",
        f"best_weights: w_similarity={rpt.best_weights.w_similarity}",
        f"arm-3: nDCG@10={rpt.arm3.ndcg_at_k:.3f} P@10={rpt.arm3.precision_at_k:.3f} "
        f"R@3={rpt.arm3.recall_at_rk:.3f} sel-P={rpt.arm3.selected_precision:.3f}",
        f"arm-2: nDCG@10={rpt.arm2.ndcg_at_k:.3f} P@10={rpt.arm2.precision_at_k:.3f} "
        f"R@3={rpt.arm2.recall_at_rk:.3f} sel-P={rpt.arm2.selected_precision:.3f}",
        f"per-fixture nDCG deltas: {[round(x, 3) for x in rpt.per_fixture_ndcg_delta]}",
    ]
    text = "\n".join(lines)
    print(text)
    out = Path("results")
    out.mkdir(exist_ok=True)
    (out / "keystone-10.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
