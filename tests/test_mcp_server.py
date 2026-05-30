import json

from context_curator.embeddings import HashingEmbedder
from context_curator.mcp_server import build_mcp, build_store_facade
from context_curator.store.sqlite_store import SqliteStore


def test_facade_exposes_all_cc_operations(tmp_path):
    store = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    facade = build_store_facade(store)
    # store
    key = facade.cc_store("shared:contracts:auth", "POST /login", tags=["auth"])
    assert key == "shared:contracts:auth"
    # retrieve
    assert facade.cc_retrieve("shared:contracts:auth")["content"] == "POST /login"
    # query
    res = facade.cc_query("auth", tags=["auth"], k=5)
    assert res and res[0]["key"] == "shared:contracts:auth"
    json.dumps(res)  # must not raise — all fields JSON-serializable
    # retrieve is also JSON-serializable
    retrieved = facade.cc_retrieve("shared:contracts:auth")
    json.dumps(retrieved)  # dict or None, must be JSON-serializable
    # list
    assert "shared:contracts:auth" in facade.cc_list("shared:contracts")
    # pin + evict
    assert facade.cc_pin("shared:contracts:auth") is True
    assert facade.cc_evict("shared:contracts:auth") is True
    assert facade.cc_retrieve("shared:contracts:auth") is None


def test_facade_retrieve_returns_none_for_missing(tmp_path):
    store = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    facade = build_store_facade(store)
    assert facade.cc_retrieve("does:not:exist") is None


def test_facade_query_returns_list_of_dicts(tmp_path):
    store = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    facade = build_store_facade(store)
    facade.cc_store("k1", "alpha", tags=["t"])
    facade.cc_store("k2", "beta", tags=["t"])
    results = facade.cc_query("anything", tags=["t"], k=10)
    assert isinstance(results, list)
    assert all(isinstance(r, dict) for r in results)
    assert {r["key"] for r in results} == {"k1", "k2"}


def test_build_mcp_registers_all_six_tools(tmp_path, monkeypatch):
    """build_mcp() must construct a FastMCP server with all six cc_* tools registered."""
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "cc.db"))
    monkeypatch.delenv("CC_ALLOWED_PREFIX", raising=False)

    server = build_mcp()

    # _tool_manager._tools is a plain dict keyed by tool name
    registered = set(server._tool_manager._tools.keys())
    expected = {"cc_store", "cc_retrieve", "cc_query", "cc_list", "cc_evict", "cc_pin"}
    assert registered == expected, f"Registered tools: {registered}"
