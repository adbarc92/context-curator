"""Deterministic offline replay engine (design §3.5)."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable

from context_curator.embeddings import HashingEmbedder
from context_curator.replay.ingest import ingest_tool_result
from context_curator.replay.schema import (
    AssistantMessage,
    DecisionLog,
    TaskSignal,
    ToolCall,
    ToolRef,
    ToolResult,
    Trace,
    UserPrompt,
)
from context_curator.replay.target import ReplayTarget
from context_curator.store.interface import Store
from context_curator.store.memory import InMemoryStore


def _default_store_factory() -> Store:
    return InMemoryStore(embedder=HashingEmbedder(dim=256))


class ReplayEngine:
    def __init__(self, target: ReplayTarget,
                 store_factory: Callable[[], Store] = _default_store_factory,
                 recent_window: int = 5) -> None:
        self._target = target
        self._store_factory = store_factory
        self._recent_window = recent_window

    def run(self, trace: Trace) -> DecisionLog:
        store = self._store_factory()
        window: deque[ToolRef] = deque(maxlen=self._recent_window)
        calls: dict[str, ToolCall] = {}
        ordinal = 0
        decisions = []
        for event in trace.events:
            if isinstance(event, ToolCall):
                window.append(ToolRef(name=event.name, call_id=event.call_id))
                calls[event.call_id] = event
            elif isinstance(event, ToolResult):
                call = calls.get(event.call_id)
                if call is None:
                    continue  # orphan (e.g. sidechain) — never ingest into the main store (§4.4)
                ingest_tool_result(event, call, trace.session_id, ordinal, store)
                ordinal += 1
            elif isinstance(event, UserPrompt):
                signal = TaskSignal(
                    turn_index=event.turn_index,
                    prompt=event.text,
                    subtask_id=event.subtask_id,
                    recent_tool_calls=list(window),
                )
                decisions.append(self._target.decide(signal, store))
            elif isinstance(event, AssistantMessage):
                continue
        return DecisionLog(
            trace_session_id=trace.session_id,
            target_name=self._target.name,
            decisions=decisions,
        )
