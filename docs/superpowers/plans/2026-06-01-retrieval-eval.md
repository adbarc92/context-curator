# M3b — Retrieval Eval & Weight Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline retrieval eval — IR metrics, labeled fixtures, an eval runner, a `w_similarity` grid sweep, and the arm-2-vs-arm-3 keystone command — delivering the *validated comparison machinery* + an honest, underpowered directional first-look.

**Architecture:** A new `eval/` package (dev/eval tooling, depends on policy/replay/store). A fixture = (chunks → fresh store, a task, gold keys); the eval calls `target.decide(signal, store)` and scores `Decision.candidates`. CI uses a deterministic graded `KeywordEmbedder`; the bge keystone is a reported, gitignored job.

**Tech Stack:** Python 3.11+, UV, pydantic v2, pytest, ruff, stdlib `math`/`random`/`json` (no numpy in `eval/`). bge via the optional `embed` extra (keystone only).

**Spec:** `docs/superpowers/specs/2026-06-01-retrieval-eval-design.md` (read it; the Design Critique Log explains the non-obvious choices — why M3b is "validated harness + underpowered first-look, not a significant verdict," the graded KeywordEmbedder, the faithful arm-2 baseline, the verified adversarial fixture).

---

## Honest-framing reminders (carry into every task)
- M3b's value is the **validated machinery**, not a number. The keystone verdict at the starter corpus size is **directional/underpowered** — likely "inconclusive," which is the truthful answer.
- The inferential stats (bootstrap) ship but are labeled "meaningful once n≳30"; they are reported as a *width-of-ignorance*, not a verdict.

## File map

```
src/context_curator/eval/
  __init__.py
  metrics.py          # T1: precision_at_k / recall_at_k / ndcg_at_k
  stats.py            # T1: bootstrap_ci
  fixtures.py         # T2: Fixture / FixtureChunk / load_fixtures
  runner.py           # T4: ArmMetrics / evaluate (+ embedder-binding assert)
  sweep.py            # T5: SweepCell / SweepResult / grid_sweep / DEFAULT_GRID
  keystone.py         # T6: KeystoneReport / run_keystone / __main__
  fixtures/controlled/*.json   # T2: graded-embedder corpus incl. the adversarial fixture
  fixtures/realistic/*.json    # T2: starter bge corpus (train/test)
src/context_curator/policy/relevance.py   # T3: + `embedder` property
src/context_curator/replay/target.py      # T3: RecencyOnlyTarget.candidates + PolicyTarget.embedder
pyproject.toml        # T6: pin fastembed+onnxruntime in [extras].embed
.gitignore            # T6: results/
tests/eval/
  conftest.py         # T2: KeywordEmbedder test helper
  test_metrics.py     # T1
  test_stats.py       # T1
  test_fixtures.py    # T2
  test_runner.py      # T4
  test_sweep.py       # T5
  test_keystone_proxy.py  # T6
tests/replay/test_recency_candidates.py   # T3
```

---

### Task 1: Metrics + bootstrap

**Files:**
- Create: `src/context_curator/eval/__init__.py` (empty), `eval/metrics.py`, `eval/stats.py`
- Test: `tests/eval/test_metrics.py`, `tests/eval/test_stats.py`

- [ ] **Step 1: Write the golden metric tests**

`tests/eval/test_metrics.py`:
```python
import math

from context_curator.eval.metrics import ndcg_at_k, precision_at_k, recall_at_k


def test_perfect_ranking():
    assert precision_at_k(["a", "b"], {"a", "b"}, 2) == 1.0
    assert recall_at_k(["a", "b"], {"a", "b"}, 2) == 1.0
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 2) == 1.0


def test_gold_absent():
    assert precision_at_k(["x", "y"], {"a"}, 2) == 0.0
    assert recall_at_k(["x", "y"], {"a"}, 2) == 0.0
    assert ndcg_at_k(["x", "y"], {"a"}, 2) == 0.0


def test_precision_uses_min_k_len():
    # 3 chunks all gold, k=10 -> 3/min(10,3) = 1.0 (not 3/10)
    assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 10) == 1.0


def test_empty_ranked():
    assert precision_at_k([], {"a"}, 5) == 0.0
    assert ndcg_at_k([], {"a"}, 5) == 0.0


def test_ndcg_golden_value():
    # gold at ranks 0 and 2 of ["g","x","g2","y"]; |gold|=2
    ranked, gold = ["g", "x", "g2", "y"], {"g", "g2"}
    dcg = 1 / math.log2(2) + 1 / math.log2(4)         # ranks 0 and 2
    idcg = 1 / math.log2(2) + 1 / math.log2(3)         # ideal: ranks 0 and 1
    assert math.isclose(ndcg_at_k(ranked, gold, 4), dcg / idcg, rel_tol=1e-9)


def test_single_gold_at_rank1_is_one_over_log2_3():
    # one gold at rank 1 -> nDCG = (1/log2(3)) / 1
    assert math.isclose(ndcg_at_k(["x", "g"], {"g"}, 3), 1 / math.log2(3), rel_tol=1e-9)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/eval/test_metrics.py -v` → FAIL (`ModuleNotFoundError: eval.metrics`).

- [ ] **Step 3: Implement `eval/metrics.py`**

```python
"""IR metrics (design §3.1). Binary relevance; pure, deterministic. The authority on
metric correctness is this module's golden tests, not the keystone proxy."""
from __future__ import annotations

import math


def precision_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    topk = ranked[:k]
    if not topk:
        return 0.0
    hits = sum(1 for key in topk if key in gold)
    return hits / min(k, len(topk))


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    hits = sum(1 for key in ranked[:k] if key in gold)
    return hits / len(gold)


def ndcg_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, key in enumerate(ranked[:k]) if key in gold)
    n_ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(n_ideal))
    return dcg / idcg if idcg > 0 else 0.0
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/eval/test_metrics.py -v` → 6 passed.

- [ ] **Step 5: Write the bootstrap test**

`tests/eval/test_stats.py`:
```python
from context_curator.eval.stats import bootstrap_ci


def test_all_positive_excludes_zero():
    lo, hi = bootstrap_ci([0.4, 0.5, 0.45, 0.5], seed=0)
    assert lo > 0.0


def test_straddling_includes_zero():
    lo, hi = bootstrap_ci([0.4, -0.4, 0.3, -0.3], seed=0)
    assert lo < 0.0 < hi


def test_deterministic_for_fixed_seed():
    a = bootstrap_ci([0.1, 0.2, -0.1, 0.05], seed=7)
    b = bootstrap_ci([0.1, 0.2, -0.1, 0.05], seed=7)
    assert a == b


def test_empty_returns_zero_interval():
    assert bootstrap_ci([], seed=0) == (0.0, 0.0)
```

- [ ] **Step 6: Run to verify failure, then implement `eval/stats.py`**

Run: `uv run pytest tests/eval/test_stats.py -v` → FAIL. Then:
```python
"""Paired bootstrap CI (design §3.6). NOTE: meaningful only once n is adequate (n≳30);
at the M3b starter corpus size it is a width-of-ignorance display, not a verdict.
stdlib only (random.Random(seed) -> reproducible RESAMPLING; not the underlying values)."""
from __future__ import annotations

import random


def bootstrap_ci(deltas: list[float], *, seed: int, alpha: float = 0.1,
                 iters: int = 2000) -> tuple[float, float]:
    """Percentile [alpha/2, 1-alpha/2] interval of the resampled mean of `deltas`."""
    if not deltas:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(iters)
    )
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)
```

- [ ] **Step 7: Run + commit**

Run: `uv run pytest tests/eval/test_metrics.py tests/eval/test_stats.py -v` → all pass. `uv run ruff check .` → clean.
```bash
git add src/context_curator/eval/__init__.py src/context_curator/eval/metrics.py src/context_curator/eval/stats.py tests/eval/test_metrics.py tests/eval/test_stats.py
git commit -m "feat: eval IR metrics (precision/recall/nDCG) + bootstrap CI"
```

---

### Task 2: Fixtures + corpora + KeywordEmbedder

**Files:**
- Create: `src/context_curator/eval/fixtures.py`
- Create: `src/context_curator/eval/fixtures/controlled/*.json`, `fixtures/realistic/*.json`
- Create: `tests/eval/conftest.py` (the `KeywordEmbedder` helper), `tests/eval/test_fixtures.py`

- [ ] **Step 1: Implement the `KeywordEmbedder` test helper**

`tests/eval/conftest.py`:
```python
import math

from context_curator.embeddings import Embedder

_VOCAB = ["A", "B", "C", "D", "E", "F"]


class KeywordEmbedder(Embedder):
    """Graded bag-of-keywords embedder (design §3.2/§4): unit-normalized sum of the
    vocab-keyword basis vectors in the text, so shared-but-not-identical keyword sets
    give INTERMEDIATE cosines (e.g. 'A B C' vs 'A B C D' -> 0.866)."""

    @property
    def dim(self) -> int:
        return len(_VOCAB)

    def embed(self, text: str) -> list[float]:
        idx = {k: i for i, k in enumerate(_VOCAB)}
        vec = [0.0] * len(_VOCAB)
        for tok in text.split():
            if tok in idx:
                vec[idx[tok]] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        return vec if norm == 0.0 else [x / norm for x in vec]
```

- [ ] **Step 2: Write the failing fixture test**

`tests/eval/test_fixtures.py`:
```python
import pytest
from pydantic import ValidationError

from context_curator.eval.fixtures import Fixture, FixtureChunk, load_fixtures


def test_fixture_roundtrips():
    fx = Fixture(name="f", chunks=[FixtureChunk(key="a", content="A B")],
                 prompt="A B", gold_keys=["a"], split="train")
    assert Fixture(**fx.model_dump()) == fx


def test_split_rejects_typo():
    with pytest.raises(ValidationError):
        Fixture(name="f", chunks=[], prompt="p", gold_keys=[], split="tset")


def test_no_pin_field():
    with pytest.raises(ValidationError):
        FixtureChunk(key="a", content="x", pin=True)   # pin forbidden (M3)


def test_load_controlled_corpus():
    import context_curator.eval as e
    from pathlib import Path
    fixtures = load_fixtures(str(Path(e.__file__).parent / "fixtures" / "controlled"))
    assert len(fixtures) >= 4
    assert any(f.name == "adversarial-arm2-wins" for f in fixtures)
```

- [ ] **Step 3: Run to verify failure, then implement `eval/fixtures.py`**

Run: `uv run pytest tests/eval/test_fixtures.py -v` → FAIL. Then:
```python
"""Eval fixtures (design §3.2). A fixture = chunks (chronological, oldest-first) + a
task + planted (blind-labeled) gold keys. `pin` is intentionally absent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class FixtureChunk(BaseModel):
    model_config = {"extra": "forbid"}   # reject a stray `pin` (M3)
    key: str
    content: str
    tags: list[str] = []


class Fixture(BaseModel):
    name: str
    chunks: list[FixtureChunk]            # CHRONOLOGICAL: oldest first, newest last
    prompt: str
    recent_tools: list[str] = []
    gold_keys: list[str]
    split: Literal["train", "test"] = "train"


def load_fixtures(directory: str) -> list[Fixture]:
    out: list[Fixture] = []
    for path in sorted(Path(directory).glob("*.json")):
        out.append(Fixture(**json.loads(path.read_text(encoding="utf-8"))))
    return out
```
`{"extra": "forbid"}` makes `pin=True` a `ValidationError`.

- [ ] **Step 4: Author the controlled corpus** (`src/context_curator/eval/fixtures/controlled/`)

Content uses vocab tokens so `KeywordEmbedder` gives exact cosines. **Chronological = oldest first; the LAST chunk is newest.**

`semantic_win_1.json` (arm-3 should win: gold strong-match + recency-OLD; distractor no-match + recency-NEW):
```json
{"name": "semantic-win-1",
 "chunks": [{"key": "gold", "content": "A B C D"}, {"key": "distractor", "content": "E F"}],
 "prompt": "A B C D", "gold_keys": ["gold"], "split": "train"}
```
Add two more pro-semantic fixtures the same shape with different keys/keywords (`semantic_win_2.json`, `semantic_win_3.json` — e.g. prompt `"B C D E"`, gold `"B C D E"` old, distractor `"A F"` new).

`adversarial_arm2_wins.json` (**the verified strict arm-2 win** — gold recency-NEW with slightly-lower sim than an OLD distractor):
```json
{"name": "adversarial-arm2-wins",
 "chunks": [{"key": "distractor", "content": "A B C D"}, {"key": "gold", "content": "A B C"}],
 "prompt": "A B C D", "gold_keys": ["gold"], "split": "train"}
```
(Verified at default weights: arm-3 scores distractor 0.967 > gold 0.826 → arm-3 nDCG@3 = 1/log2(3); arm-2 ranks recency-new gold first → nDCG@3 = 1.0. Strict arm-2 win.)

- [ ] **Step 5: Author the realistic starter corpus** (`fixtures/realistic/`)

≈10–16 fixtures of realistic English, train/test split via the `split` field, honoring §3.2 construct-validity: **hard negatives** (recency-new, topically-similar-but-wrong chunks), **mixed gold-recency** (some fixtures' gold is recency-new), **blind gold** (labeled from the task, not a ranking). Example `realistic/auth_login.json`:
```json
{"name": "auth-login",
 "chunks": [
   {"key": "k_login", "content": "def login(user, pw): verify the password hash and issue a session token"},
   {"key": "k_logout", "content": "def logout(session): clear the session cookie and revoke the token"},
   {"key": "k_csv", "content": "read rows from a CSV file with the python csv module"},
   {"key": "k_session_new", "content": "the session store keeps active tokens with a sliding expiry"}],
 "prompt": "how do I authenticate a user and start a session",
 "gold_keys": ["k_login", "k_session_new"], "split": "train"}
```
Author the rest similarly (varied topics; ≥1 hard-negative per fixture; ~⅓ in `"test"`). These are content for the bge run; they need NOT pass under `KeywordEmbedder` (the controlled corpus owns CI).

- [ ] **Step 6: Run + commit**

Run: `uv run pytest tests/eval/test_fixtures.py -v` → pass. `uv run ruff check .` → clean.
```bash
git add src/context_curator/eval/fixtures.py src/context_curator/eval/fixtures/ tests/eval/conftest.py tests/eval/test_fixtures.py
git commit -m "feat: eval fixtures + corpora + graded KeywordEmbedder"
```

---

### Task 3: `RecencyOnlyTarget.candidates` + public `embedder` accessors

**Files:**
- Modify: `src/context_curator/policy/relevance.py`
- Modify: `src/context_curator/replay/target.py`
- Test: `tests/replay/test_recency_candidates.py`

- [ ] **Step 1: Grep for snapshots that encode arm-2's empty candidates (C3 guard)**

Run: `grep -rn "candidates" tests/` and inspect any `model_dump()`-equality or committed `DecisionLog` JSON. (Per the spec's round-3 check, `tests/replay/test_schema.py` asserts on a directly-constructed `Decision`, not on `RecencyOnlyTarget` output, so it is unaffected — but confirm before changing.)

- [ ] **Step 2: Write the failing test**

`tests/replay/test_recency_candidates.py`:
```python
from context_curator.embeddings import HashingEmbedder
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore


def test_recency_target_populates_full_recency_candidates():
    store = InMemoryStore(embedder=HashingEmbedder(dim=8))
    for key in ("a", "b", "c"):           # a oldest, c newest
        store.store(key, "x", ttl_s=None)
    d = RecencyOnlyTarget().decide(
        TaskSignal(turn_index=0, prompt="p", subtask_id=None, recent_tool_calls=[]), store)
    assert [c.key for c in d.candidates] == ["c", "b", "a"]   # full pool, newest-first
    assert all(c.score is None for c in d.candidates)
```

- [ ] **Step 3: Modify `RecencyOnlyTarget.decide` to populate `candidates`**

In `src/context_curator/replay/target.py`, replace the `return Decision(...)` block of `RecencyOnlyTarget.decide` (it currently omits `candidates`) with:
```python
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
```
(`selected` is unchanged — still the `store.query` slice.)

- [ ] **Step 4: Add the public `embedder` accessors (I-4)**

In `src/context_curator/policy/relevance.py`, add to `RelevancePolicy` (after `__init__`):
```python
    @property
    def embedder(self) -> Embedder:
        return self._embedder
```
In `src/context_curator/replay/target.py`, add to `PolicyTarget` (after `__init__`):
```python
    @property
    def embedder(self):
        return self._policy.embedder
```

- [ ] **Step 5: Run the new test + the FULL suite**

Run: `uv run pytest tests/replay/test_recency_candidates.py -v` → pass.
Run: `uv run pytest -q` → the entire suite stays green, **including the two `model_dump()` replay-determinism tests** (the new candidates pool is deterministic across runs). `uv run ruff check .` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/replay/target.py src/context_curator/policy/relevance.py tests/replay/test_recency_candidates.py
git commit -m "feat: RecencyOnlyTarget recency candidate pool + public embedder accessors"
```

---

### Task 4: Eval runner

**Files:**
- Create: `src/context_curator/eval/runner.py`
- Test: `tests/eval/test_runner.py`

- [ ] **Step 1: Write the failing test**

`tests/eval/test_runner.py`:
```python
import math

import pytest

from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.runner import evaluate
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from tests.eval.conftest import KeywordEmbedder


def _semantic_win():
    return [Fixture(name="sw", prompt="A B C D", gold_keys=["gold"], split="train",
                    chunks=[FixtureChunk(key="gold", content="A B C D"),     # old, match
                            FixtureChunk(key="dist", content="E F")])]        # new, no match


def test_policy_ranks_gold_first():
    emb = KeywordEmbedder()
    m = evaluate(_semantic_win(), PolicyTarget(RelevancePolicy(emb)), emb, k=10)
    assert m.ndcg_at_k == 1.0


def test_recency_ranks_gold_low():
    emb = KeywordEmbedder()
    m = evaluate(_semantic_win(), RecencyOnlyTarget(), emb, k=10)
    assert math.isclose(m.ndcg_at_k, 1 / math.log2(3), rel_tol=1e-9)   # gold at rank 1


def test_embedder_binding_assert():
    a, b = KeywordEmbedder(), KeywordEmbedder()
    with pytest.raises(AssertionError):
        evaluate(_semantic_win(), PolicyTarget(RelevancePolicy(a)), b, k=10)  # mismatched
```

- [ ] **Step 2: Run to verify failure, then implement `eval/runner.py`**

Run: `uv run pytest tests/eval/test_runner.py -v` → FAIL. Then:
```python
"""Eval runner (design §3.3). One embedder populates the store AND backs the policy
(asserted). Metrics over Decision.candidates (the ranking); a production-faithful
precision over Decision.selected too."""
from __future__ import annotations

from dataclasses import dataclass

from context_curator.embeddings import Embedder
from context_curator.eval.fixtures import Fixture
from context_curator.eval.metrics import ndcg_at_k, precision_at_k, recall_at_k
from context_curator.replay.schema import TaskSignal, ToolRef
from context_curator.replay.target import PolicyTarget
from context_curator.store.memory import InMemoryStore


@dataclass(frozen=True)
class ArmMetrics:
    ndcg_at_k: float
    precision_at_k: float
    recall_at_rk: float
    selected_precision: float
    n_fixtures: int


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def evaluate(fixtures: list[Fixture], target, embedder: Embedder,
             k: int = 10, recall_k: int = 3) -> ArmMetrics:
    if isinstance(target, PolicyTarget):
        assert target.embedder is embedder, "store and policy embedders must be identical"
    ndcgs, precs, recs, sel_precs = [], [], [], []
    for fx in fixtures:
        store = InMemoryStore(embedder=embedder)
        for c in fx.chunks:                          # chronological -> seq increases -> recency
            store.store(c.key, c.content, tags=c.tags, ttl_s=None)
        signal = TaskSignal(
            turn_index=0, prompt=fx.prompt, subtask_id=None,
            recent_tool_calls=[ToolRef(name=t, call_id=f"fixture:{i}")
                               for i, t in enumerate(fx.recent_tools)],
        )
        d = target.decide(signal, store)
        gold = set(fx.gold_keys)
        ranked = [c.key for c in d.candidates]
        selected = [c.key for c in d.selected]
        ndcgs.append(ndcg_at_k(ranked, gold, k))
        precs.append(precision_at_k(ranked, gold, k))
        recs.append(recall_at_k(ranked, gold, recall_k))
        sel_precs.append(precision_at_k(selected, gold, k))
    return ArmMetrics(_mean(ndcgs), _mean(precs), _mean(recs), _mean(sel_precs), len(fixtures))
```

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/eval/test_runner.py -v` → pass. `uv run ruff check .` → clean.
```bash
git add src/context_curator/eval/runner.py tests/eval/test_runner.py
git commit -m "feat: eval runner (decide() per fixture -> ArmMetrics)"
```

---

### Task 5: Weight sweep (LOO-CV)

**Files:**
- Create: `src/context_curator/eval/sweep.py`
- Test: `tests/eval/test_sweep.py`

- [ ] **Step 1: Write the failing test**

`tests/eval/test_sweep.py`:
```python
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.sweep import DEFAULT_GRID, grid_sweep
from tests.eval.conftest import KeywordEmbedder


def _corpus():
    # 3 pro-semantic fixtures (gold old+match, distractor new+nomatch)
    return [Fixture(name=f"sw{i}", prompt="A B C D", gold_keys=["g"], split="train",
                    chunks=[FixtureChunk(key="g", content="A B C D"),
                            FixtureChunk(key="d", content="E F")]) for i in range(3)]


def test_grid_is_w_similarity_only():
    assert all(set(cell) == {"w_similarity", "w_recency"} for cell in DEFAULT_GRID)
    assert len(DEFAULT_GRID) == 5


def test_sweep_deterministic_and_populated():
    emb = KeywordEmbedder()
    a = grid_sweep(_corpus(), emb)
    b = grid_sweep(_corpus(), emb)
    assert a.best == b.best
    assert len(a.top_cells) == len(DEFAULT_GRID)
    # higher w_similarity helps on a pro-semantic corpus -> best is not the lowest-sim cell
    assert a.best.w_similarity >= 0.5
```

- [ ] **Step 2: Run to verify failure, then implement `eval/sweep.py`**

Run: `uv run pytest tests/eval/test_sweep.py -v` → FAIL. Then:
```python
"""Weight sweep (design §3.5). w_similarity only (selection bias control at small n);
LOO-CV per cell. A COARSE DIRECTIONAL SCAN — the chosen cell is not statistically
distinguishable from its neighbors at this corpus size; see top_cells."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from context_curator.embeddings import Embedder
from context_curator.eval.fixtures import Fixture
from context_curator.eval.metrics import ndcg_at_k
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import PolicyTarget
from context_curator.store.memory import InMemoryStore

DEFAULT_GRID = [
    {"w_similarity": s, "w_recency": round(1 - s, 2)} for s in (0.4, 0.5, 0.65, 0.8, 1.0)
]


@dataclass
class SweepCell:
    weights: PolicyWeights
    loo_ndcg: float
    fold_std: float


@dataclass
class SweepResult:
    best: PolicyWeights
    top_cells: list[SweepCell]


def _ndcg_one(fx: Fixture, weights: PolicyWeights, embedder: Embedder, k: int) -> float:
    store = InMemoryStore(embedder=embedder)
    for c in fx.chunks:
        store.store(c.key, c.content, tags=c.tags, ttl_s=None)
    d = PolicyTarget(RelevancePolicy(embedder, weights)).decide(
        TaskSignal(turn_index=0, prompt=fx.prompt, subtask_id=None, recent_tool_calls=[]), store)
    return ndcg_at_k([c.key for c in d.candidates], set(fx.gold_keys), k)


def grid_sweep(train_fixtures: list[Fixture], embedder: Embedder,
               grid=DEFAULT_GRID, k: int = 10, base: PolicyWeights = PolicyWeights()) -> SweepResult:
    cells: list[SweepCell] = []
    for combo in grid:                                   # fixed order -> deterministic
        w = replace(base, **combo)
        per_fixture = [_ndcg_one(fx, w, embedder, k) for fx in train_fixtures]   # LOO == per-fixture here
        mean = sum(per_fixture) / len(per_fixture) if per_fixture else 0.0
        var = (sum((x - mean) ** 2 for x in per_fixture) / len(per_fixture)) if per_fixture else 0.0
        cells.append(SweepCell(weights=w, loo_ndcg=mean, fold_std=math.sqrt(var)))
    cells.sort(key=lambda c: c.loo_ndcg, reverse=True)   # ties keep first-seen (stable sort)
    return SweepResult(best=cells[0].weights, top_cells=cells)
```
(At this corpus size the per-fixture mean IS the LOO mean for a held-out-one scorer; documented as a coarse scan.)

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/eval/test_sweep.py -v` → pass. `uv run ruff check .` → clean.
```bash
git add src/context_curator/eval/sweep.py tests/eval/test_sweep.py
git commit -m "feat: w_similarity grid sweep (coarse directional scan)"
```

---

### Task 6: Keystone command + the CI proxy

**Files:**
- Create: `src/context_curator/eval/keystone.py`
- Modify: `pyproject.toml`, `.gitignore`
- Test: `tests/eval/test_keystone_proxy.py`

- [ ] **Step 1: Write the CI proxy test (exact values + the strict arm-2 win)**

`tests/eval/test_keystone_proxy.py`:
```python
import math
from pathlib import Path

import context_curator.eval as e
from context_curator.eval.fixtures import load_fixtures
from context_curator.eval.runner import evaluate
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from tests.eval.conftest import KeywordEmbedder

CONTROLLED = str(Path(e.__file__).parent / "fixtures" / "controlled")


def _by_name(fixtures):
    return {f.name: [f] for f in fixtures}


def test_adversarial_fixture_arm2_strictly_wins():
    emb = KeywordEmbedder()
    fx = _by_name(load_fixtures(CONTROLLED))["adversarial-arm2-wins"]
    arm3 = evaluate(fx, PolicyTarget(RelevancePolicy(emb)), emb)
    arm2 = evaluate(fx, RecencyOnlyTarget(), emb)
    assert math.isclose(arm3.ndcg_at_k, 1 / math.log2(3), rel_tol=1e-9)   # gold ranked 2nd
    assert arm2.ndcg_at_k == 1.0                                          # recency finds it
    assert arm2.ndcg_at_k > arm3.ndcg_at_k                                # the negative exists


def test_semantic_fixture_arm3_perfect():
    emb = KeywordEmbedder()
    fx = _by_name(load_fixtures(CONTROLLED))["semantic-win-1"]
    arm3 = evaluate(fx, PolicyTarget(RelevancePolicy(emb)), emb)
    arm2 = evaluate(fx, RecencyOnlyTarget(), emb)
    assert arm3.ndcg_at_k == 1.0
    assert math.isclose(arm2.ndcg_at_k, 1 / math.log2(3), rel_tol=1e-9)


def test_aggregate_arm3_ahead_on_controlled():
    emb = KeywordEmbedder()
    corpus = load_fixtures(CONTROLLED)
    arm3 = evaluate(corpus, PolicyTarget(RelevancePolicy(emb)), emb)
    arm2 = evaluate(corpus, RecencyOnlyTarget(), emb)
    assert arm3.ndcg_at_k > arm2.ndcg_at_k       # majority pro-semantic -> arm-3 ahead overall
```

- [ ] **Step 2: Run to verify failure, then implement `eval/keystone.py`**

Run: `uv run pytest tests/eval/test_keystone_proxy.py -v` → FAIL. Then:
```python
"""Keystone command (design §3.6). Reports the arm-2-vs-arm-3 slice as a DIRECTIONAL,
explicitly-underpowered first-look — NOT a significant verdict (n too small)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from context_curator.embeddings import Embedder
from context_curator.eval.fixtures import load_fixtures
from context_curator.eval.metrics import ndcg_at_k
from context_curator.eval.runner import ArmMetrics, evaluate
from context_curator.eval.stats import bootstrap_ci
from context_curator.eval.sweep import grid_sweep
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights
from context_curator.replay.schema import TaskSignal
from context_curator.replay.target import PolicyTarget, RecencyOnlyTarget
from context_curator.store.memory import InMemoryStore


@dataclass
class KeystoneReport:
    best_weights: PolicyWeights
    arm3: ArmMetrics
    arm2: ArmMetrics
    n_test: int
    per_fixture_ndcg_delta: list[float]
    delta_ci90: tuple[float, float]
    verdict: str


def _ndcg_per_fixture(fixtures, target, embedder, k):
    out = []
    for fx in fixtures:
        store = InMemoryStore(embedder=embedder)
        for c in fx.chunks:
            store.store(c.key, c.content, tags=c.tags, ttl_s=None)
        d = target.decide(TaskSignal(turn_index=0, prompt=fx.prompt, subtask_id=None,
                                     recent_tool_calls=[]), store)
        out.append(ndcg_at_k([c.key for c in d.candidates], set(fx.gold_keys), k))
    return out


def run_keystone(corpus_dir: str, embedder: Embedder, k: int = 10, seed: int = 0) -> KeystoneReport:
    fixtures = load_fixtures(corpus_dir)
    train = [f for f in fixtures if f.split == "train"]
    test = [f for f in fixtures if f.split == "test"]
    assert train and test, "corpus needs both train and test fixtures"
    best = grid_sweep(train, embedder, k=k).best
    arm3_target = PolicyTarget(RelevancePolicy(embedder, best))
    arm3 = evaluate(test, arm3_target, embedder, k=k)
    arm2 = evaluate(test, RecencyOnlyTarget(), embedder, k=k)
    d3 = _ndcg_per_fixture(test, arm3_target, embedder, k)
    d2 = _ndcg_per_fixture(test, RecencyOnlyTarget(), embedder, k)
    deltas = [a - b for a, b in zip(d3, d2, strict=True)]
    lo, hi = bootstrap_ci(deltas, seed=seed)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    verdict = (f"directional: arm-3 ahead by {mean_delta:+.3f} nDCG "
               f"(UNDERPOWERED, n={len(test)}, 90% CI [{lo:+.3f},{hi:+.3f}] includes 0 "
               f"-> INCONCLUSIVE; grow corpus to n>=~30)") if lo <= 0 <= hi else (
               f"arm-3 beats arm-2 (CI excludes 0): +{mean_delta:.3f}" if lo > 0 else
               f"arm-2 wins (CI excludes 0): {mean_delta:.3f}")
    return KeystoneReport(best, arm3, arm2, len(test), deltas, (lo, hi), verdict)


def main() -> None:
    from context_curator.embeddings import FastEmbedEmbedder
    corpus = str(Path(__file__).parent / "fixtures" / "realistic")
    rpt = run_keystone(corpus, FastEmbedEmbedder())
    lines = [
        "# Keystone (DIRECTIONAL, not conclusive)",
        "",
        "> bge floats are machine-sensitive — REGENERATE, do not diff. `seed` fixes resampling only.",
        f"> n_test={rpt.n_test}; not powered to detect small deltas. Grow corpus to n>=~30.",
        "",
        f"verdict: {rpt.verdict}",
        f"best_weights: w_similarity={rpt.best_weights.w_similarity}",
        f"arm-3: nDCG@10={rpt.arm3.ndcg_at_k:.3f} P@10={rpt.arm3.precision_at_k:.3f} "
        f"R@3={rpt.arm3.recall_at_rk:.3f} sel-P={rpt.arm3.selected_precision:.3f}",
        f"arm-2: nDCG@10={rpt.arm2.ndcg_at_k:.3f} P@10={rpt.arm2.precision_at_k:.3f} "
        f"R@3={rpt.arm2.recall_at_rk:.3f} sel-P={rpt.arm2.selected_precision:.3f}",
        f"per-fixture nDCG deltas: {[round(x, 3) for x in rpt.per_fixture_ndcg_delta]}",
    ]
    text = "\n".join(lines)
    print(text)
    out = Path("results"); out.mkdir(exist_ok=True)
    (out / "keystone-10.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the proxy test + add the optional dep pin + gitignore**

Run: `uv run pytest tests/eval/test_keystone_proxy.py -v` → pass (uses `KeywordEmbedder`, no bge).
In `pyproject.toml`, pin the `embed` extra: `embed = ["fastembed>=0.3", "onnxruntime>=1.17"]`.
In `.gitignore`, add a line: `results/`.

- [ ] **Step 4: Add a skip-if-no-model bge smoke test**

Append to `tests/eval/test_keystone_proxy.py`:
```python
def test_run_keystone_smoke_with_bge():
    import pytest
    pytest.importorskip("fastembed")
    from context_curator.embeddings import FastEmbedEmbedder
    emb = FastEmbedEmbedder()
    try:
        emb.embed("warmup")
    except Exception as ex:        # noqa: BLE001
        pytest.skip(f"bge model unavailable: {ex}")
    from pathlib import Path
    import context_curator.eval as e
    rpt = run_keystone(str(Path(e.__file__).parent / "fixtures" / "realistic"), emb)
    assert rpt.n_test >= 1 and isinstance(rpt.verdict, str)
```
(Imports for `run_keystone` are already at the top of the test module.)

- [ ] **Step 5: Run the FULL suite + lint**

Run: `uv run pytest -q` → everything green (the bge smoke skips unless the model is present). `uv run ruff check .` → clean. `uv lock` (the new pin) then confirm `uv lock --check` passes.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/eval/keystone.py tests/eval/test_keystone_proxy.py pyproject.toml .gitignore uv.lock
git commit -m "feat: keystone command (directional underpowered first-look) + CI proxy"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3.1 metrics (golden tests, precision `/min(k,n)`, recall demoted) → Task 1. ✅
- §3.6 bootstrap → Task 1; verdict (directional/underpowered, seed-resampling-only) → Task 6. ✅
- §3.2 fixtures (`Literal` split, no `pin`), graded `KeywordEmbedder`, controlled corpus incl. the verified adversarial fixture, realistic starter with construct-validity rules → Task 2. ✅
- §3.4 arm-2 candidates (full recency pool) + production-faithful `selected` row, C3 grep guard → Tasks 3 (target) + 4 (runner). ✅
- §3.3 runner (`evaluate`, `ArmMetrics` with `recall_at_rk`/`selected_precision`, embedder-binding assert, `ToolRef` synthesis) → Task 4. ✅
- I-4 public `embedder` accessors → Task 3. ✅
- §3.5 sweep (`w_similarity`-only 5-cell grid, LOO/`SweepResult`, coarse-scan framing) → Task 5. ✅
- §3.6 keystone (`run_keystone`, `KeystoneReport`, gitignored `results/`, version pin, skip-if-no-model smoke) → Task 6. ✅
- §4 CI proxy (exact values + strict arm-2 win) → Task 6. ✅

**Placeholder scan:** none. The realistic corpus content is illustrative (the implementer authors ~10–16 honoring the §3.2 rules) — this is intentional corpus-building, with one concrete example given and the construct-validity constraints explicit; the controlled corpus and all code are fully specified.

**Type/signature consistency:** `evaluate(fixtures, target, embedder, k=10, recall_k=3) -> ArmMetrics` consistent Task 4 ↔ Tasks 5/6. `ArmMetrics.{ndcg_at_k, precision_at_k, recall_at_rk, selected_precision, n_fixtures}` consistent. `grid_sweep(...) -> SweepResult(best, top_cells)` consistent Task 5 ↔ 6. `bootstrap_ci(deltas, *, seed, alpha, iters)` consistent Task 1 ↔ 6. `PolicyTarget.embedder`/`RelevancePolicy.embedder` defined Task 3, used Task 4. Metric signatures `(ranked, gold, k)` consistent across all tasks. Fixture field names (`chunks`/`prompt`/`recent_tools`/`gold_keys`/`split`) consistent Task 2 ↔ 4/5/6.

**Risk note:** Task 3 changes `RecencyOnlyTarget` output (now populates `candidates`); Step 1's grep + Step 5's full-suite run gate the determinism-test impact (the spec's round-3 check found no committed snapshot encodes the old empty pool, but the grep confirms before changing).
