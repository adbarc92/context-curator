# M5 — Cross-Turn Eviction-Regret Metric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic cross-turn eviction-regret metric — old-gold re-selection recall@k over a multi-turn accumulated pool — to the eval harness, validated on hand-built fixtures.

**Architecture:** One standalone module `eval/eviction_regret.py` with a pydantic `Session`/`SessionTurn` schema, a `RegretReport` dataclass, and an `eviction_regret(...)` evaluator that replays each session turn-by-turn through the **existing** replay targets (`RecencyOnlyTarget`/`Bm25Target`/`PolicyTarget`), reading `decision.candidates[:k]` to count regret = (old needed chunk not surfaced)/(old needed). No production code changes.

**Tech Stack:** Python + UV; pydantic v2; pytest; ruff (`E,F,I,UP,B`, ≤100). Reuses `InMemoryStore`, the replay targets, `TaskSignal`, `FixtureChunk`, and the `KeywordEmbedder` test double — all on `main`.

**Spec:** `docs/superpowers/specs/2026-06-04-m5-eviction-regret-design.md` (hardened through 3 critique rounds).

**Branch:** `feat/m5-eviction-regret` (already checked out, off `main`).

---

## Conventions
- Run everything via `uv run`; ignore the `VIRTUAL_ENV` mismatch warning. Lint: `uv run ruff check <files>`. TDD; commit per task.
- **Verified signatures (don't change):** `eval/fixtures.py::FixtureChunk(key, content, tags=[])`. `replay/schema.py::TaskSignal(turn_index, prompt, subtask_id, recent_tool_calls)`; `Decision.candidates: list[SelectedChunk]` with `.key` (the FULL ranked pool for all three arms). `store/memory.py::InMemoryStore(embedder=...)`, `.store(key, content, tags=, ttl_s=)`, `all_live_chunks()` newest-first. `replay/target.py::RecencyOnlyTarget()` (name `"recency-only"`), `Bm25Target()` (name `"bm25"`), `PolicyTarget(RelevancePolicy(embedder))` (name `"semantic-policy"`, `.embedder` property); each `.decide(signal, store) -> Decision`. `policy/relevance.py::RelevancePolicy`. `tests/eval/conftest.py::KeywordEmbedder` (embeds only vocab tokens `A B C D E F`).
- **Pinned params (spec §3):** `lag=2`, `k=5`.

---

## Task 1: the metric module + core behavior tests

**Files:** Create `src/context_curator/eval/eviction_regret.py`. Test: `tests/eval/test_eviction_regret.py`.

- [ ] **Step 1: write the failing core test** — `tests/eval/test_eviction_regret.py`:
```python
import pytest

from context_curator.eval.eviction_regret import (
    Session,
    SessionTurn,
    eviction_regret,
)
from context_curator.eval.fixtures import FixtureChunk
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from tests.eval.conftest import KeywordEmbedder


def _stale_auth() -> Session:
    # turn 0 introduces `gold` (vocab "A B C"); turns 1-2 introduce 6 fillers (disjoint vocab
    # "D E F"); turn 3's prompt "A B C" needs `gold` (age 3 >= lag 2). 7 chunks available at turn 3.
    return Session(name="stale-auth", turns=[
        SessionTurn(prompt="A B C", new_chunks=[FixtureChunk(key="gold", content="A B C")]),
        SessionTurn(prompt="D E F",
                    new_chunks=[FixtureChunk(key=f"f{i}", content="D E F") for i in (1, 2, 3)]),
        SessionTurn(prompt="D E F",
                    new_chunks=[FixtureChunk(key=f"f{i}", content="D E F") for i in (4, 5, 6)]),
        SessionTurn(prompt="A B C", needed_keys=["gold"]),
    ])


def _no_old_needs() -> Session:
    # `x` introduced and needed in the SAME turn (age 0 < lag) -> zero old need-events.
    return Session(name="no-old-needs", turns=[
        SessionTurn(prompt="A", new_chunks=[FixtureChunk(key="x", content="A")], needed_keys=["x"]),
    ])


def test_recency_arm_buries_old_gold_high_regret():
    r = eviction_regret([_stale_auth()], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=2)
    assert r.rate == 1.0
    assert r.arm == "recency-only"
    assert r.old_need_events == 1 and r.regret_events == 1


def test_semantic_arm_resurfaces_old_gold_zero_regret():
    emb = KeywordEmbedder()
    target = PolicyTarget(RelevancePolicy(emb))
    r = eviction_regret([_stale_auth()], target, emb, k=5, lag=2)
    assert r.rate == 0.0
    assert r.arm == "semantic-policy"
    assert r.old_need_events == 1 and r.regret_events == 0


def test_no_old_needs_rate_is_none():
    r = eviction_regret([_no_old_needs()], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=2)
    assert r.rate is None and r.old_need_events == 0


def test_validation_rejects_unintroduced_needed_key():
    s = Session(name="bad", turns=[SessionTurn(prompt="p", needed_keys=["ghost"])])
    with pytest.raises(ValueError):
        eviction_regret([s], RecencyOnlyTarget(), KeywordEmbedder())


def test_validation_rejects_duplicate_key():
    s = Session(name="dup", turns=[
        SessionTurn(prompt="p", new_chunks=[FixtureChunk(key="x", content="A")]),
        SessionTurn(prompt="p", new_chunks=[FixtureChunk(key="x", content="A")]),
    ])
    with pytest.raises(ValueError):
        eviction_regret([s], RecencyOnlyTarget(), KeywordEmbedder())
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_eviction_regret.py -v` → ImportError (`eviction_regret` not defined).

- [ ] **Step 3: implement** — `src/context_curator/eval/eviction_regret.py`:
```python
"""Cross-turn eviction-regret metric (design §10.2, reframed §3.1): old-gold re-selection recall over
a multi-turn accumulated pool. At the turn an OLD (age >= lag) chunk is needed again, did the arm's
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
    needed_keys: list[str] = []              # available chunks genuinely required this turn (planted)


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
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_eviction_regret.py -v` → 5 pass. (`recency` rate 1.0: at turn 3 the recency arm's `candidates` are newest-first `[f6..f1, gold]`, `gold` at index 6 is outside top-5. `semantic` rate 0.0: `gold` scores `0.65·1.0 + 0.35·exp(-0.6) ≈ 0.842` vs each filler `≤ 0.35`, so `gold` ranks #1, inside top-5.) `uv run ruff check src/context_curator/eval/eviction_regret.py tests/eval/test_eviction_regret.py`.

- [ ] **Step 5: commit** — `git add src/context_curator/eval/eviction_regret.py tests/eval/test_eviction_regret.py && git commit -m "feat(m5): cross-turn eviction-regret metric (old-gold re-selection recall) + core tests"`

---

## Task 2: parameter, contract, and smoke tests

**Files:** Modify `tests/eval/test_eviction_regret.py` (append). No source changes expected — these tests validate the Task 1 implementation; if any fails, it reveals a real bug to fix in `eviction_regret.py`.

- [ ] **Step 1: append the tests**:
```python
from context_curator.eval.eviction_regret import RegretReport
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import Bm25Target
from context_curator.store.memory import InMemoryStore


def test_lag_too_large_excludes_the_need():
    # age(gold)=3 < lag 4 -> no old need-events -> rate None
    r = eviction_regret([_stale_auth()], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=4)
    assert r.old_need_events == 0 and r.rate is None


def test_larger_k_admits_the_buried_chunk():
    # k=7 over the 7-chunk pool admits gold (recency position 7) -> recency regret 0.0
    r = eviction_regret([_stale_auth()], RecencyOnlyTarget(), KeywordEmbedder(), k=7, lag=2)
    assert r.rate == 0.0


def test_candidates_is_full_pool_for_all_arms():
    emb = KeywordEmbedder()
    chunks = [c for turn in _stale_auth().turns for c in turn.new_chunks]   # the 7 chunks
    store = InMemoryStore(embedder=emb)
    for c in chunks:
        store.store(c.key, c.content, tags=c.tags, ttl_s=None)
    sig = TaskSignal(turn_index=3, prompt="A B C", subtask_id=None, recent_tool_calls=[])
    for target in (RecencyOnlyTarget(), Bm25Target(), PolicyTarget(RelevancePolicy(emb))):
        assert len(target.decide(sig, store).candidates) == len(chunks)   # full pool, not truncated


def test_bm25_arm_runs_and_reports():
    r = eviction_regret([_stale_auth()], Bm25Target(), KeywordEmbedder(), k=5, lag=2)
    assert isinstance(r, RegretReport) and r.arm == "bm25" and r.old_need_events == 1


def test_empty_session_does_not_throw():
    s = Session(name="empty", turns=[SessionTurn(prompt="p")])
    r = eviction_regret([s], RecencyOnlyTarget(), KeywordEmbedder(), k=5, lag=2)
    assert r.rate is None and r.old_need_events == 0
```

- [ ] **Step 2: run** — `uv run pytest tests/eval/test_eviction_regret.py -v` → all (Task 1's 5 + these 5) pass. If `test_larger_k_admits_the_buried_chunk` or any other fails, the implementation has a real bug (e.g. slicing `candidates` wrong) — fix `eviction_regret.py`, do not weaken the test.

- [ ] **Step 3: full eval suite + lint** — `uv run pytest tests/eval -q` (no regressions — M5 adds only a new module/test); `uv run ruff check src/context_curator/eval/eviction_regret.py tests/eval/test_eviction_regret.py`.

- [ ] **Step 4: commit** — `git add tests/eval/test_eviction_regret.py && git commit -m "test(m5): eviction-regret param/contract/smoke coverage (lag, k, full-pool, bm25, empty)"`

---

## Final verification
- [ ] `uv run pytest -q` green; `uv run ruff check .` clean.
- [ ] **Confirm no production change:** `git diff main --stat -- src/context_curator/curator/ src/context_curator/policy/ src/context_curator/eval/keystone.py` is EMPTY (M5 only adds `eval/eviction_regret.py` + its test).
- [ ] Then the final whole-branch review → PR.

## Spec coverage map (self-review)
| Spec § | Task |
|---|---|
| §3 regret definition (age≥lag, micro-average, lag=0 semantics) | 1 |
| §3 pinned contract (`candidates` = full pool) | 2 (`test_candidates_is_full_pool_for_all_arms`) |
| §4 evaluator (validation, embedder-identity assert, replay) | 1 |
| §5 hand-built `stale-auth` + `no-old-needs` fixtures | 1 |
| §6 recency=1.0 / semantic=0.0 / None / arm field | 1 |
| §6 lag=4 excludes / k=7 admits / bm25 smoke / empty session | 2 |
| §6 validation (un-introduced key, duplicate key) | 1 |
| §7 file structure (one module + one test, no prod change) | 1, 2, Final |
| §8 bases on main, dependency-free | (branch off main; verified Final) |
