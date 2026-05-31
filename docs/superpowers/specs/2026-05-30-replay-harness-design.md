# Replay Harness — Design (ContextCurator §10.0)

**Status:** Approved-pending-review
**Parent design:** `DESIGN.md` v1.3 §10.0 (keystone), §10.1–10.4 (eval layers it feeds)
**Milestone:** the §10.0 keystone, built after M0/M1 (store exists; policy/hooks do not yet)
**Stack:** Python + UV (matches the M0/M1 store)

---

## 1. Purpose & thesis

Turn real (and synthetic) Claude Code sessions into **deterministic, offline replays** that drive a pluggable decision-maker and emit a structured **decision log**. This converts most evaluation from flaky, expensive live runs into reproducible replay — the precondition for regression tests, weight tuning, and CI (DESIGN.md §10.0).

The policy and hooks the harness will ultimately exercise are M2–M4 and **do not exist yet**. So v1 builds the *infrastructure* and wires the **M1 recency-only store query** as the first decision target (the deliberate "arm 2" baseline of §10.4). At M3 the semantic policy implements the same `decide()` Protocol (§3.4) and the forward-stable decision-log schema (§3.1) is unchanged — but capturing *offload* (a v1 non-goal) will later extend the engine, so the honest claim is "stable seam + stable log schema," not "zero future engine changes" (§6).

## 2. Scope

**In scope (v1)**
- A frozen, normalized **trace schema**.
- Two **capture adapters**: a synthetic `TraceBuilder` and a Claude Code transcript-JSONL adapter.
- A deterministic **replay engine** with a per-user-turn decision loop and a harness-local capture-during-replay ingest.
- A **`ReplayTarget` seam** + a `RecencyOnlyTarget` baseline.
- A serializable **decision log** + deterministic replay tests.

**Non-goals (v1)** — deferred, mostly to M3:
- Gold labels / precision@k / nDCG / eviction-regret (§10.2) — needs the labeled fixture set and a real policy.
- The semantic `PolicyTarget` (M3).
- A live capture hook (overlaps M2) and inferred subtask-boundary detection.
- Onload *injection* into a live window (that is the M4 hook path; the harness only computes and logs the decision).

## 3. Architecture

All code lives under `src/context_curator/replay/`. Like the build-time subagents, this is **dev/eval tooling — not bundled into the M7 runtime plugin**. It depends on the existing `context_curator.store` and `context_curator.tokens`.

```
trace (synthetic | transcript)
        │
        ▼
  ReplayEngine.run(trace)
   ├─ walk events in order, maintaining a TaskSignal (prompt + subtask + last-N tool calls)
   ├─ on tool_result → ingest_tool_result(...) writes a chunk to a fresh Store   (capture side)
   └─ on user_prompt → target.decide(signal, store) → Decision                   (onload side)
        │
        ▼
   DecisionLog  (serializable, deterministic; regression-diffed; M3 metrics consume it)
```

The ordering models reality: a user prompt for turn *N* sees only chunks captured from turns *1..N-1*; turn *N*'s own tool results are ingested for turn *N+1*. Capture-then-onload is therefore exercised within a single session.

### 3.1 Trace schema — `replay/schema.py`

Frozen pydantic v2 models (same style as `Chunk`). Events form a discriminated union on `kind`.

```python
class UserPrompt(BaseModel):        # decision point
    kind: Literal["user_prompt"] = "user_prompt"
    turn_index: int
    text: str
    subtask_id: str | None = None   # optional explicit boundary tag (synthetic sets it; adapter leaves None)

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

class AssistantMessage(BaseModel):  # carried for fidelity; ignored by the v1 decision loop
    kind: Literal["assistant_message"] = "assistant_message"
    text: str

TraceEvent = Annotated[
    UserPrompt | ToolCall | ToolResult | AssistantMessage,
    Field(discriminator="kind"),
]

class Trace(BaseModel):
    session_id: str
    source: str                     # "synthetic" | "transcript"
    events: list[TraceEvent]
```

Decision-side models (also in `schema.py`):

```python
class ToolRef(BaseModel):               # slim window entry — NOT the full ToolCall
    name: str
    call_id: str

class TaskSignal(BaseModel):
    turn_index: int
    prompt: str
    subtask_id: str | None
    recent_tool_calls: list[ToolRef]    # last N, in order; name+id only (no raw args, I3)

class SelectedChunk(BaseModel):
    key: str
    score: float | None                 # None for recency baseline; populated by M3 policy
    tokens: int                         # from the store's single tokenizer (§3.4), not re-estimated

class Decision(BaseModel):
    turn_index: int
    subtask_id: str | None
    prompt_preview: str                 # prompt[:80] (char slice — deterministic), human-readable only
    selected: list[SelectedChunk]       # what was onloaded (top-k under budget)
    total_tokens: int                   # sum of selected tokens; <= token_budget by construction
    # --- forward-stable fields (empty in v1; populated by the M3 policy target) ---
    candidates: list[SelectedChunk] = Field(default_factory=list)  # full ranked pool, for nDCG/regret
    offloaded: list[str] = Field(default_factory=list)             # select_offload keys (M3)

class DecisionLog(BaseModel):
    trace_session_id: str
    target_name: str
    decisions: list[Decision]
```

**Determinism note (load-bearing — this is the keystone's headline property).** Recency must NOT depend on wall-clock. Both stores stamp `created_at` via `utcnow_iso()` *at write time*, so during a fast replay loop `created_at` is non-deterministic (and platform-dependent tie structure). v1 therefore orders recency by an internal **monotonic write-sequence** (§3.4), making the onload set a pure function of write order.

**Scope of the byte-stability guarantee (explicit):**
- The serialized artifact is the **`DecisionLog`** (`model_dump()`), not `TaskSignal`. `Decision` does **not** embed `TaskSignal`, so raw `args`/window blobs never reach the log.
- `SelectedChunk` carries only `key`, `score`, `tokens` — no `tags`, no `content`, no timestamps.
- In **v1 the log contains no floats** (`score` is always `None`) and no `dict`s, so `model_dump()` is byte-stable across runs and machines. **M3 caveat:** when `PolicyTarget` populates float `score`s (embedding-derived, machine-sensitive), the log must **quantize/round `score`** before any cross-machine diff — flagged now so §6's weight-sweep workflow doesn't inherit a float-determinism landmine.
- The empty-decision contract (turn 1, or a store with no candidates) is `selected=[]`, `total_tokens=0`, `candidates=[]`, `offloaded=[]`.

### 3.2 Capture adapters — `replay/capture/`

- **`synthetic.py` — `TraceBuilder`** (fluent, deterministic):
  ```python
  trace = (TraceBuilder("sess-1")
           .user("set up auth")
           .tool("Read", {"path": "auth.py"}).result("def login(): ...")
           .user("now add logout", subtask_id="logout")
           .build())
  ```
  Auto-assigns `turn_index` (incremented per `.user(...)`) and `call_id`s (monotonic, e.g. `c0,c1,...`).

- **`transcript.py` — `parse_transcript(path) -> Trace`**: maps Claude Code session-JSONL records → normalized events. **The exact JSONL field names are pinned against a real transcript at implementation time and isolated entirely within this file**, so a Claude Code format change is contained here.

  **Structural assumptions (stated, because the engine's correctness depends on them):**
  - **A turn-starting user record is one whose content is a plain text message — NOT one whose content is a `tool_result` block.** In Claude Code JSONL, tool results are delivered in *user-role* records; only genuine user prompts start a turn and increment `turn_index`. A `tool_result`-bearing user record maps to a `ToolResult` event, never a `UserPrompt`. (This is the single most load-bearing adapter rule.)
  - **An assistant record may carry text AND multiple `tool_use` blocks.** It maps to one `AssistantMessage` (if it has text) followed by one `ToolCall` per `tool_use` block, **in block order**.
  - **`tool_result` records match their `tool_use` by id** (`tool_use_id`/`call_id`); the normalized `ToolResult.call_id` carries it. Uniqueness is *not* assumed across the session — the ingest ordinal (§3.3) makes duplicates harmless.
  - **Sidechain / sub-agent records are skipped in v1.** The harness models the *main session* (DESIGN §4.4: raw sub-agent output never enters the main window). Records flagged as sidechain (e.g. `isSidechain`) are dropped. **Orphan handling:** the adapter tracks `tool_use` ids it dropped as sidechain and **also drops any `tool_result` matching them**, so an orphaned sidechain result never becomes a main-session `ToolResult`. (Belt-and-suspenders with the engine's own "ingest only when the tool_use is in the main-session map" guard, §3.5.)
  - **Unknown/auxiliary record kinds** (thinking, system, meta) are skipped (forward-tolerant).
  - `subtask_id` is left `None` by the adapter (only synthetic traces set it; see §3.1 note).
  - Fixture cases prove: a `tool_result`-bearing user record does **not** increment `turn_index`; a sidechain-orphan `tool_result` is dropped.

  **Fixture realism:** the committed `sample_transcript.jsonl` is a **hand-scrubbed** real transcript — stable synthetic session id, `call_id`s, and paths, with machine-specific fields (timestamps, cwd, git branch, absolute paths) normalized — so the adapter test is portable and deterministic. It is documented as scrubbed, not raw.

### 3.3 Ingest seam — `replay/ingest.py`

A small, named seam that M2 can later promote/replace with the real capture logic:

```python
def ingest_tool_result(result: ToolResult, call: ToolCall,
                       session_id: str, ordinal: int, store: Store) -> None:
    """Write a tool result into the store as a candidate chunk (v1 capture stand-in).
    `call` is REQUIRED and non-optional: the engine only calls this when the matching
    tool_use is present in the MAIN session (I4). A tool_result with no matching main-
    session tool_use (e.g. its tool_use was a skipped sidechain) is dropped by the engine
    BEFORE this is called — never ingested with a synthesized `call` — so raw sub-agent
    output cannot leak into the main store (DESIGN §4.4)."""
    if result.error:
        return
    # `ordinal`: monotonic per-replay ingest counter (engine-supplied) → key uniqueness even
    # if `call_id` repeats; without it a duplicate call_id would overwrite (ON CONFLICT).
    key = f"session:{session_id}:tool:{ordinal:06d}:{result.call_id}"
    # source preserves the canonical (CamelCase) tool name for the §9 poisoning audit;
    # only the tag is lowercased for case-insensitive filtering.
    store.store(key, result.content, tags=[call.name.lower()],
                source=f"tool:{call.name}", ttl_s=None)  # ttl_s=None: replay chunks never expire (C2)
```

v1 mapping is intentionally minimal (successful, main-session tool results only). Richer
capture (decisions, contracts, file-ledger) is M2's job.

**Cross-doc contract change (needs §5/§9 sign-off):** this *narrows* DESIGN §5's `source` set
— v1 ingest only ever emits `tool:{ToolName}` (canonical case). §5's `tool:read` example
generalizes to `tool:{name}`; §9's `cc-guard` must match `source` against canonical CamelCase
tool names. `ttl_s=None` keeps replay candidates resident for the whole session.

### 3.4 ReplayTarget seam — `replay/target.py`

```python
class ReplayTarget(Protocol):
    name: str
    def decide(self, signal: TaskSignal, store: Store) -> Decision: ...

class RecencyOnlyTarget:
    name = "recency-only"
    def __init__(self, k: int = 10, token_budget: int | None = None,
                 tags: list[str] | None = None) -> None: ...
    def decide(self, signal: TaskSignal, store: Store) -> Decision:
        chunks = store.query(signal.prompt, tags=self.tags, k=self.k,
                             token_budget=self.token_budget)
        selected = [SelectedChunk(key=c.key, score=None,
                                  tokens=estimate_tokens(c.content)) for c in chunks]
        return Decision(turn_index=signal.turn_index, subtask_id=signal.subtask_id,
                        prompt_preview=signal.prompt[:80],
                        selected=selected, total_tokens=sum(s.tokens for s in selected))
```

**The seam contract.** `decide(signal, store)` gives the target read access to the *whole* store and makes it responsible for **candidate selection + ranking**; the engine only supplies the `TaskSignal` and records the returned `Decision`. This is deliberate: the recency baseline delegates ranking to `store.query`, while M3's `PolicyTarget` will instead pull candidates (e.g. `store.list`/iterate) and apply its own scoring formula (DESIGN §4.2) — both fit the same Protocol because ranking is the *target's* concern, not the engine's. `PolicyTarget` populates `score`s and the forward-stable `candidates`/`offloaded` fields. **Honest limit:** wiring `select_offload` (DESIGN §6) needs an active-window representation the engine does not yet pass; that is a deliberate v1 non-goal and a known future engine extension, not a drop-in.

**`k` vs `token_budget` precedence (pinned).** `store.query` already enforces both as first-fit in recency order: walk newest→oldest, stop when either `k` items are selected **or** the next item would exceed `token_budget` (whichever binds first). `total_tokens <= token_budget` always holds. A test exercises a trace where *both* limits bind simultaneously.

**Single token source of truth (invariant, not coincidence).** Per-chunk token counts come from one pure, content-only `estimate_tokens` (`context_curator.tokens`). `RecencyOnlyTarget` re-applies it to each selected chunk's content — this is a *second call site* (the store already used it for budgeting), so the invariant is stated explicitly: **`estimate_tokens` must remain pure and depend only on the chunk's own content**, so the store's budget decision and the target's reported `tokens` always coincide. A test asserts `Decision.total_tokens` equals the store's own budget accounting on a budget-binding case **and on the `token_budget=None` case** (where the store skips its budget loop and the target still sums per-chunk), so the invariant isn't silently scoped to the budget-binding path. The M3 tokenizer swap must preserve this purity (or the store must start returning the counts it used).

**Stable recency (the C-fix) — mechanism pinned.** `created_at` (wall-clock, §3.1) and a `rowid` tiebreak (unchanged by `ON CONFLICT … DO UPDATE`) both fail. v1 adds an **internal monotonic write-sequence** ordered `seq DESC`, store-internal (NOT on the `Chunk` schema):

- **SqliteStore** — add a `seq INTEGER NOT NULL` column to `_DDL`. **SQLite owns the sequence** (no Python counter → reopen-safe and thread-safe under `check_same_thread=False`). The **INSERT's column list/VALUES** sets `seq = (SELECT COALESCE(MAX(seq), 0) + 1 FROM chunks)`, **and** the `ON CONFLICT(key) DO UPDATE SET …` clause **must also include `seq = (SELECT COALESCE(MAX(seq),0)+1 FROM chunks)`** (both sites, not just one) so a re-store moves the key to the front (the exact case the old `rowid` tiebreak got wrong). **Both** `query` branches (unscoped *and* tenant-scoped) change `ORDER BY created_at DESC` → `ORDER BY seq DESC`. *Migration scope:* `_DDL` is `CREATE TABLE IF NOT EXISTS`, so **v1 assumes fresh DBs** — no `ALTER TABLE` migration of a pre-existing M1 store (dev/eval DBs are throwaway).
- **InMemoryStore** — an instance counter (`self._next_seq`), assigned per `store()` into a parallel `key → seq` map (overwrite reassigns a higher seq); `query` sorts by that seq descending.
- **Equivalence scope:** recency *order* is identical across backends by construction. **Membership** is identical only **absent expiry** — `InMemoryStore` currently has *no* TTL filtering while `SqliteStore` does. The replay ingest writes `ttl_s=None` (§3.3), so nothing expires mid-replay and the backends stay membership-equivalent. The InMemory-TTL gap is a tracked pre-existing asymmetry (out of scope here; revisit if/when TTL parity matters).
- Tests: `test_store_seq_ordering.py` (write A,B, re-write A ⇒ A newest; identical on both backends) **plus a reopen test** (write, close, reopen `SqliteStore`, write, assert the new write sorts newest — proving the `COALESCE(MAX)+1` seeding). A recency-order assertion is added to the shared contract suite (no existing contract test pins order, so this is additive).

This is a genuine M1 store change (its own task, landing first in the plan), not a one-liner.

### 3.5 Replay engine — `replay/engine.py`

```python
class ReplayEngine:
    def __init__(self, target: ReplayTarget,
                 store_factory: Callable[[], Store] = ...,   # default: fresh InMemoryStore
                 recent_window: int = 5) -> None: ...
    def run(self, trace: Trace) -> DecisionLog: ...
```

`run` builds a **fresh store per replay** (default `InMemoryStore` — deterministic, no disk), maintains a `deque(maxlen=recent_window)` of recent `ToolRef`s, a `call_id → ToolCall` map, and a monotonic ingest `ordinal`. For each event in order:
- `ToolCall` → append `ToolRef(name, call_id)` to the window; record `call_id → ToolCall` in the map.
- `ToolResult` → look up its `call_id` in the map. **If absent (no matching main-session tool_use — e.g. a sidechain orphan that slipped past the adapter), skip it entirely** (do not ingest; I4). If present, `ingest_tool_result(result, call, session_id, ordinal, store)` then `ordinal += 1`.
- `UserPrompt` → build `TaskSignal` (with `list(window)` of `ToolRef`s) and append `target.decide(signal, store)`.
- `AssistantMessage` → ignore.

Returns the `DecisionLog`. The `call_id → ToolCall` map is bounded by trace size (acceptable for dev tooling; noted, not pruned in v1).

**Window semantics (pinned):** `recent_tool_calls` is the last `recent_window` `ToolCall`s seen so far in event order, **including errored calls** (they are still recent activity signal) and regardless of whether their results were ingested. The window does **not** reset at subtask boundaries in v1. Because a `UserPrompt` for turn *N* is processed before turn *N*'s own tools, its signal reflects tool calls from prior turns only.

**`subtask_id` is a forward hook, not functional in v1.** It is carried `UserPrompt → TaskSignal → Decision` and appears in the log, but no v1 decision logic reads it (the recency target ignores it; the adapter never sets it). A test asserts only that a synthetic `subtask_id` is carried through to the decision log. It exists because DESIGN §4.2's task signal includes "active subtask"; M3 may consume it.

## 4. Testing (v1 — no gold labels)

Deterministic, no LLM. Under `tests/replay/`:
- **`test_schema.py`** — event/Trace round-trip; discriminated-union parsing from dicts.
- **`test_synthetic_builder.py`** — `TraceBuilder` assigns turn indices / call ids; `subtask_id` carried.
- **`test_transcript_adapter.py`** — parse `fixtures/sample_transcript.jsonl` → assert the normalized event sequence; unknown records skipped; a **`tool_result`-bearing user record does not increment `turn_index`** (I2); an assistant record with text + 2 `tool_use` blocks emits 1 `AssistantMessage` + 2 `ToolCall`s in order; a **sidechain-orphan `tool_result` is dropped** (I4).
- **`test_ingest.py`** — a tool result becomes a retrievable chunk with the expected key/tags/source; `error=True` results are skipped; a **duplicate `call_id`** produces two distinct chunks (ordinal namespacing, I1), neither overwritten.
- **`test_recency_target.py`** — against a hand-built store state: onloads the most-recent matching chunk; `total_tokens` correct, `<= token_budget`, and **equal to the store's own budget accounting** (I1); a case where **`k` and `token_budget` bind simultaneously** resolves by first-fit (§3.4); empty store → `selected=[]`, `total_tokens=0`.
- **`test_store_seq_ordering.py`** (store change) — recency follows write-sequence not wall-clock: writing A then B then **re-writing A** orders A newest (overwrite bumps `seq`); identical order on `InMemoryStore` and `SqliteStore`.
- **`test_engine_determinism.py`** — (a) replaying the same trace twice yields **byte-identical** `DecisionLog` (`model_dump()` equality), and a tight ingest loop does not flake (the C-fix); (b) a turn only sees chunks captured in prior turns (turn 1 → empty decision); (c) the last-N tool-call window (incl. errored calls) is honored; (d) a synthetic `subtask_id` is carried through to the log; (e) a turn whose only prior results were `error=True` yields an empty decision.

## 5. File structure

```
src/context_curator/replay/
  __init__.py
  schema.py            # Trace + events + TaskSignal/Decision/SelectedChunk/DecisionLog
  capture/
    __init__.py
    synthetic.py       # TraceBuilder
    transcript.py      # parse_transcript (CC JSONL, isolated)
  ingest.py            # ingest_tool_result (M2-alignable seam)
  target.py            # ReplayTarget Protocol + RecencyOnlyTarget
  engine.py            # ReplayEngine
tests/replay/
  fixtures/sample_transcript.jsonl   # hand-scrubbed, stable ids/paths
  test_schema.py
  test_synthetic_builder.py
  test_transcript_adapter.py
  test_ingest.py
  test_recency_target.py
  test_engine_determinism.py
```
**Store change (prerequisite, lands first in the plan):** add an internal monotonic write-sequence to `Store` writes and order `query` by `seq DESC` (§3.4), in both `SqliteStore` (new `seq` column) and `InMemoryStore` (parallel `key → seq` map), with `test_store_seq_ordering.py` and a recency-order assertion added to the shared contract suite. `created_at` is retained for TTL only.

## 6. How this connects to M3 (forward-compat, not built now)

- `PolicyTarget(ReplayTarget)` implements the same `decide(signal, store)` Protocol behind which it pulls candidates and applies its own scoring (DESIGN §4.2), populating `score`s and the already-present `candidates` field. The engine and `DecisionLog` schema do not change for onload.
- The forward-stable `Decision.candidates` (full ranked pool with scores) + a labeled fixture set (synthetic traces with *planted* gold chunks, gold known by construction) enables precision@k / nDCG / eviction-regret (§10.2) — these need the candidate pool, which is why the field exists in v1 even though it is empty then.
- Weight sweeps = run the engine over the fixture corpus with different `PolicyTarget` configs and diff decision logs / metrics. This is the §10.4 arm-2-vs-arm-3 keystone comparison.
- **The one honest exception to "no engine changes":** capturing `select_offload` (DESIGN §6) requires the engine to pass an active-window representation to the target, which v1 does not build. The `Decision.offloaded` field is reserved for it, but the engine extension is explicitly deferred.

---

## Design Critique Log

This design passed three independent adversarial review rounds (a fresh reviewer each round, each seeing the prior round's revision) before being presented.

### Critique Round 1
**Findings (Critical):** the headline determinism guarantee was **false** — recency ranked on `created_at`, which `store()` stamps with wall-clock *at replay time* (C1), and the proposed `rowid DESC` tiebreak does not survive the store's `ON CONFLICT … DO UPDATE` (an overwrite keeps its old `rowid`) (C2). **Important:** `call_id` uniqueness was assumed but unenforced, so a duplicate would silently overwrite a captured chunk (I1); passing the whole store to `decide()` plus an onload-only `Decision` made the "M3 drops in with no engine changes" promise false, and the log couldn't express candidates/scores/offload (I2); two independent token-estimate call sites could desync and the `k`-vs-`token_budget` precedence was unspecified (I3); the transcript JSONL assumptions (sidechains, multi-`tool_use`, fixture realism) were unstated (I4); `subtask_id` was plumbed but unused (I5).
**Resolved by:** replacing wall-clock recency with an **internal monotonic write-sequence** (`seq`); namespacing the ingest key with a monotonic **ordinal**; adding forward-stable `Decision.candidates`/`offloaded` and softening the M3 promise to "stable seam + stable log schema"; pinning the single-tokenizer invariant and first-fit `k`/budget precedence; enumerating the transcript structural assumptions; and labeling `subtask_id` a carried-but-unused forward hook with a carry-through test.

### Critique Round 2
**Findings (Critical):** the round-1 `seq` fix was the *least-specified* part — generation mechanism, the conflict-update `SET seq`, and reopen/threading behavior were all implicit, any of which could resurrect the overwrite bug (C1); `seq DESC` had to change **both** SQL query branches, and `InMemoryStore` has **no TTL filtering** so the two backends diverge on *membership* under expiry (C2); residual determinism gaps — `args`/`tags`/float-`score` ordering and whether `TaskSignal` reaches the log (C3). **Important:** the token count was still recomputed at a second call site (I1); the transcript adapter didn't state how a real user prompt is distinguished from a `tool_result`-bearing user record (I2); `recent_tool_calls` dragged full `ToolCall.args` blobs into every signal (I3); a sidechain-orphan `tool_result` would be ingested with a synthesized `call`, leaking sub-agent output into the main store and violating DESIGN §4.4 (I4); the `source` reconciliation lowercased tool names, diverging from the §9 audit's canonical case (I5).
**Resolved by:** pinning the SQLite-owned `seq` (`COALESCE(MAX(seq),0)+1` on insert **and** conflict-update, both query branches `seq DESC`, reopen-safe, thread-safe); ingesting with `ttl_s=None` and scoping the backend-equivalence claim to "absent expiry" (the InMemory-TTL gap noted as tracked); stating the byte-stability scope (no floats/`args` in the v1 log, `Decision` excludes `TaskSignal`, M3 must quantize `score`); the token invariant + assertion test; the explicit turn-vs-`tool_result` adapter rule; a slim `ToolRef` (name+id) window entry; engine-skips-ingest-when-no-main-session-tool_use plus adapter orphan-drop; and preserving canonical tool-name case in `source` (flagged as a §5/§9 cross-doc change).

### Critique Round 3
**Verdict: IMPLEMENTATION-READY.** The reviewer verified the load-bearing claims against the actual source: the `COALESCE(MAX(seq),0)+1` conflict subquery is off-by-one-free and overwrite-correct; `seq DESC` composes with the existing per-row expiry/scope/tag/first-fit `query` loop; `ttl_s=None` flows through `_compute_expires_at` to "never expires"; and the two backends' different `k`/budget application orders were proven to yield identical result sets (newest-first + first-fit means `k`-truncation never moves where the budget stops). Only three **Minor** items remained.
**Resolved by:** noting v1 assumes fresh DBs (no `ALTER` migration); clarifying the `seq` value is set in the INSERT **and** the conflict-update; and extending the token-invariant test to the `token_budget=None` case. No design decision changed.
