from context_curator.embeddings import Embedder
from context_curator.models import Chunk
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights


class FakeEmbedder(Embedder):
    """Deterministic 3-dim embedder keyed by a leading token, for exact scoring."""
    _VECS = {"auth": [1.0, 0.0, 0.0], "csv": [0.0, 1.0, 0.0], "far": [0.0, 0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return self._VECS.get(text.split()[0], [0.0, 0.0, 0.0])


def _chunk(key, topic, *, pin=False, tags=None, emb=None):
    return Chunk(key=key, content=f"{topic} content", tags=tags or [], pin=pin,
                 embedding=emb if emb is not None else FakeEmbedder().embed(topic))


def _policy(**overrides):
    return RelevancePolicy(FakeEmbedder(), PolicyWeights(**overrides))


def test_semantically_near_outranks_far():
    cands = [_chunk("near", "auth"), _chunk("far", "far")]   # newest-first
    ranked = _policy().scored("auth query", cands)
    assert ranked[0][0].key == "near"


def test_pin_always_wins_and_never_offloaded():
    cands = [_chunk("relevant", "auth"), _chunk("pinned", "far", pin=True)]
    ranked = _policy().scored("auth query", cands)
    assert ranked[0][0].key == "pinned"                      # pin_bias dominates
    assert _policy().offload_keys(ranked) == []              # a pin is never offloaded


def test_recency_decay_differentiates_when_similarity_equal():
    # both "far" (similarity 0); newer (index 0) must outrank older (index 1)
    cands = [_chunk("new", "far"), _chunk("old", "far")]
    ranked = _policy().scored("auth query", cands)
    assert [c.key for c, _ in ranked] == ["new", "old"]


def test_incoming_index_tiebreak_on_exact_tie():
    # w_recency=0 + equal similarity (both "far", sim 0) => exact score tie => index breaks it
    cands = [_chunk("first", "far"), _chunk("second", "far")]
    ranked = _policy(w_recency=0.0).scored("auth query", cands)
    assert [c.key for c, _ in ranked] == ["first", "second"]


def test_recency_decay_size_stable():
    p = _policy()
    small = p.scored("far q", [_chunk("a", "far"), _chunk("b", "far")])
    big = p.scored("far q", [_chunk(f"k{i}", "far") for i in range(50)])
    # rank-0 score identical regardless of N (decay independent of pool size)
    assert abs(small[0][1] - big[0][1]) < 1e-9


def test_similarity_affine_floor():
    import math
    # cosine exactly at sim_floor -> similarity 0; cosine 1.0 -> similarity 1.0
    p = _policy(w_recency=0.0, w_similarity=1.0, sim_floor=0.5)
    # exact unit vector at 60° from [1,0,0]: cos = 0.5 exactly
    at_floor = _chunk("f", "x", emb=[0.5, math.sqrt(0.75), 0.0])
    perfect = _chunk("p", "x", emb=[1.0, 0.0, 0.0])      # cos = 1.0
    ranked = dict((c.key, s) for c, s in p.scored("auth q", [perfect, at_floor]))
    assert abs(ranked["f"]) < 1e-9
    assert abs(ranked["p"] - 1.0) < 1e-9


def test_dim_mismatch_reembed_newest_first_under_cap():
    # two dim-mismatched candidates (2-dim stored vs 3-dim active), reembed_cap=1:
    # the NEWEST (index 0) is re-embedded; the older scores similarity 0.
    # NOTE: chunks need distinct content so the sha1 cache doesn't short-circuit the cap.
    p = _policy(reembed_cap=1, w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    newest = _chunk("newest", "auth", emb=[0.1, 0.2])    # wrong dim -> reembed -> 'auth'
    oldest = _chunk("oldest", "csv", emb=[0.3, 0.4])     # wrong dim, over cap -> sim 0
    ranked = dict((c.key, s) for c, s in p.scored("auth q", [newest, oldest]))
    assert ranked["newest"] > 0.0
    assert ranked["oldest"] == 0.0


def test_select_offload_subthreshold_nonpinned():
    cands = [_chunk("keep", "auth"), _chunk("drop", "far"), _chunk("pinkeep", "far", pin=True)]
    keys = _policy(eviction_threshold=0.5).select_offload("auth q", cands)
    assert "drop" in keys and "keep" not in keys and "pinkeep" not in keys


def test_pick_respects_k_and_budget_break():
    cands = [_chunk(f"k{i}", "auth", emb=[1.0, 0.0, 0.0]) for i in range(5)]
    cands = [c.model_copy(update={"content": "x" * 100}) for c in cands]  # ~25 tokens each
    pairs = _policy().scored("auth q", cands)
    assert len(_policy().pick(pairs, k=2)) == 2                       # k cap
    assert len(_policy().pick(pairs, k=10, token_budget=30)) == 1     # first-fit break


def test_sim_floor_one_does_not_crash():
    # sim_floor=1.0 is a degenerate sweep value; must not ZeroDivisionError
    cands = [_chunk("a", "auth"), _chunk("b", "far")]
    ranked = _policy(sim_floor=1.0).scored("auth query", cands)   # no exception
    assert len(ranked) == 2


def test_similarity_upper_clamped():
    # a non-unit-normalized stored embedding (cosine could exceed 1 pre-clamp) -> sim <= 1
    p = _policy(w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    big = _chunk("big", "x", emb=[5.0, 0.0, 0.0])   # non-unit vec; cos with [1,0,0] = 1.0
    ranked = dict((c.key, s) for c, s in p.scored("auth q", [big]))
    assert ranked["big"] <= 1.0


def test_scored_with_similarity_exposes_raw_cosine():
    p = _policy(w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    perfect = _chunk("p", "auth", emb=[1.0, 0.0, 0.0])     # cos with "auth" task = 1.0
    triples = p.scored_with_similarity("auth q", [perfect])
    _c, _score, cos = triples[0]
    assert abs(cos - 1.0) < 1e-9


def test_scored_with_similarity_zero_cosine_when_no_embedding():
    # reembed_cap=0 + dim-mismatched stored emb -> emb None -> cos 0.0 (round-2 I4)
    p = _policy(reembed_cap=0, w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    mism = _chunk("m", "auth", emb=[0.1, 0.2])             # wrong dim, over cap -> None
    _c, _score, cos = p.scored_with_similarity("auth q", [mism])[0]
    assert cos == 0.0


def test_scored_with_similarity_raw_cosine_is_pre_rescale():
    import math
    # 60deg from [1,0,0] -> raw cosine 0.5 EXACTLY. Even with sim_floor=0.5 (which rescales
    # this chunk's similarity to 0), scored_with_similarity must report the RAW 0.5 — that's
    # the value the onload gate thresholds on, not the rescaled sim.
    p = _policy(w_recency=0.0, w_similarity=1.0, sim_floor=0.5)
    at_floor = _chunk("f", "x", emb=[0.5, math.sqrt(0.75), 0.0])
    _c, score, cos = p.scored_with_similarity("auth q", [at_floor])[0]
    assert abs(cos - 0.5) < 1e-9        # RAW cosine exposed
    assert abs(score) < 1e-9            # rescaled sim at the floor is 0 -> score 0


def test_scored_delegates_and_threads_query_tags():
    # round-3 C2: scored() must keep passing query_tags through the delegate, else the
    # tag term silently zeroes.
    p = _policy(w_tag=1.0)
    cands = [_chunk("tagged", "far", tags=["topic"])]
    with_tags = dict((c.key, s) for c, s in p.scored("far q", cands, query_tags=["topic"]))
    without = dict((c.key, s) for c, s in p.scored("far q", cands))
    assert with_tags["tagged"] > without["tagged"]
