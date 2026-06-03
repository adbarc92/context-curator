import math

from context_curator.embeddings import Embedder

_VOCAB = ["A", "B", "C", "D", "E", "F"]


class KeywordEmbedder(Embedder):
    """Graded bag-of-keywords embedder (design §3.2/§4): unit-normalized sum of the
    vocab-keyword basis vectors in the text, so shared-but-not-identical keyword sets
    give INTERMEDIATE cosines (e.g. 'A B C' vs 'A B C D' -> 0.866)."""

    @property
    def dim(self) -> int:
        return len(_VOCAB)

    def embed(self, text: str) -> list[float]:
        idx = {k: i for i, k in enumerate(_VOCAB)}
        vec = [0.0] * len(_VOCAB)
        for tok in text.split():
            if tok in idx:
                vec[idx[tok]] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        return vec if norm == 0.0 else [x / norm for x in vec]
