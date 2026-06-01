"""Harness-local capture-during-replay seam (design §3.3). M2-alignable.

`call` is REQUIRED: the engine only calls this when the matching tool_use is present
in the MAIN session, so raw sub-agent output cannot leak into the main store (§4.4)."""
from __future__ import annotations

from context_curator.capture.tool_result import capture_tool_result
from context_curator.replay.schema import ToolCall, ToolResult
from context_curator.store.interface import Store


def ingest_tool_result(result: ToolResult, call: ToolCall,
                       session_id: str, ordinal: int, store: Store) -> None:
    capture_tool_result(store, session_id=session_id, tool_name=call.name,
                        content=result.content, error=result.error,
                        call_id=result.call_id, ordinal=ordinal, ttl_s=None, max_content=None)
