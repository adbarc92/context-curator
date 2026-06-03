from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.gold_judge import drop_rate_by_bm25_tercile, judge_corpus


class _StubJudge:
    """Deterministic judge: a gold key is 'relevant' unless its content contains 'WRONG'."""

    def is_relevant(self, prompt, chunk_content):
        return "WRONG" not in chunk_content


def test_judge_drops_flat_wrong_gold():
    good = Fixture(name="good", chunks=[FixtureChunk(key="g", content="relevant answer")],
                   prompt="p", gold_keys=["g"], split="test")
    bad = Fixture(name="bad", chunks=[FixtureChunk(key="g", content="WRONG unrelated")],
                  prompt="p", gold_keys=["g"], split="test")
    kept, dropped = judge_corpus([good, bad], _StubJudge())
    assert [f.name for f in kept] == ["good"]
    assert [f.name for f in dropped] == ["bad"]


def test_drop_rate_by_bm25_tercile_flags_bias():
    # dropped fixtures' BM25 recalls high; kept low -> judge stripped BM25-favorable -> bias signal
    dropped_recall = [0.9, 0.8, 1.0]
    kept_recall = [0.1, 0.0, 0.2]
    biased = drop_rate_by_bm25_tercile(kept_recall, dropped_recall)
    assert biased["high_tercile_drop_rate"] > biased["low_tercile_drop_rate"]
