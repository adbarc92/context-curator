from context_curator.policy.weights import ONLOAD_COSINE_THRESHOLD, ONLOAD_WEIGHTS, PolicyWeights


def test_onload_weights_sim_floor_equals_gate_threshold():
    # round-2 C1: gate floor == score floor by construction, so the admitted cosine band
    # ranks by a real recency+similarity blend (not recency-only).
    assert ONLOAD_WEIGHTS.sim_floor == ONLOAD_COSINE_THRESHOLD


def test_onload_weights_only_overrides_sim_floor():
    # everything else matches the default policy operating point
    default = PolicyWeights()
    assert ONLOAD_WEIGHTS.w_recency == default.w_recency
    assert ONLOAD_WEIGHTS.w_similarity == default.w_similarity
    assert ONLOAD_WEIGHTS.pin_bias == default.pin_bias


def test_threshold_is_provisional_placeholder_value():
    assert ONLOAD_COSINE_THRESHOLD == 0.15
