import os
import random

from context_curator.entities import entities_match, entity_tags
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.learned.feasibility import prior_refetch_scores
from context_curator.models import Chunk
from context_curator.onload.format import PER_CHUNK_CHARS, format_block
from context_curator.onload.select import stickiness_select
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


# --- stickiness_select (live selection over Store chunks) --------------------------------------

F = S + "r" + S + "f.py"


def _ch(key, content, ents=(), *, pin=False):
    return Chunk(key=key, content=content, pin=pin, tags=["read", *entity_tags(ents)])


def test_select_takes_newest_first_input_and_ranks_the_newest_sticky_copy_first():
    newest_first = [_ch("f3", "third", {F}), _ch("bash", "ls output"),
                    _ch("f2", "second", {F}), _ch("f1", "first", {F})]
    out = stickiness_select("unrelated", newest_first, k=2, token_budget=None)
    assert [c.key for c in out] == ["f3", "f2"]


def test_select_excludes_pins_and_conventions():
    newest_first = [_ch("pinned", "p", {F}, pin=True), _ch("proj:app:conventions", "c", {F}),
                    _ch("f2", "b", {F}), _ch("f1", "a", {F})]
    out = stickiness_select("x", newest_first, k=10, token_budget=None)
    assert [c.key for c in out] == ["f2", "f1"]


def test_select_budgets_on_rendered_size_so_a_big_sticky_chunk_is_injected():
    big = "y" * 40_000                                  # ~10 000 tokens raw, 1 200 chars rendered
    newest_first = [_ch("big2", big + "2", {F}), _ch("small", "z" * 40), _ch("big1", big, {F})]
    out = stickiness_select("q", newest_first, k=10, token_budget=1500)
    assert out[0].key == "big2"                         # full-size budgeting would inject nothing
    assert sum(min(len(c.content), PER_CHUNK_CHARS) for c in out) // 4 <= 1500


def test_select_drops_byte_identical_duplicates():
    newest_first = [_ch("r3", "same", {F}), _ch("r2", "same", {F}), _ch("r1", "other", {F})]
    out = stickiness_select("q", newest_first, k=10, token_budget=None)
    assert [c.key for c in out] == ["r3", "r1"]


def test_select_respects_k_and_breaks_at_the_budget():
    newest_first = [_ch(f"k{i}", f"{i}" * 80) for i in range(5)]   # ~20 tokens each, distinct
    assert len(stickiness_select("q", newest_first, k=2, token_budget=None)) == 2
    assert len(stickiness_select("q", newest_first, k=10, token_budget=30)) == 1


def test_format_block_renders_the_same_body_the_budget_counted():
    c = _ch("k", "w" * (PER_CHUNK_CHARS + 50))
    assert ("w" * PER_CHUNK_CHARS + "…") in format_block([c], title="T")


def test_rank_returns_each_key_once_even_if_candidates_repeat_a_key():
    cands = [("g", "a", {F}), ("x", "b", set()), ("g", "c", {F})]
    out = stickiness_rank("q", cands)
    assert sorted(out) == ["g", "x"] and len(out) == 2
