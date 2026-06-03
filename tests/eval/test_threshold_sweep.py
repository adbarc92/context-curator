from context_curator.eval.threshold_sweep import sweep_threshold


def test_sweep_picks_highest_threshold_meeting_recall_floor():
    # gold cosines for 4 fixtures (chosen off the grid values to avoid >= boundary ties).
    # micro-recall(t) = fraction of gold whose cosine >= t.
    gold_cosines = [0.75, 0.65, 0.62, 0.45]
    grid = [0.4, 0.5, 0.6, 0.7]
    # recall_floor=0.5 -> need >=50% of gold to clear; highest threshold meeting it:
    # t=0.6 -> 3/4=0.75 OK ; t=0.7 -> 1/4=0.25 < 0.5. So chosen=0.6.
    res = sweep_threshold(gold_cosines, grid=grid, recall_floor=0.5, seed=0)
    assert res.chosen == 0.6
    assert res.curve[0.4] == 1.0 and res.curve[0.7] == 0.25


def test_sweep_per_cell_ci_present_for_each_grid_point():
    res = sweep_threshold([0.8, 0.7, 0.6, 0.5], grid=[0.4, 0.6], recall_floor=0.5, seed=0)
    assert set(res.ci.keys()) == {0.4, 0.6}
    for lo, hi in res.ci.values():
        assert lo <= hi
