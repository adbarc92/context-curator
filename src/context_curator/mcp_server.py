"""context-curator-mcp — thin MCP adapter over a Store (DESIGN.md §4.1).

`build_store_facade` returns a plain object whose methods mirror the cc_* tools;
it is the unit-testable seam. `build_mcp` registers those methods as MCP tools.
The facade returns JSON-serializable dicts (chunks as dicts), since MCP tool
results cross a process boundary."""
from __future__ import annotations

import os

from context_curator.embeddings import Embedder, HashingEmbedder
from context_curator.store.interface import Store
from context_curator.store.sqlite_store import SqliteStore


class _StoreFacade:
    def __init__(self, store: Store) -> None:
        self._store = store

    def cc_store(
        self,
        key: str,
        content: str,
        tags: list[str] | None = None,
        ttl_s: int | None = 86400,
        pin: bool = False,
        source: str = "tool:read",
        provenance: str | None = None,
    ) -> str:
        """Write/offload a chunk into the store. Returns the key."""
        return self._store.store(
            key, content, tags=tags, ttl_s=ttl_s, pin=pin, source=source, provenance=provenance
        )

    def cc_retrieve(self, key: str) -> dict | None:
        """Exact fetch by key. Returns the chunk as a dict, or None if absent/expired."""
        c = self._store.retrieve(key)
        return c.model_dump() if c is not None else None

    def cc_query(
        self,
        task_context: str,
        tags: list[str] | None = None,
        k: int = 10,
        token_budget: int | None = None,
    ) -> list[dict]:
        """Ranked retrieval (recency v1). Returns a list of chunk dicts."""
        return [
            c.model_dump()
            for c in self._store.query(task_context, tags=tags, k=k, token_budget=token_budget)
        ]

    def cc_list(self, prefix: str) -> list[str]:
        """Enumerate keys under prefix. Returns a list of key strings."""
        return self._store.list(prefix)

    def cc_evict(self, key: str) -> bool:
        """Remove a chunk from the store. Returns True if it existed."""
        return self._store.evict(key)

    def cc_pin(self, key: str) -> bool:
        """Mark a chunk pinned (never expires). Returns True if the chunk exists."""
        return self._store.pin(key)


def build_store_facade(store: Store) -> _StoreFacade:
    """Return a JSON-serializing facade over `store`. Unit-testable without MCP."""
    return _StoreFacade(store)


def build_default_store() -> Store:
    """Construct the store from environment variables (used by build_mcp)."""
    db_path = os.environ.get("CC_DB_PATH", "context-curator.db")
    allowed_prefix = os.environ.get("CC_ALLOWED_PREFIX") or None
    embedder: Embedder = HashingEmbedder(dim=256)
    return SqliteStore(db_path=db_path, embedder=embedder, allowed_prefix=allowed_prefix)


def build_mcp():
    """Register the facade methods as MCP tools and return the FastMCP server.

    Uses `mcp.tool(name=<tool_name>)(fn)` to register pre-defined facade methods
    with explicit cc_* names. Confirmed against mcp 1.27.2 (FastMCP.tool signature:
    `(name: str | None = None, ...) -> Callable[[AnyFunction], AnyFunction]`).
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("context-curator-mcp")
    facade = build_store_facade(build_default_store())

    mcp.tool(name="cc_store")(facade.cc_store)
    mcp.tool(name="cc_retrieve")(facade.cc_retrieve)
    mcp.tool(name="cc_query")(facade.cc_query)
    mcp.tool(name="cc_list")(facade.cc_list)
    mcp.tool(name="cc_evict")(facade.cc_evict)
    mcp.tool(name="cc_pin")(facade.cc_pin)
    return mcp


def main() -> None:
    build_mcp().run()


if __name__ == "__main__":
    main()
