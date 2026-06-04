import math

import pytest

from context_curator.eval.stats import bootstrap_ci, cluster_bootstrap_ci


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


def test_cluster_ci_wider_than_iid_when_clustered():
    # two tight clusters far apart -> clustered CI must be wider than iid on the same values
    deltas = [0.0, 0.01, -0.01, 0.40, 0.41, 0.39]
    clusters = ["s1", "s1", "s1", "s2", "s2", "s2"]
    lo_c, hi_c = cluster_bootstrap_ci(deltas, clusters, seed=0)
    lo_i, hi_i = bootstrap_ci(deltas, seed=0)
    assert (hi_c - lo_c) > (hi_i - lo_i)


def test_cluster_ci_single_session_is_width_of_ignorance():
    lo, hi = cluster_bootstrap_ci([0.1, 0.2], ["s1", "s1"], seed=0)
    assert lo == -math.inf and hi == math.inf


def test_cluster_ci_length_mismatch_raises():
    with pytest.raises(ValueError):
        cluster_bootstrap_ci([0.1, 0.2], ["s1"], seed=0)
