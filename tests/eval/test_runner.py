import math

import pytest

from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.runner import evaluate
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from tests.eval.conftest import KeywordEmbedder


def _semantic_win():
    return [Fixture(name="sw", prompt="A B C D", gold_keys=["gold"], split="train",
                    chunks=[FixtureChunk(key="gold", content="A B C D"),     # old, match
                            FixtureChunk(key="dist", content="E F")])]        # new, no match


def test_policy_ranks_gold_first():
    emb = KeywordEmbedder()
    m = evaluate(_semantic_win(), PolicyTarget(RelevancePolicy(emb)), emb, k=10)
    assert m.ndcg_at_k == 1.0


def test_recency_ranks_gold_low():
    emb = KeywordEmbedder()
    m = evaluate(_semantic_win(), RecencyOnlyTarget(), emb, k=10)
    assert math.isclose(m.ndcg_at_k, 1 / math.log2(3), rel_tol=1e-9)   # gold at rank 1


def test_embedder_binding_assert():
    a, b = KeywordEmbedder(), KeywordEmbedder()
    with pytest.raises(AssertionError):
        evaluate(_semantic_win(), PolicyTarget(RelevancePolicy(a)), b, k=10)  # mismatched
