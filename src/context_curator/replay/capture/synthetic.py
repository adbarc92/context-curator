"""Fluent builder for deterministic synthetic traces (design §3.2)."""
from __future__ import annotations

from context_curator.replay.schema import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    Trace,
    TraceEvent,
    UserPrompt,
)


class TraceBuilder:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._events: list[TraceEvent] = []
        self._turn = -1
        self._next_call = 0
        self._last_call_id: str | None = None

    def user(self, text: str, subtask_id: str | None = None) -> "TraceBuilder":
        self._turn += 1
        self._events.append(UserPrompt(turn_index=self._turn, text=text, subtask_id=subtask_id))
        return self

    def tool(self, name: str, args: dict | None = None) -> "TraceBuilder":
        call_id = f"c{self._next_call}"
        self._next_call += 1
        self._last_call_id = call_id
        self._events.append(ToolCall(call_id=call_id, name=name, args=args or {}))
        return self

    def result(self, content: str, error: bool = False) -> "TraceBuilder":
        if self._last_call_id is None:
            raise ValueError("result() requires a preceding tool() call")
        self._events.append(ToolResult(call_id=self._last_call_id, content=content, error=error))
        self._last_call_id = None
        return self

    def assistant(self, text: str) -> "TraceBuilder":
        self._events.append(AssistantMessage(text=text))
        return self

    def build(self) -> Trace:
        return Trace(session_id=self._session_id, source="synthetic", events=list(self._events))
