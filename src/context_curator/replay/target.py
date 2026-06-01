"""Replay decision targets (design §3.4). v1 = recency-only baseline (the §10.4 arm-2
baseline); M3 adds a semantic PolicyTarget behind the same Protocol."""
from __future__ import annotations

from typing import Protocol

from context_curator.replay.schema import Decision, SelectedChunk, TaskSignal
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens


class ReplayTarget(Protocol):
    name: str

    def decide(self, signal: TaskSignal, store: Store) -> Decision: ...


class RecencyOnlyTarget:
    name = "recency-only"

    def __init__(self, k: int = 10, token_budget: int | None = None,
                 tags: list[str] | None = None) -> None:
        self.k = k
        self.token_budget = token_budget
        self.tags = tags

    def decide(self, signal: TaskSignal, store: Store) -> Decision:
        chunks = store.query(signal.prompt, tags=self.tags, k=self.k,
                             token_budget=self.token_budget)
        selected = [
            SelectedChunk(key=c.key, score=None, tokens=estimate_tokens(c.content))
            for c in chunks
        ]
        return Decision(
            turn_index=signal.turn_index,
            subtask_id=signal.subtask_id,
            prompt_preview=signal.prompt[:80],
            selected=selected,
            total_tokens=sum(s.tokens for s in selected),
        )
