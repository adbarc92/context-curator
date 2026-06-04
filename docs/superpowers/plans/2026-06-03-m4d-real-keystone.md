# M4d — Real-Data Keystone Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the real-transcript evaluation harness (entity/re-fetch gold labeler, session-clustered CI, self-correcting precision gate, adapted audit, lexical-bias diagnostic, flag-on regression tests) and run it to produce an honest real-data verdict — most likely INCONCLUSIVE-underpowered with a needed-N — without changing production unless the verdict is a gated GREEN.

**Architecture:** Two phases. **Phase A** = deterministic, CI-testable harness (no bge, no real data): turn a `Trace` into per-turn `Fixture`s with downstream-use gold, add a session-cluster bootstrap + precision gate, thread session ids through the keystone, adapt the audit, add the lexical-bias diagnostic and flag-on regression tests. **Phase B** = the non-deterministic run (harvest the local `_real_local/` corpus → keystone → clustered CI + precision gate → verdict-or-needed-N doc), and a contingent GREEN-only production flip.

**Tech Stack:** Python + UV; pydantic v2; pytest; ruff (`E,F,I,UP,B`, ≤100). Reuses the M4c eval stack.

**Spec:** `docs/superpowers/specs/2026-06-03-m4d-real-keystone-design.md` (hardened through 3 critique rounds + a self-correcting-gate refinement).

**Branch:** `feat/m4d-real-keystone` (already checked out, off `main`).

---

## Conventions
- Run everything via `uv run`; ignore the `VIRTUAL_ENV` mismatch warning. Lint: `uv run ruff check <files>`. TDD; commit per task.
- **Existing signatures (don't change):** `replay/schema.py`: `Trace(session_id, source, events)`; events are `UserPrompt(kind,turn_index,text,subtask_id)`, `ToolCall(kind,call_id,name,args:dict)`, `ToolResult(kind,call_id,content,error)`, `AssistantMessage(kind,text)`. `replay/capture/transcript.py::parse_transcript(path)->Trace`. `eval/fixtures.py`: `Fixture(name,chunks,prompt,recent_tools,gold_keys,split)`, `FixtureChunk(key,content,tags)`, `load_fixtures(dir)` (skips `_`-prefixed). `eval/stats.py::bootstrap_ci(deltas,*,seed,alpha=0.1,iters=2000)->(lo,hi)`. `eval/keystone.py::run_keystone(corpus_dir,embedder,k=10,seed=0)->KeystoneReport` with fields `best_weights,arm3,arm2,arm_bm25,n_test,per_fixture_ndcg_delta,delta_ci90,verdict`. `eval/bm25.py::bm25_scores(prompt,docs)`, `Bm25Target`.
- Pinned constants (spec §5.7): `MEI=0.10`, `W=5`, `n_sessions≥3` floor, precision gate `width≤MEI`, lexical-bias margin `+0.15` BM25 R@3, min candidates `5` / min gold `1`.

---

# PHASE A — Deterministic harness (CI, no bge/real data)

## Task 1: `Fixture.session_id`

**Files:** Modify `src/context_curator/eval/fixtures.py`. Test: `tests/eval/test_fixtures.py` (append).

- [ ] **Step 1: failing test** — append to `tests/eval/test_fixtures.py`:
```python
def test_fixture_carries_session_id():
    fx = Fixture(name="f", chunks=[FixtureChunk(key="a", content="A")],
                 prompt="p", gold_keys=["a"], split="test", session_id="sess-1")
    assert fx.session_id == "sess-1"


def test_fixture_session_id_defaults_none():
    fx = Fixture(name="f", chunks=[FixtureChunk(key="a", content="A")],
                 prompt="p", gold_keys=["a"], split="test")
    assert fx.session_id is None
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_fixtures.py -k session_id -v` → fail (unexpected kwarg / attribute).

- [ ] **Step 3: implement** — in `fixtures.py`, add the field to `Fixture` (after `split`):
```python
    split: Literal["train", "test"] = "train"
    session_id: str | None = None        # M4d: source-transcript identity for session-clustered CI
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_fixtures.py -v`. `uv run ruff check src/context_curator/eval/fixtures.py`.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): Fixture.session_id for session-clustered CI"`

---

## Task 2: entity extraction + path equivalence

**Files:** Create `src/context_curator/eval/real_corpus.py`. Test: `tests/eval/test_real_corpus.py`.

- [ ] **Step 1: failing test** — `tests/eval/test_real_corpus.py`:
```python
from context_curator.eval.real_corpus import entities_match, extract_entities
from context_curator.replay.schema import ToolCall


def _c(name, **args):
    return ToolCall(call_id="x", name=name, args=args)


def test_extract_path_args():
    assert extract_entities(_c("Read", file_path="/a/b.py"))
    assert extract_entities(_c("Grep", path="/a")) 
    assert extract_entities(_c("NotebookEdit", notebook_path="/a/n.ipynb"))
    # pattern-only Glob (no path) yields no entity
    assert extract_entities(_c("Glob", pattern="**/*.py")) == set()
    # path-less tool yields nothing
    assert extract_entities(_c("Bash", command="ls")) == set()


def test_equivalence_exact_and_dir_containment():
    read = extract_entities(_c("Read", file_path="/a/b.py"))
    grep_dir = extract_entities(_c("Grep", path="/a"))
    other = extract_entities(_c("Read", file_path="/c/d.py"))
    assert entities_match(read, read)            # exact
    assert entities_match(grep_dir, read)        # /a contains /a/b.py
    assert not entities_match(read, other)       # disjoint
    assert not entities_match(extract_entities(_c("Glob", pattern="*")), read)  # empty never matches
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_real_corpus.py -v` → ImportError.

- [ ] **Step 3: implement** — `src/context_curator/eval/real_corpus.py` (entity layer only; harvest added next task):
```python
"""Real-transcript corpus harvest (design §3). Turns a parsed `Trace` into per-turn `Fixture`s with
DOWNSTREAM-USE gold (a prior chunk is gold iff its file-path entity is re-FETCHED by a retrieval call
within W turns, excluding verify-Read-after-Edit). Deterministic; no bge, no LLM. Entities are
extracted from each chunk's producing `ToolCall.args` (recovered via the call_id embedded in the
chunk key)."""
from __future__ import annotations

import os

from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.replay.schema import ToolCall, Trace, UserPrompt

_PATH_ARGS = ("file_path", "notebook_path", "path")        # structured path args (Bash deferred)
_RETRIEVAL = {"read", "grep", "glob", "notebookread"}      # lowercased; re-fetch = needed-but-absent
_EDIT = {"edit", "write", "multiedit", "notebookedit"}     # edits never generate gold (churn)


def _canon(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def extract_entities(call: ToolCall) -> set[str]:
    """Canonical absolute file-path entities from a tool call's args. Pattern-only / path-less
    calls yield the empty set."""
    out: set[str] = set()
    for key in _PATH_ARGS:
        v = call.args.get(key)
        if isinstance(v, str) and v:
            out.add(_canon(v))
    return out


def _contains(dir_: str, file_: str) -> bool:
    return file_.startswith(dir_.rstrip(os.sep) + os.sep)


def entities_match(a: set[str], b: set[str]) -> bool:
    """True iff some entity in `a` equals, contains, or is contained by some entity in `b`.
    Empty sets never match (pattern-only Glob / path-less calls)."""
    for x in a:
        for y in b:
            if x == y or _contains(x, y) or _contains(y, x):
                return True
    return False
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_real_corpus.py -v`; `uv run ruff check src/context_curator/eval/real_corpus.py tests/eval/test_real_corpus.py`.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): entity extraction + path-equivalence for downstream-use gold"`

---

## Task 3: `harvest_trace` — turn segmentation, downstream-use gold, fixtures

**Files:** Modify `src/context_curator/eval/real_corpus.py`. Test: `tests/eval/test_real_corpus.py` (append).

- [ ] **Step 1: failing test** — append:
```python
from context_curator.eval.real_corpus import harvest_trace
from context_curator.replay.schema import ToolResult, Trace, UserPrompt


def _trace(events):
    return Trace(session_id="sess-1", source="t", events=events)


def _prior_chunks(n):
    # n filler turns each producing one unrelated chunk, so a later turn has >=5 candidates
    ev = []
    for i in range(n):
        ev.append(UserPrompt(turn_index=i, text=f"filler {i}"))
        ev.append(ToolCall(call_id=f"f{i}", name="Read", args={"file_path": f"/filler/{i}.py"}))
        ev.append(ToolResult(call_id=f"f{i}", content=f"filler content {i}"))
    return ev


def test_harvest_marks_refetch_as_gold():
    # turn 0 reads /a/b.py (chunk g); 5 fillers; final turn T re-Greps /a -> chunk g is gold for T
    events = [
        UserPrompt(turn_index=0, text="open the auth file"),
        ToolCall(call_id="g", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="g", content="def login(): ..."),
        *_prior_chunks(5),
        UserPrompt(turn_index=6, text="search the auth dir"),
        ToolCall(call_id="r", name="Grep", args={"path": "/a"}),
        ToolResult(call_id="r", content="match"),
    ]
    fxs = harvest_trace(_trace(events), w=5, min_candidates=5)
    # the turn-6 fixture must exist and have chunk "g" as gold
    f6 = [f for f in fxs if f.prompt == "search the auth dir"]
    assert f6 and "g" in f6[0].gold_keys
    assert f6[0].session_id == "sess-1"


def test_verify_read_after_edit_is_not_gold():
    # turn 0 reads /a/b.py (chunk g); fillers; final turn EDITS then re-READS /a/b.py -> NOT gold
    events = [
        UserPrompt(turn_index=0, text="open file"),
        ToolCall(call_id="g", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="g", content="x"),
        *_prior_chunks(5),
        UserPrompt(turn_index=6, text="fix and verify"),
        ToolCall(call_id="e", name="Edit", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="e", content="edited"),
        ToolCall(call_id="v", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="v", content="x2"),
    ]
    fxs = harvest_trace(_trace(events), w=5, min_candidates=5)
    f6 = [f for f in fxs if f.prompt == "fix and verify"]
    assert f6 and "g" not in f6[0].gold_keys     # verify-read-after-edit excluded


def test_drops_turns_below_min_candidates():
    events = [
        UserPrompt(turn_index=0, text="first"),
        ToolCall(call_id="g", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="g", content="x"),
        UserPrompt(turn_index=1, text="second re-reads"),
        ToolCall(call_id="r", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="r", content="x2"),
    ]
    # turn 1 has only 1 candidate (<5) -> dropped
    assert harvest_trace(_trace(events), w=5, min_candidates=5) == []
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_real_corpus.py -k "harvest or verify or min_candidates" -v` → fail.

- [ ] **Step 3: implement** — append to `real_corpus.py`:
```python
class _Turn:
    __slots__ = ("index", "prompt", "subtask_id", "calls", "results")

    def __init__(self, index: int, prompt: str, subtask_id: str | None) -> None:
        self.index = index
        self.prompt = prompt
        self.subtask_id = subtask_id
        self.calls: list[ToolCall] = []          # tool calls issued in this turn (ordered)
        self.results: list[ToolResult] = []      # tool results captured in this turn (ordered)


def _segment(trace: Trace) -> list[_Turn]:
    """Split the flat event list into turns delimited by UserPrompt. Events before the first
    UserPrompt are ignored (no task to attribute them to)."""
    turns: list[_Turn] = []
    for ev in trace.events:
        if isinstance(ev, UserPrompt):
            turns.append(_Turn(ev.turn_index, ev.text, ev.subtask_id))
        elif turns and isinstance(ev, ToolCall):
            turns[-1].calls.append(ev)
        elif turns and isinstance(ev, ToolResult):
            turns[-1].results.append(ev)
        # AssistantMessage: ignored for gold (design defers assistant-reference gold)
    return turns


def harvest_trace(trace: Trace, *, w: int = 5, min_candidates: int = 5) -> list[Fixture]:
    """Per-turn fixtures with downstream-use gold (design §3.3). Gold = a prior candidate chunk whose
    entity is re-fetched by a RETRIEVAL call within [T, T+w], excluding a re-fetch of an entity that
    was EDITED earlier in that same turn (verify-after-edit)."""
    turns = _segment(trace)
    call_by_id = {c.call_id: c for c in (c for t in turns for c in t.calls)}

    # cumulative candidate chunks captured BEFORE each turn index (chronological, oldest first)
    chunks_before: list[list[tuple[str, str, set[str]]]] = []  # per turn: [(key, content, entities)]
    running: list[tuple[str, str, set[str]]] = []
    for t in turns:
        chunks_before.append(list(running))
        for res in t.results:
            producing = call_by_id.get(res.call_id)
            ents = extract_entities(producing) if producing else set()
            running.append((res.call_id, res.content, ents))

    fixtures: list[Fixture] = []
    for i, t in enumerate(turns):
        candidates = chunks_before[i]
        if len(candidates) < min_candidates:
            continue
        # entities genuinely re-fetched (needed-but-absent) within the forward window [i, i+w]
        refetched: set[str] = set()
        for tw in turns[i: i + w + 1]:
            edited_here: set[str] = set()
            for call in tw.calls:
                ents = extract_entities(call)
                low = call.name.lower()
                if low in _EDIT:
                    edited_here |= ents
                elif low in _RETRIEVAL:
                    for e in ents:
                        if e not in edited_here:          # exclude verify-read-after-edit
                            refetched.add(e)
        gold = [key for key, _c, ents in candidates
                if ents and any(entities_match({e}, ents) for e in refetched)]
        if not gold:
            continue
        fixtures.append(Fixture(
            name=f"{trace.session_id}:t{t.index}",
            chunks=[FixtureChunk(key=k, content=c) for k, c, _e in candidates],
            prompt=t.prompt, recent_tools=[], gold_keys=gold,
            split="train", session_id=trace.session_id,
        ))
    return fixtures
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_real_corpus.py -v`; ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): harvest_trace — downstream-use gold (retrieval re-fetch, W=5, verify-edit excluded)"`

---

## Task 4: `harvest_corpus` — multi-file, session split, sample-trace integration test

**Files:** Modify `src/context_curator/eval/real_corpus.py`. Test: `tests/eval/test_real_corpus.py` + `tests/eval/_traces/sample.jsonl` (committed, synthetic — no private content).

- [ ] **Step 1: write the committed sample trace** — `tests/eval/_traces/sample.jsonl` (one CC-format record per line; two sessions so split-by-session is exercised). Keep it small but ≥5 candidates before a gold turn. Example (abbreviated — write ≥6 turns in session A with a re-fetch, and a session B):
```json
{"type":"user","sessionId":"A","message":{"role":"user","content":"open auth"}}
{"type":"assistant","sessionId":"A","message":{"role":"assistant","content":[{"type":"tool_use","id":"a1","name":"Read","input":{"file_path":"/p/auth.py"}}]}}
{"type":"user","sessionId":"A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"a1","content":"def login"}]}}
```
(Author the full file so that, after `parse_transcript`, session A yields ≥1 kept fixture with gold and session B yields ≥1; include ≥5 filler read turns before the gold turn. Verify with the test below.)

- [ ] **Step 2: failing test** — append:
```python
from pathlib import Path

import context_curator.eval as e
from context_curator.eval.real_corpus import harvest_corpus


def test_harvest_corpus_from_sample_jsonl_splits_by_session():
    sample = str(Path(e.__file__).parent.parent.parent.parent / "tests" / "eval" / "_traces" / "sample.jsonl")
    fxs = harvest_corpus([sample], w=5, min_candidates=5, test_frac=0.5, seed=0)
    assert fxs, "sample must yield >=1 fixture"
    sessions = {f.session_id for f in fxs}
    # no session straddles splits
    for s in sessions:
        splits = {f.split for f in fxs if f.session_id == s}
        assert len(splits) == 1, f"session {s} straddles train/test"
```
(If the sample yields only one session's fixtures, expand the sample so both A and B survive; the no-straddle invariant is the real assertion.)

- [ ] **Step 3: implement** — append to `real_corpus.py`:
```python
import random

from context_curator.replay.capture.transcript import parse_transcript


def harvest_corpus(paths: list[str], *, w: int = 5, min_candidates: int = 5,
                   test_frac: float = 0.75, seed: int = 0) -> list[Fixture]:
    """Harvest every transcript, then assign train/test BY WHOLE SESSION (no session straddles a
    split — prevents leakage, design §3.4). `test_frac` = fraction of SESSIONS placed in test."""
    by_session: dict[str, list[Fixture]] = {}
    for p in paths:
        for fx in harvest_trace(parse_transcript(p), w=w, min_candidates=min_candidates):
            by_session.setdefault(fx.session_id or "unknown", []).append(fx)
    sessions = sorted(by_session)
    rng = random.Random(seed)
    rng.shuffle(sessions)
    n_test = max(1, round(len(sessions) * test_frac)) if sessions else 0
    test_sessions = set(sessions[:n_test])
    out: list[Fixture] = []
    for s in sessions:
        split = "test" if s in test_sessions else "train"
        for fx in by_session[s]:
            out.append(fx.model_copy(update={"split": split}))
    return out
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_real_corpus.py -v`; ruff. (Adjust `sample.jsonl` until the test passes with ≥1 fixture and no straddle.)

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): harvest_corpus — multi-transcript, split-by-session + sample trace"`

---

## Task 5: `cluster_bootstrap_ci`

**Files:** Modify `src/context_curator/eval/stats.py`. Test: `tests/eval/test_stats.py` (create or append).

- [ ] **Step 1: failing test** — append to `tests/eval/test_stats.py`:
```python
import math

from context_curator.eval.stats import bootstrap_ci, cluster_bootstrap_ci


def test_cluster_ci_wider_than_iid_when_clustered():
    # two tight clusters far apart -> clustered CI must be wider than iid on the same values
    deltas = [0.0, 0.01, -0.01, 0.40, 0.41, 0.39]
    clusters = ["s1", "s1", "s1", "s2", "s2", "s2"]
    lo_c, hi_c = cluster_bootstrap_ci(deltas, clusters, seed=0)
    lo_i, hi_i = bootstrap_ci(deltas, seed=0)
    assert (hi_c - lo_c) > (hi_i - lo_i)


def test_cluster_ci_single_session_is_width_of_ignorance():
    lo, hi = cluster_bootstrap_ci([0.1, 0.2], ["s1", "s1"], seed=0)
    assert lo == -math.inf and hi == math.inf


def test_cluster_ci_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        cluster_bootstrap_ci([0.1, 0.2], ["s1"], seed=0)
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_stats.py -k cluster -v` → ImportError.

- [ ] **Step 3: implement** — append to `stats.py`:
```python
import math


def cluster_bootstrap_ci(deltas: list[float], cluster_ids: list[str], *, seed: int,
                         alpha: float = 0.1, iters: int = 2000) -> tuple[float, float]:
    """Session-clustered percentile CI (design §5.3): resample whole sessions with replacement, then
    take all of a resampled session's deltas. Respects intra-session correlation (the true N is the
    number of sessions). Contract: len mismatch -> ValueError; 0 sessions -> (0,0); 1 session ->
    (-inf,+inf) width-of-ignorance (a single cluster carries no between-session information)."""
    if len(deltas) != len(cluster_ids):
        raise ValueError("deltas and cluster_ids must have equal length")
    by_cluster: dict[str, list[float]] = {}
    for d, c in zip(deltas, cluster_ids, strict=True):
        by_cluster.setdefault(c, []).append(d)
    clusters = list(by_cluster)
    if not clusters:
        return (0.0, 0.0)
    if len(clusters) == 1:
        return (-math.inf, math.inf)
    rng = random.Random(seed)
    n = len(clusters)
    means: list[float] = []
    for _ in range(iters):
        pool: list[float] = []
        for _ in range(n):
            pool.extend(by_cluster[clusters[rng.randrange(n)]])
        means.append(sum(pool) / len(pool))
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_stats.py -v`; ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): cluster_bootstrap_ci (session-clustered CI, n_sessions=1 -> width-of-ignorance)"`

---

## Task 6: precision gate + needed-N

**Files:** Create `src/context_curator/eval/precision_gate.py`. Test: `tests/eval/test_precision_gate.py`.

- [ ] **Step 1: failing test** — `tests/eval/test_precision_gate.py`:
```python
from context_curator.eval.precision_gate import precision_gate


def test_floor_below_three_sessions_is_harness_only():
    g = precision_gate(lo=0.2, hi=0.25, n_sessions=2, mei=0.10)
    assert g.status == "harness-only" and g.needed_n is None


def test_wide_ci_is_underpowered_with_needed_n():
    # width 0.30 > MEI 0.10 -> underpowered; needed_n = ceil(5 * (0.30/0.10)^2) = 45
    g = precision_gate(lo=0.0, hi=0.30, n_sessions=5, mei=0.10)
    assert g.status == "inconclusive-underpowered" and g.needed_n == 45


def test_tight_ci_passes_to_verdict():
    g = precision_gate(lo=0.12, hi=0.20, n_sessions=10, mei=0.10)   # width 0.08 <= MEI
    assert g.status == "verdict" and g.needed_n is None
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_precision_gate.py -v` → ImportError.

- [ ] **Step 3: implement** — `src/context_curator/eval/precision_gate.py`:
```python
"""Self-correcting precision gate (design §5.4). Replaces a guessed MIN_SESSIONS: a verdict is
rendered only when the session-clustered CI is precise enough to place the effect (width <= MEI);
otherwise INCONCLUSIVE-underpowered with a computed needed-N (how many sessions to reach the
target)."""
from __future__ import annotations

import math
from dataclasses import dataclass

_FLOOR = 3   # definitional minimum sessions for the clustered bootstrap to be meaningful


@dataclass
class GateResult:
    status: str               # "harness-only" | "inconclusive-underpowered" | "verdict"
    needed_n: int | None      # sessions needed to reach width<=MEI (only when underpowered)


def precision_gate(*, lo: float, hi: float, n_sessions: int, mei: float = 0.10) -> GateResult:
    if n_sessions < _FLOOR:
        return GateResult("harness-only", None)
    width = hi - lo
    if not math.isfinite(width) or width > mei:
        if math.isfinite(width) and width > 0:
            needed = math.ceil(n_sessions * (width / mei) ** 2)
        else:
            needed = None
        return GateResult("inconclusive-underpowered", needed)
    return GateResult("verdict", None)
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_precision_gate.py -v`; ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): self-correcting precision gate + needed-N"`

---

## Task 7: keystone — emit per-fixture session ids

**Files:** Modify `src/context_curator/eval/keystone.py`. Test: `tests/eval/test_keystone_proxy.py` (append).

- [ ] **Step 1: failing test** — append:
```python
def test_keystone_report_exposes_session_ids_aligned_with_deltas():
    from dataclasses import fields

    from context_curator.eval import keystone
    names = {f.name for f in fields(keystone.KeystoneReport)}
    assert "test_session_ids" in names
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_keystone_proxy.py -k session_ids -v` → fail.

- [ ] **Step 3: implement** — in `keystone.py`: add `test_session_ids: list[str | None]` to `KeystoneReport` (after `per_fixture_ndcg_delta`), and in `run_keystone` build it aligned with the test fixtures used for the deltas, passing it into the constructor:
```python
@dataclass
class KeystoneReport:
    best_weights: PolicyWeights
    arm3: ArmMetrics
    arm2: ArmMetrics
    arm_bm25: ArmMetrics
    n_test: int
    per_fixture_ndcg_delta: list[float]
    test_session_ids: list[str | None]
    delta_ci90: tuple[float, float]
    verdict: str
```
In `run_keystone`, after computing `deltas` over `test`, add `session_ids = [f.session_id for f in test]` and update the `KeystoneReport(...)` call to pass `session_ids` in the new position:
```python
    return KeystoneReport(
        best, arm3, arm2, arm_bm25, len(test), deltas,
        [f.session_id for f in test], (lo, hi), verdict,
    )
```
(The per-fixture deltas are built by `zip` over `test` in the same order, so `test_session_ids[i]` aligns with `per_fixture_ndcg_delta[i]`.)

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_keystone_proxy.py -v` (the M4c bge smoke + proxy tests still pass; the new field is additive). `uv run pytest tests/eval -q`; ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): keystone emits per-fixture test_session_ids for clustered CI"`

---

## Task 8: adapt `corpus_audit` — optional hard_neg, pinned distractor, degenerate-recency flag

**Files:** Modify `src/context_curator/eval/corpus_audit.py`. Test: `tests/eval/test_corpus_audit.py` (append).

- [ ] **Step 1: failing test** — append:
```python
from context_curator.eval.corpus_audit import audit_corpus


def _fx_real(name, gold_pos, n=12):
    from context_curator.eval.fixtures import Fixture, FixtureChunk
    chunks = [FixtureChunk(key=f"{name}:c{i}", content=f"content {i}") for i in range(n)]  # no tags
    return Fixture(name=name, chunks=chunks, prompt="p", gold_keys=[f"{name}:c{gold_pos}"],
                   split="test")


def test_audit_real_mode_ignores_missing_hard_negs():
    # mixed-recency real corpus with NO hard_neg tags must still pass when require_hard_neg=False
    positions = [1, 5, 9, 1, 6, 10, 2, 7, 11]
    corpus = [_fx_real(f"f{i}", p) for i, p in enumerate(positions)]
    rep = audit_corpus(corpus, n_chunks_min=8, require_hard_neg=False)
    assert rep.ok is True


def test_audit_real_mode_flags_degenerate_recency():
    corpus = [_fx_real(f"f{i}", 11) for i in range(9)]   # all gold newest
    rep = audit_corpus(corpus, n_chunks_min=8, require_hard_neg=False)
    assert rep.ok is False and "recency" in rep.reason.lower()
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_corpus_audit.py -k real_mode -v` → fail (unexpected `require_hard_neg`).

- [ ] **Step 3: implement** — in `corpus_audit.py`, add the `require_hard_neg` parameter (default `True`, preserving M4c behavior) and gate the hard-neg block on it:
```python
def audit_corpus(fixtures: list[Fixture], *, n_chunks_min: int = 12,
                 require_hard_neg: bool = True) -> AuditReport:
    n = len(fixtures)
    counts = [0, 0, 0]
    for fx in fixtures:
        if len(fx.chunks) < n_chunks_min:
            return AuditReport(False, f"fixture {fx.name} has <{n_chunks_min} chunks", (0, 0, 0), n)
        if require_hard_neg:
            n_hard = sum(1 for c in fx.chunks if "hard_neg" in c.tags)
            if n_hard < _HARD_NEG_MIN:
                return AuditReport(False, f"fixture {fx.name} has <{_HARD_NEG_MIN} hard negatives",
                                   (0, 0, 0), n)
        counts[_gold_third(fx)] += 1
    # ... unchanged: empty check + recency-third under-representation check ...
```
(Leave the rest of the function — the empty-corpus guard and the recency-third loop — exactly as is. The degenerate-recency "fail" is already produced by the existing recency-third check; the test confirms it fires in real mode.)

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_corpus_audit.py -v` (M4c synthetic tests still pass — default `require_hard_neg=True`); ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): corpus_audit real mode (optional hard_neg; recency-degeneracy still flagged)"`

---

## Task 9: lexical-bias diagnostic

**Files:** Modify `src/context_curator/eval/real_corpus.py`. Test: `tests/eval/test_real_corpus.py` (append).

- [ ] **Step 1: failing test** — append:
```python
from context_curator.eval.real_corpus import lexical_bias


def test_lexical_bias_flags_when_gold_is_lexically_trivial():
    # gold chunk shares the prompt's rare term; controls do not -> BM25 recall on gold >> control
    from context_curator.eval.fixtures import Fixture, FixtureChunk
    chunks = [FixtureChunk(key="gold", content="authentication token rotation")] + \
             [FixtureChunk(key=f"n{i}", content="the system the system") for i in range(6)]
    fx = Fixture(name="f", chunks=chunks, prompt="authentication token",
                 gold_keys=["gold"], split="test")
    rep = lexical_bias([fx], k=3, margin=0.15, seed=0)
    assert rep.degenerate is True
    assert rep.gold_recall > rep.control_recall
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/eval/test_real_corpus.py -k lexical_bias -v` → ImportError.

- [ ] **Step 3: implement** — append to `real_corpus.py`:
```python
from dataclasses import dataclass

from context_curator.eval.bm25 import bm25_scores
from context_curator.eval.metrics import recall_at_k


@dataclass
class LexicalBiasReport:
    gold_recall: float
    control_recall: float
    margin: float
    degenerate: bool


def lexical_bias(fixtures: list[Fixture], *, k: int = 3, margin: float = 0.15,
                 seed: int = 0) -> LexicalBiasReport:
    """Design §5.2: BM25 R@k on the gold set vs a per-fixture random non-gold control of the same
    count. `degenerate` iff gold_recall >= control_recall + margin (gold is lexically trivial)."""
    rng = random.Random(seed)
    g_recs, c_recs = [], []
    for fx in fixtures:
        docs = {c.key: c.content for c in fx.chunks}
        ranked = [k_ for k_, _ in sorted(bm25_scores(fx.prompt, docs).items(),
                                         key=lambda kv: (-kv[1], kv[0]))]
        gold = set(fx.gold_keys)
        g_recs.append(recall_at_k(ranked, gold, k))
        non_gold = [c.key for c in fx.chunks if c.key not in gold]
        control = set(rng.sample(non_gold, min(len(gold), len(non_gold)))) if non_gold else set()
        c_recs.append(recall_at_k(ranked, control, k) if control else 0.0)
    gr = sum(g_recs) / len(g_recs) if g_recs else 0.0
    cr = sum(c_recs) / len(c_recs) if c_recs else 0.0
    return LexicalBiasReport(gr, cr, margin, gr >= cr + margin)
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/eval/test_real_corpus.py -v`; ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(m4d): lexical-bias diagnostic (BM25 R@3 on gold vs control + margin)"`

---

## Task 10: flag-on regression tests

**Files:** Create `tests/curator/test_onload_flag_on.py`.

- [ ] **Step 1: read the existing curator test setup** — open `tests/test_curator_handler.py` (or `tests/curator/`) to reuse its fixture/monkeypatch pattern for spinning the handler with `CC_CURATOR_ONLOAD` ON. The new test MUST follow that setup exactly (warm daemon, store seeding). Do not invent a new harness.

- [ ] **Step 2: write the failing test** — `tests/curator/test_onload_flag_on.py`. The core contract (design §6.1, M4c round-3): with the flag ON and a store containing one chunk that is **clearly irrelevant** to the prompt (cosine below the gate), the onload selection must **EXCLUDE** it (NOT a self-fulfilling "gold is selected"); and on an empty/zero-survivor selection it must fall back to recency. Mirror the existing handler test's construction; assert the irrelevant key is absent from the injected keys:
```python
def test_onload_excludes_known_irrelevant_chunk(monkeypatch, tmp_path):
    # follow tests/test_curator_handler.py setup: enable flag, seed store, run the onload path
    monkeypatch.setenv("CC_CURATOR_ONLOAD", "1")
    # ... seed: one on-topic chunk + one KNOWN-IRRELEVANT chunk (e.g. prompt about auth,
    #     chunk about "css grid colors"); run the handler's onload selection ...
    # injected = <keys the handler selected>
    assert "irrelevant:css" not in injected           # the gate must drop it
```
(Fill the seeding/handler-invocation lines from the existing test's pattern. The assertion contract — known-irrelevant EXCLUDED — is the non-negotiable part.)

- [ ] **Step 3: run, expect fail (or pass if the gate already works)** — `uv run pytest tests/curator/test_onload_flag_on.py -v`. If it passes immediately, that's fine (it's a regression guard); if it fails, that's a real onload-gate bug to file — report it, do not paper over it.

- [ ] **Step 4: full curator suite** — `uv run pytest tests -k curator -q`; ruff.

- [ ] **Step 5: commit** — `git add -A && git commit -m "test(m4d): flag-on regression — onload excludes known-irrelevant chunk"`

---

# PHASE B — The run + contingent flip (non-deterministic)

## Task 11: harvest local corpus, run keystone, compute the verdict-or-needed-N

**Files:** `.gitignore` (+ `_real_local/`); local-only `src/context_curator/eval/fixtures/_real_local/`; `docs/superpowers/keystone-real.md` (aggregate-only verdict of record). Optional thin CLI `src/context_curator/eval/keystone_real.py`.

- [ ] **Step 1: gitignore the local corpus** — add to `.gitignore`:
```
src/context_curator/eval/fixtures/_real_local/
```
Commit: `git add .gitignore && git commit -m "chore(m4d): gitignore local real-transcript corpus"`

- [ ] **Step 2: assemble the local corpus (local-only, never committed)** — copy the chosen local projects' Claude Code `.jsonl` transcripts into `_real_local/` (privacy §9). Harvest + split:
```bash
uv run python -c "
from context_curator.eval.real_corpus import harvest_corpus
import glob, json, os
paths = glob.glob('src/context_curator/eval/fixtures/_real_local/*.jsonl')
fxs = harvest_corpus(paths, w=5, min_candidates=5, test_frac=0.75, seed=0)
import pathlib
out='src/context_curator/eval/fixtures/_real_local/_fixtures'; os.makedirs(out, exist_ok=True)
for fx in fxs:
    pathlib.Path(out, fx.name.replace(':','_')+'.json').write_text(fx.model_dump_json(indent=1))
print('fixtures', len(fxs), 'sessions', len({f.session_id for f in fxs}),
      'test', sum(1 for f in fxs if f.split=='test'))
"
```
Record `n_fixtures` and `n_sessions`.

- [ ] **Step 3: precision-gate FIRST (before peeking at the effect)** — run the keystone (bge) over `_real_local/_fixtures`, then the clustered CI + gate:
```bash
uv sync --extra embed
KEYSTONE_CORPUS=src/context_curator/eval/fixtures/_real_local/_fixtures uv run python -c "
from context_curator.embeddings import FastEmbedEmbedder
from context_curator.eval.keystone import run_keystone
from context_curator.eval.stats import cluster_bootstrap_ci
from context_curator.eval.precision_gate import precision_gate
r=run_keystone('src/context_curator/eval/fixtures/_real_local/_fixtures', FastEmbedEmbedder(), seed=0)
lo,hi=cluster_bootstrap_ci(r.per_fixture_ndcg_delta, [s or 'NA' for s in r.test_session_ids], seed=0)
n_sess=len(set(r.test_session_ids))
g=precision_gate(lo=lo, hi=hi, n_sessions=n_sess, mei=0.10)
print('n_test',r.n_test,'n_sessions',n_sess,'clustered CI',(round(lo,4),round(hi,4)),'gate',g)
print('arms semantic',round(r.arm3.ndcg_at_k,4),'bm25',round(r.arm_bm25.ndcg_at_k,4),'recency',round(r.arm2.ndcg_at_k,4))
"
```

- [ ] **Step 4: run the diagnostics** — `audit_corpus(..., require_hard_neg=False)` (recency degeneracy), `lexical_bias(...)` (the §5.2 guard), the §5.5 selection-bias numbers (drop rate; characterize dropped vs kept turns), and the §5.1 bias-direction check (gold path-token overlap with prompts vs gold content-term overlap — report it; the verdict table stays symmetric regardless, this is a recorded caveat). Record all.

- [ ] **Step 5: write `docs/superpowers/keystone-real.md`** — aggregate-only (NO transcript content): n_test + **n_sessions**; per-arm nDCG; the **clustered** CI; the **precision-gate outcome** (verdict / INCONCLUSIVE-underpowered + **needed-N** / harness-only); the lexical-bias number + degenerate flag; the audit recency histogram; the selection-bias report; the §6 verdict (applying the §6 table only if the gate said "verdict" and lexical-bias is clear); fastembed/onnxruntime versions; transcript **sha256 + local path** manifest. State plainly if the outcome is "capture ~N more sessions and re-run."

- [ ] **Step 6: commit** — `git add docs/superpowers/keystone-real.md && git commit -m "feat(m4d): real-data keystone verdict of record (aggregate-only)"` (NOTE: only the doc is committed; `_real_local/` stays gitignored.)

---

## Task 12 (CONTINGENT — GREEN only): the production flip

**Skip this task unless Task 11's outcome is GREEN** (`gate=="verdict"`, lexical-bias clear, clustered `lo>0`, `m≥0.10`) **and the §6.1 safety gates pass** (production-capped path exercised, warm-daemon p95 within budget, reversible rollout). If not GREEN, STOP — the milestone ends at Task 11 with the harness + honest verdict; for NEGATIVE-decommission bring the numbers to the user for sign-off (do not remove anything).

**Files:** `src/context_curator/curator/config.py`, `src/context_curator/policy/weights.py`, `docs/` rollback note.

- [ ] **Step 1: re-confirm GREEN + safety gates** — re-read Task 11's verdict doc; verify the capped-path precondition and perf/robustness numbers are recorded. If any are missing, downgrade to "tune threshold, keep dark" and STOP.
- [ ] **Step 2: set the swept threshold** — in `src/context_curator/policy/weights.py`, set the `ONLOAD_BGE_COSINE_THRESHOLD` constant to the train-split swept value (with a machine/version-stamp comment; the value + recall-floor + per-cell CIs are recorded in the verdict doc).
- [ ] **Step 3: flip the default** — in `src/context_curator/curator/config.py:23`, change `os.environ.get("CC_CURATOR_ONLOAD", "0")` → `"1"`.
- [ ] **Step 4: full suite incl. flag-on regression** — `uv run pytest -q` (all green, the flag-on tests now exercise the live default); `uv run ruff check .`.
- [ ] **Step 5: document rollback** — a short note (in the verdict doc or `docs/`) that `CC_CURATOR_ONLOAD=0` is the fast kill-switch.
- [ ] **Step 6: commit** — `git add -A && git commit -m "feat(m4d): flip onload default ON + set data-driven bge threshold (GREEN verdict)"`

---

## Final verification
- [ ] `uv run pytest -q` green; `uv run ruff check .` clean.
- [ ] **Confirm no production change unless GREEN:** if Task 12 was skipped, `git diff main -- src/context_curator/curator/config.py src/context_curator/policy/weights.py` is EMPTY.
- [ ] **Confirm no transcript content committed:** `git diff main --stat` shows nothing under `_real_local/`; the verdict doc contains aggregates only.
- [ ] Then the final whole-branch review → PR.

## Spec coverage map (self-review)
| Spec § | Task |
|---|---|
| §3.1 parse + session_id | 1, 3, 4 |
| §3.3 entity extraction + path-equivalence + retrieval-only + verify-edit exclusion | 2, 3 |
| §3.4 keep/drop + split-by-session | 3, 4 |
| §5.1 measured bias direction (reported caveat, symmetric table) | 11 (Step 4) |
| §5.2 lexical-bias diagnostic | 9 |
| §5.3 cluster_bootstrap_ci (contract incl. n_sessions=1) | 5 |
| §5.4 precision gate + needed-N | 6 |
| §5.6 diagnostic audit (optional hard_neg, recency degeneracy) | 8 |
| keystone session-id threading | 7 |
| §6.1 flag-on regression (known-irrelevant excluded) | 10 |
| §3/§4 the run + privacy (gitignore, aggregate-only doc) | 11 |
| §6 verdict application + contingent GREEN flip | 11, 12 |
| §6/§8 no production change unless GREEN; no content committed | Final verification |
