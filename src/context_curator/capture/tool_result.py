"""Canonical 'tool result -> chunk' mapping (design §3.2). Used by live hooks AND
offline replay (via replay/ingest.py), so there is one source of truth."""
from __future__ import annotations

from hashlib import sha1

from context_curator.store.interface import Store

CAPTURE_MAX_CONTENT = 32_768  # bytes; larger live content is head-truncated + marked


def capture_tool_result(store: Store, *, session_id: str, tool_name: str, content: str,
                        error: bool = False, call_id: str | None = None,
                        ordinal: int | None = None, ttl_s: int | None = None,
                        max_content: int | None = None) -> str | None:
    """Returns the written key, or None if skipped. `max_content` truncates oversized
    content; it is None on the replay path so replay stays structurally byte-identical."""
    if error:
        return None
    if ordinal is not None:
        suffix = f"{ordinal:06d}:{call_id}"
    elif call_id:
        suffix = call_id
    else:
        suffix = sha1(content.encode("utf-8")).hexdigest()[:12]
    if max_content is not None and len(content) > max_content:
        content = content[:max_content] + "\n…[truncated]"
    key = f"session:{session_id}:tool:{suffix}"
    store.store(key, content, tags=[tool_name.lower()], source=f"tool:{tool_name}", ttl_s=ttl_s)
    return key
