import statistics

import pytest

pytest.importorskip("fastembed")                            # only runs with the `embed` extra

from context_curator.embeddings import FastEmbedEmbedder  # noqa: E402
from context_curator.policy.relevance import _cosine  # noqa: E402
from context_curator.policy.weights import ONLOAD_BGE_COSINE_THRESHOLD  # noqa: E402

_RELEVANT = [
    ("how do I authenticate a user", "the login flow validates credentials and issues a token"),
    ("parse a CSV file in python", "use the csv module to read comma-separated rows"),
    ("write data to sqlite", "open a sqlite connection and execute an insert statement"),
    ("handle an HTTP timeout", "set a request timeout and catch the timeout exception"),
    ("sort a list of numbers", "call sorted() to order the integers ascending"),
    ("format a date string", "use strftime to render the datetime as ISO 8601"),
    ("read an environment variable", "os.environ.get returns the configured value"),
    ("compress a directory", "create a zip archive of the folder contents"),
]
_UNRELATED = [
    ("how do I authenticate a user", "the recipe simmers tomatoes with basil for an hour"),
    ("parse a CSV file in python", "the hiking trail climbs steeply past the waterfall"),
    ("write data to sqlite", "she painted the fence a bright shade of blue"),
    ("handle an HTTP timeout", "the orchestra tuned their instruments before the concert"),
    ("sort a list of numbers", "the cat napped in the warm afternoon sun"),
    ("format a date string", "the bakery sells fresh sourdough every morning"),
    ("read an environment variable", "the river froze solid during the cold snap"),
    ("compress a directory", "he planted tulip bulbs along the garden path"),
]


def _cos(emb, a, b):
    return _cosine(emb.embed(a), emb.embed(b))


def test_bge_separates_relevant_from_unrelated_around_threshold():
    emb = FastEmbedEmbedder()
    rel = [_cos(emb, q, d) for q, d in _RELEVANT]
    unrel = [_cos(emb, q, d) for q, d in _UNRELATED]
    mrel, munrel = statistics.mean(rel), statistics.mean(unrel)
    assert mrel - munrel > 0.1                               # populations separate
    assert munrel < ONLOAD_BGE_COSINE_THRESHOLD < mrel       # 0.55 sits between the means
