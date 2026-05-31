"""Claude Code transcript JSONL -> normalized Trace (design §3.2). ALL format-specific
knowledge is isolated here so a CC format change is contained to this file.

Real Claude Code transcript format (confirmed from live sessions):
- Records are JSONL, one JSON object per line.
- Top-level fields: type ("user"|"assistant" are the message-bearing types; others
  like "queue-operation", "ai-title", "last-prompt", "attachment" are skipped),
  isSidechain (bool), sessionId (str), message (object with role + content list).
- message.content: list of typed blocks OR a plain string.
- assistant content blocks: {"type":"text","text":"..."} and/or
  {"type":"tool_use","id":"<id>","name":"<name>","input":{...},...}.
- user content blocks (tool results): {"type":"tool_result","tool_use_id":"<id>",
  "content":"..." or [{"type":"text","text":"..."}],...}.
- isSidechain:true marks sub-agent output; these records are excluded from the main
  session trace (DESIGN §4.4).
"""
from __future__ import annotations

import json
from pathlib import Path

from context_curator.replay.schema import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    Trace,
    TraceEvent,
    UserPrompt,
)


def _content_blocks(message: dict) -> list:
    """CC message.content is either a plain string or a list of typed blocks."""
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content or []


def _is_tool_result_record(message: dict) -> bool:
    return any(b.get("type") == "tool_result" for b in _content_blocks(message))


def _text_of(blocks: list) -> str:
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def parse_transcript(path: str | Path) -> Trace:
    events: list[TraceEvent] = []
    turn = -1
    session_id = "unknown"
    seen_tool_use: set[str] = set()  # main-session tool_use ids, to drop sidechain orphans

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("isSidechain"):
            continue  # model the main session only (DESIGN §4.4)
        rec_type = rec.get("type")
        message = rec.get("message") or {}
        session_id = rec.get("sessionId", session_id)
        blocks = _content_blocks(message)

        if rec_type == "assistant":
            text = _text_of(blocks)
            if text:
                events.append(AssistantMessage(text=text))
            for b in blocks:
                if b.get("type") == "tool_use":
                    seen_tool_use.add(b["id"])
                    events.append(ToolCall(call_id=b["id"], name=b["name"],
                                           args=b.get("input") or {}))
        elif rec_type == "user":
            if _is_tool_result_record(message):
                for b in blocks:
                    if b.get("type") != "tool_result":
                        continue
                    tid = b.get("tool_use_id")
                    if tid not in seen_tool_use:
                        continue  # orphan (matching tool_use was sidechain/absent) — drop
                    content = b.get("content")
                    if isinstance(content, list):
                        content = _text_of(content)
                    events.append(ToolResult(call_id=tid, content=content or "",
                                             error=bool(b.get("is_error"))))
            else:
                turn += 1
                events.append(UserPrompt(turn_index=turn, text=_text_of(blocks)))
        # other record types (system, summary, thinking, queue-operation, etc.) are
        # skipped — forward-tolerant: unknown types produce no events

    return Trace(session_id=session_id, source="transcript", events=events)
