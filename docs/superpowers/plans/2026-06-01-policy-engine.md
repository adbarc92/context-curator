# M3a — Relevance Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the semantic relevance policy (`RelevancePolicy.scored/pick/select_*`) + a real local embedder (bge-small via fastembed) + a `PolicyTarget` for the replay harness (the arm-3 target vs the recency-only arm-2 baseline).

**Architecture:** Pure `policy/` package (product, replay-agnostic) scoring `Chunk` lists by `w_recency·recency_decay + w_similarity·similarity + w_tag·tag_match + pin_bias`; a `PolicyTarget` adapter in `replay/` (eval depends on product). Candidates come from a new full-live-set `Store.all_live_chunks()`. Store-first ordering ("plumbing before policy").

**Tech Stack:** Python 3.11+, UV, pydantic v2, pytest, ruff. Adds `fastembed` as an **optional** dependency (ONNX, no torch). Pure-Python cosine (no numpy in `policy/`).

**Spec:** `docs/superpowers/specs/2026-06-01-policy-engine-design.md` (read it; the Design Critique Log explains the non-obvious choices — single scoring pass, size-stable decay, affine similarity floor, capped re-embed, the all_live_chunks contract, InMemory TTL parity).

---

## Task ordering & file map

Store changes first (Tasks 1–2), then embedder + policy (Tasks 3–4), then the replay adapter (Task 5).

```
src/context_curator/
  store/
    expiry.py            # NEW (Task 1): is_expired(created_at, ttl_s, pin, now?)
    sqlite_store.py      # MODIFY: _is_expired -> shared helper (T1); all_live_chunks (T2)
    memory.py            # MODIFY: TTL parity in query/retrieve (T1); all_live_chunks (T2)
    interface.py         # MODIFY: add all_live_chunks to the ABC (T2)
  embeddings.py          # MODIFY (T3): FastEmbedEmbedder + _unit_normalize
  policy/
    __init__.py          # NEW (T4)
    weights.py           # NEW (T4): PolicyWeights
    relevance.py         # NEW (T4): RelevancePolicy
  replay/target.py       # MODIFY (T5): add PolicyTarget
pyproject.toml           # MODIFY (T3): [project.optional-dependencies].embed
tests/
  test_store_expiry.py        # T1
  test_store_contract.py      # MODIFY: TTL-parity + all_live_chunks contract (T1/T2)
  test_tenant_isolation.py    # MODIFY: all_live_chunks wildcard-scope (T2)
  test_fastembed.py           # T3
  test_policy_relevance.py    # T4
  replay/test_policy_target.py# T5
```

---

### Task 1: Shared expiry helper + `InMemoryStore` TTL parity

**Files:**
- Create: `src/context_curator/store/expiry.py`
- Modify: `src/context_curator/store/sqlite_store.py` (`_is_expired`)
- Modify: `src/context_curator/store/memory.py` (`query`, `retrieve`)
- Test: `tests/test_store_expiry.py`, `tests/test_store_contract.py`

- [ ] **Step 1: Write the failing expiry-helper test**

`tests/test_store_expiry.py`:
```python
from datetime import UTC, datetime, timedelta

from context_curator.store.expiry import is_expired


def _ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def test_pinned_never_expires():
    assert is_expired(_ago(10_000), ttl_s=1, pin=True) is False


def test_none_ttl_never_expires():
    assert is_expired(_ago(10_000), ttl_s=None, pin=False) is False


def test_past_ttl_is_expired():
    assert is_expired(_ago(100), ttl_s=10, pin=False) is True


def test_future_ttl_is_live():
    assert is_expired(_ago(1), ttl_s=3600, pin=False) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store_expiry.py -v` → FAIL (`ModuleNotFoundError: store.expiry`).

- [ ] **Step 3: Implement `store/expiry.py`**

```python
"""Shared chunk-expiry predicate (design §round-3 #2). One implementation for both
backends: InMemoryStore holds Chunks (no expires_at column), SqliteStore has rows."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


def is_expired(created_at: str, ttl_s: int | None, pin: bool,
               now: datetime | None = None) -> bool:
    """A chunk is expired iff it is not pinned, has a finite ttl_s, and
    created_at + ttl_s <= now. Pinned or ttl_s=None never expire."""
    if pin or ttl_s is None:
        return False
    expires_at = datetime.fromisoformat(created_at) + timedelta(seconds=ttl_s)
    return expires_at <= (now or datetime.now(UTC))
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_store_expiry.py -v` → 4 passed.

- [ ] **Step 5: Refactor `SqliteStore._is_expired` to use the helper**

In `src/context_curator/store/sqlite_store.py`, add to imports:
```python
from context_curator.store.expiry import is_expired
```
Replace `_is_expired`:
```python
    def _is_expired(self, row: sqlite3.Row) -> bool:
        return is_expired(row["created_at"], row["ttl_s"], bool(row["pin"]))
```
(The precomputed `expires_at` column is still used by `sweep_expired`'s SQL; only the per-row Python check is unified.)

- [ ] **Step 6: Add TTL parity to `InMemoryStore`**

In `src/context_curator/store/memory.py`, add the import:
```python
from context_curator.store.expiry import is_expired
```
In `retrieve`, filter expired:
```python
    def retrieve(self, key: str) -> Chunk | None:
        c = self._data.get(key)
        if c is None or not is_within_scope(key, self._allowed_prefix):
            return None
        if is_expired(c.created_at, c.ttl_s, c.pin):
            return None
        return c
```
In `query`, add the expiry filter to the comprehension:
```python
        cands = [
            c for c in self._data.values()
            if is_within_scope(c.key, self._allowed_prefix)
            and not is_expired(c.created_at, c.ttl_s, c.pin)
            and (tags is None or set(tags).issubset(set(c.tags)))
        ]
```

- [ ] **Step 7: Add a TTL-parity contract test (both backends)**

Append to `tests/test_store_contract.py` (runs under `[memory]` AND `[sqlite]` via the `store` fixture):
```python
def test_expired_chunk_absent_on_retrieve(store):
    store.store("k", "v", ttl_s=0)          # expires immediately
    assert store.retrieve("k") is None       # now true on BOTH backends (TTL parity)


def test_expired_chunk_excluded_from_query(store):
    store.store("live", "x", ttl_s=3600)
    store.store("dead", "x", ttl_s=0)
    assert {c.key for c in store.query("q", k=10)} == {"live"}
```

- [ ] **Step 8: Run the FULL suite (no regression)**

Run: `uv run pytest -q` → all green. Critical check: M0/M1 contract tests, replay determinism (replay ingests with `ttl_s=None` → never expires → wall-clock never reached), and M2 still pass. Run `uv run ruff check .` → clean.

- [ ] **Step 9: Commit**

```bash
git add src/context_curator/store/expiry.py src/context_curator/store/sqlite_store.py src/context_curator/store/memory.py tests/test_store_expiry.py tests/test_store_contract.py
git commit -m "feat: shared expiry helper + InMemoryStore TTL parity"
```

---

### Task 2: `Store.all_live_chunks()` (ABC + both backends)

**Files:**
- Modify: `src/context_curator/store/interface.py`
- Modify: `src/context_curator/store/sqlite_store.py`
- Modify: `src/context_curator/store/memory.py`
- Test: `tests/test_store_contract.py`, `tests/test_tenant_isolation.py`

- [ ] **Step 1: Write the failing contract tests**

Append to `tests/test_store_contract.py`:
```python
def test_all_live_chunks_full_set_recency_ordered(store):
    store.store("a", "x")
    store.store("b", "x")
    store.store("c", "x")
    assert [c.key for c in store.all_live_chunks()] == ["c", "b", "a"]


def test_all_live_chunks_excludes_expired(store):
    store.store("live", "x", ttl_s=3600)
    store.store("dead", "x", ttl_s=0)
    assert {c.key for c in store.all_live_chunks()} == {"live"}


def test_all_live_chunks_not_truncated(store):
    for i in range(50):
        store.store(f"k{i}", "x")
    assert len(store.all_live_chunks()) == 50   # NOT limited by query's k


def test_all_live_chunks_scope_enforced(scoped_store):
    scoped_store.store("proj:a:1", "x")
    scoped_store.store("proj:b:1", "x")      # out of scope
    assert {c.key for c in scoped_store.all_live_chunks()} == {"proj:a:1"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store_contract.py -k all_live -v` → FAIL (`AttributeError: all_live_chunks`).

- [ ] **Step 3: Add `all_live_chunks` to the `Store` ABC**

In `src/context_curator/store/interface.py`, add after `query`:
```python
    @abstractmethod
    def all_live_chunks(self) -> list[Chunk]:
        """ALL non-expired chunks in recency order (seq DESC), scope-enforced, with NO
        k/token_budget/tag truncation. The policy's full candidate source (DESIGN §6)."""
```

- [ ] **Step 4: Implement `SqliteStore.all_live_chunks`**

In `sqlite_store.py`, add (e.g. after `query`):
```python
    def all_live_chunks(self) -> list[Chunk]:
        if self._allowed_prefix is None:
            rows = self._conn.execute("SELECT * FROM chunks ORDER BY seq DESC").fetchall()
        else:
            p = self._allowed_prefix
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE key = ? OR key LIKE ? ORDER BY seq DESC",
                (p, p + ":%"),
            ).fetchall()
        out: list[Chunk] = []
        for row in rows:
            if self._is_expired(row):
                continue
            # defence-in-depth: SQL LIKE treats `_`/`%` as wildcards (same guard as query)
            if not is_within_scope(row["key"], self._allowed_prefix):
                continue
            out.append(self._row_to_chunk(row))
        return out
```

- [ ] **Step 5: Implement `InMemoryStore.all_live_chunks`**

In `memory.py`:
```python
    def all_live_chunks(self) -> list[Chunk]:
        cands = [
            c for c in self._data.values()
            if is_within_scope(c.key, self._allowed_prefix)
            and not is_expired(c.created_at, c.ttl_s, c.pin)
        ]
        cands.sort(key=lambda c: self._seq[c.key], reverse=True)  # recency newest-first
        return cands
```

- [ ] **Step 6: Run the contract tests**

Run: `uv run pytest tests/test_store_contract.py -k all_live -v` → all pass under `[memory]` and `[sqlite]`.

- [ ] **Step 7: Add the wildcard-scope security test (sqlite)**

Append to `tests/test_tenant_isolation.py` (this file already has the SqliteStore tenant fuzz helpers):
```python
def test_all_live_chunks_scope_with_like_wildcard_in_prefix(tmp_path):
    # an allowed_prefix containing '_' (a SQL LIKE wildcard) must not leak via all_live_chunks
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store("proj:my_app:tenant:t1:doc", "mine")
    seed.store("proj:myXapp:tenant:t1:doc", "leak?")   # 'X' matches '_' in LIKE
    scoped = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32),
                         allowed_prefix="proj:my_app:tenant:t1")
    assert {c.key for c in scoped.all_live_chunks()} == {"proj:my_app:tenant:t1:doc"}
```
(Match the existing imports in that file; it already imports `SqliteStore` and `HashingEmbedder`.)

- [ ] **Step 8: Run full suite + lint**

Run: `uv run pytest -q` → green. `uv run ruff check .` → clean.

- [ ] **Step 9: Commit**

```bash
git add src/context_curator/store/interface.py src/context_curator/store/sqlite_store.py src/context_curator/store/memory.py tests/test_store_contract.py tests/test_tenant_isolation.py
git commit -m "feat: Store.all_live_chunks (full live candidate set) on both backends"
```

---

### Task 3: `FastEmbedEmbedder` + optional fastembed dependency

**Files:**
- Modify: `src/context_curator/embeddings.py`
- Modify: `pyproject.toml`
- Test: `tests/test_fastembed.py`

- [ ] **Step 1: Add the optional dependency**

In `pyproject.toml`, add after the `[project]` table (do NOT add to `[project] dependencies`):
```toml
[project.optional-dependencies]
embed = ["fastembed>=0.3"]
```

- [ ] **Step 2: Implement `_unit_normalize` + `FastEmbedEmbedder` in `embeddings.py`**

Append to `src/context_curator/embeddings.py` (it already defines `Embedder` and `HashingEmbedder`; reuse `math` — add `import math` at top if absent):
```python
def _unit_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class FastEmbedEmbedder(Embedder):
    """bge-small-en-v1.5 via fastembed (ONNX, no torch). Lazy-loads the model on first
    embed; `fastembed` is an OPTIONAL dep, so the import is inside `embed`."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model = None

    @property
    def dim(self) -> int:
        return 384

    def embed(self, text: str) -> list[float]:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(self._model_name)
        vec = next(iter(self._model.embed([text])))      # numpy row
        return _unit_normalize([float(x) for x in vec])  # defensive re-normalize
```

- [ ] **Step 3: Write the model test (skips when the model isn't available)**

`tests/test_fastembed.py`:
```python
import math

import pytest


@pytest.fixture(scope="module")
def embedder():
    """Skip the whole module unless the bge model can actually be constructed —
    probing the MODEL, not just the package (a 130MB download must not block CI)."""
    pytest.importorskip("fastembed")
    from context_curator.embeddings import FastEmbedEmbedder
    emb = FastEmbedEmbedder()
    try:
        emb.embed("warmup")        # forces model construction/download
    except Exception as e:         # noqa: BLE001
        pytest.skip(f"bge model unavailable: {e}")
    return emb


def test_dim_and_unit_norm(embedder):
    v = embedder.embed("hello world")
    assert len(v) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-5)


def test_deterministic(embedder):
    assert embedder.embed("a query about authentication") == \
           embedder.embed("a query about authentication")


def test_cos_distribution_validates_sim_floor(embedder):
    # related vs unrelated short-text pairs; sim_floor=0.5 must sit between the bands
    def cos(a, b):
        va, vb = embedder.embed(a), embedder.embed(b)
        return sum(x * y for x, y in zip(va, vb))
    related = [
        cos("how do I log in a user", "user authentication and login flow"),
        cos("parse a CSV file in python", "read csv rows with the python csv module"),
        cos("fix a failing unit test", "the pytest assertion is failing"),
        cos("deploy the app to production", "production deployment pipeline"),
    ]
    unrelated = [
        cos("how do I log in a user", "the weather in Tokyo tomorrow"),
        cos("parse a CSV file in python", "best recipe for banana bread"),
        cos("fix a failing unit test", "history of the Roman empire"),
        cos("deploy the app to production", "how tall is Mount Everest"),
    ]
    assert max(unrelated) < 0.5 < min(related)
    assert min(related) - max(unrelated) > 0.1
```

- [ ] **Step 4: Run the tests**

Run: `uv sync --extra embed` (installs fastembed locally so the test actually runs), then `uv run pytest tests/test_fastembed.py -v`.
Expected: 3 passed (first run downloads the model). If the model can't download (offline), the module **skips** — that is acceptable; it must never fail-by-download in CI (CI does not install the `embed` extra).
If `test_cos_distribution_validates_sim_floor` fails, the **default `sim_floor` is what changes** (re-measure), not the test bands.

- [ ] **Step 5: Confirm the base install is unaffected**

Run: `uv run python -c "import context_curator.embeddings as e; print(hasattr(e, 'FastEmbedEmbedder'))"` → `True` (importing the module must NOT require fastembed — the import is lazy inside `embed`). Run `uv run ruff check .` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/embeddings.py pyproject.toml tests/test_fastembed.py
git commit -m "feat: FastEmbedEmbedder (bge-small, optional fastembed dep) + cos-probe"
```

---

### Task 4: `PolicyWeights` + `RelevancePolicy`

**Files:**
- Create: `src/context_curator/policy/__init__.py` (empty), `policy/weights.py`, `policy/relevance.py`
- Test: `tests/test_policy_relevance.py`

- [ ] **Step 1: Implement `policy/weights.py`**

```python
"""Policy scoring weights (design §3.2). Provisional defaults; M3b sweeps them."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyWeights:
    w_recency: float = 0.35
    w_similarity: float = 0.65
    w_tag: float = 0.0              # tags are tool-provenance not topic -> OFF by default
    pin_bias: float = 1000.0
    eviction_threshold: float = 0.15
    decay_lambda: float = 0.1       # recency = exp(-decay_lambda * rank)
    sim_floor: float = 0.5          # affine-rescale cosine above this floor
    reembed_cap: int = 128          # max inline re-embeds per scoring pass on dim mismatch
```

- [ ] **Step 2: Write the failing property tests**

`tests/test_policy_relevance.py`:
```python
from context_curator.embeddings import Embedder
from context_curator.models import Chunk
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import PolicyWeights


class FakeEmbedder(Embedder):
    """Deterministic 3-dim embedder keyed by a leading token, for exact scoring."""
    _VECS = {"auth": [1.0, 0.0, 0.0], "csv": [0.0, 1.0, 0.0], "far": [0.0, 0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return self._VECS.get(text.split()[0], [0.0, 0.0, 0.0])


def _chunk(key, topic, *, pin=False, tags=None, emb=None):
    return Chunk(key=key, content=f"{topic} content", tags=tags or [], pin=pin,
                 embedding=emb if emb is not None else FakeEmbedder().embed(topic))


def _policy(**overrides):
    return RelevancePolicy(FakeEmbedder(), PolicyWeights(**overrides))


def test_semantically_near_outranks_far():
    cands = [_chunk("near", "auth"), _chunk("far", "far")]   # newest-first
    ranked = _policy().scored("auth query", cands)
    assert ranked[0][0].key == "near"


def test_pin_always_wins_and_never_offloaded():
    cands = [_chunk("relevant", "auth"), _chunk("pinned", "far", pin=True)]
    ranked = _policy().scored("auth query", cands)
    assert ranked[0][0].key == "pinned"                      # pin_bias dominates
    assert _policy().offload_keys(ranked) == []              # a pin is never offloaded


def test_recency_decay_differentiates_when_similarity_equal():
    # both "far" (similarity 0); newer (index 0) must outrank older (index 1)
    cands = [_chunk("new", "far"), _chunk("old", "far")]
    ranked = _policy().scored("auth query", cands)
    assert [c.key for c, _ in ranked] == ["new", "old"]


def test_incoming_index_tiebreak_on_exact_tie():
    # w_recency=0 + equal similarity (both "far", sim 0) => exact score tie => index breaks it
    cands = [_chunk("first", "far"), _chunk("second", "far")]
    ranked = _policy(w_recency=0.0).scored("auth query", cands)
    assert [c.key for c, _ in ranked] == ["first", "second"]


def test_recency_decay_size_stable():
    p = _policy()
    small = p.scored("far q", [_chunk("a", "far"), _chunk("b", "far")])
    big = p.scored("far q", [_chunk(f"k{i}", "far") for i in range(50)])
    # rank-0 score identical regardless of N (decay independent of pool size)
    assert abs(small[0][1] - big[0][1]) < 1e-9


def test_similarity_affine_floor():
    # cosine exactly at sim_floor -> similarity 0; cosine 1.0 -> similarity 1.0
    p = _policy(w_recency=0.0, w_similarity=1.0, sim_floor=0.5)
    at_floor = _chunk("f", "x", emb=[0.5, 0.866, 0.0])   # cos with [1,0,0] = 0.5
    perfect = _chunk("p", "x", emb=[1.0, 0.0, 0.0])      # cos = 1.0
    ranked = dict((c.key, s) for c, s in p.scored("auth q", [perfect, at_floor]))
    assert abs(ranked["f"]) < 1e-9
    assert abs(ranked["p"] - 1.0) < 1e-9


def test_dim_mismatch_reembed_newest_first_under_cap():
    # two dim-mismatched candidates (2-dim stored vs 3-dim active), reembed_cap=1:
    # the NEWEST (index 0) is re-embedded; the older scores similarity 0.
    p = _policy(reembed_cap=1, w_recency=0.0, w_similarity=1.0, sim_floor=0.0)
    newest = _chunk("newest", "auth", emb=[0.1, 0.2])    # wrong dim -> reembed -> 'auth'
    oldest = _chunk("oldest", "auth", emb=[0.3, 0.4])    # wrong dim, over cap -> sim 0
    ranked = dict((c.key, s) for c, s in p.scored("auth q", [newest, oldest]))
    assert ranked["newest"] > 0.0
    assert ranked["oldest"] == 0.0


def test_select_offload_subthreshold_nonpinned():
    cands = [_chunk("keep", "auth"), _chunk("drop", "far"), _chunk("pinkeep", "far", pin=True)]
    keys = _policy(eviction_threshold=0.5).select_offload("auth q", cands)
    assert "drop" in keys and "keep" not in keys and "pinkeep" not in keys


def test_pick_respects_k_and_budget_break():
    cands = [_chunk(f"k{i}", "auth", emb=[1.0, 0.0, 0.0]) for i in range(5)]
    cands = [c.model_copy(update={"content": "x" * 100}) for c in cands]  # ~25 tokens each
    pairs = _policy().scored("auth q", cands)
    assert len(_policy().pick(pairs, k=2)) == 2                       # k cap
    assert len(_policy().pick(pairs, k=10, token_budget=30)) == 1     # first-fit break
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_policy_relevance.py -v` → FAIL (`ModuleNotFoundError: policy.relevance`).

- [ ] **Step 4: Implement `policy/relevance.py`**

```python
"""Relevance policy (design §3.3). Pure: operates on Chunk lists + task text + tags;
no replay/JSON knowledge. Single scoring pass per call; task embedded once."""
from __future__ import annotations

import math
from hashlib import sha1

from context_curator.embeddings import Embedder
from context_curator.models import Chunk
from context_curator.policy.weights import PolicyWeights
from context_curator.tokens import estimate_tokens


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class RelevancePolicy:
    def __init__(self, embedder: Embedder, weights: PolicyWeights = PolicyWeights()) -> None:
        self._embedder = embedder
        self._w = weights

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
                sim = max(0.0, (cos - w.sim_floor) / (1.0 - w.sim_floor))
            tag = (len(qtags & set(c.tags)) / len(qtags)) if qtags else 0.0
            score = (w.w_recency * recency + w.w_similarity * sim
                     + w.w_tag * tag + (w.pin_bias if c.pin else 0.0))
            results.append((c, score, i))
        results.sort(key=lambda t: (-t[1], t[2]))   # (-score, incoming_index)
        return [(c, s) for (c, s, _i) in results]

    def pick(self, scored_pairs: list[tuple[Chunk, float]], k: int = 10,
             token_budget: int | None = None) -> list[Chunk]:
        out: list[Chunk] = []
        used = 0
        for c, _ in scored_pairs:
            if token_budget is not None:
                t = estimate_tokens(c.content)
                if used + t > token_budget:
                    break                       # first-fit BREAK (matches arm-2)
                used += t
            out.append(c)
            if len(out) >= k:
                break
        return out

    def offload_keys(self, scored_pairs: list[tuple[Chunk, float]]) -> list[str]:
        return [c.key for c, s in scored_pairs
                if not c.pin and s < self._w.eviction_threshold]

    def select_onload(self, task_text: str, candidates: list[Chunk],
                      query_tags: list[str] | None = None, k: int = 10,
                      token_budget: int | None = None) -> list[Chunk]:
        return self.pick(self.scored(task_text, candidates, query_tags), k, token_budget)

    def select_offload(self, task_text: str, candidates: list[Chunk],
                       query_tags: list[str] | None = None) -> list[str]:
        return self.offload_keys(self.scored(task_text, candidates, query_tags))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_policy_relevance.py -v` → all passed.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/policy/ tests/test_policy_relevance.py
git commit -m "feat: RelevancePolicy (semantic scoring, single pass) + PolicyWeights"
```

---

### Task 5: `PolicyTarget` (arm-3 replay target)

**Files:**
- Modify: `src/context_curator/replay/target.py`
- Test: `tests/replay/test_policy_target.py`

- [ ] **Step 1: Write the failing test**

`tests/replay/test_policy_target.py`:
```python
from context_curator.embeddings import Embedder
from context_curator.policy.relevance import RelevancePolicy
from context_curator.replay.capture.synthetic import TraceBuilder
from context_curator.replay.engine import ReplayEngine
from context_curator.replay.schema import Decision, TaskSignal, ToolRef
from context_curator.replay.target import PolicyTarget, ReplayTarget
from context_curator.store.memory import InMemoryStore


class FakeEmbedder(Embedder):
    _VECS = {"auth": [1.0, 0.0], "other": [0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 2

    def embed(self, text: str) -> list[float]:
        return self._VECS.get(text.split()[0], [0.0, 0.0])


def _target():
    return PolicyTarget(RelevancePolicy(FakeEmbedder()))


def test_satisfies_protocol_and_populates_decision():
    t: ReplayTarget = _target()                         # structural conformance
    assert t.name == "semantic-policy"
    store = InMemoryStore(embedder=FakeEmbedder())
    store.store("session:s:tool:0", "auth thing", ttl_s=None)
    sig = TaskSignal(turn_index=0, prompt="auth please", subtask_id=None,
                     recent_tool_calls=[ToolRef(name="Read", call_id="c0")])
    d = t.decide(sig, store)
    assert isinstance(d, Decision)
    assert d.candidates and d.candidates[0].score is not None
    assert d.offloaded == []                            # offload deferred to M4


def test_uses_all_live_chunks_not_truncated_query():
    # 30 chunks: a k=10 query would hide older ones; the policy must see all 30
    store = InMemoryStore(embedder=FakeEmbedder())
    for i in range(30):
        store.store(f"session:s:tool:{i}", "other", ttl_s=None)
    store.store("session:s:tool:target", "auth content", ttl_s=None)  # newest, relevant
    sig = TaskSignal(turn_index=0, prompt="auth please", subtask_id=None, recent_tool_calls=[])
    d = _target().decide(sig, store)
    assert len(d.candidates) == 31                       # saw the full live set
    assert d.selected[0].key == "session:s:tool:target"  # relevant one ranked first


def test_byte_identical_across_runs():
    trace = (TraceBuilder("s")
             .user("auth please")
             .tool("Read", {}).result("auth content")
             .user("auth again")
             .build())
    a = ReplayEngine(target=_target()).run(trace).model_dump()
    b = ReplayEngine(target=_target()).run(trace).model_dump()
    assert a == b                                        # single-machine determinism
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/replay/test_policy_target.py -v` → FAIL (`ImportError: cannot import name 'PolicyTarget'`).

- [ ] **Step 3: Implement `PolicyTarget` in `replay/target.py`**

Add imports at the top of `src/context_curator/replay/target.py` (it already imports `Decision`, `SelectedChunk`, `TaskSignal`, `estimate_tokens`, `Store`):
```python
from context_curator.policy.relevance import RelevancePolicy
```
Append the class:
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/replay/test_policy_target.py -v` → 3 passed.

- [ ] **Step 5: Run the FULL suite + lint**

Run: `uv run pytest -q` → everything green (all prior + policy + target). `uv run ruff check .` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/replay/target.py tests/replay/test_policy_target.py
git commit -m "feat: PolicyTarget (arm-3 semantic replay target over RelevancePolicy)"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3.1 `FastEmbedEmbedder` + optional dep + cos-probe → Task 3. ✅
- §3.2 `PolicyWeights` (incl. `decay_lambda`/`sim_floor`/`reembed_cap`) → Task 4. ✅
- §3.3 `RelevancePolicy` (single-pass `scored`→`pick`/`offload_keys`, `_score` via incoming-index recency, affine similarity, capped recency-priority re-embed, break-semantics `pick`, internal cosine) → Task 4. ✅
- §3.3-C1 `all_live_chunks` (full set, seq DESC, per-row expiry, defence-in-depth scope) → Task 2; InMemory TTL parity + shared `expiry` helper → Task 1. ✅
- §3.4 `PolicyTarget` (all_live_chunks source, no query_tags, single pass, rounded scores, offloaded=[], subtask_id not embedded) → Task 5. ✅
- §4 property tests (pin, semantic, recency-decay differentiates + size-stable, tie-break on exact tie via w_recency=0, affine floor, reembed-cap newest-first, offload, k/budget break) → Tasks 4/5; cos-probe → Task 3; all_live_chunks + wildcard-scope contract → Task 2. ✅
- §6 / DESIGN §6 interface amendment → already committed in the spec commit; the ABC method lands in Task 2. ✅

**Placeholder scan:** none. `sim_floor`/weights are provisional-by-design (M3b finalizes), not placeholders. The cos-probe uses concrete numeric bars.

**Type/signature consistency:** `RelevancePolicy.scored(task_text, candidates, query_tags=None) -> [(Chunk,float)]`, `pick(scored_pairs, k, token_budget)`, `offload_keys(scored_pairs)` identical in Task 4 def and Task 5 `PolicyTarget` use. `PolicyWeights` field names (`w_recency/w_similarity/w_tag/pin_bias/eviction_threshold/decay_lambda/sim_floor/reembed_cap`) consistent Task 4 ↔ tests. `Store.all_live_chunks() -> list[Chunk]` identical across ABC (Task 2), both impls, and `PolicyTarget`. `is_expired(created_at, ttl_s, pin, now=None)` consistent Task 1 ↔ both stores. `FakeEmbedder` in policy tests (3-dim) and target tests (2-dim) are independent fixtures — intentional.

**Risk note:** Task 1 changes existing store behavior (InMemory now expires). Step 8 re-runs the entire prior suite; the replay determinism path is safe because replay ingests with `ttl_s=None` (verified in the round-2/3 critique).
