import os
import random

from context_curator.entities import entities_match
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.learned.feasibility import prior_refetch_scores
from context_curator.onload.stickiness import refetch_counts, stickiness_rank

S = os.sep


def _brute(sets):
    return [sum(1 for j in range(i) if entities_match(sets[i], sets[j])) for i in range(len(sets))]


_PARTS = ["a", "b", "ab", "c.py", "a.py", "src", "x"]


def _rand_entity(rng):
    depth = rng.randint(0, 4)
    p = S + S.join(rng.choice(_PARTS) for _ in range(depth))
    if rng.random() < 0.1:
        p += S                                          # trailing sep: matched via rstrip()
    return p


def _rand_sets(rng, n):
    out = []
    for _ in range(n):
        r = rng.random()
        k = 0 if r < 0.2 else (1 if r < 0.85 else rng.randint(2, 3))
        out.append({_rand_entity(rng) for _ in range(k)})
    return out


def test_refetch_counts_equal_the_pairwise_definition_on_random_paths():
    rng = random.Random(0)
    for _ in range(400):
        sets = _rand_sets(rng, rng.randint(0, 40))
        assert refetch_counts(sets) == _brute(sets)


def test_refetch_counts_equal_the_eval_prior_refetch_scores():
    rng = random.Random(1)
    for t in range(50):
        sets = _rand_sets(rng, 30)
        fx = Fixture(name=f"f{t}", prompt="p", gold_keys=["k0"], recent_tools=[],
                     chunks=[FixtureChunk(key=f"k{i}", content="c", entities=sorted(s))
                             for i, s in enumerate(sets)])
        ref = prior_refetch_scores(fx)
        assert [float(c) for c in refetch_counts(sets)] == [ref[f"k{i}"] for i in range(30)]


def test_root_contains_everything_and_siblings_do_not_match():
    assert refetch_counts([{S}, {S + "a"}]) == [0, 1]
    assert refetch_counts([{S + "a"}, {S + "ab"}]) == [0, 0]
    assert refetch_counts([set(), set()]) == [0, 0]


def test_rank_orders_by_refetch_then_bm25_then_newer():
    f, g = S + "r" + S + "f.py", S + "r" + S + "g.py"
    cands = [
        ("old_f", "alpha", {f}),            # count 0
        ("g1", "nothing", {g}),             # count 0
        ("f2", "beta", {f}),                # count 1
        ("lex", "zebra zebra", set()),      # count 0, best bm25 for the prompt
        ("f3", "gamma", {f}),               # count 2  -> first
        ("tie_new", "quiet", set()),        # count 0, bm25 0, newest of the zero-bm25 ties
    ]
    assert stickiness_rank("zebra", cands) == ["f3", "f2", "lex", "tie_new", "g1", "old_f"]


def test_rank_of_nothing_is_empty():
    assert stickiness_rank("p", []) == []
