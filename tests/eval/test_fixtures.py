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


def test_load_controlled_corpus():
    from pathlib import Path

    import context_curator.eval as e

    fixtures = load_fixtures(str(Path(e.__file__).parent / "fixtures" / "controlled"))
    assert len(fixtures) >= 4
    assert any(f.name == "adversarial-arm2-wins" for f in fixtures)
