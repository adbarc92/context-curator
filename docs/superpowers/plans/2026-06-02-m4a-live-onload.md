# M4a — Live Onload (the read half) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `UserPromptSubmit` to inject the task-relevant slice of the live store via `additionalContext`, and `SessionStart` to seed the durable pinned/convention set — the read half of the curation loop.

**Architecture:** Two thin hooks reuse the existing fail-open `run_hook` plumbing. A new `onload/` package holds pure selection (`onload_select` raw-cosine gate + `seed_select`) and an `additionalContext` formatter. `RelevancePolicy` gains `scored_with_similarity` (exposes the raw cosine the gate needs); `scored` delegates to it. The live path is uniformly `HashingEmbedder` (256-dim, no model load) with a gate-pinned `ONLOAD_WEIGHTS` so the admitted cosine band ranks by a real recency+similarity blend. The inject JSON is the sole stdout writer (stdout-only contract); everything else logs to stderr. No store changes — dedup is deferred to M4b.

**Tech Stack:** Python 3.11+, UV, pydantic v2, pytest, ruff, SQLite. Run everything via `uv run`.

**Spec:** `docs/superpowers/specs/2026-06-02-live-onload-design.md` (hardened through 3 critique rounds).

**Branch:** `feat/m4-onload` (already checked out, off `main`).

---

## Conventions for every task

- Run tests with `uv run pytest`. Lint with `uv run ruff check .`.
- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.
- Do **not** commit to `main`. Commit on `feat/m4-onload`. No `Co-Authored-By`, no attribution footers.
- After each task's final step run the FULL suite (`uv run pytest -q`) to confirm no regression, then `uv run ruff check .`.

---

## File Structure

**Modify:**
- `src/context_curator/policy/weights.py` — add `ONLOAD_COSINE_THRESHOLD` + `ONLOAD_WEIGHTS` (co-located to avoid a circular import).
- `src/context_curator/policy/relevance.py` — add `scored_with_similarity`; `scored` delegates.
- `src/context_curator/hooks/_io.py` — `HookResult.additional_context`; `run_hook` emits the inject JSON (stdout-only) + per-hook `fail_label`.
- `.claude/settings.json` — register `UserPromptSubmit` + `SessionStart`.

**Create:**
- `src/context_curator/onload/__init__.py` — empty package marker.
- `src/context_curator/onload/select.py` — `onload_select`, `seed_select`, constants, `_CONV_RE`.
- `src/context_curator/onload/format.py` — `format_block`.
- `src/context_curator/hooks/user_prompt_submit.py` — onload hook.
- `src/context_curator/hooks/session_start.py` — seed hook.

**Tests:**
- `tests/test_onload_weights.py` (new) — gate↔floor reconciliation.
- `tests/test_policy_relevance.py` (modify) — `scored_with_similarity` + delegation.
- `tests/test_onload_format.py` (new) — `format_block`.
- `tests/test_onload_select.py` (new) — gate, exclusions, k/budget, seed, gate characterization.
- `tests/test_hooks_onload.py` (new) — `run_hook` inject (exact bytes) + both handlers (real sqlite).
- `tests/test_hooks_onload_smoke.py` (new) — subprocess stdout structural guard + latency ceiling.

---

## Task 1: `ONLOAD_WEIGHTS` + gate threshold (co-located in weights.py)

**Why:** The gate thresholds on raw cosine; the score's `sim_floor` must equal that threshold or the admitted band `cos ∈ [threshold, 0.5)` rescales to similarity 0 and ranks by recency only (round-2 C1). Co-locating the constant and the weights in `weights.py` keeps them equal by construction and avoids the circular import that would arise if the threshold lived in `select.py` (round-3 C1).

**Files:**
- Modify: `src/context_curator/policy/weights.py`
- Test: `tests/test_onload_weights.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_onload_weights.py`:

```python
from context_curator.policy.weights import ONLOAD_COSINE_THRESHOLD, ONLOAD_WEIGHTS, PolicyWeights


def test_onload_weights_sim_floor_equals_gate_threshold():
    # round-2 C1: gate floor == score floor by construction, so the admitted cosine band
    # ranks by a real recency+similarity blend (not recency-only).
    assert ONLOAD_WEIGHTS.sim_floor == ONLOAD_COSINE_THRESHOLD


def test_onload_weights_only_overrides_sim_floor():
    # everything else matches the default policy operating point
    default = PolicyWeights()
    assert ONLOAD_WEIGHTS.w_recency == default.w_recency
    assert ONLOAD_WEIGHTS.w_similarity == default.w_similarity
    assert ONLOAD_WEIGHTS.pin_bias == default.pin_bias


def test_threshold_is_provisional_placeholder_value():
    assert ONLOAD_COSINE_THRESHOLD == 0.15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_onload_weights.py -v`
Expected: FAIL — `ImportError: cannot import name 'ONLOAD_COSINE_THRESHOLD'`.

- [ ] **Step 3: Implement**

Append to `src/context_curator/policy/weights.py` (after the `PolicyWeights` class):

```python


# Onload operating point (design §3.2). The threshold lives HERE next to ONLOAD_WEIGHTS so
# the two cannot drift and so neither side imports onload/select (round-3 C1: avoids a
# weights -> select -> relevance -> weights cycle). ONLOAD_WEIGHTS pins sim_floor to the gate
# threshold (round-2 C1) so an admitted chunk's similarity grows from 0 at the gate boundary.
# 0.15 is an UNTUNED placeholder for HashingEmbedder; re-derived on the bge swap (M4b).
ONLOAD_COSINE_THRESHOLD: float = 0.15
ONLOAD_WEIGHTS = PolicyWeights(sim_floor=ONLOAD_COSINE_THRESHOLD)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_onload_weights.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/policy/weights.py tests/test_onload_weights.py
git commit -m "feat(m4a): ONLOAD_WEIGHTS + gate threshold co-located in weights.py"
```

---

## Task 2: `scored_with_similarity` (raw cosine) + `scored` delegates

**Why:** The onload gate thresholds on the **raw** cosine (before the affine rescale). Expose it from a single scoring pass; `scored` delegates so there is one implementation (round-1 M4). Must preserve `query_tags` (round-3 C2) and return `0.0` cosine on the no-embedding branch (round-2 I4).

**Files:**
- Modify: `src/context_curator/policy/relevance.py:30-65`
- Test: `tests/test_policy_relevance.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_policy_relevance.py`:

```python
def test_scored_with_similarity_exposes_raw_cosine():
    p = _policy(w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    perfect = _chunk("p", "auth", emb=[1.0, 0.0, 0.0])     # cos with "auth" task = 1.0
    triples = p.scored_with_similarity("auth q", [perfect])
    _c, _score, cos = triples[0]
    assert abs(cos - 1.0) < 1e-9


def test_scored_with_similarity_zero_cosine_when_no_embedding():
    # reembed_cap=0 + dim-mismatched stored emb -> emb None -> cos 0.0 (round-2 I4)
    p = _policy(reembed_cap=0, w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    mism = _chunk("m", "auth", emb=[0.1, 0.2])             # wrong dim, over cap -> None
    _c, _score, cos = p.scored_with_similarity("auth q", [mism])[0]
    assert cos == 0.0


def test_scored_delegates_and_threads_query_tags():
    # round-3 C2: scored() must keep passing query_tags through the delegate, else the
    # tag term silently zeroes.
    p = _policy(w_tag=1.0)
    cands = [_chunk("tagged", "far", tags=["topic"])]
    with_tags = dict((c.key, s) for c, s in p.scored("far q", cands, query_tags=["topic"]))
    without = dict((c.key, s) for c, s in p.scored("far q", cands))
    assert with_tags["tagged"] > without["tagged"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_policy_relevance.py -v -k "scored_with_similarity or threads_query_tags"`
Expected: FAIL — `AttributeError: 'RelevancePolicy' object has no attribute 'scored_with_similarity'`.

- [ ] **Step 3: Implement**

In `src/context_curator/policy/relevance.py`, replace the entire `scored` method (currently lines 30-65) with `scored_with_similarity` + a thin `scored` delegate. Replace:

```python
    def scored(self, task_text: str, candidates: list[Chunk],
               query_tags: list[str] | None = None) -> list[tuple[Chunk, float]]:
        """Embed task ONCE; score every candidate (candidates MUST be recency newest-first);
        return (chunk, score) sorted by (-score, incoming_index). reembed_cap is the
        per-pass budget; mismatched candidates beyond it score similarity 0."""
        task_emb = self._embedder.embed(task_text)
        qtags = set(query_tags or [])
        cache: dict[str, list[float]] = {}
        reembed_used = 0
        w = self._w
        results: list[tuple[Chunk, float, int]] = []
        for i, c in enumerate(candidates):
            recency = math.exp(-w.decay_lambda * i)
            emb = c.embedding
            if emb is None or len(emb) != self._embedder.dim:
                h = sha1(c.content.encode("utf-8")).hexdigest()
                if h in cache:
                    emb = cache[h]
                elif reembed_used < w.reembed_cap:
                    emb = self._embedder.embed(c.content)
                    cache[h] = emb
                    reembed_used += 1
                else:
                    emb = None  # over cap -> similarity 0
            if emb is None:
                sim = 0.0
            else:
                cos = _cosine(task_emb, emb)
                denom = max(1e-9, 1.0 - w.sim_floor)
                sim = min(1.0, max(0.0, (cos - w.sim_floor) / denom))
            tag = (len(qtags & set(c.tags)) / len(qtags)) if qtags else 0.0
            score = (w.w_recency * recency + w.w_similarity * sim
                     + w.w_tag * tag + (w.pin_bias if c.pin else 0.0))
            results.append((c, score, i))
        results.sort(key=lambda t: (-t[1], t[2]))   # (-score, incoming_index)
        return [(c, s) for (c, s, _i) in results]
```

with:

```python
    def scored_with_similarity(
        self, task_text: str, candidates: list[Chunk], query_tags: list[str] | None = None
    ) -> list[tuple[Chunk, float, float]]:
        """Embed task ONCE; score every candidate (candidates MUST be recency newest-first);
        return (chunk, score, RAW_COSINE) sorted by (-score, incoming_index). The raw cosine
        is the value BEFORE the affine rescale — the onload gate thresholds on it (design §3.2).
        It is 0.0 whenever there is no comparable embedding (over reembed_cap / dim mismatch with
        no re-embed): no comparison -> cosine 0 -> gate-excluded (round-2 I4). reembed_cap is the
        per-pass budget; mismatched candidates beyond it score similarity 0."""
        task_emb = self._embedder.embed(task_text)
        qtags = set(query_tags or [])
        cache: dict[str, list[float]] = {}
        reembed_used = 0
        w = self._w
        results: list[tuple[Chunk, float, float, int]] = []
        for i, c in enumerate(candidates):
            recency = math.exp(-w.decay_lambda * i)
            emb = c.embedding
            if emb is None or len(emb) != self._embedder.dim:
                h = sha1(c.content.encode("utf-8")).hexdigest()
                if h in cache:
                    emb = cache[h]
                elif reembed_used < w.reembed_cap:
                    emb = self._embedder.embed(c.content)
                    cache[h] = emb
                    reembed_used += 1
                else:
                    emb = None  # over cap -> similarity 0
            cos = 0.0
            if emb is None:
                sim = 0.0
            else:
                cos = _cosine(task_emb, emb)
                denom = max(1e-9, 1.0 - w.sim_floor)
                sim = min(1.0, max(0.0, (cos - w.sim_floor) / denom))
            tag = (len(qtags & set(c.tags)) / len(qtags)) if qtags else 0.0
            score = (w.w_recency * recency + w.w_similarity * sim
                     + w.w_tag * tag + (w.pin_bias if c.pin else 0.0))
            results.append((c, score, cos, i))
        results.sort(key=lambda t: (-t[1], t[3]))   # (-score, incoming_index)
        return [(c, s, cos) for (c, s, cos, _i) in results]

    def scored(self, task_text: str, candidates: list[Chunk],
               query_tags: list[str] | None = None) -> list[tuple[Chunk, float]]:
        """(chunk, score) view of scored_with_similarity — the single scoring impl (round-1 M4).
        Keeps query_tags so the tag term is preserved (round-3 C2)."""
        return [(c, s) for c, s, _cos in
                self.scored_with_similarity(task_text, candidates, query_tags)]
```

(The `sha1` and `math` imports already exist at the top of the file; no import change.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_policy_relevance.py -v`
Expected: PASS — the 3 new tests AND all pre-existing relevance tests (byte-identical `scored` output → no regression).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/policy/relevance.py tests/test_policy_relevance.py
git commit -m "feat(m4a): scored_with_similarity exposes raw cosine; scored delegates"
```

---

## Task 3: `format_block` (additionalContext formatter)

**Why:** Render selected chunks into the `additionalContext` text block, labelling each with its key/source so the model knows it is auto-onloaded curated context.

**Files:**
- Create: `src/context_curator/onload/__init__.py`
- Create: `src/context_curator/onload/format.py`
- Test: `tests/test_onload_format.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_onload_format.py`:

```python
from context_curator.models import Chunk
from context_curator.onload.format import format_block


def _c(key, content, source="tool:read"):
    return Chunk(key=key, content=content, source=source)


def test_empty_returns_empty_string():
    assert format_block([], title="Anything") == ""


def test_names_each_key_marker_and_body():
    out = format_block([_c("k1", "hello"), _c("k2", "world")], title="Ctx")
    assert "## Ctx" in out
    assert "_(auto-onloaded by ContextCurator)_" in out
    assert "[k1]" in out and "[k2]" in out
    assert "hello" in out and "world" in out


def test_truncates_overlong_content():
    out = format_block([_c("k", "x" * 5000)], title="T", per_chunk_chars=100)
    assert "…" in out
    assert ("x" * 5000) not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_onload_format.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'context_curator.onload'`.

- [ ] **Step 3: Implement**

Create `src/context_curator/onload/__init__.py` (empty file — package marker, no re-exports):

```python
```

Create `src/context_curator/onload/format.py`:

```python
"""Render selected chunks into an additionalContext block (design §3.3)."""
from __future__ import annotations

from context_curator.models import Chunk


def format_block(chunks: list[Chunk], *, title: str, per_chunk_chars: int = 1200) -> str:
    """Render selected chunks as an additionalContext block, or "" if empty. Each line names
    the source key/provenance so the model knows this is auto-onloaded curated context.

    NOTE (design §3.3): the caller budgets on estimate_tokens(content); per-line boilerplate
    and the per_chunk_chars truncation here make the rendered size only an approximation of
    that budget — acceptable at M4a's k<=10 / ~1500-token scale."""
    if not chunks:
        return ""
    lines = [f"## {title}", "_(auto-onloaded by ContextCurator)_"]
    for c in chunks:
        body = c.content if len(c.content) <= per_chunk_chars else c.content[:per_chunk_chars] + "…"
        lines.append(f"- **[{c.key}]** ({c.source}): {body}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_onload_format.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/onload/__init__.py src/context_curator/onload/format.py tests/test_onload_format.py
git commit -m "feat(m4a): onload format_block additionalContext renderer"
```

---

## Task 4: `onload_select` (raw-cosine gate) + `seed_select`

**Why:** The two pure selectors. `onload_select` admits non-pinned, non-convention candidates whose raw cosine clears the gate, ranked by full score; `seed_select` returns ALL pins (budget-exempt) plus `proj:*:conventions` under budget. Conventions are excluded from `onload_select` so SessionStart and UserPromptSubmit don't double-inject the durable set on a post-compaction turn (round-3 I1).

**Files:**
- Create: `src/context_curator/onload/select.py`
- Test: `tests/test_onload_select.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_onload_select.py`:

```python
from context_curator.embeddings import Embedder, HashingEmbedder
from context_curator.models import Chunk
from context_curator.onload.select import onload_select, seed_select
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import ONLOAD_COSINE_THRESHOLD, ONLOAD_WEIGHTS
from context_curator.store.memory import InMemoryStore

_THRESH = ONLOAD_COSINE_THRESHOLD


class _Emb(Embedder):
    """Deterministic 3-dim embedder keyed by leading token (mirrors the policy-test fake)."""
    _V = {"auth": [1.0, 0.0, 0.0], "csv": [0.0, 1.0, 0.0], "far": [0.0, 0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return self._V.get(text.split()[0], [0.0, 0.0, 0.0])


def _policy():
    return RelevancePolicy(_Emb(), ONLOAD_WEIGHTS)


def _chunk(key, topic, *, pin=False):
    return Chunk(key=key, content=f"{topic} content", pin=pin, embedding=_Emb().embed(topic))


def _onload(policy, prompt, cands, *, k=10, token_budget=None):
    return onload_select(policy, prompt, cands, cos_threshold=_THRESH, k=k, token_budget=token_budget)


# --- onload_select gate + exclusions ---------------------------------------

def test_offtopic_prompt_selects_nothing():
    cands = [_chunk("a", "far"), _chunk("b", "csv")]          # cos 0 vs "auth"
    assert _onload(_policy(), "auth query", cands) == []


def test_relevant_chunk_selected():
    cands = [_chunk("rel", "auth"), _chunk("off", "far")]
    out = _onload(_policy(), "auth query", cands)
    assert [c.key for c in out] == ["rel"]


def test_pinned_excluded_even_when_relevant():
    cands = [_chunk("pinned", "auth", pin=True), _chunk("rel", "auth")]
    out = _onload(_policy(), "auth query", cands)
    assert [c.key for c in out] == ["rel"]


def test_conventions_excluded_even_when_relevant():
    cands = [_chunk("proj:myapp:conventions", "auth"), _chunk("rel", "auth")]
    out = _onload(_policy(), "auth query", cands)
    assert "proj:myapp:conventions" not in [c.key for c in out]
    assert "rel" in [c.key for c in out]


def test_k_respected():
    cands = [_chunk(f"k{i}", "auth") for i in range(5)]
    out = _onload(_policy(), "auth query", cands, k=2)
    assert len(out) == 2


# --- gate characterization with the REAL HashingEmbedder (round-3 I4) -------

def _hchunk(key, content):
    return Chunk(key=key, content=content, embedding=HashingEmbedder().embed(content))


def test_gate_excludes_lexically_disjoint_offtopic():
    policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)
    prompt = "authenticate authorize user session"
    relevant = _hchunk("rel", "authenticate authorize user session token")
    disjoint = _hchunk("off", "quarterly financial revenue spreadsheet")   # no shared tokens
    out = _onload(policy, prompt, [relevant, disjoint])
    keys = [c.key for c in out]
    assert "rel" in keys and "off" not in keys


def test_gate_known_limitation_stopword_overlap_passes():
    # HONEST characterization (round-3 I4): HashingEmbedder does NOT strip stopwords, so a
    # chunk sharing only stopwords scores cosine ~0.7 and is NOT excluded. We assert the
    # false positive to document the gate is lexical-and-permissive, not semantic (M4b/bge job).
    policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)
    prompt = "how do i and the of a to in"
    stopword_only = _hchunk("sw", "how do i and the of a to in")
    out = _onload(policy, prompt, [stopword_only])
    assert "sw" in [c.key for c in out]


# --- seed_select ------------------------------------------------------------

def _mem():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_seed_includes_all_pins_even_past_budget():
    s = _mem()
    s.store("p1", "x" * 8000, pin=True)
    s.store("p2", "y" * 8000, pin=True)
    keys = {c.key for c in seed_select(s, token_budget=100)}
    assert "p1" in keys and "p2" in keys              # pins never budget-truncated (round-1 M2)


def test_seed_includes_conventions_under_budget():
    s = _mem()
    s.store("proj:myapp:conventions", "conventions body", pin=False)
    keys = {c.key for c in seed_select(s, token_budget=1500)}
    assert "proj:myapp:conventions" in keys


def test_seed_excludes_nonpin_nonconvention():
    s = _mem()
    s.store("session:s:tool:c", "ordinary captured tool output", pin=False)
    assert seed_select(s, token_budget=1500) == []


def test_seed_convention_key_boundary_not_matched():
    # ends "-conventions" not ":conventions" -> the proj:[^:]+:conventions regex must NOT match
    s = _mem()
    s.store("shared:decisions:naming-conventions", "x", pin=False)
    assert seed_select(s, token_budget=1500) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_onload_select.py -v`
Expected: FAIL — `ImportError: cannot import name 'onload_select' from 'context_curator.onload.select'` (module does not exist yet).

- [ ] **Step 3: Implement**

Create `src/context_curator/onload/select.py`:

```python
"""Onload selection (design §3.2): per-prompt raw-cosine gate + SessionStart seed set."""
from __future__ import annotations

import re

from context_curator.models import Chunk
from context_curator.policy.relevance import RelevancePolicy
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens

# k / budgets live here (no cross-dependency); the gate threshold + ONLOAD_WEIGHTS live in
# policy/weights.py to avoid a circular import (round-3 C1). cos_threshold is a parameter, so
# this module does NOT import from weights.
ONLOAD_K = 10
ONLOAD_TOKEN_BUDGET = 1500
SEED_TOKEN_BUDGET = 1500

# proj:{project}:conventions are SessionStart's job -> excluded from per-prompt onload so a
# post-compaction turn doesn't double-inject the durable set (round-3 I1).
_CONV_RE = re.compile(r"proj:[^:]+:conventions")


def onload_select(policy: RelevancePolicy, task_text: str, candidates: list[Chunk], *,
                  cos_threshold: float, k: int, token_budget: int | None) -> list[Chunk]:
    """Per-prompt onload: candidates whose RAW COSINE >= cos_threshold (round-1 C3), ranked by
    full score, first-fit under k+budget. EXCLUDES pins AND proj:*:conventions (both seeded at
    SessionStart). `policy` carries ONLOAD_WEIGHTS (sim_floor == cos_threshold, round-2 C1) so
    the admitted band ranks by a real recency+similarity blend, not recency alone. No cross-turn
    dedup — the relevant slice is (re)injected every turn (round-2 M1)."""
    eligible = [(c, score)
                for c, score, cos in policy.scored_with_similarity(task_text, candidates)
                if not c.pin and not _CONV_RE.fullmatch(c.key) and cos >= cos_threshold]
    return policy.pick(eligible, k, token_budget)


def seed_select(store: Store, *, token_budget: int | None) -> list[Chunk]:
    """SessionStart durable set (no task signal, NO embedding): ALL pinned chunks (never
    budget-truncated — round-1 M2) + proj:{project}:conventions under the remaining budget."""
    chunks = store.all_live_chunks()                   # newest-first
    pins = [c for c in chunks if c.pin]                # always included
    conv = [c for c in chunks if not c.pin and _CONV_RE.fullmatch(c.key)]
    out, used = list(pins), sum(estimate_tokens(c.content) for c in pins)
    for c in conv:
        t = estimate_tokens(c.content)
        if token_budget is not None and used + t > token_budget:
            break
        out.append(c)
        used += t
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_onload_select.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/onload/select.py tests/test_onload_select.py
git commit -m "feat(m4a): onload_select raw-cosine gate + seed_select durable set"
```

---

## Task 5: Hook-I/O — `additional_context` + stdout-only inject + per-hook fail label

**Why:** Let a handler emit `additionalContext` on exit 0. The inject JSON must be the **sole** stdout writer with no trailing newline (round-1 C1 / round-2 I3), and onload failures need a greppable label distinct from capture (round-1 I3 — realized as `fail_label`).

**Files:**
- Modify: `src/context_curator/hooks/_io.py`
- Test: `tests/test_hooks_onload.py` (create; the handler golden tests are appended in Tasks 6-7)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hooks_onload.py`:

```python
import json

import context_curator.hooks._io as io
from context_curator.hooks._io import HookResult, run_hook


def _noexit(monkeypatch):
    monkeypatch.setattr(io.sys, "exit", lambda code: None)


def test_inject_emits_exact_json_no_trailing_newline(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "UserPromptSubmit"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(0, additional_context="HELLO"), needs_store=False)
    out = capsys.readouterr()
    expected = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "HELLO"}}
    )
    assert out.out == expected          # EXACT bytes — no prefix, no trailing newline
    assert out.err == ""                # nothing leaked to stderr


def test_no_additional_context_writes_nothing_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "UserPromptSubmit"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(0), needs_store=False)
    assert capsys.readouterr().out == ""


def test_message_goes_to_stderr_not_stdout(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "X"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(0, message="note", additional_context="CTX"), needs_store=False)
    out = capsys.readouterr()
    assert "note" in out.err and "note" not in out.out


def test_onload_failure_logs_distinct_label(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {})
    monkeypatch.setattr(io, "open_store", lambda: object())   # avoid touching a real DB
    _noexit(monkeypatch)

    def boom(event, store):
        raise RuntimeError("x")

    run_hook(boom, needs_store=True, fail_label="onload")
    assert "onload failed" in capsys.readouterr().err         # greppable, distinct from "capture"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hooks_onload.py -v`
Expected: FAIL — `TypeError: HookResult.__init__() got an unexpected keyword argument 'additional_context'`.

- [ ] **Step 3: Implement**

In `src/context_curator/hooks/_io.py`:

(a) Add the field to `HookResult` (currently lines 19-22):

```python
@dataclass
class HookResult:
    exit_code: int          # 0 allow, 2 block
    message: str = ""       # -> stderr when blocking
    additional_context: str | None = None   # -> stdout inject JSON on exit 0 (design §3.4)
```

(b) Add the inject emitter just above `run_hook`:

```python
def _emit_inject(event: dict, text: str) -> None:
    """STDOUT-ONLY contract (design §3.4): write EXACTLY the inject JSON and nothing else.
    json.dump emits no trailing newline (unlike print(json.dumps(...))) — do NOT switch to
    print() or add a newline; any stray byte silently breaks Claude Code's additionalContext
    parse."""
    name = event.get("hook_event_name") or event.get("hookEventName") or ""
    obj = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}
    json.dump(obj, sys.stdout)
```

(c) Replace `run_hook` (currently lines 46-62) with:

```python
def run_hook(handler: Callable[..., HookResult], *, needs_store: bool,
             fail_label: str = "capture") -> None:
    try:
        event = read_event()
        if needs_store:
            result = handler(event, open_store())
        else:
            result = handler(event)
    except Exception as e:        # FAIL-OPEN
        if not needs_store:       # the guard: make the bypass visible
            log(f"{ALERT} guard crashed, allowing tool: {e}")
        else:                     # distinct, greppable per-hook label (round-1 I3)
            log(f"context-curator: {fail_label} failed: {e}")
        sys.exit(0)
        return
    if result.additional_context is not None:
        _emit_inject(event, result.additional_context)
    if result.message:
        log(result.message)
    sys.exit(result.exit_code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hooks_onload.py tests/test_hooks_io.py -v`
Expected: PASS — new inject tests pass AND the existing `test_hooks_io.py` stays green (capture path unchanged; default `fail_label="capture"` preserves the old `capture failed:` message).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/hooks/_io.py tests/test_hooks_onload.py
git commit -m "feat(m4a): HookResult.additional_context + stdout-only inject in run_hook"
```

---

## Task 6: `UserPromptSubmit` onload hook

**Why:** The per-prompt read half. Embed the prompt with `HashingEmbedder` + `ONLOAD_WEIGHTS`, gate-select over the live store, inject the block. Whitespace prompt short-circuits; a stderr breadcrumb reports the count.

**Files:**
- Create: `src/context_curator/hooks/user_prompt_submit.py`
- Test: `tests/test_hooks_onload.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hooks_onload.py`:

```python
def _sqlite(tmp_path):
    # real backend that ships (round-3 I2): exercises seq-DESC order + JSON deserialize.
    # Local imports — this block is appended BELOW Task 5's tests, so module-level imports
    # here would trip ruff E402/I (the project lints E + I).
    from context_curator.embeddings import HashingEmbedder
    from context_curator.store.sqlite_store import SqliteStore
    return SqliteStore(db_path=str(tmp_path / "o.db"), embedder=HashingEmbedder())


def test_ups_relevant_chunk_named_in_block(tmp_path):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:c1", "authenticate authorize user session token")
    r = ups.handle({"prompt": "authenticate authorize user session",
                    "hook_event_name": "UserPromptSubmit"}, s)
    assert r.additional_context is not None
    assert "session:x:tool:c1" in r.additional_context


def test_ups_offtopic_prompt_no_injection(tmp_path):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:c1", "quarterly financial revenue spreadsheet")
    r = ups.handle({"prompt": "authenticate authorize user session"}, s)
    assert r.additional_context is None


def test_ups_whitespace_prompt_no_injection_and_breadcrumb(tmp_path, capsys):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    r = ups.handle({"prompt": "   "}, s)
    assert r.additional_context is None
    assert "empty prompt" in capsys.readouterr().err


def test_ups_pins_and_conventions_excluded(tmp_path):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("pinnedkey", "authenticate authorize user session", pin=True)
    s.store("proj:myapp:conventions", "authenticate authorize user session")
    s.store("session:x:tool:c1", "authenticate authorize user session")
    r = ups.handle({"prompt": "authenticate authorize user session"}, s)
    ctx = r.additional_context or ""
    assert "session:x:tool:c1" in ctx
    assert "pinnedkey" not in ctx and "proj:myapp:conventions" not in ctx


def test_ups_breadcrumb_reports_count(tmp_path, capsys):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:c1", "authenticate authorize user session")
    ups.handle({"prompt": "authenticate authorize user session"}, s)
    assert "onloaded 1 chunk" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hooks_onload.py -v -k ups`
Expected: FAIL — `ModuleNotFoundError: No module named 'context_curator.hooks.user_prompt_submit'`.

- [ ] **Step 3: Implement**

Create `src/context_curator/hooks/user_prompt_submit.py`:

```python
"""UserPromptSubmit onload hook (design §3.5): inject the task-relevant slice of the live
store. Uniform HashingEmbedder live path (no model load); fail-open; stdout-only inject."""
from __future__ import annotations

from context_curator.embeddings import HashingEmbedder
from context_curator.hooks._io import HookResult, log, run_hook
from context_curator.onload.format import format_block
from context_curator.onload.select import ONLOAD_K, ONLOAD_TOKEN_BUDGET, onload_select
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import ONLOAD_COSINE_THRESHOLD, ONLOAD_WEIGHTS
from context_curator.store.interface import Store

_TITLE = "Relevant context from earlier in this project"


def handle(event: dict, store: Store) -> HookResult:
    prompt = (event.get("prompt") or "").strip()
    if not prompt:                                   # whitespace embeds to the zero vector
        log("context-curator: onloaded 0 (empty prompt)")
        return HookResult(0)
    policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)   # gate floor == score floor
    chunks = onload_select(policy, prompt, store.all_live_chunks(),
                           cos_threshold=ONLOAD_COSINE_THRESHOLD, k=ONLOAD_K,
                           token_budget=ONLOAD_TOKEN_BUDGET)
    log(f"context-curator: onloaded {len(chunks)} chunk(s)" if chunks
        else "context-curator: onloaded 0 (off-topic)")
    block = format_block(chunks, title=_TITLE)
    return HookResult(0, additional_context=block or None)


def main() -> None:
    run_hook(handle, needs_store=True, fail_label="onload")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hooks_onload.py -v -k ups`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/hooks/user_prompt_submit.py tests/test_hooks_onload.py
git commit -m "feat(m4a): UserPromptSubmit onload hook"
```

---

## Task 7: `SessionStart` seed hook

**Why:** Seed the durable set (pins + conventions) once per session start. `source` is intentionally ignored — re-seeding on every start (incl. `compact`) restores the durable set exactly when the window was trimmed (round-3 I1); conventions are not double-injected because `onload_select` excludes them.

**Files:**
- Create: `src/context_curator/hooks/session_start.py`
- Test: `tests/test_hooks_onload.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hooks_onload.py`:

```python
def test_session_start_seeds_pins_and_conventions(tmp_path):
    from context_curator.hooks import session_start as ss
    s = _sqlite(tmp_path)
    s.store("pinnedkey", "an important pinned decision", pin=True)
    s.store("proj:myapp:conventions", "the project conventions body")
    s.store("session:x:tool:c1", "ordinary captured output", pin=False)
    r = ss.handle({"hook_event_name": "SessionStart", "source": "compact"}, s)
    ctx = r.additional_context
    assert ctx is not None
    assert "pinnedkey" in ctx and "proj:myapp:conventions" in ctx
    assert "session:x:tool:c1" not in ctx          # non-pin non-convention not seeded


def test_session_start_empty_store_no_injection(tmp_path):
    from context_curator.hooks import session_start as ss
    s = _sqlite(tmp_path)
    r = ss.handle({"hook_event_name": "SessionStart", "source": "startup"}, s)
    assert r.additional_context is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hooks_onload.py -v -k session_start`
Expected: FAIL — `ModuleNotFoundError: No module named 'context_curator.hooks.session_start'`.

- [ ] **Step 3: Implement**

Create `src/context_curator/hooks/session_start.py`:

```python
"""SessionStart seed hook (design §3.5): inject the durable pinned/convention set once.
`source` (startup/resume/compact/clear) is intentionally ignored — re-seeding on all of
them restores the durable set after a compaction trim (round-3 I1). No embedding."""
from __future__ import annotations

from context_curator.hooks._io import HookResult, log, run_hook
from context_curator.onload.format import format_block
from context_curator.onload.select import SEED_TOKEN_BUDGET, seed_select
from context_curator.store.interface import Store

_TITLE = "Project context: pinned decisions, contracts, conventions"


def handle(event: dict, store: Store) -> HookResult:
    chunks = seed_select(store, token_budget=SEED_TOKEN_BUDGET)
    log(f"context-curator: seeded {len(chunks)} pinned/convention chunk(s)")
    block = format_block(chunks, title=_TITLE)
    return HookResult(0, additional_context=block or None)


def main() -> None:
    run_hook(handle, needs_store=True, fail_label="seed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hooks_onload.py -v -k session_start`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/hooks/session_start.py tests/test_hooks_onload.py
git commit -m "feat(m4a): SessionStart seed hook"
```

---

## Task 8: Register hooks + subprocess stdout structural guard + latency ceiling

**Why:** Wire both hooks into `.claude/settings.json`, and prove end-to-end (real subprocess) that stdout carries **exactly** the inject JSON or nothing — the structural guard a single in-process capture can't give (round-2 I3 / round-3 C4). Plus a declared 1000-chunk latency ceiling (round-3 I3). The subprocess uses `sys.executable -m <module>` (matching the existing `tests/test_hooks_smoke.py`), which invokes the interpreter directly — no `uv run` resolver output can pollute stdout.

**Files:**
- Modify: `.claude/settings.json`
- Test: `tests/test_hooks_onload_smoke.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hooks_onload_smoke.py`:

```python
import json
import os
import pathlib
import subprocess
import sys
import time

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


def _run(module, event, env):
    return subprocess.run([sys.executable, "-m", module], input=json.dumps(event),
                          capture_output=True, text=True, env=env)


def test_settings_registers_both_onload_hooks():
    # The red->green anchor for this task: empty arrays -> IndexError before registration.
    settings = json.loads(pathlib.Path(".claude/settings.json").read_text())
    hooks = settings["hooks"]
    ups_cmd = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    ss_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "user_prompt_submit" in ups_cmd
    assert "session_start" in ss_cmd


def test_user_prompt_submit_stdout_is_exactly_inject_json(tmp_path):
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store(
        "session:s:tool:c", "authenticate authorize user session token")
    r = _run("context_curator.hooks.user_prompt_submit",
             {"prompt": "authenticate authorize user session",
              "hook_event_name": "UserPromptSubmit"}, env)
    assert r.returncode == 0
    obj = json.loads(r.stdout)                       # parses cleanly => stdout is pure JSON
    assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "session:s:tool:c" in obj["hookSpecificOutput"]["additionalContext"]
    assert r.stdout == json.dumps(obj)               # exact bytes: no prefix/suffix/newline


def test_user_prompt_submit_offtopic_stdout_empty(tmp_path):
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store(
        "session:s:tool:c", "quarterly financial revenue spreadsheet")
    r = _run("context_curator.hooks.user_prompt_submit",
             {"prompt": "authenticate authorize user session",
              "hook_event_name": "UserPromptSubmit"}, env)
    assert r.returncode == 0
    assert r.stdout == ""                            # no injection => empty stdout


def test_session_start_stdout_is_exactly_inject_json(tmp_path):
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store("p", "a pinned decision", pin=True)
    r = _run("context_curator.hooks.session_start",
             {"hook_event_name": "SessionStart", "source": "startup"}, env)
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert r.stdout == json.dumps(obj)
    assert "p" in obj["hookSpecificOutput"]["additionalContext"]


def test_onload_latency_ceiling_1000_chunks(tmp_path):
    # round-3 I3: declared ceiling — at 1000 live chunks UserPromptSubmit p50 < 300ms on the
    # dev reference machine. Generous; if a slow CI trips it, convert to xfail rather than
    # weakening the budget.
    from context_curator.hooks import user_prompt_submit as ups
    s = SqliteStore(db_path=str(tmp_path / "big.db"), embedder=HashingEmbedder())
    for i in range(1000):
        s.store(f"session:x:tool:c{i}", f"chunk {i} authenticate authorize user session")
    event = {"prompt": "authenticate authorize user session", "hook_event_name": "UserPromptSubmit"}
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        ups.handle(event, s)
        times.append(time.perf_counter() - t0)
    times.sort()
    p50 = times[len(times) // 2]
    assert p50 < 0.3, f"p50={p50*1000:.0f}ms exceeds 300ms at 1000 chunks"
```

- [ ] **Step 2: Run tests to verify the registration test fails**

Run: `uv run pytest tests/test_hooks_onload_smoke.py -v`
Expected: `test_settings_registers_both_onload_hooks` FAILS — `IndexError: list index out of range` (the `UserPromptSubmit`/`SessionStart` arrays are still empty). The three stdout/structural-guard tests and the latency test invoke the modules directly and should already PASS (modules exist from Tasks 6-7) — that is expected; the registration test is this task's red→green anchor.

- [ ] **Step 3: Register both hooks**

In `.claude/settings.json`, replace the two empty arrays:

```json
    "SessionStart": [],
    "UserPromptSubmit": [],
```

with:

```json
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "uv run python -m context_curator.hooks.session_start"}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "uv run python -m context_curator.hooks.user_prompt_submit"}]}
    ],
```

(Leave `"Stop": []` as-is. The result must remain valid JSON — keep the trailing comma rules correct: `SessionStart` and `UserPromptSubmit` are followed by `"Stop"`, so both need a trailing comma after their closing `]`.)

- [ ] **Step 4: Run tests + full suite + lint**

Run: `uv run pytest tests/test_hooks_onload_smoke.py -v`
Expected: PASS (4 passed).

Run: `uv run python -c "import json; json.load(open('.claude/settings.json'))"`
Expected: no output, exit 0 (settings.json is valid JSON).

Run: `uv run pytest -q`
Expected: PASS — the entire suite green (M2 capture, M3a policy, replay, and all M4a tests).

Run: `uv run ruff check .`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add .claude/settings.json tests/test_hooks_onload_smoke.py
git commit -m "feat(m4a): register onload hooks + subprocess stdout guard + latency ceiling"
```

---

## Final verification (after all tasks)

- [ ] Run the full suite once more: `uv run pytest -q` — all green.
- [ ] `uv run ruff check .` — clean.
- [ ] Manual smoke (optional, sanity):

```bash
echo '{"prompt":"authenticate user session","hook_event_name":"UserPromptSubmit"}' | uv run python -m context_curator.hooks.user_prompt_submit
```

Expected: stdout is a single JSON object (or empty if the live store has nothing relevant); the `context-curator: ...` breadcrumb appears on stderr only.

Then hand off to the final whole-branch code review (subagent-driven-development's terminal review) before opening the PR.

---

## Spec coverage map (self-review)

| Spec section | Task |
|---|---|
| §3.1 uniform HashingEmbedder live path | Task 6 (`RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)`) |
| §3.2 `scored_with_similarity` raw cosine + delegate (C2/C3/I4) | Task 2 |
| §3.2 `onload_select` gate + pins/conventions exclusion (C1/I1) | Task 4 |
| §3.2 `seed_select` pins-always + convention regex | Task 4 |
| §3.2 `ONLOAD_WEIGHTS` gate↔floor reconciliation (C1) | Task 1 |
| §3.3 `format_block` + budget-approx note | Task 3 |
| §3.4 `HookResult.additional_context` + stdout-only inject (C1/I3) | Task 5 |
| §3.5 UserPromptSubmit (whitespace, breadcrumb, fail label) | Task 6 |
| §3.5 SessionStart (source-ignored, no double-inject) | Task 7 |
| §3.6 settings.json registration | Task 8 |
| §4 latency ceiling (1000-chunk test) | Task 8 |
| §5 gate characterization (disjoint + stopword limitation) | Task 4 |
| §5 real-sqlite goldens (I2), subprocess structural guard (I3/C4) | Tasks 6-8 |
