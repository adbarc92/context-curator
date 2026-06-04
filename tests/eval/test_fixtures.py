import pytest
from pydantic import ValidationError

from context_curator.eval.fixtures import Fixture, FixtureChunk, load_fixtures


def test_fixture_roundtrips():
    fx = Fixture(name="f", chunks=[FixtureChunk(key="a", content="A B")],
                 prompt="A B", gold_keys=["a"], split="train")
    assert Fixture(**fx.model_dump()) == fx


def test_split_rejects_typo():
    with pytest.raises(ValidationError):
        Fixture(name="f", chunks=[], prompt="p", gold_keys=[], split="tset")


def test_no_pin_field():
    with pytest.raises(ValidationError):
        FixtureChunk(key="a", content="x", pin=True)   # pin forbidden (M3)


def test_load_fixtures_skips_underscore_metadata(tmp_path):
    import json

    (tmp_path / "good.json").write_text(json.dumps(
        {"name": "g", "chunks": [{"key": "k", "content": "c"}],
         "prompt": "p", "gold_keys": ["k"], "split": "test"}), encoding="utf-8")
    # a co-located metadata file (e.g. the frozen cosine matrix) must NOT be parsed as a Fixture
    (tmp_path / "_bge_cosines.json").write_text(json.dumps({"meta": 1}), encoding="utf-8")
    fixtures = load_fixtures(str(tmp_path))
    assert [f.name for f in fixtures] == ["g"]


def test_load_controlled_corpus():
    from pathlib import Path

    import context_curator.eval as e

    fixtures = load_fixtures(str(Path(e.__file__).parent / "fixtures" / "controlled"))
    assert len(fixtures) >= 4
    assert any(f.name == "adversarial-arm2-wins" for f in fixtures)


def test_fixture_carries_session_id():
    fx = Fixture(name="f", chunks=[FixtureChunk(key="a", content="A")],
                 prompt="p", gold_keys=["a"], split="test", session_id="sess-1")
    assert fx.session_id == "sess-1"


def test_fixture_session_id_defaults_none():
    fx = Fixture(name="f", chunks=[FixtureChunk(key="a", content="A")],
                 prompt="p", gold_keys=["a"], split="test")
    assert fx.session_id is None
