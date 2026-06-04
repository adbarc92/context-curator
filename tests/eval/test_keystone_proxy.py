import math
from pathlib import Path

import context_curator.eval as e
from context_curator.eval.fixtures import load_fixtures
from context_curator.eval.runner import evaluate
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from tests.eval.conftest import KeywordEmbedder

CONTROLLED = str(Path(e.__file__).parent / "fixtures" / "controlled")


def _by_name(fixtures):
    return {f.name: [f] for f in fixtures}


def test_adversarial_fixture_arm2_strictly_wins():
    emb = KeywordEmbedder()
    fx = _by_name(load_fixtures(CONTROLLED))["adversarial-arm2-wins"]
    arm3 = evaluate(fx, PolicyTarget(RelevancePolicy(emb)), emb)
    arm2 = evaluate(fx, RecencyOnlyTarget(), emb)
    assert math.isclose(arm3.ndcg_at_k, 1 / math.log2(3), rel_tol=1e-9)   # gold ranked 2nd
    assert arm2.ndcg_at_k == 1.0                                          # recency finds it
    assert arm2.ndcg_at_k > arm3.ndcg_at_k                                # the negative exists


def test_semantic_fixture_arm3_perfect():
    emb = KeywordEmbedder()
    fx = _by_name(load_fixtures(CONTROLLED))["semantic-win-1"]
    arm3 = evaluate(fx, PolicyTarget(RelevancePolicy(emb)), emb)
    arm2 = evaluate(fx, RecencyOnlyTarget(), emb)
    assert arm3.ndcg_at_k == 1.0
    assert math.isclose(arm2.ndcg_at_k, 1 / math.log2(3), rel_tol=1e-9)


def test_aggregate_arm3_ahead_on_controlled():
    emb = KeywordEmbedder()
    corpus = load_fixtures(CONTROLLED)
    arm3 = evaluate(corpus, PolicyTarget(RelevancePolicy(emb)), emb)
    arm2 = evaluate(corpus, RecencyOnlyTarget(), emb)
    assert arm3.ndcg_at_k > arm2.ndcg_at_k       # majority pro-semantic -> arm-3 ahead overall


def test_run_keystone_smoke_with_bge():
    import pytest
    pytest.importorskip("fastembed")
    from context_curator.embeddings import FastEmbedEmbedder
    emb = FastEmbedEmbedder()
    try:
        emb.embed("warmup")
    except Exception as ex:        # noqa: BLE001
        pytest.skip(f"bge model unavailable: {ex}")
    from context_curator.eval.keystone import run_keystone
    rpt = run_keystone(str(Path(e.__file__).parent / "fixtures" / "realistic"), emb)
    assert rpt.n_test >= 1 and isinstance(rpt.verdict, str)


def test_keystone_scores_three_arms_and_no_legacy_n30():
    import inspect
    from dataclasses import fields

    from context_curator.eval import keystone

    src = inspect.getsource(keystone)
    assert "n>=~30" not in src and "n≥~30" not in src
    names = {f.name for f in fields(keystone.KeystoneReport)}
    assert "arm_bm25" in names


def test_keystone_report_exposes_session_ids_aligned_with_deltas():
    from dataclasses import fields

    from context_curator.eval import keystone
    names = {f.name for f in fields(keystone.KeystoneReport)}
    assert "test_session_ids" in names
