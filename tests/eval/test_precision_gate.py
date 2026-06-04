from context_curator.eval.precision_gate import precision_gate


def test_floor_below_three_sessions_is_harness_only():
    g = precision_gate(lo=0.2, hi=0.25, n_sessions=2, mei=0.10)
    assert g.status == "harness-only" and g.needed_n is None


def test_wide_ci_is_underpowered_with_needed_n():
    # width 0.30 > MEI 0.10 -> underpowered; needed_n = ceil(5 * (0.30/0.10)^2) = 45
    g = precision_gate(lo=0.0, hi=0.30, n_sessions=5, mei=0.10)
    assert g.status == "inconclusive-underpowered" and g.needed_n == 45


def test_tight_ci_passes_to_verdict():
    g = precision_gate(lo=0.12, hi=0.20, n_sessions=10, mei=0.10)   # width 0.08 <= MEI
    assert g.status == "verdict" and g.needed_n is None
