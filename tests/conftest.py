import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore
from context_curator.store.sqlite_store import SqliteStore

SCOPE_PREFIX = "proj:a"


def _memory_factory(tmp_path, allowed_prefix=None):
    return InMemoryStore(embedder=HashingEmbedder(dim=64), allowed_prefix=allowed_prefix)


def _sqlite_factory(tmp_path, allowed_prefix=None):
    return SqliteStore(db_path=str(tmp_path / "cc.db"),
                       embedder=HashingEmbedder(dim=64), allowed_prefix=allowed_prefix)


# Each factory takes (tmp_path, allowed_prefix) and returns a Store.
STORE_FACTORIES = [
    pytest.param(_memory_factory, id="memory"),
    pytest.param(_sqlite_factory, id="sqlite"),
]


@pytest.fixture(params=STORE_FACTORIES)
def store_factory(request):
    return request.param


@pytest.fixture
def store(store_factory, tmp_path):
    return store_factory(tmp_path)


@pytest.fixture
def scoped_store(store_factory, tmp_path):
    """A store scoped to SCOPE_PREFIX, for scope-enforcement contract tests."""
    return store_factory(tmp_path, allowed_prefix=SCOPE_PREFIX)
