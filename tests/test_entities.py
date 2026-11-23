import os

from context_curator.entities import (
    ENTITY_TAG_PREFIX,
    canon_path,
    entities_from_args,
    entities_from_tags,
    entities_match,
    entity_tags,
)


def test_canon_path_is_absolute_and_normalized(tmp_path):
    p = str(tmp_path / "a" / ".." / "b.py")
    assert canon_path(p) == os.path.normcase(os.path.normpath(str(tmp_path / "b.py")))


def test_entities_from_args_reads_the_three_path_keys(tmp_path):
    args = {"file_path": str(tmp_path / "f.py"), "notebook_path": str(tmp_path / "n.ipynb"),
            "path": str(tmp_path), "pattern": "*.py"}
    assert entities_from_args(args) == {canon_path(str(tmp_path / "f.py")),
                                        canon_path(str(tmp_path / "n.ipynb")),
                                        canon_path(str(tmp_path))}


def test_entities_from_args_ignores_empty_and_non_string_values():
    assert entities_from_args({"file_path": "", "path": None, "notebook_path": 3}) == set()
    assert entities_from_args({"pattern": "*.py"}) == set()
    assert entities_from_args(None) == set()


def test_match_equal_and_containment_both_ways(tmp_path):
    d, f = canon_path(str(tmp_path / "src")), canon_path(str(tmp_path / "src" / "x.py"))
    assert entities_match({f}, {f})
    assert entities_match({d}, {f}) and entities_match({f}, {d})


def test_match_is_boundary_aware_and_empty_never_matches(tmp_path):
    a, b = canon_path(str(tmp_path / "src")), canon_path(str(tmp_path / "src2" / "x.py"))
    assert not entities_match({a}, {b})
    assert not entities_match(set(), {a}) and not entities_match({a}, set())


def test_tags_round_trip(tmp_path):
    ents = {canon_path(str(tmp_path / "f.py")), canon_path(str(tmp_path / "g.py"))}
    tags = entity_tags(ents)
    assert tags == sorted(tags) and all(t.startswith(ENTITY_TAG_PREFIX) for t in tags)
    assert entities_from_tags(["read", *tags]) == ents


def test_non_mapping_args_yield_no_entities():
    for bad in (["x"], "str", 5):
        assert entities_from_args(bad) == set()
