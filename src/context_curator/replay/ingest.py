"""Harness-local capture-during-replay seam (design §3.3). M2-alignable.

`call` is REQUIRED: the engine only calls this when the matching tool_use is present
in the MAIN session, so raw sub-agent output cannot leak into the main store (§4.4)."""
from __future__ import annotations

from context_curator.replay.schema import ToolCall, ToolResult
from context_curator.store.interface import Store


def ingest_tool_result(result: ToolResult, call: ToolCall,
                       session_id: str, ordinal: int, store: Store) -> None:
    if result.error:
        return
    # `ordinal` guarantees key uniqueness even if call_id repeats (else ON CONFLICT overwrite).
    key = f"session:{session_id}:tool:{ordinal:06d}:{result.call_id}"
    store.store(
        key,
        result.content,
        tags=[call.name.lower()],     # lowercased for case-insensitive tag filtering
        source=f"tool:{call.name}",   # canonical case for the §9 poisoning audit
        ttl_s=None,                   # replay candidates never expire mid-session (§3.3)
    )
