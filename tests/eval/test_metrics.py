import math

from context_curator.eval.metrics import ndcg_at_k, precision_at_k, recall_at_k


def test_perfect_ranking():
    assert precision_at_k(["a", "b"], {"a", "b"}, 2) == 1.0
    assert recall_at_k(["a", "b"], {"a", "b"}, 2) == 1.0
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 2) == 1.0


def test_gold_absent():
    assert precision_at_k(["x", "y"], {"a"}, 2) == 0.0
    assert recall_at_k(["x", "y"], {"a"}, 2) == 0.0
    assert ndcg_at_k(["x", "y"], {"a"}, 2) == 0.0


def test_precision_uses_min_k_len():
    # 3 chunks all gold, k=10 -> 3/min(10,3) = 1.0 (not 3/10)
    assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 10) == 1.0


def test_empty_ranked():
    assert precision_at_k([], {"a"}, 5) == 0.0
    assert ndcg_at_k([], {"a"}, 5) == 0.0


def test_ndcg_golden_value():
    # gold at ranks 0 and 2 of ["g","x","g2","y"]; |gold|=2
    ranked, gold = ["g", "x", "g2", "y"], {"g", "g2"}
    dcg = 1 / math.log2(2) + 1 / math.log2(4)         # ranks 0 and 2
    idcg = 1 / math.log2(2) + 1 / math.log2(3)         # ideal: ranks 0 and 1
    assert math.isclose(ndcg_at_k(ranked, gold, 4), dcg / idcg, rel_tol=1e-9)


def test_single_gold_at_rank1_is_one_over_log2_3():
    # one gold at rank 1 -> nDCG = (1/log2(3)) / 1
    assert math.isclose(ndcg_at_k(["x", "g"], {"g"}, 3), 1 / math.log2(3), rel_tol=1e-9)
