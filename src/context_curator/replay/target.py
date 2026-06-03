"""Replay decision targets (design §3.4). v1 = recency-only baseline (the §10.4 arm-2
baseline); M3 adds a semantic PolicyTarget behind the same Protocol."""
from __future__ import annotations

from typing import Protocol

from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.schema import Decision, SelectedChunk, TaskSignal
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens


class ReplayTarget(Protocol):
    name: str

    def decide(self, signal: TaskSignal, store: Store) -> Decision: ...


class PolicyTarget:
    """Arm-3 semantic target: drives RelevancePolicy through the replay harness.
    Single scoring pass per decide (scored -> pick)."""

    name = "semantic-policy"

    def __init__(self, policy: RelevancePolicy, k: int = 10,
                 token_budget: int | None = None, score_ndigits: int = 6) -> None:
        self._policy = policy
        self._k = k
        self._token_budget = token_budget
        self._nd = score_ndigits

    @property
    def embedder(self):
        return self._policy.embedder

    def decide(self, signal: TaskSignal, store: Store) -> Decision:
        parts = [signal.prompt]
        if signal.recent_tool_calls:                     # subtask_id NOT embedded (opaque ID)
            parts.append(" ".join(r.name for r in signal.recent_tool_calls))
        task_text = "\n".join(parts)

        candidates = store.all_live_chunks()             # FULL live set (not truncating query)
        pool = self._policy.scored(task_text, candidates)            # ONE scoring pass
        chosen = self._policy.pick(pool, self._k, self._token_budget)
        score_by_key = {c.key: s for c, s in pool}

        selected = [
            SelectedChunk(key=c.key, score=round(score_by_key[c.key], self._nd),
                          tokens=estimate_tokens(c.content))
            for c in chosen
        ]
        candidate_pool = [
            SelectedChunk(key=c.key, score=round(s, self._nd),
                          tokens=estimate_tokens(c.content))
            for c, s in pool
        ]
        return Decision(
            turn_index=signal.turn_index,
            subtask_id=signal.subtask_id,
            prompt_preview=signal.prompt[:80],
            selected=selected,
            total_tokens=sum(s.tokens for s in selected),
            candidates=candidate_pool,
        )


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
        candidates = [
            SelectedChunk(key=c.key, score=None, tokens=estimate_tokens(c.content))
            for c in store.all_live_chunks()      # full recency pool (newest-first), for nDCG
        ]
        return Decision(
            turn_index=signal.turn_index,
            subtask_id=signal.subtask_id,
            prompt_preview=signal.prompt[:80],
            selected=selected,
            total_tokens=sum(s.tokens for s in selected),
            candidates=candidates,
        )
