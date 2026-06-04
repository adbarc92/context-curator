"""The committed powered corpus self-certifies as FAIR and adequately SIZED (design §8).
Runs in CI WITHOUT bge — the audit is positional/tag-only. See the verdict of record:
docs/superpowers/keystone-powered.md."""
from pathlib import Path

import context_curator.eval as e
from context_curator.eval.corpus_audit import audit_corpus
from context_curator.eval.fixtures import load_fixtures

POWERED = str(Path(e.__file__).parent / "fixtures" / "powered")


def test_powered_corpus_is_fair_and_sized():
    fx = load_fixtures(POWERED)
    test = [f for f in fx if f.split == "test"]
    assert len(test) >= 25                   # 90%-power target for MEI=0.10 (n_test=26 achieved)
    assert audit_corpus(fx).ok is True       # self-certifies as FAIR (gold mixed across thirds)
    for f in fx:
        assert len(f.gold_keys) >= 3                                          # recall resolution
        assert sum(1 for c in f.chunks if "hard_neg" in c.tags) >= 2          # tempts BM25
