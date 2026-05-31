# Replay Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic offline replay harness (DESIGN.md §10.0): capture Claude Code sessions (synthetic + real transcript), replay them through a pluggable `ReplayTarget`, and emit a byte-stable `DecisionLog`; wire the M1 recency-only store query as the v1 baseline target.

**Architecture:** A frozen normalized trace schema, two capture adapters (synthetic builder + transcript JSONL), a harness-local ingest seam, a `ReplayTarget` Protocol with a `RecencyOnlyTarget`, and a replay engine producing a `DecisionLog`. Determinism rests on a prerequisite store change: an internal monotonic write-sequence (`seq`) so recency is write-order, not wall-clock.

**Tech Stack:** Python 3.11+, UV, pydantic v2, pytest, ruff, stdlib `sqlite3`, `collections.deque`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-30-replay-harness-design.md` (read it; the Design Critique Log explains *why* several non-obvious choices exist).

---

## Scope & sequencing

Task 1 is the **prerequisite store change** and must land first (the harness's determinism depends on it). Tasks 2–7 build the harness. Transcript adapter (Task 7) is last because it pins the real Claude Code JSONL format and is isolated behind one file.

All replay code lives under `src/context_curator/replay/` — dev/eval tooling, not bundled into the M7 runtime plugin.

## File structure

```
src/context_curator/
  store/sqlite_store.py        # MODIFY: add `seq` column + ordering (Task 1)
  store/memory.py              # MODIFY: add seq counter + ordering (Task 1)
  replay/
    __init__.py
    schema.py                  # Task 2: events, ToolRef, TaskSignal, SelectedChunk, Decision, DecisionLog
    capture/
      __init__.py
      synthetic.py             # Task 3: TraceBuilder
      transcript.py            # Task 7: parse_transcript (CC JSONL, isolated)
    ingest.py                  # Task 4: ingest_tool_result
    target.py                  # Task 5: ReplayTarget Protocol + RecencyOnlyTarget
    engine.py                  # Task 6: ReplayEngine
tests/
  test_store_contract.py       # MODIFY: add recency-order contract test (Task 1)
  test_store_seq.py            # Task 1: seq-specific + reopen tests
  replay/
    fixtures/sample_transcript.jsonl   # Task 7 (hand-scrubbed)
    test_schema.py             # Task 2
    test_synthetic_builder.py  # Task 3
    test_ingest.py             # Task 4
    test_recency_target.py     # Task 5
    test_engine_determinism.py # Task 6
    test_transcript_adapter.py # Task 7
```

---

### Task 1: Store monotonic write-sequence (`seq`) — prerequisite

Make recency deterministic and overwrite-correct: order `query` by an internal `seq` (write order), not wall-clock `created_at`. `created_at` is retained for TTL only.

**Files:**
- Modify: `src/context_curator/store/sqlite_store.py`
- Modify: `src/context_curator/store/memory.py`
- Test: `tests/test_store_seq.py`
- Modify: `tests/test_store_contract.py`

- [ ] **Step 1: Write the failing seq tests**

`tests/test_store_seq.py`:
```python
from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


def _sqlite(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))


def test_recency_is_write_order_not_walltime(tmp_path):
    s = _sqlite(tmp_path)
    s.store("a", "x")
    s.store("b", "x")
    s.store("c", "x")
    assert [c.key for c in s.query("q", k=10)] == ["c", "b", "a"]


def test_overwrite_moves_key_to_front(tmp_path):
    s = _sqlite(tmp_path)
    s.store("a", "x")
    s.store("b", "x")
    s.store("a", "x2")  # re-store: a is now newest
    assert [c.key for c in s.query("q", k=10)] == ["a", "b"]


def test_seq_survives_reopen(tmp_path):
    db = str(tmp_path / "cc.db")
    s1 = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=32))
    s1.store("a", "x")
    s1.store("b", "x")
    s1.close()
    s2 = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=32))
    s2.store("c", "x")  # must seed seq from MAX(seq) of existing rows -> newest
    assert [c.key for c in s2.query("q", k=10)] == ["c", "b", "a"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store_seq.py -v`
Expected: FAIL (ordering is currently by `created_at`, and there is no `seq` column).

- [ ] **Step 3: Add `seq` to the SQLite DDL and writes**

In `src/context_curator/store/sqlite_store.py`, add the column to `_DDL` (after `expires_at`):
```python
    expires_at       TEXT,                   -- precomputed, nullable; NULL when pinned/ttl NULL
    seq              INTEGER NOT NULL        -- monotonic write order; recency ranks on this
);
"""
```
Replace the `store()` INSERT statement so the seq is computed in SQL on both the insert and the conflict-update (via `excluded.seq`):
```python
        self._conn.execute(
            """INSERT INTO chunks
               (key, content, tags, source, created_at, last_onloaded_at, pin,
                ttl_s, provenance, embedding, expires_at, seq)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,
                       (SELECT COALESCE(MAX(seq), 0) + 1 FROM chunks))
               ON CONFLICT(key) DO UPDATE SET
                 content=excluded.content, tags=excluded.tags, source=excluded.source,
                 created_at=excluded.created_at, pin=excluded.pin, ttl_s=excluded.ttl_s,
                 provenance=excluded.provenance, embedding=excluded.embedding,
                 expires_at=excluded.expires_at, seq=excluded.seq""",
            (
                key, content, json.dumps(chunk.tags), source, created_at, None,
                1 if pin else 0, ttl_s, provenance, json.dumps(chunk.embedding),
                _compute_expires_at(created_at, ttl_s, pin),
            ),
        )
```
Note: `excluded.seq` is the VALUES-computed `MAX(seq)+1`, so a conflict-update moves the key to the front. The parameter tuple is unchanged (11 params; `seq` is the SQL subquery, not a bind param). *Migration: `_DDL` is `CREATE TABLE IF NOT EXISTS`, so v1 assumes fresh DBs — no `ALTER` of pre-existing stores.*

- [ ] **Step 4: Order both `query` branches by `seq DESC`**

In `query()`, change both SQL statements:
```python
        if self._allowed_prefix is None:
            rows = self._conn.execute(
                "SELECT * FROM chunks ORDER BY seq DESC"
            ).fetchall()
        else:
            p = self._allowed_prefix
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE key = ? OR key LIKE ? ORDER BY seq DESC",
                (p, p + ":%"),
            ).fetchall()
```

- [ ] **Step 5: Add the seq counter to `InMemoryStore`**

In `src/context_curator/store/memory.py`, add the counter in `__init__`:
```python
    def __init__(self, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        self._data: dict[str, Chunk] = {}
        self._seq: dict[str, int] = {}
        self._next_seq: int = 0
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix
```
At the end of `store()` (after assigning `self._data[key]`), record the seq:
```python
        self._next_seq += 1
        self._seq[key] = self._next_seq
        return key
```
In `query()`, sort by seq instead of `created_at`:
```python
        cands.sort(key=lambda c: self._seq[c.key], reverse=True)  # write-order recency
```
In `evict()`, drop the seq entry too:
```python
    def evict(self, key: str) -> bool:
        self._seq.pop(key, None)
        return self._data.pop(key, None) is not None
```

- [ ] **Step 6: Run the seq tests**

Run: `uv run pytest tests/test_store_seq.py -v`
Expected: 3 passed.

- [ ] **Step 7: Add a recency-order assertion to the shared contract suite**

Append to `tests/test_store_contract.py` (runs against `[memory]` and `[sqlite]`):
```python
def test_query_orders_by_write_recency(store):
    store.store("a", "x")
    store.store("b", "x")
    store.store("c", "x")
    assert [c.key for c in store.query("q", k=10)] == ["c", "b", "a"]
    store.store("a", "x2")  # re-store moves a to front on both backends
    assert [c.key for c in store.query("q", k=10)] == ["a", "c", "b"]
```

- [ ] **Step 8: Run the full suite (nothing else regresses)**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all pass (existing query tests use set/single-element comparisons, so the order change does not break them); ruff clean.

- [ ] **Step 9: Commit**

```bash
git add src/context_curator/store/sqlite_store.py src/context_curator/store/memory.py tests/test_store_seq.py tests/test_store_contract.py
git commit -m "feat: deterministic write-order recency via internal seq (replay prerequisite)"
```

---

### Task 2: Trace schema

**Files:**
- Create: `src/context_curator/replay/__init__.py` (empty)
- Create: `src/context_curator/replay/schema.py`
- Test: `tests/replay/test_schema.py`

- [ ] **Step 1: Write the failing test**

`tests/replay/test_schema.py`:
```python
from pydantic import TypeAdapter

from context_curator.replay.schema import (
    AssistantMessage,
    Decision,
    DecisionLog,
    SelectedChunk,
    TaskSignal,
    ToolCall,
    ToolRef,
    ToolResult,
    Trace,
    TraceEvent,
    UserPrompt,
)


def test_trace_roundtrips_with_discriminated_events():
    trace = Trace(
        session_id="s1",
        source="synthetic",
        events=[
            UserPrompt(turn_index=0, text="hi"),
            ToolCall(call_id="c0", name="Read", args={"path": "a.py"}),
            ToolResult(call_id="c0", content="data"),
            AssistantMessage(text="done"),
        ],
    )
    restored = Trace(**trace.model_dump())
    assert restored == trace
    # discriminated union picks the right type from a raw dict
    ev = TypeAdapter(TraceEvent).validate_python({"kind": "tool_result", "call_id": "c0", "content": "x"})
    assert isinstance(ev, ToolResult)


def test_decision_forward_stable_fields_default_empty():
    d = Decision(turn_index=1, subtask_id=None, prompt_preview="p",
                 selected=[SelectedChunk(key="k", score=None, tokens=3)], total_tokens=3)
    assert d.candidates == []
    assert d.offloaded == []


def test_task_signal_uses_slim_tool_refs():
    sig = TaskSignal(turn_index=0, prompt="p", subtask_id=None,
                     recent_tool_calls=[ToolRef(name="Read", call_id="c0")])
    assert sig.recent_tool_calls[0].name == "Read"


def test_decision_log_serializes_without_floats_in_v1():
    log = DecisionLog(trace_session_id="s1", target_name="recency-only", decisions=[])
    import json
    json.dumps(log.model_dump())  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/replay/test_schema.py -v`
Expected: FAIL (`ModuleNotFoundError: context_curator.replay.schema`).

- [ ] **Step 3: Implement `schema.py`**

```python
"""Normalized replay trace schema (replay harness design §3.1). Frozen pydantic v2."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class UserPrompt(BaseModel):
    kind: Literal["user_prompt"] = "user_prompt"
    turn_index: int
    text: str
    subtask_id: str | None = None


class ToolCall(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    args: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    content: str
    error: bool = False


class AssistantMessage(BaseModel):
    kind: Literal["assistant_message"] = "assistant_message"
    text: str


TraceEvent = Annotated[
    UserPrompt | ToolCall | ToolResult | AssistantMessage,
    Field(discriminator="kind"),
]


class Trace(BaseModel):
    session_id: str
    source: str
    events: list[TraceEvent]


class ToolRef(BaseModel):
    name: str
    call_id: str


class TaskSignal(BaseModel):
    turn_index: int
    prompt: str
    subtask_id: str | None
    recent_tool_calls: list[ToolRef]


class SelectedChunk(BaseModel):
    key: str
    score: float | None
    tokens: int


class Decision(BaseModel):
    turn_index: int
    subtask_id: str | None
    prompt_preview: str
    selected: list[SelectedChunk]
    total_tokens: int
    candidates: list[SelectedChunk] = Field(default_factory=list)
    offloaded: list[str] = Field(default_factory=list)


class DecisionLog(BaseModel):
    trace_session_id: str
    target_name: str
    decisions: list[Decision]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/replay/test_schema.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/replay/__init__.py src/context_curator/replay/schema.py tests/replay/test_schema.py
git commit -m "feat: replay trace schema (events, signal, decision log)"
```

---

### Task 3: Synthetic `TraceBuilder`

**Files:**
- Create: `src/context_curator/replay/capture/__init__.py` (empty)
- Create: `src/context_curator/replay/capture/synthetic.py`
- Test: `tests/replay/test_synthetic_builder.py`

- [ ] **Step 1: Write the failing test**

`tests/replay/test_synthetic_builder.py`:
```python
from context_curator.replay.capture.synthetic import TraceBuilder
from context_curator.replay.schema import AssistantMessage, ToolCall, ToolResult, UserPrompt


def test_builder_assigns_turn_indices_and_call_ids():
    trace = (
        TraceBuilder("s1")
        .user("first")
        .tool("Read", {"path": "a.py"}).result("aaa")
        .user("second", subtask_id="task-2")
        .assistant("ok")
        .build()
    )
    assert trace.session_id == "s1"
    assert trace.source == "synthetic"
    kinds = [type(e) for e in trace.events]
    assert kinds == [UserPrompt, ToolCall, ToolResult, UserPrompt, AssistantMessage]
    assert trace.events[0].turn_index == 0
    assert trace.events[3].turn_index == 1
    assert trace.events[3].subtask_id == "task-2"
    # call_id of the tool matches its result
    assert trace.events[1].call_id == trace.events[2].call_id


def test_result_without_preceding_tool_raises():
    import pytest
    with pytest.raises(ValueError):
        TraceBuilder("s1").user("x").result("orphan")


def test_error_result_flag():
    trace = TraceBuilder("s1").user("x").tool("Bash", {}).result("boom", error=True).build()
    assert trace.events[-1].error is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/replay/test_synthetic_builder.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `synthetic.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/replay/test_synthetic_builder.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/replay/capture/__init__.py src/context_curator/replay/capture/synthetic.py tests/replay/test_synthetic_builder.py
git commit -m "feat: synthetic TraceBuilder for deterministic replay fixtures"
```

---

### Task 4: Ingest seam

**Files:**
- Create: `src/context_curator/replay/ingest.py`
- Test: `tests/replay/test_ingest.py`

- [ ] **Step 1: Write the failing test**

`tests/replay/test_ingest.py`:
```python
from context_curator.embeddings import HashingEmbedder
from context_curator.replay.ingest import ingest_tool_result
from context_curator.replay.schema import ToolCall, ToolResult
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=32))


def test_ingest_writes_retrievable_chunk_with_canonical_source():
    store = _store()
    call = ToolCall(call_id="c0", name="WebFetch", args={})
    ingest_tool_result(ToolResult(call_id="c0", content="body"), call, "s1", 0, store)
    key = "session:s1:tool:000000:c0"
    chunk = store.retrieve(key)
    assert chunk is not None
    assert chunk.content == "body"
    assert chunk.tags == ["webfetch"]        # tag lowercased for matching
    assert chunk.source == "tool:WebFetch"   # source preserves canonical case (§9 audit)
    assert chunk.ttl_s is None               # replay chunks never expire


def test_error_results_are_skipped():
    store = _store()
    call = ToolCall(call_id="c0", name="Bash", args={})
    ingest_tool_result(ToolResult(call_id="c0", content="boom", error=True), call, "s1", 0, store)
    assert store.list("session:s1") == []


def test_duplicate_call_id_does_not_overwrite():
    store = _store()
    call = ToolCall(call_id="dup", name="Read", args={})
    ingest_tool_result(ToolResult(call_id="dup", content="first"), call, "s1", 0, store)
    ingest_tool_result(ToolResult(call_id="dup", content="second"), call, "s1", 1, store)
    keys = sorted(store.list("session:s1"))
    assert keys == ["session:s1:tool:000000:dup", "session:s1:tool:000001:dup"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/replay/test_ingest.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `ingest.py`**

```python
"""Harness-local capture-during-replay seam (design §3.3). M2-alignable.

`call` is REQUIRED: the engine only calls this when the matching tool_use is present
in the MAIN session, so raw sub-agent output cannot leak into the main store (§4.4)."""
from __future__ import annotations

from context_curator.replay.schema import ToolCall, ToolResult
from context_curator.store.interface import Store


def ingest_tool_result(result: ToolResult, call: ToolCall,
                       session_id: str, ordinal: int, store: Store) -> None:
    if result.error:
        return
    # `ordinal` guarantees key uniqueness even if call_id repeats (else ON CONFLICT overwrite).
    key = f"session:{session_id}:tool:{ordinal:06d}:{result.call_id}"
    store.store(
        key,
        result.content,
        tags=[call.name.lower()],     # lowercased for case-insensitive tag filtering
        source=f"tool:{call.name}",   # canonical case for the §9 poisoning audit
        ttl_s=None,                   # replay candidates never expire mid-session (§3.3)
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/replay/test_ingest.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/replay/ingest.py tests/replay/test_ingest.py
git commit -m "feat: replay ingest seam (tool_result -> store chunk)"
```

---

### Task 5: `ReplayTarget` + `RecencyOnlyTarget`

**Files:**
- Create: `src/context_curator/replay/target.py`
- Test: `tests/replay/test_recency_target.py`

- [ ] **Step 1: Write the failing test**

`tests/replay/test_recency_target.py`:
```python
from context_curator.embeddings import HashingEmbedder
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore


def _signal():
    return TaskSignal(turn_index=0, prompt="do the thing", subtask_id=None, recent_tool_calls=[])


def _store_with(*contents):
    s = InMemoryStore(embedder=HashingEmbedder(dim=32))
    for i, c in enumerate(contents):
        s.store(f"k{i}", c, tags=["t"], ttl_s=None)
    return s


def test_onloads_most_recent_first():
    store = _store_with("oldest", "newest")
    d = RecencyOnlyTarget(tags=["t"]).decide(_signal(), store)
    assert [s.key for s in d.selected] == ["k1", "k0"]
    assert d.score_is_none()  # helper below; or assert all(s.score is None ...)


def test_total_tokens_matches_and_respects_budget():
    store = _store_with("x" * 100, "y" * 100, "z" * 100)  # ~25 tokens each
    d = RecencyOnlyTarget(tags=["t"], token_budget=30).decide(_signal(), store)
    assert len(d.selected) == 1
    assert d.total_tokens == sum(s.tokens for s in d.selected)
    assert d.total_tokens <= 30


def test_k_and_budget_bind_simultaneously():
    store = _store_with("x" * 40, "y" * 40, "z" * 40)  # ~10 tokens each
    # k=2 and budget=15: budget allows 1 (10, next 10 -> 20>15), k allows 2 -> first-fit wins -> 1
    d = RecencyOnlyTarget(tags=["t"], k=2, token_budget=15).decide(_signal(), store)
    assert len(d.selected) == 1


def test_no_budget_sums_all_selected():
    store = _store_with("aa", "bb")
    d = RecencyOnlyTarget(tags=["t"]).decide(_signal(), store)
    assert d.total_tokens == sum(s.tokens for s in d.selected)


def test_empty_store_empty_decision():
    store = InMemoryStore(embedder=HashingEmbedder(dim=32))
    d = RecencyOnlyTarget(tags=["t"]).decide(_signal(), store)
    assert d.selected == [] and d.total_tokens == 0
```

> Replace `d.score_is_none()` with `all(s.score is None for s in d.selected)` — there is no helper method; the line documents intent. Use the plain assertion in the actual test.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/replay/test_recency_target.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `target.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/replay/test_recency_target.py -v`
Expected: all passed (after replacing the `score_is_none()` documentation line with `assert all(s.score is None for s in d.selected)`).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/replay/target.py tests/replay/test_recency_target.py
git commit -m "feat: ReplayTarget protocol + RecencyOnlyTarget baseline"
```

---

### Task 6: Replay engine

**Files:**
- Create: `src/context_curator/replay/engine.py`
- Test: `tests/replay/test_engine_determinism.py`

- [ ] **Step 1: Write the failing test**

`tests/replay/test_engine_determinism.py`:
```python
from context_curator.replay.capture.synthetic import TraceBuilder
from context_curator.replay.engine import ReplayEngine
from context_curator.replay.schema import ToolResult
from context_curator.replay.target import RecencyOnlyTarget


def _engine():
    return ReplayEngine(target=RecencyOnlyTarget(tags=["read"]))


def _trace():
    return (
        TraceBuilder("s1")
        .user("turn 0")                                   # decision: nothing captured yet
        .tool("Read", {"path": "a.py"}).result("alpha")
        .user("turn 1")                                   # decision: sees alpha
        .tool("Read", {"path": "b.py"}).result("beta")
        .user("turn 2")                                   # decision: sees beta, alpha (newest first)
        .build()
    )


def test_byte_identical_across_runs():
    trace = _trace()
    a = _engine().run(trace).model_dump()
    b = _engine().run(trace).model_dump()
    assert a == b


def test_turn_only_sees_prior_turns():
    log = _engine().run(_trace())
    # turn 0: empty; turn 1: alpha; turn 2: beta then alpha
    assert log.decisions[0].selected == []
    assert [s.key for s in log.decisions[1].selected] == ["session:s1:tool:000000:c0"]
    assert [s.key for s in log.decisions[2].selected] == [
        "session:s1:tool:000001:c1", "session:s1:tool:000000:c0",
    ]


def test_window_holds_last_n_including_errors():
    trace = (
        TraceBuilder("s1")
        .tool("Read", {}).result("ok")
        .tool("Bash", {}).result("boom", error=True)
        .user("now")
        .build()
    )
    log = ReplayEngine(target=RecencyOnlyTarget(), recent_window=5).run(trace)
    refs = [r.name for r in _last_signal_tool_names(log)] if False else None  # see note
    # The window is internal to the signal; assert via a recording target instead:
    assert log.decisions[0].turn_index == 0


def test_subtask_id_carried_to_log():
    trace = TraceBuilder("s1").user("x", subtask_id="sub-9").build()
    log = _engine().run(trace)
    assert log.decisions[0].subtask_id == "sub-9"


def test_only_error_results_yield_empty_decision():
    trace = (
        TraceBuilder("s1")
        .tool("Bash", {}).result("boom", error=True)
        .user("now")
        .build()
    )
    log = ReplayEngine(target=RecencyOnlyTarget(tags=["bash"])).run(trace)
    assert log.decisions[0].selected == []


def test_orphan_tool_result_is_skipped_not_ingested():
    # a ToolResult whose call_id has no matching ToolCall in the trace must be dropped
    trace = TraceBuilder("s1").user("x").build()
    trace.events.append(ToolResult(call_id="ghost", content="leak"))
    trace.events.append(  # add a second turn that would see a leak if ingested
        TraceBuilder("s1").user("y").build().events[0]
    )
    log = ReplayEngine(target=RecencyOnlyTarget()).run(trace)
    # the appended UserPrompt has turn_index 0 (built fresh); just assert no ghost chunk leaked
    all_selected = [s.key for d in log.decisions for s in d.selected]
    assert not any("ghost" in k for k in all_selected)
```

> The `test_window_holds_last_n_including_errors` body above is a stub. Replace it with a **recording target** that captures the `TaskSignal` it receives, then assert the window contents. Add this minimal recording target inside the test module:
> ```python
> from context_curator.replay.schema import Decision
> class _Recorder:
>     name = "recorder"
>     def __init__(self): self.signals = []
>     def decide(self, signal, store):
>         self.signals.append(signal)
>         return Decision(turn_index=signal.turn_index, subtask_id=signal.subtask_id,
>                         prompt_preview=signal.prompt[:80], selected=[], total_tokens=0)
>
> def test_window_holds_last_n_including_errors():
>     rec = _Recorder()
>     trace = (TraceBuilder("s1").tool("Read", {}).result("ok")
>              .tool("Bash", {}).result("boom", error=True).user("now").build())
>     ReplayEngine(target=rec, recent_window=5).run(trace)
>     names = [r.name for r in rec.signals[0].recent_tool_calls]
>     assert names == ["Read", "Bash"]  # both calls, incl. the errored one
> ```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/replay/test_engine_determinism.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `engine.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/replay/test_engine_determinism.py -v`
Expected: all passed (with the recording-target replacement applied).

- [ ] **Step 5: Run the full suite + lint**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all green; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/replay/engine.py tests/replay/test_engine_determinism.py
git commit -m "feat: deterministic replay engine (capture-then-onload, byte-stable log)"
```

---

### Task 7: Transcript adapter + scrubbed fixture

This task pins the real Claude Code transcript JSONL format and is isolated in one file.

**Files:**
- Create: `src/context_curator/replay/capture/transcript.py`
- Create: `tests/replay/fixtures/sample_transcript.jsonl`
- Test: `tests/replay/test_transcript_adapter.py`

- [ ] **Step 1: Obtain and scrub a real transcript into the fixture**

Claude Code stores session transcripts as JSONL under `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl` (on this machine, e.g. `C:\Users\barclay\.claude\projects\d--MajorProjects-INFRASTRUCTURE-context-curator\`). Read one to confirm the record shape, then **hand-scrub** a short excerpt into `tests/replay/fixtures/sample_transcript.jsonl` with: a stable `sessionId` (`"sample"`), stable `tool_use` ids (`c0`, `c1`, …), normalized paths, and **at least** these records (one per line, compact JSON):
  1. a `type:"user"` record with a **plain-text** prompt (message.content is a string or a single `{"type":"text"}` block),
  2. a `type:"assistant"` record whose `message.content` is a list with a `{"type":"text"}` block **and two** `{"type":"tool_use","id":...,"name":...,"input":{...}}` blocks,
  3. two `type:"user"` records each carrying a `{"type":"tool_result","tool_use_id":...,"content":...}` block (the results of the two calls),
  4. a second **plain-text** `type:"user"` prompt,
  5. a `type:"user"` record with `"isSidechain": true` whose content is a `tool_result` for a `tool_use_id` that never appears in the main session (the sidechain-orphan case).

Expected normalized output (the assertion target for Step 2): events = `UserPrompt(turn_index=0)`, `AssistantMessage`, `ToolCall(c0)`, `ToolCall(c1)`, `ToolResult(c0)`, `ToolResult(c1)`, `UserPrompt(turn_index=1)` — and the sidechain orphan record (#5) produces **no event**.

- [ ] **Step 2: Write the failing test**

`tests/replay/test_transcript_adapter.py`:
```python
from pathlib import Path

from context_curator.replay.capture.transcript import parse_transcript
from context_curator.replay.schema import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    UserPrompt,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def test_parses_expected_normalized_sequence():
    trace = parse_transcript(FIXTURE)
    assert trace.source == "transcript"
    kinds = [type(e) for e in trace.events]
    assert kinds == [UserPrompt, AssistantMessage, ToolCall, ToolCall,
                     ToolResult, ToolResult, UserPrompt]


def test_tool_result_user_record_does_not_increment_turn_index():
    trace = parse_transcript(FIXTURE)
    prompts = [e for e in trace.events if isinstance(e, UserPrompt)]
    assert [p.turn_index for p in prompts] == [0, 1]  # tool_result user records are NOT turns


def test_sidechain_orphan_result_is_dropped():
    trace = parse_transcript(FIXTURE)
    # the sidechain tool_use_id never appears as a ToolResult in the normalized trace
    result_ids = {e.call_id for e in trace.events if isinstance(e, ToolResult)}
    assert "sidechain-orphan" not in result_ids


def test_two_tool_use_blocks_become_two_calls_in_order():
    trace = parse_transcript(FIXTURE)
    calls = [e for e in trace.events if isinstance(e, ToolCall)]
    assert [c.call_id for c in calls] == ["c0", "c1"]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/replay/test_transcript_adapter.py -v`
Expected: FAIL (module missing).

- [ ] **Step 4: Implement `transcript.py`**

```python
"""Claude Code transcript JSONL -> normalized Trace (design §3.2). ALL format-specific
knowledge is isolated here so a CC format change is contained to this file."""
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
    session_id = "sample"
    seen_tool_use: set[str] = set()  # main-session tool_use ids, to drop sidechain orphans

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
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
        # other record types (system, summary, thinking) are skipped (forward-tolerant)

    return Trace(session_id=session_id, source="transcript", events=events)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/replay/test_transcript_adapter.py -v`
Expected: 4 passed. If field names differ from the real transcript you inspected, adjust **only** `transcript.py` and the fixture; the assertions encode the design's structural contract and should hold.

- [ ] **Step 6: Full suite + lint**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all green; ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/context_curator/replay/capture/transcript.py tests/replay/fixtures/sample_transcript.jsonl tests/replay/test_transcript_adapter.py
git commit -m "feat: Claude Code transcript adapter (isolated) + scrubbed fixture"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3.1 trace schema → Task 2. ✅ (events, ToolRef, TaskSignal, SelectedChunk, Decision incl. forward-stable `candidates`/`offloaded`, DecisionLog)
- §3.2 capture adapters → Task 3 (synthetic), Task 7 (transcript, with all structural rules: turn-vs-tool_result, multi-tool_use order, sidechain-orphan drop, scrubbed fixture). ✅
- §3.3 ingest seam (ordinal key, ttl_s=None, canonical `source` case, error-skip) → Task 4. ✅
- §3.4 ReplayTarget + RecencyOnlyTarget + token invariant (incl. `token_budget=None`) + k/budget precedence → Task 5. ✅
- §3.4 stable recency (`seq`, both query branches, reopen, contract recency test) → Task 1. ✅
- §3.5 engine (window incl. errors, orphan-skip ingest guard, subtask carry-through, empty-decision) → Task 6. ✅
- Determinism (byte-identical log, no floats/args in v1 log) → Task 6 tests. ✅

**Placeholder scan:** The one documentation line `d.score_is_none()` in Task 5 and the stubbed window test in Task 6 are both explicitly flagged with the exact replacement code in a note — not left as a TODO. Task 7 Step 1 requires inspecting a real transcript, which is intentional, isolated work with a concrete expected-output contract. No other placeholders.

**Type consistency:** `ingest_tool_result(result, call, session_id, ordinal, store)` signature matches between Task 4 (def) and Task 6 (call). `RecencyOnlyTarget(k, token_budget, tags)` matches Task 5 (def) and Tasks 6 tests. `ToolRef(name, call_id)`, `TaskSignal(...recent_tool_calls: list[ToolRef])`, `Decision(...)` consistent Task 2 → 5 → 6. Key format `session:{sid}:tool:{ordinal:06d}:{call_id}` identical in Task 4 def and Task 6 test assertions. `seq` column/ordering consistent across Task 1 sqlite + memory.

**Note on Task 1 ordering vs existing tests:** existing query contract tests compare as sets or single-element lists, so changing recency from `created_at` to `seq` does not break them (verified against `test_store_contract.py`). The token-budget trim test checks only count, not which chunk.
