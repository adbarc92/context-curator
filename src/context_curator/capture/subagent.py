"""Capture a subagent's final summary text (design §3.2 / I1). The structured
{summary, contracts_touched, ...} schema is NOT in the hook payload — the hook
extracts final-message text and optional fenced JSON; this function takes primitives."""
from __future__ import annotations

from context_curator.store.interface import Store


def capture_subagent_summary(store: Store, *, subagent_id: str, summary: str,
                             contracts_touched: list[str] | None = None,
                             ttl_s: int | None = None) -> str | None:
    if not summary:
        return None
    tags = ["exploration", *(contracts_touched or [])]
    key = f"shared:exploration:{subagent_id}"
    store.store(key, summary, tags=tags, source="subagent:explore",
                provenance=subagent_id or "unknown-subagent", ttl_s=ttl_s)
    return key
