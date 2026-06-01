"""PostToolUse capture hook (design §3.4). Field names pinned against a real payload."""
from __future__ import annotations

import json

from context_curator.capture.file_ledger import capture_file_write
from context_curator.capture.tool_result import CAPTURE_MAX_CONTENT, capture_tool_result
from context_curator.guard.config import CAPTURE_TTL_S
from context_curator.hooks._io import HookResult, run_hook
from context_curator.store.interface import Store

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}


def handle(event: dict, store: Store) -> HookResult:
    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    session_id = event.get("session_id", "")
    call_id = event.get("call_id")

    if tool_name in _WRITE_TOOLS and tool_input.get("file_path"):
        capture_file_write(store, session_id=session_id, tool_name=tool_name,
                           path=tool_input["file_path"], ttl_s=CAPTURE_TTL_S)

    resp = event.get("tool_response")
    if resp is not None:
        content = resp if isinstance(resp, str) else json.dumps(resp, sort_keys=True)
        capture_tool_result(store, session_id=session_id, tool_name=tool_name or "tool",
                            content=content, call_id=call_id, ordinal=None,
                            ttl_s=CAPTURE_TTL_S, max_content=CAPTURE_MAX_CONTENT)
    return HookResult(0)


def main() -> None:
    run_hook(handle, needs_store=True)


if __name__ == "__main__":
    main()
