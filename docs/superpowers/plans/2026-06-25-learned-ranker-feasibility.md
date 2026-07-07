# Learned Ranker — Cycle 1 Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an eval-only feasibility harness that answers, on the 5 staged real sessions, whether a learned Tier-1 logistic ranker moves vs BM25 at re-onload selection — the bitter-lesson go/no-go for the full build.

**Architecture:** Extend the harvested fixture with eval-side metadata (producing tool, entities), add a cheap feature extractor, fit a logistic via leave-one-session-out, and report `learned − BM25` nDCG@10 with a session-clustered CI, an order-of-magnitude needed-N, and a circularity audit. Ships nothing to production, so train-serve parity is out of scope.

**Tech Stack:** Python 3.11 / UV · pydantic · scikit-learn (eval/dev only) · existing eval helpers (`bm25_scores`, `ndcg_at_k`, `cluster_bootstrap_ci`, `precision_gate`, `lexical_bias`).

## Global Constraints
- Run everything via `uv run --no-sync` (the installed `cc-mcp.exe` is locked; a plain `uv run` collides → `os error 32`).
- **Zero production runtime dependencies.** scikit-learn lives in a dev/optional group; Cycle 1 ships nothing to `src/.../onload`, `replay/target.py`, or any hook.
- **Pre-registration before fitting:** commit `docs/superpowers/learned-feasibility-prereg.md` (MEI 0.10, seed 0, fixed L2, feature list, the 5 session shas, decision rule) BEFORE Task 7 runs the model.
- Transcripts in `src/context_curator/eval/fixtures/_real_local/` are gitignored, aggregate-only (DESIGN §9) — never commit them.
- MEI = +0.10 nDCG@10. CI = 90% session-clustered. Seed = 0. L2 `C` fixed a-priori (no tuning at 5 sessions).
- Decision keys off the **effect sign + magnitude** of `learned − BM25`, not a precise needed-N.
- Match the surrounding eval style: pure functions, `from __future__ import annotations`, deterministic, stdlib + the listed helpers.

---

## File structure
- `src/context_curator/eval/fixtures.py` — **modify**: add `FixtureChunk.producing_tool`, `FixtureChunk.entities`.
- `src/context_curator/eval/real_corpus.py` — **modify**: `harvest_trace` populates the two new fields.
- `src/context_curator/eval/learned/__init__.py` — **create**: empty package marker.
- `src/context_curator/eval/learned/features.py` — **create**: tool canon, per-candidate raw features, matrix + z-score norm.
- `src/context_curator/eval/learned/feasibility.py` — **create**: LOSO fit/score, clustered CI + gate, circularity audit, report + `__main__`.
- `tests/eval/learned/test_features.py` — **create**.
- `tests/eval/learned/test_feasibility.py` — **create**.
- `pyproject.toml` — **modify**: add `[dependency-groups] learn = ["scikit-learn>=1.4"]`.
- `docs/superpowers/learned-feasibility-prereg.md` — **create** (Task 7, committed before the run).
- `docs/superpowers/keystone-learned.md` — **create/overwrite** with the feasibility verdict (Task 7).

---

### Task 1: Eval-side fixture metadata (producing tool + entities)

**Files:**
- Modify: `src/context_curator/eval/fixtures.py:12-16`
- Modify: `src/context_curator/eval/real_corpus.py:84-121`
- Test: `tests/eval/learned/test_features.py` (new file; first test lands here)

**Interfaces:**
- Produces: `FixtureChunk(key, content, tags=[], producing_tool: str | None = None, entities: list[str] = [])`; `harvest_trace` populates `producing_tool` from the producing `ToolCall.name` and `entities` from `extract_entities`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/learned/test_features.py
from context_curator.eval.real_corpus import harvest_trace
from context_curator.replay.schema import ToolCall, ToolResult, Trace, UserPrompt


def _trace(events):
    return Trace(session_id="sess-1", source="t", events=events)


def _prior(n):
    ev = []
    for i in range(n):
        ev.append(UserPrompt(turn_index=i, text=f"filler {i}"))
        ev.append(ToolCall(call_id=f"f{i}", name="Read", args={"file_path": f"/filler/{i}.py"}))
        ev.append(ToolResult(call_id=f"f{i}", content=f"filler {i}"))
    return ev


def test_harvest_persists_tool_and_entities():
    events = [
        UserPrompt(turn_index=0, text="open auth"),
        ToolCall(call_id="g", name="Grep", args={"path": "/a"}),
        ToolResult(call_id="g", content="match in /a"),
        *_prior(5),
        UserPrompt(turn_index=6, text="search auth dir"),
        ToolCall(call_id="r", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="r", content="x"),
    ]
    fxs = harvest_trace(_trace(events), w=5, min_candidates=5)
    fx = [f for f in fxs if f.prompt == "search auth dir"][0]
    chunk_g = [c for c in fx.chunks if c.key == "g"][0]
    assert chunk_g.producing_tool == "Grep"
    assert chunk_g.entities  # /a was extracted as an entity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/eval/learned/test_features.py::test_harvest_persists_tool_and_entities -v`
Expected: FAIL — `producing_tool` is an unexpected/absent attribute (or `extra=forbid` error once populated).

- [ ] **Step 3: Add the fields to `FixtureChunk`**

```python
# src/context_curator/eval/fixtures.py
class FixtureChunk(BaseModel):
    model_config = {"extra": "forbid"}   # reject a stray `pin` (M3)
    key: str
    content: str
    tags: list[str] = []
    producing_tool: str | None = None    # eval-side: the tool whose result produced this chunk
    entities: list[str] = []             # eval-side: canonical file entities (for the circularity audit)
```

- [ ] **Step 4: Carry tool + entities through `harvest_trace`**

In `src/context_curator/eval/real_corpus.py`, change the `running.append(...)` line and the two consumers (the chunk build and the gold unpack) to 4-tuples:

```python
    chunks_before: list[list[tuple[str, str, set[str], str | None]]] = []
    running: list[tuple[str, str, set[str], str | None]] = []
    for t in turns:
        chunks_before.append(list(running))
        for res in t.results:
            producing = call_by_id.get(res.call_id)
            ents = extract_entities(producing) if producing else set()
            running.append((res.call_id, res.content, ents, producing.name if producing else None))
```

Update the gold comprehension (was `for key, _c, ents in candidates`):

```python
        gold = [key for key, _c, ents, _tool in candidates
                if ents and any(entities_match({e}, ents) for e in refetched)]
```

Update the `FixtureChunk` build (was `FixtureChunk(key=k, content=c) for k, c, _e in candidates`):

```python
            chunks=[FixtureChunk(key=k, content=c, producing_tool=tool, entities=sorted(ents))
                    for k, c, ents, tool in candidates],
```

- [ ] **Step 5: Run the new test + the existing harvest suite**

Run: `uv run --no-sync pytest tests/eval/learned/test_features.py::test_harvest_persists_tool_and_entities tests/eval/test_real_corpus.py -v`
Expected: PASS (new test passes; existing `test_real_corpus.py` still green — the 4-tuple change is internal).

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/eval/fixtures.py src/context_curator/eval/real_corpus.py tests/eval/learned/test_features.py
git commit -m "feat(eval): persist producing_tool + entities on harvested FixtureChunk (track-B cycle 1)"
```

---

### Task 2: scikit-learn dev dependency group

**Files:**
- Modify: `pyproject.toml` (`[dependency-groups]`)

- [ ] **Step 1: Add the group**

Add (or extend) in `pyproject.toml`:

```toml
[dependency-groups]
learn = ["scikit-learn>=1.4"]
```

- [ ] **Step 2: Sync the group**

Run: `uv sync --group learn`
Expected: resolves + installs scikit-learn; exit 0.

- [ ] **Step 3: Verify import**

Run: `uv run --no-sync python -c "from sklearn.linear_model import LogisticRegression; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(eval): add scikit-learn 'learn' dependency group (dev/eval only)"
```

---

### Task 3: Feature extraction (`features.py`)

**Files:**
- Create: `src/context_curator/eval/learned/__init__.py` (empty)
- Create: `src/context_curator/eval/learned/features.py`
- Test: `tests/eval/learned/test_features.py` (append)

**Interfaces:**
- Produces:
  - `TOOL_VOCAB: list[str]` and `canon_tool(name: str | None) -> str`
  - `feature_names() -> list[str]`
  - `candidate_matrix(fx: Fixture) -> tuple[list[list[float]], list[int], list[str]]` → `(X_raw, y, keys)` where row i corresponds to `fx.chunks[i]`, `y[i]=1` iff that key is gold, `keys[i]` is the chunk key. `X_raw` columns follow `feature_names()` (pre-normalization).
  - `fit_norm(X: list[list[float]]) -> tuple[list[float], list[float]]` → `(means, stds)` (std forced to 1.0 where 0).
  - `apply_norm(X, means, stds) -> list[list[float]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/learned/test_features.py  (append)
import math
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.learned.features import (
    canon_tool, feature_names, candidate_matrix, fit_norm, apply_norm,
)


def test_canon_tool_lowercases_and_buckets_unknown():
    assert canon_tool("Read") == "read"
    assert canon_tool("Grep") == "grep"
    assert canon_tool("WebFetch") == "other"
    assert canon_tool(None) == "other"


def _fx():
    return Fixture(
        name="s:t1",
        prompt="warehouse restock",
        gold_keys=["k1"],
        session_id="s",
        chunks=[
            FixtureChunk(key="k0", content="unrelated text", producing_tool="Bash"),
            FixtureChunk(key="k1", content="warehouse restock logic", producing_tool="Read"),
        ],
    )


def test_candidate_matrix_shape_and_label():
    X, y, keys = candidate_matrix(_fx())
    assert keys == ["k0", "k1"]
    assert y == [0, 1]
    assert len(X) == 2 and len(X[0]) == len(feature_names())
    # recency_rank: oldest=0.0, newest=1.0 over 2 chunks
    ri = feature_names().index("recency_rank")
    assert X[0][ri] == 0.0 and X[1][ri] == 1.0


def test_norm_zscore_handles_constant_column():
    X = [[1.0, 5.0], [3.0, 5.0]]
    means, stds = fit_norm(X)
    assert means == [2.0, 5.0]
    assert stds[1] == 1.0  # constant column → std 1, not 0
    Z = apply_norm(X, means, stds)
    assert Z[0][0] == -1.0 and Z[1][0] == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/eval/learned/test_features.py -v`
Expected: FAIL — `context_curator.eval.learned.features` does not exist.

- [ ] **Step 3: Implement `features.py`**

```python
# src/context_curator/eval/learned/__init__.py
```
(empty file)

```python
# src/context_curator/eval/learned/features.py
"""Cheap, eval-side features for the learned re-onload ranker (track B, cycle 1).
Pure/deterministic. Tool one-hot is lowercased + bucketed so it is Cycle-2 serve-ready."""
from __future__ import annotations

import math

from context_curator.eval.bm25 import bm25_scores
from context_curator.eval.fixtures import Fixture

TOOL_VOCAB = [
    "read", "grep", "glob", "notebookread",
    "edit", "write", "multiedit", "notebookedit",
    "bash", "other",
]
_BASE_FEATURES = ["bm25", "recency_rank", "chunk_log_len"]


def canon_tool(name: str | None) -> str:
    low = (name or "").lower()
    return low if low in TOOL_VOCAB else "other"


def feature_names() -> list[str]:
    return [*_BASE_FEATURES, *(f"tool={t}" for t in TOOL_VOCAB)]


def candidate_matrix(fx: Fixture) -> tuple[list[list[float]], list[int], list[str]]:
    docs = {c.key: c.content for c in fx.chunks}
    bm = bm25_scores(fx.prompt, docs)
    n = len(fx.chunks)
    gold = set(fx.gold_keys)
    X: list[list[float]] = []
    y: list[int] = []
    keys: list[str] = []
    for i, c in enumerate(fx.chunks):
        recency = (i / (n - 1)) if n > 1 else 1.0   # oldest=0.0 .. newest=1.0
        row = [bm.get(c.key, 0.0), recency, math.log1p(len(c.content))]
        tool = canon_tool(c.producing_tool)
        row.extend(1.0 if tool == t else 0.0 for t in TOOL_VOCAB)
        X.append(row)
        y.append(1 if c.key in gold else 0)
        keys.append(c.key)
    return X, y, keys


def fit_norm(X: list[list[float]]) -> tuple[list[float], list[float]]:
    if not X:
        return [], []
    cols = len(X[0])
    n = len(X)
    means = [sum(r[j] for r in X) / n for j in range(cols)]
    stds = []
    for j in range(cols):
        var = sum((r[j] - means[j]) ** 2 for r in X) / n
        s = math.sqrt(var)
        stds.append(s if s > 1e-12 else 1.0)
    return means, stds


def apply_norm(X: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    return [[(r[j] - means[j]) / stds[j] for j in range(len(r))] for r in X]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/eval/learned/test_features.py -v`
Expected: PASS (all feature tests).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/eval/learned/__init__.py src/context_curator/eval/learned/features.py tests/eval/learned/test_features.py
git commit -m "feat(eval): cheap Tier-1 features + z-score norm for the learned ranker (track-B cycle 1)"
```

---

### Task 4: LOSO fit + scoring (`feasibility.py`, part 1)

**Files:**
- Create: `src/context_curator/eval/learned/feasibility.py`
- Test: `tests/eval/learned/test_feasibility.py` (new)

**Interfaces:**
- Consumes: `candidate_matrix`, `fit_norm`, `apply_norm`, `feature_names` (Task 3); `ndcg_at_k` (`eval.metrics`); `bm25_scores` (`eval.bm25`).
- Produces:
  - `fit_logistic(fixtures: list[Fixture], *, C: float = 1.0, seed: int = 0) -> tuple[object, list[float], list[float]]` → `(model, means, stds)`. Fits an L2 `LogisticRegression(class_weight="balanced", C=C, random_state=seed, max_iter=1000)` on the stacked candidate rows of `fixtures`.
  - `learned_ndcg(model, means, stds, fx: Fixture, k: int = 10) -> float` — rank `fx` candidates by model score, nDCG@k vs gold.
  - `bm25_ndcg(fx: Fixture, k: int = 10) -> float` — rank by BM25, nDCG@k vs gold.
  - `loso_deltas(by_session: dict[str, list[Fixture]], *, C: float, seed: int, k: int = 10) -> tuple[list[float], list[str], list[float], list[float]]` → `(deltas, session_ids, learned_ndcgs, bm25_ndcgs)`; for each held-out session, fit on the rest, score that session's fixtures, `delta = learned − bm25` per fixture.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/learned/test_feasibility.py
from pathlib import Path
import context_curator.eval as e
from context_curator.replay.capture.transcript import parse_transcript
from context_curator.eval.real_corpus import harvest_trace
from context_curator.eval.learned.feasibility import (
    fit_logistic, learned_ndcg, bm25_ndcg, loso_deltas,
)


def _by_session():
    base = Path(e.__file__).parent.parent.parent.parent / "tests" / "eval" / "_traces"
    out: dict[str, list] = {}
    for f in ("sample_a.jsonl", "sample_b.jsonl"):
        for fx in harvest_trace(parse_transcript(str(base / f)), w=5, min_candidates=5):
            out.setdefault(fx.session_id or "?", []).append(fx)
    return out


def test_fit_and_score_returns_unit_interval_ndcg():
    by = _by_session()
    flat = [fx for fxs in by.values() for fx in fxs]
    assert flat, "sample traces must yield fixtures"
    model, means, stds = fit_logistic(flat, C=1.0, seed=0)
    fx = flat[0]
    assert 0.0 <= learned_ndcg(model, means, stds, fx) <= 1.0
    assert 0.0 <= bm25_ndcg(fx) <= 1.0


def test_loso_holds_out_each_session_once():
    by = _by_session()
    deltas, sids, learned, bm = loso_deltas(by, C=1.0, seed=0)
    assert len(deltas) == sum(len(v) for v in by.values())
    assert set(sids) == set(by)            # every session appears as held-out
    assert len(learned) == len(bm) == len(deltas)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/eval/learned/test_feasibility.py -v`
Expected: FAIL — `feasibility` module/functions undefined.

- [ ] **Step 3: Implement `feasibility.py` (part 1)**

```python
# src/context_curator/eval/learned/feasibility.py
"""Track-B cycle 1: eval-only feasibility of a learned re-onload ranker via leave-one-session-out.
Ships nothing to production. Deterministic given a fixed corpus + seed."""
from __future__ import annotations

from context_curator.eval.fixtures import Fixture
from context_curator.eval.learned.features import apply_norm, candidate_matrix, fit_norm
from context_curator.eval.metrics import ndcg_at_k


def fit_logistic(fixtures: list[Fixture], *, C: float = 1.0, seed: int = 0):
    from sklearn.linear_model import LogisticRegression

    X_all: list[list[float]] = []
    y_all: list[int] = []
    for fx in fixtures:
        X, y, _ = candidate_matrix(fx)
        X_all.extend(X)
        y_all.extend(y)
    means, stds = fit_norm(X_all)
    Z = apply_norm(X_all, means, stds)
    model = LogisticRegression(
        class_weight="balanced", C=C, random_state=seed, max_iter=1000
    )
    model.fit(Z, y_all)
    return model, means, stds


def learned_ndcg(model, means, stds, fx: Fixture, k: int = 10) -> float:
    X, _, keys = candidate_matrix(fx)
    if not X:
        return 0.0
    Z = apply_norm(X, means, stds)
    scores = model.decision_function(Z)
    ranked = [key for key, _s in sorted(zip(keys, scores), key=lambda t: (-t[1], t[0]))]
    return ndcg_at_k(ranked, set(fx.gold_keys), k)


def bm25_ndcg(fx: Fixture, k: int = 10) -> float:
    from context_curator.eval.bm25 import bm25_scores

    docs = {c.key: c.content for c in fx.chunks}
    sc = bm25_scores(fx.prompt, docs)
    ranked = [key for key, _s in sorted(sc.items(), key=lambda t: (-t[1], t[0]))]
    return ndcg_at_k(ranked, set(fx.gold_keys), k)


def loso_deltas(by_session, *, C: float, seed: int, k: int = 10):
    deltas: list[float] = []
    session_ids: list[str] = []
    learned_ndcgs: list[float] = []
    bm25_ndcgs: list[float] = []
    sessions = sorted(by_session)
    for held in sessions:
        train = [fx for s in sessions if s != held for fx in by_session[s]]
        model, means, stds = fit_logistic(train, C=C, seed=seed)
        for fx in by_session[held]:
            ln = learned_ndcg(model, means, stds, fx, k)
            bn = bm25_ndcg(fx, k)
            learned_ndcgs.append(ln)
            bm25_ndcgs.append(bn)
            deltas.append(ln - bn)
            session_ids.append(held)
    return deltas, session_ids, learned_ndcgs, bm25_ndcgs
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/eval/learned/test_feasibility.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/eval/learned/feasibility.py tests/eval/learned/test_feasibility.py
git commit -m "feat(eval): LOSO logistic fit + learned/BM25 nDCG scoring (track-B cycle 1)"
```

---

### Task 5: Circularity audit features (`feasibility.py`, part 2)

**Files:**
- Modify: `src/context_curator/eval/learned/feasibility.py`
- Test: `tests/eval/learned/test_feasibility.py` (append)

**Interfaces:**
- Consumes: `Fixture` chunks' `entities` (Task 1); `entities_match` (`eval.real_corpus`); `ndcg_at_k`.
- Produces:
  - `prior_refetch_scores(fx: Fixture) -> dict[str, float]` — per candidate, count of earlier candidates whose entities match it.
  - `same_dir_scores(fx: Fixture, *, w_loc: int = 5) -> dict[str, float]` — per candidate, 1.0 if any of the `w_loc` preceding candidates shares a directory, else 0.0.
  - `solo_ndcg(by_session, score_fn, *, k: int = 10) -> float` — mean nDCG@k ranking each fixture by `score_fn(fx) -> {key: score}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/learned/test_feasibility.py  (append)
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.learned.feasibility import (
    prior_refetch_scores, same_dir_scores, solo_ndcg,
)


def test_prior_refetch_counts_matching_entities():
    fx = Fixture(
        name="s:t", prompt="p", gold_keys=["k2"], session_id="s",
        chunks=[
            FixtureChunk(key="k0", content="a", entities=["/a/b.py"]),
            FixtureChunk(key="k1", content="b", entities=["/c/d.py"]),
            FixtureChunk(key="k2", content="c", entities=["/a/b.py"]),
        ],
    )
    sc = prior_refetch_scores(fx)
    assert sc["k0"] == 0.0 and sc["k1"] == 0.0 and sc["k2"] == 1.0  # k2 repeats k0's entity


def test_same_dir_recent_flags_shared_directory():
    fx = Fixture(
        name="s:t", prompt="p", gold_keys=["k1"], session_id="s",
        chunks=[
            FixtureChunk(key="k0", content="a", entities=["/a/b.py"]),
            FixtureChunk(key="k1", content="b", entities=["/a/c.py"]),
        ],
    )
    sc = same_dir_scores(fx, w_loc=5)
    assert sc["k0"] == 0.0 and sc["k1"] == 1.0  # k1 shares /a with preceding k0


def test_solo_ndcg_is_unit_interval():
    fx = Fixture(
        name="s:t", prompt="p", gold_keys=["k1"], session_id="s",
        chunks=[FixtureChunk(key="k0", content="a", entities=["/x/y.py"]),
                FixtureChunk(key="k1", content="b", entities=["/x/y.py"])],
    )
    val = solo_ndcg({"s": [fx]}, prior_refetch_scores)
    assert 0.0 <= val <= 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/eval/learned/test_feasibility.py -k "refetch or same_dir or solo" -v`
Expected: FAIL — functions undefined.

- [ ] **Step 3: Implement the audit functions**

Append to `feasibility.py`:

```python
import os

from context_curator.eval.real_corpus import entities_match


def prior_refetch_scores(fx: Fixture) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, c in enumerate(fx.chunks):
        ents = set(c.entities)
        count = 0
        if ents:
            for j in range(i):
                if entities_match(ents, set(fx.chunks[j].entities)):
                    count += 1
        out[c.key] = float(count)
    return out


def _dirs(entities: list[str]) -> set[str]:
    return {os.path.dirname(e) for e in entities if e}


def same_dir_scores(fx: Fixture, *, w_loc: int = 5) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, c in enumerate(fx.chunks):
        dirs = _dirs(c.entities)
        flag = 0.0
        if dirs:
            for j in range(max(0, i - w_loc), i):
                if dirs & _dirs(fx.chunks[j].entities):
                    flag = 1.0
                    break
        out[c.key] = flag
    return out


def solo_ndcg(by_session, score_fn, *, k: int = 10) -> float:
    vals: list[float] = []
    for fxs in by_session.values():
        for fx in fxs:
            sc = score_fn(fx)
            ranked = [key for key, _s in sorted(sc.items(), key=lambda t: (-t[1], t[0]))]
            vals.append(ndcg_at_k(ranked, set(fx.gold_keys), k))
    return sum(vals) / len(vals) if vals else 0.0
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/eval/learned/test_feasibility.py -v`
Expected: PASS (all feasibility tests, old + new).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/eval/learned/feasibility.py tests/eval/learned/test_feasibility.py
git commit -m "feat(eval): circularity audit (prior-refetch + same-dir solo rankers) (track-B cycle 1)"
```

---

### Task 6: Assemble the report (`feasibility.py`, part 3) + `__main__`

**Files:**
- Modify: `src/context_curator/eval/learned/feasibility.py`
- Test: `tests/eval/learned/test_feasibility.py` (append)

**Interfaces:**
- Consumes: `loso_deltas`, `solo_ndcg`, `prior_refetch_scores`, `same_dir_scores` (Tasks 4–5); `cluster_bootstrap_ci` (`eval.stats`); `precision_gate` (`eval.precision_gate`); `lexical_bias` (`eval.real_corpus`); `harvest_trace`, `parse_transcript`.
- Produces:
  - `needed_n_range(deltas, session_ids, *, mei, seed, iters=200) -> tuple[int | None, int | None]` — bootstrap whole sessions `iters` times; for each resample compute `cluster_bootstrap_ci` width and `ceil(n*(width/mei)**2)`; return `(min, max)` of the finite estimates (or `(None, None)`).
  - `run_feasibility(paths: list[str], *, mei: float = 0.10, seed: int = 0, C: float = 1.0, w_loc: int = 5, k: int = 10) -> dict` — harvest per session, group, LOSO, clustered CI, `precision_gate`, needed-N range, circularity audit (solo nDCG vs learned mean), `lexical_bias`. Returns a report dict.
  - `format_report(rep: dict) -> str` — markdown.
  - `main()` — run over `src/context_curator/eval/fixtures/_real_local/*.jsonl`, print + write `docs/superpowers/keystone-learned.md`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/learned/test_feasibility.py  (append)
from pathlib import Path
import context_curator.eval as e
from context_curator.eval.learned.feasibility import run_feasibility, format_report


def test_run_feasibility_on_sample_traces_returns_wellformed_report():
    base = Path(e.__file__).parent.parent.parent.parent / "tests" / "eval" / "_traces"
    paths = [str(base / "sample_a.jsonl"), str(base / "sample_b.jsonl")]
    rep = run_feasibility(paths, mei=0.10, seed=0)
    for key in ("mean_delta", "ci", "n_sessions", "gate_status",
                "learned_mean_ndcg", "bm25_mean_ndcg", "circularity", "lexical_degenerate"):
        assert key in rep
    assert rep["n_sessions"] == 2
    assert isinstance(format_report(rep), str) and "verdict" in format_report(rep).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/eval/learned/test_feasibility.py::test_run_feasibility_on_sample_traces_returns_wellformed_report -v`
Expected: FAIL — `run_feasibility`/`format_report` undefined.

- [ ] **Step 3: Implement assembly + `main`**

Append to `feasibility.py`:

```python
import glob
import math
import random
from pathlib import Path

from context_curator.eval.precision_gate import precision_gate
from context_curator.eval.real_corpus import harvest_trace, lexical_bias
from context_curator.eval.stats import cluster_bootstrap_ci
from context_curator.replay.capture.transcript import parse_transcript


def _harvest_by_session(paths: list[str], *, w: int = 5, min_candidates: int = 5):
    by: dict[str, list[Fixture]] = {}
    for p in paths:
        for fx in harvest_trace(parse_transcript(p), w=w, min_candidates=min_candidates):
            by.setdefault(fx.session_id or "?", []).append(fx)
    return by


def needed_n_range(deltas, session_ids, *, mei, seed, iters=200):
    by: dict[str, list[float]] = {}
    for d, s in zip(deltas, session_ids):
        by.setdefault(s, []).append(d)
    clusters = list(by)
    n = len(clusters)
    if n < 2:
        return (None, None)
    rng = random.Random(seed)
    ests: list[int] = []
    for _ in range(iters):
        chosen = [clusters[rng.randrange(n)] for _ in range(n)]
        d2, s2 = [], []
        for idx, c in enumerate(chosen):
            d2.extend(by[c]); s2.extend([f"{c}#{idx}"] * len(by[c]))
        lo, hi = cluster_bootstrap_ci(d2, s2, seed=seed)
        width = hi - lo
        if math.isfinite(width) and width > 0:
            ests.append(math.ceil(n * (width / mei) ** 2))
    return (min(ests), max(ests)) if ests else (None, None)


def run_feasibility(paths, *, mei=0.10, seed=0, C=1.0, w_loc=5, k=10) -> dict:
    by = _harvest_by_session(paths)
    deltas, sids, learned, bm = loso_deltas(by, C=C, seed=seed, k=k)
    lo, hi = cluster_bootstrap_ci(deltas, sids, seed=seed)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    gate = precision_gate(lo=lo, hi=hi, n_sessions=len(by), mei=mei)
    nmin, nmax = needed_n_range(deltas, sids, mei=mei, seed=seed)
    learned_mean = sum(learned) / len(learned) if learned else 0.0
    bm_mean = sum(bm) / len(bm) if bm else 0.0
    circ = {
        "prior_refetch_solo_ndcg": solo_ndcg(by, prior_refetch_scores, k=k),
        "same_dir_solo_ndcg": solo_ndcg(by, lambda fx: same_dir_scores(fx, w_loc=w_loc), k=k),
    }
    flat = [fx for fxs in by.values() for fx in fxs]
    lb = lexical_bias(flat, k=3, margin=0.15, seed=seed)
    return {
        "mean_delta": mean_delta, "ci": [lo, hi], "n_sessions": len(by),
        "n_fixtures": len(deltas), "gate_status": gate.status, "needed_n": gate.needed_n,
        "needed_n_range": [nmin, nmax], "learned_mean_ndcg": learned_mean,
        "bm25_mean_ndcg": bm_mean, "circularity": circ,
        "lexical_gold_r3": lb.gold_recall, "lexical_control_r3": lb.control_recall,
        "lexical_degenerate": lb.degenerate, "mei": mei,
        "per_session": {s: len(v) for s, v in by.items()},
    }


def format_report(rep: dict) -> str:
    lo, hi = rep["ci"]
    md = rep["mean_delta"]
    mei = rep["mei"]
    if rep["n_sessions"] < 3:
        verdict = f"FEASIBILITY-ONLY (n_sessions={rep['n_sessions']} < 3)"
    elif md <= 0:
        verdict = f"NO-GO: learned does not beat BM25 (mean {md:+.3f} <= 0)"
    elif lo > 0 and md >= mei:
        verdict = f"STRONG SIGNAL: learned − BM25 {md:+.3f} >= MEI, CI>0 → GO to cycle 2"
    elif md > 0:
        verdict = f"WEAK POSITIVE: learned − BM25 {md:+.3f} (< MEI or CI includes 0) → judgement call"
    else:
        verdict = f"UNCLEAR: {md:+.3f}"
    lines = [
        "# Learned ranker — Cycle 1 feasibility (eval-only)",
        "",
        "> Deterministic on a fixed corpus + seed; gold labels are CWD-dependent (os.path). No bge.",
        "",
        f"verdict: {verdict}",
        f"mean(learned − BM25) nDCG@10: {md:+.4f}; clustered 90% CI [{lo:+.4f}, {hi:+.4f}]",
        f"n_sessions={rep['n_sessions']} n_fixtures={rep['n_fixtures']} "
        f"per_session={rep['per_session']}",
        f"arms: learned nDCG@10={rep['learned_mean_ndcg']:.3f} "
        f"bm25 nDCG@10={rep['bm25_mean_ndcg']:.3f}",
        f"precision gate: {rep['gate_status']} (needed_n={rep['needed_n']}, "
        f"range~{rep['needed_n_range']})",
        f"circularity audit (solo nDCG@10): {rep['circularity']} "
        f"— flag any within MEI of learned ({rep['learned_mean_ndcg']:.3f})",
        f"lexical-bias: gold_R@3={rep['lexical_gold_r3']:.3f} "
        f"control={rep['lexical_control_r3']:.3f} degenerate={rep['lexical_degenerate']}",
    ]
    return "\n".join(lines)


def main() -> None:
    paths = sorted(glob.glob("src/context_curator/eval/fixtures/_real_local/*.jsonl"))
    rep = run_feasibility(paths)
    text = format_report(rep)
    print(text)
    Path("docs/superpowers/keystone-learned.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/eval/learned/test_feasibility.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full eval suite (no regressions)**

Run: `uv run --no-sync pytest -p no:cacheprovider tests/eval -q`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/eval/learned/feasibility.py tests/eval/learned/test_feasibility.py
git commit -m "feat(eval): feasibility report (clustered CI, gate, needed-N range, audits) + CLI (track-B cycle 1)"
```

---

### Task 7: Pre-register, run on the 5 real sessions, record the verdict

**Files:**
- Create: `docs/superpowers/learned-feasibility-prereg.md` (commit BEFORE running)
- Create/overwrite: `docs/superpowers/keystone-learned.md` (the verdict — generated)

**Interfaces:**
- Consumes: `python -m context_curator.eval.learned.feasibility` (Task 6 `main`).

- [ ] **Step 1: Write + commit the pre-registration (BEFORE any run)**

Create `docs/superpowers/learned-feasibility-prereg.md` recording (verbatim from the spec §3.6): MEI 0.10; seed 0; fixed `C=1.0`; feature list (`bm25`, `recency_rank`, `chunk_log_len`, `tool_type` one-hot lowercased); LOSO over the 5 sessions; `learned − BM25` headline (deployable, not the oracle max); decision rule = effect sign + harvestability, needed-N order-of-magnitude only; circularity audit gate; stopping rule (learned ⊀ BM25 → ship BM25). Then record the **5 session shas**:

```bash
uv run --no-sync python -c "import glob,hashlib; [print(hashlib.sha256(open(p,'rb').read()).hexdigest()[:16], p) for p in sorted(glob.glob('src/context_curator/eval/fixtures/_real_local/*.jsonl'))]"
```
Paste the 16-char shas + filenames into the prereg doc. Commit it:

```bash
git add docs/superpowers/learned-feasibility-prereg.md
git commit -m "docs(track-b): pre-register the cycle-1 feasibility run (before fitting)"
```

- [ ] **Step 2: Run the feasibility harness**

Run: `uv run --no-sync python -m context_curator.eval.learned.feasibility`
Expected: prints the report and writes `docs/superpowers/keystone-learned.md`. Capture the printed `verdict:` line.

- [ ] **Step 3: Sanity-check the run**

Confirm: `n_sessions=5`; `learned nDCG@10` and `bm25 nDCG@10` both in [0,1]; the circularity line shows neither solo ranker within MEI of the learned mean (else a feature is circular — note it). Record any anomaly directly in `keystone-learned.md`.

- [ ] **Step 4: Commit the verdict-of-record**

```bash
git add docs/superpowers/keystone-learned.md
git commit -m "eval(track-b): cycle-1 feasibility verdict — learned ranker vs BM25 on 5 real sessions"
```

- [ ] **Step 5: Decide GO / NO-GO and report**

Apply §3.2: if `learned − BM25` is clearly positive and ~20–40 sessions plausibly resolve it → propose Cycle 2 (writing-plans for the full build). If it ties/loses → record NO-GO, ship BM25 (track A), retire the question. State the decision explicitly to the user; do not auto-start Cycle 2.

---

## Self-Review
- **Spec coverage:** §3.1 features → Tasks 1,3. §3.2 power dry-run (LOSO, sign rule, needed-N range) → Tasks 4,6,7. §3.3 circularity audit → Task 5. §3.4 components (features.py, feasibility.py, FixtureChunk field, prereg, keystone-learned.md) → Tasks 1,3–7. §3.5 deployable `learned − BM25` baseline (not oracle max) → Task 4 (`bm25_ndcg`, no `max`). §3.6 pre-registration (shas, MEI, fixed L2, decision rule) → Task 7 Step 1. §3.7 testing → Tasks 1,3,4,5,6. §3.8 sklearn dev group, zero prod deps → Task 2.
- **Placeholder scan:** no TBD/TODO; every code step shows full code; commands have expected output.
- **Type consistency:** `candidate_matrix → (X,y,keys)` consumed identically in `fit_logistic`/`learned_ndcg`; `loso_deltas → (deltas, session_ids, learned, bm25)` consumed in `run_feasibility`; `precision_gate(lo=,hi=,n_sessions=,mei=)` and `cluster_bootstrap_ci(deltas, cluster_ids, seed=)` match the real signatures; `lexical_bias(fixtures, k, margin, seed)` matches.
- **Scope:** Cycle 1 only — no `LearnedTarget`, no production wiring, no artifact, no schema migration. Gated handoff to Cycle 2 in Task 7 Step 5.
