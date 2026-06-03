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
    assert ONLOAD_WEIGHTS.eviction_threshold == default.eviction_threshold


def test_threshold_is_a_sane_cosine_gate():
    # An untuned placeholder (re-derived in M4b) — assert only that it is a sane cosine gate,
    # not a hardcoded value that would break on legitimate tuning.
    assert 0.0 < ONLOAD_COSINE_THRESHOLD < 1.0


def test_bge_weights_sim_floor_equals_bge_threshold():
    from context_curator.policy.weights import ONLOAD_BGE_COSINE_THRESHOLD, ONLOAD_BGE_WEIGHTS
    assert ONLOAD_BGE_WEIGHTS.sim_floor == ONLOAD_BGE_COSINE_THRESHOLD


def test_bge_threshold_is_provisional_mid_band():
    from context_curator.policy.weights import ONLOAD_BGE_COSINE_THRESHOLD
    assert ONLOAD_BGE_COSINE_THRESHOLD == 0.55       # provisional; M3b tunes it


def test_bge_weights_disable_inline_reembed():
    # the curator's on-demand embed is the sole candidate-embed path; the policy must not
    # inline-reembed NULL candidates (round-3 C-3 / §8 latency bound)
    from context_curator.policy.weights import ONLOAD_BGE_WEIGHTS
    assert ONLOAD_BGE_WEIGHTS.reembed_cap == 0
