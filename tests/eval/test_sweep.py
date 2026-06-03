from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.sweep import DEFAULT_GRID, grid_sweep
from tests.eval.conftest import KeywordEmbedder


def _corpus():
    # 3 pro-semantic fixtures (gold old+match, distractor new+nomatch)
    return [Fixture(name=f"sw{i}", prompt="A B C D", gold_keys=["g"], split="train",
                    chunks=[FixtureChunk(key="g", content="A B C D"),
                            FixtureChunk(key="d", content="E F")]) for i in range(3)]


def test_grid_is_w_similarity_only():
    assert all(set(cell) == {"w_similarity", "w_recency"} for cell in DEFAULT_GRID)
    assert len(DEFAULT_GRID) == 5


def test_sweep_deterministic_and_populated():
    emb = KeywordEmbedder()
    a = grid_sweep(_corpus(), emb)
    b = grid_sweep(_corpus(), emb)
    assert a.best == b.best
    assert len(a.top_cells) == len(DEFAULT_GRID)
    # higher w_similarity helps on a pro-semantic corpus -> best is not the lowest-sim cell
    assert a.best.w_similarity >= 0.5
