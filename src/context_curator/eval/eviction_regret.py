"""Cross-turn eviction-regret metric (design §10.2, reframed §3.1): old-gold re-selection recall
over a multi-turn accumulated pool. At the turn an OLD (age >= lag) chunk is needed again, did arm's
top-k re-surface it? Memoryless re-selection from a persist-all store IS the CLI architecture (no
per-chunk eviction; DESIGN §4.2/§11). Deterministic; reuses the replay targets for their ranking."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from context_curator.eval.fixtures import FixtureChunk
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import PolicyTarget
from context_curator.store.memory import InMemoryStore


class SessionTurn(BaseModel):
    prompt: str
    new_chunks: list[FixtureChunk] = []      # chunks introduced this turn
    needed_keys: list[str] = []              # available chunks genuinely required this turn


class Session(BaseModel):
    name: str
    turns: list[SessionTurn]


@dataclass
class RegretReport:
    rate: float | None                       # regret / old_need, or None when no old need-events
    regret_events: int
    old_need_events: int
    arm: str


def _validate(sessions: list[Session]) -> None:
    """Every needed_key must be introduced by some turn; no key introduced more than once."""
    for s in sessions:
        introduced: set[str] = set()
        for turn in s.turns:
            for c in turn.new_chunks:
                if c.key in introduced:
                    raise ValueError(f"session {s.name}: key {c.key!r} introduced more than once")
                introduced.add(c.key)
        for turn in s.turns:
            for key in turn.needed_keys:
                if key not in introduced:
                    raise ValueError(f"session {s.name}: needed_key {key!r} never introduced")


def eviction_regret(sessions: list[Session], target, embedder, *, k: int = 5,
                    lag: int = 2) -> RegretReport:
    """Micro-averaged old-gold re-selection regret across sessions. A regret event = an available
    chunk with age >= lag that is needed at turn T but not in the arm's top-k ranking at T."""
    _validate(sessions)
    if isinstance(target, PolicyTarget):
        assert target.embedder is embedder, "store and policy embedders must be identical"
    regret = 0
    old_need = 0
    for s in sessions:
        available: list[FixtureChunk] = []
        introduced_turn: dict[str, int] = {}
        for t_idx, turn in enumerate(s.turns):
            for c in turn.new_chunks:
                available.append(c)
                introduced_turn[c.key] = t_idx
            store = InMemoryStore(embedder=embedder)
            for c in available:                       # chronological -> recency well-defined
                store.store(c.key, c.content, tags=c.tags, ttl_s=None)
            decision = target.decide(
                TaskSignal(turn_index=t_idx, prompt=turn.prompt, subtask_id=None,
                           recent_tool_calls=[]),
                store,
            )
            surfaced = {sc.key for sc in decision.candidates[:k]}
            for key in turn.needed_keys:
                if key not in introduced_turn:        # not yet available at this turn
                    continue
                if t_idx - introduced_turn[key] >= lag:
                    old_need += 1
                    if key not in surfaced:
                        regret += 1
    rate = regret / old_need if old_need else None
    return RegretReport(rate, regret, old_need, target.name)
