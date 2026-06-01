"""Deterministic who-WROTE-what ledger (design §3.2). Write tools only; Read excluded."""
from __future__ import annotations

from context_curator.store.interface import Store


def capture_file_write(store: Store, *, session_id: str, tool_name: str, path: str,
                       ttl_s: int | None = None) -> str:
    key = f"shared:file_ledger:{path}"
    store.store(key, f"{tool_name} wrote {path}", tags=["file-touch"],
                source="file-ledger", provenance=session_id or "unknown-session", ttl_s=ttl_s)
    return key
