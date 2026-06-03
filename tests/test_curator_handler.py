from context_curator.curator import handler
from context_curator.embeddings import Embedder, NullEmbedder
from context_curator.store.sqlite_store import SqliteStore


class _Emb(Embedder):
    _V = {"auth": [1.0, 0.0, 0.0], "far": [0.0, 0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 3

    def embed(self, text):
        return self._V.get(text.split()[0], [0.0, 0.0, 0.0])


def _store(tmp_path):                                   # store rows; embeddings via _Emb
    return SqliteStore(db_path=str(tmp_path / "h.db"), embedder=_Emb())


def test_dark_flag_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(handler.config, "CURATOR_ONLOAD_ENABLED", False)
    s = _store(tmp_path)
    s.store("session:x:tool:c", "auth content")
    out = handler.handle_onload(s, _Emb(), {"prompt": "auth q", "k": 10, "token_budget": None})
    assert out == {"ok": True, "keys": []}


def test_flag_on_selects_relevant_excludes_durable(tmp_path, monkeypatch):
    monkeypatch.setattr(handler.config, "CURATOR_ONLOAD_ENABLED", True)
    monkeypatch.setattr(handler, "ONLOAD_BGE_COSINE_THRESHOLD", 0.5)   # _Emb cosine 1.0 vs 0.0
    s = _store(tmp_path)
    s.store("session:x:tool:rel", "auth content")
    s.store("session:x:tool:off", "far content")
    s.store("pinnedkey", "auth content", pin=True)
    s.store("proj:app:conventions", "auth content")
    keys = handler.handle_onload(s, _Emb(),
                                 {"prompt": "auth q", "k": 10, "token_budget": None})["keys"]
    assert "session:x:tool:rel" in keys
    assert "session:x:tool:off" not in keys
    assert "pinnedkey" not in keys and "proj:app:conventions" not in keys


def test_on_demand_embeds_null_chunk_in_memory_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(handler.config, "CURATOR_ONLOAD_ENABLED", True)
    monkeypatch.setattr(handler, "ONLOAD_BGE_COSINE_THRESHOLD", 0.5)
    s = SqliteStore(db_path=str(tmp_path / "h.db"), embedder=NullEmbedder())
    s.store("session:x:tool:fresh", "auth content")     # embedding=NULL (NullEmbedder)
    assert s.retrieve("session:x:tool:fresh").embedding is None
    keys = handler.handle_onload(s, _Emb(),
                                 {"prompt": "auth q", "k": 10, "token_budget": None})["keys"]
    assert "session:x:tool:fresh" in keys               # embedded on-demand IN MEMORY -> selected
    # handler wrote NOTHING (round-3 C-1)
    assert s.retrieve("session:x:tool:fresh").embedding is None
