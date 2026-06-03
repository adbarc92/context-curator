from context_curator.eval.stats import bootstrap_ci


def test_all_positive_excludes_zero():
    lo, hi = bootstrap_ci([0.4, 0.5, 0.45, 0.5], seed=0)
    assert lo > 0.0


def test_straddling_includes_zero():
    lo, hi = bootstrap_ci([0.4, -0.4, 0.3, -0.3], seed=0)
    assert lo < 0.0 < hi


def test_deterministic_for_fixed_seed():
    a = bootstrap_ci([0.1, 0.2, -0.1, 0.05], seed=7)
    b = bootstrap_ci([0.1, 0.2, -0.1, 0.05], seed=7)
    assert a == b


def test_empty_returns_zero_interval():
    assert bootstrap_ci([], seed=0) == (0.0, 0.0)
