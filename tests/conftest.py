import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore


def _memory_factory(tmp_path):
    return InMemoryStore(embedder=HashingEmbedder(dim=64))


# Each factory takes a tmp_path (sqlite needs it; memory ignores it) and returns a Store.
STORE_FACTORIES = [
    pytest.param(_memory_factory, id="memory"),
]


@pytest.fixture(params=STORE_FACTORIES)
def store(request, tmp_path):
    return request.param(tmp_path)
