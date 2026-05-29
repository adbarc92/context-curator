# ContextCurator M0/M1 — Scaffold & Working-Memory Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Python/UV project and the SQLite-backed `context-curator-mcp` store behind the frozen §6 store interface, contract-tested, with tenant isolation enforced server-side and embeddings stored at write time.

**Architecture:** A pure-Python `Store` abstract interface (§6 of DESIGN.md) with two implementations: an `InMemoryStore` reference (proves the wire format in M0) and a `SqliteStore` (the real v1 backend, M1). One shared parametrized **contract-test suite** runs against every implementation, so both must satisfy identical behavior. Embeddings are computed at write time via a pluggable `Embedder` (a deterministic hashing embedder for v1; the real model is a deferred M3 decision). The MCP server is a thin adapter exposing the store as `cc_*` tools — all logic lives in the store, testable without MCP.

**Tech Stack:** Python 3.11+, UV, pydantic v2, pytest, ruff, the official `mcp` Python SDK (FastMCP). SQLite via the stdlib `sqlite3`. No ML/vector dependency in M0/M1 (embeddings serialize as JSON; cosine ranking is M3).

---

## Scope & boundaries

This plan covers **M0 (scaffold & substrate)** and **M1 (working-memory store)** only. Explicitly out of scope (later plans):
- Replay harness (§10.0) — next plan, immediately after this one.
- Capture hooks `PostToolUse`/`SubagentStop` (M2), onload hooks (M4).
- Relevance ranking by embedding cosine + the policy engine `score/select_*` (M3). In M1, `query` ranks by **recency only** — this is intentionally the "arm 2 / dumb baseline" from §10.4, and is genuinely useful on its own.

## Design decisions locked here (clarifying DESIGN.md gaps)

1. **`key` is the single canonical identifier.** The §5 schema lists both a keyspace key and an inner `id`; for v1 the storage `key` (e.g. `shared:contracts:auth`) subsumes `id`. No separate `id` field.
2. **Tenant isolation mechanism.** §6's `query` signature has no scope parameter, but §5 requires server-side enforcement. Resolution: the store is constructed with an optional `allowed_prefix` (the tenant/project scope, set by the MCP server from config). `query`, `list`, and `retrieve` **cannot return any key outside that prefix**, enforced in SQL. A `None` prefix means unrestricted (single-tenant / dev).
3. **Embeddings stored at write time** via a pluggable `Embedder`; v1 default is a deterministic `HashingEmbedder` (no heavy dependency). The real model is the §12-deferred M3 decision. Storing now validates the serialization path.
4. **Tags filtered in Python; tenant scope filtered in SQL.** Adequate at single-machine chunk counts; a tag index is a later optimization. Isolation MUST be in SQL (server-side), tags need not be.
5. **TTL:** `expires_at = created_at + ttl_s`. Expired chunks read as absent and are lazily deleted on access. **Pinned chunks never expire.**

## File structure

```
context-curator/
├── pyproject.toml                         # UV project, deps, ruff/pytest config
├── README.md                              # short project intro
├── .claude/
│   └── settings.json                      # empty hook set + Context7 (M0 substrate)
├── CLAUDE.md                              # project rule: use Context7 for lib docs
├── src/context_curator/
│   ├── __init__.py
│   ├── models.py                          # Chunk pydantic model (§5)
│   ├── keys.py                            # key grammar + tenant scope helpers
│   ├── embeddings.py                      # Embedder ABC + HashingEmbedder
│   ├── store/
│   │   ├── __init__.py
│   │   ├── interface.py                   # Store ABC (frozen §6)
│   │   ├── memory.py                      # InMemoryStore reference (M0)
│   │   └── sqlite_store.py                # SqliteStore (M1)
│   └── mcp_server.py                      # FastMCP adapter exposing cc_* tools (M1)
└── tests/
    ├── conftest.py                        # parametrized `store` fixture + helpers
    ├── test_models.py
    ├── test_keys.py
    ├── test_embeddings.py
    ├── test_store_contract.py             # runs against EVERY Store impl
    ├── test_sqlite_ttl.py                 # TTL/pin specifics
    └── test_tenant_isolation.py           # security-critical fuzz (100% pass)
```

---

# M0 — Scaffold & substrate

### Task 1: Initialize the UV project

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/context_curator/__init__.py`

- [ ] **Step 1: Initialize UV and create the package layout**

Run:
```bash
cd d:/MajorProjects/INFRASTRUCTURE/context-curator
uv init --lib --name context-curator --python 3.11
```
This creates `pyproject.toml` and a `src/` layout. If `uv init` creates a differently-named package dir, rename it to `src/context_curator`.

- [ ] **Step 2: Overwrite `pyproject.toml` with the project config**

```toml
[project]
name = "context-curator"
version = "0.0.1"
description = "Relevance-driven working-set policy and curated context store for Claude Code."
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.6",
    "mcp>=1.2",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/context_curator"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 3: Write `README.md`**

```markdown
# ContextCurator

Relevance-driven working-set policy and a curated, durable context store for Claude Code.
See `DESIGN.md` for the full design and `docs/superpowers/plans/` for implementation plans.

## Develop
```bash
uv sync --all-groups
uv run pytest
```
```

- [ ] **Step 4: Ensure `src/context_curator/__init__.py` exists and is empty (or just a docstring)**

```python
"""ContextCurator — curated context store and relevance policy for Claude Code."""
```

- [ ] **Step 5: Sync and verify the environment**

Run: `uv sync --all-groups`
Expected: resolves and installs pydantic, mcp, pytest, ruff with no errors.

Run: `uv run python -c "import context_curator; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock README.md src/context_curator/__init__.py
git commit -m "chore: scaffold UV project for context-curator"
```

---

### Task 2: Chunk model (§5 schema)

**Files:**
- Create: `src/context_curator/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from datetime import datetime, timezone

from context_curator.models import Chunk, utcnow_iso


def test_chunk_defaults():
    c = Chunk(key="shared:contracts:auth", content="POST /login -> {token}")
    assert c.key == "shared:contracts:auth"
    assert c.tags == []
    assert c.source == "tool:read"
    assert c.pin is False
    assert c.ttl_s == 86400
    assert c.last_onloaded_at is None
    assert c.embedding is None
    # created_at is a valid ISO8601 timestamp
    datetime.fromisoformat(c.created_at)


def test_chunk_explicit_fields_roundtrip():
    c = Chunk(
        key="shared:decisions:1",
        content="Use SQLite for v1",
        tags=["backend", "decision"],
        source="decision",
        pin=True,
        ttl_s=None,
        provenance="session-abc",
        embedding=[0.1, 0.2, 0.3],
    )
    dumped = c.model_dump()
    restored = Chunk(**dumped)
    assert restored == c
    assert restored.pin is True
    assert restored.ttl_s is None


def test_utcnow_iso_is_timezone_aware():
    ts = utcnow_iso()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed) == timezone.utc.utcoffset(parsed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'context_curator.models'`.

- [ ] **Step 3: Write `src/context_curator/models.py`**

```python
"""Chunk value schema (DESIGN.md §5)."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    """Timezone-aware UTC timestamp in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


class Chunk(BaseModel):
    """A stored unit of curated context.

    `key` is the canonical storage identifier (it subsumes §5's `id`).
    """

    key: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source: str = "tool:read"
    created_at: str = Field(default_factory=utcnow_iso)
    last_onloaded_at: str | None = None
    pin: bool = False
    ttl_s: int | None = 86400
    provenance: str | None = None
    embedding: list[float] | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/models.py tests/test_models.py
git commit -m "feat: add Chunk value model (DESIGN §5)"
```

---

### Task 3: Key grammar + tenant scope helpers

**Files:**
- Create: `src/context_curator/keys.py`
- Test: `tests/test_keys.py`

- [ ] **Step 1: Write the failing test**

`tests/test_keys.py`:
```python
from context_curator.keys import is_within_scope, tenant_prefix


def test_tenant_prefix_extracted():
    key = "proj:acme:tenant:t42:offloaded:9"
    assert tenant_prefix(key) == "proj:acme:tenant:t42"


def test_tenant_prefix_absent():
    assert tenant_prefix("shared:contracts:auth") is None
    assert tenant_prefix("session:abc:turn_log") is None


def test_within_scope_none_allows_everything():
    assert is_within_scope("anything:at:all", None) is True


def test_within_scope_exact_and_child():
    scope = "proj:acme:tenant:t42"
    assert is_within_scope("proj:acme:tenant:t42", scope) is True
    assert is_within_scope("proj:acme:tenant:t42:offloaded:9", scope) is True


def test_within_scope_rejects_sibling_and_prefix_collision():
    scope = "proj:acme:tenant:t42"
    # different tenant
    assert is_within_scope("proj:acme:tenant:t99:x", scope) is False
    # prefix collision must NOT match (t420 is not inside t42)
    assert is_within_scope("proj:acme:tenant:t420:x", scope) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'context_curator.keys'`.

- [ ] **Step 3: Write `src/context_curator/keys.py`**

```python
"""Keyspace grammar and tenant-scope enforcement helpers (DESIGN.md §5)."""
from __future__ import annotations

TENANT_SEGMENT = "tenant"


def tenant_prefix(key: str) -> str | None:
    """Return the `...:tenant:{id}` prefix of `key`, or None if it has no tenant."""
    parts = key.split(":")
    if TENANT_SEGMENT in parts:
        i = parts.index(TENANT_SEGMENT)
        if i + 1 < len(parts):
            return ":".join(parts[: i + 2])
    return None


def is_within_scope(key: str, allowed_prefix: str | None) -> bool:
    """True if `key` is inside `allowed_prefix`.

    `None` scope allows everything. Matching is boundary-aware: a scope of
    `proj:acme:tenant:t42` matches `...t42` and `...t42:child` but never `...t420`.
    """
    if allowed_prefix is None:
        return True
    return key == allowed_prefix or key.startswith(allowed_prefix + ":")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_keys.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/keys.py tests/test_keys.py
git commit -m "feat: add keyspace grammar and tenant-scope helpers"
```

---

### Task 4: Embedder interface + deterministic hashing embedder

**Files:**
- Create: `src/context_curator/embeddings.py`
- Test: `tests/test_embeddings.py`

- [ ] **Step 1: Write the failing test**

`tests/test_embeddings.py`:
```python
import math

from context_curator.embeddings import Embedder, HashingEmbedder


def test_hashing_embedder_is_an_embedder():
    assert issubclass(HashingEmbedder, Embedder)


def test_embedding_has_fixed_dim():
    emb = HashingEmbedder(dim=128)
    v = emb.embed("hello world")
    assert len(v) == 128


def test_embedding_is_deterministic():
    emb = HashingEmbedder(dim=64)
    assert emb.embed("auth contract") == emb.embed("auth contract")


def test_embedding_is_unit_normalized_for_nonempty():
    emb = HashingEmbedder(dim=64)
    v = emb.embed("some real tokens here")
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)


def test_empty_text_returns_zero_vector():
    emb = HashingEmbedder(dim=32)
    v = emb.embed("   ")
    assert v == [0.0] * 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_embeddings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'context_curator.embeddings'`.

- [ ] **Step 3: Write `src/context_curator/embeddings.py`**

```python
"""Pluggable embedder. v1 default is a deterministic hashing embedder; the real
model is a deferred M3 decision (DESIGN.md §12)."""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a `dim`-length embedding for `text`."""


class HashingEmbedder(Embedder):
    """Deterministic bag-of-words hashing embedder. Cheap, dependency-free,
    good enough to validate the write-time embedding path and serve as a crude
    similarity placeholder until M3 selects a real model."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in text.lower().split():
            h = int(hashlib.sha1(tok.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_embeddings.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/embeddings.py tests/test_embeddings.py
git commit -m "feat: add Embedder interface and deterministic HashingEmbedder"
```

---

### Task 5: Store interface (frozen §6) + InMemoryStore reference + contract suite

This is the M0 keystone: freeze the interface and prove the wire format with a trivial in-memory reference. The contract suite written here is reused unchanged against `SqliteStore` in M1.

**Files:**
- Create: `src/context_curator/store/__init__.py`
- Create: `src/context_curator/store/interface.py`
- Create: `src/context_curator/store/memory.py`
- Create: `tests/conftest.py`
- Test: `tests/test_store_contract.py`

- [ ] **Step 1: Write `src/context_curator/store/interface.py` (the frozen contract)**

```python
"""Store interface — DESIGN.md §6. Frozen; implementations must satisfy the
contract suite in tests/test_store_contract.py."""
from __future__ import annotations

from abc import ABC, abstractmethod

from context_curator.models import Chunk


class Store(ABC):
    @abstractmethod
    def store(
        self,
        key: str,
        content: str,
        tags: list[str] | None = None,
        ttl_s: int | None = 86400,
        pin: bool = False,
        source: str = "tool:read",
        provenance: str | None = None,
    ) -> str:
        """Write/offload a chunk. Computes and stores its embedding. Returns the key."""

    @abstractmethod
    def retrieve(self, key: str) -> Chunk | None:
        """Exact fetch. Returns None if absent, expired, or outside the allowed scope."""

    @abstractmethod
    def query(
        self,
        task_context: str,
        tags: list[str] | None = None,
        k: int = 10,
        token_budget: int | None = None,
    ) -> list[Chunk]:
        """Ranked retrieval, with content. v1 ranks by recency (M3 adds similarity).
        Never returns chunks outside the allowed scope."""

    @abstractmethod
    def list(self, prefix: str) -> list[str]:
        """Enumerate keys under `prefix` (debug/inspection). Scope-constrained."""

    @abstractmethod
    def evict(self, key: str) -> bool:
        """Remove a chunk from the STORE (not the context window). True if removed."""

    @abstractmethod
    def pin(self, key: str) -> bool:
        """Mark a chunk pinned (never expires). True if the chunk exists."""
```

- [ ] **Step 2: Write `src/context_curator/store/__init__.py`**

```python
from context_curator.store.interface import Store
from context_curator.store.memory import InMemoryStore

__all__ = ["Store", "InMemoryStore"]
```

- [ ] **Step 3: Write the contract suite `tests/test_store_contract.py` (the failing test)**

```python
import pytest

from context_curator.models import Chunk


def test_store_returns_key_and_retrieve_roundtrips(store):
    key = store.store("shared:contracts:auth", "POST /login -> {token}", tags=["auth"])
    assert key == "shared:contracts:auth"
    got = store.retrieve(key)
    assert isinstance(got, Chunk)
    assert got.content == "POST /login -> {token}"
    assert got.tags == ["auth"]


def test_store_computes_embedding_at_write_time(store):
    store.store("k1", "some content tokens", tags=[])
    got = store.retrieve("k1")
    assert got.embedding is not None
    assert len(got.embedding) > 0


def test_retrieve_missing_returns_none(store):
    assert store.retrieve("does:not:exist") is None


def test_overwrite_updates_content(store):
    store.store("k1", "v1")
    store.store("k1", "v2")
    assert store.retrieve("k1").content == "v2"


def test_evict_removes(store):
    store.store("k1", "v1")
    assert store.evict("k1") is True
    assert store.retrieve("k1") is None
    assert store.evict("k1") is False  # already gone


def test_pin_sets_pin_flag(store):
    store.store("k1", "v1", pin=False)
    assert store.pin("k1") is True
    assert store.retrieve("k1").pin is True
    assert store.pin("missing") is False


def test_list_returns_keys_under_prefix(store):
    store.store("shared:contracts:a", "x")
    store.store("shared:contracts:b", "y")
    store.store("session:s1:turn_log", "z")
    keys = set(store.list("shared:contracts"))
    assert keys == {"shared:contracts:a", "shared:contracts:b"}


def test_query_returns_chunks_with_content(store):
    store.store("k1", "alpha", tags=["x"])
    store.store("k2", "beta", tags=["x"])
    results = store.query("anything", tags=["x"], k=10)
    assert all(isinstance(r, Chunk) for r in results)
    assert {r.content for r in results} == {"alpha", "beta"}


def test_query_tag_filter(store):
    store.store("k1", "alpha", tags=["keep"])
    store.store("k2", "beta", tags=["drop"])
    results = store.query("anything", tags=["keep"], k=10)
    assert [r.key for r in results] == ["k1"]


def test_query_respects_k(store):
    for i in range(5):
        store.store(f"k{i}", f"content {i}", tags=["t"])
    assert len(store.query("anything", tags=["t"], k=3)) == 3


def test_query_token_budget_trims(store):
    # each content is ~25 chars => ~6 tokens via len//4
    for i in range(5):
        store.store(f"k{i}", "x" * 100, tags=["t"])
    # budget of 30 tokens => 100-char (25-token) chunks: only 1 fits
    results = store.query("anything", tags=["t"], k=10, token_budget=30)
    assert len(results) == 1
```

- [ ] **Step 4: Write `tests/conftest.py` with the parametrized `store` fixture**

> In M0 the fixture yields only `InMemoryStore`. Task 7 (M1) appends `SqliteStore` to `STORE_FACTORIES`, and the entire contract suite re-runs against it with no changes.

```python
import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore


def _memory_factory(tmp_path):
    return InMemoryStore(embedder=HashingEmbedder(dim=64))


# Each factory takes a tmp_path (sqlite needs it; memory ignores it) and returns a Store.
STORE_FACTORIES = [
    pytest.param(_memory_factory, id="memory"),
]


@pytest.fixture(params=STORE_FACTORIES)
def store(request, tmp_path):
    return request.param(tmp_path)
```

- [ ] **Step 5: Write `src/context_curator/store/memory.py` (minimal pass)**

```python
"""In-memory reference Store. Proves the wire format; not used in production."""
from __future__ import annotations

from context_curator.embeddings import Embedder
from context_curator.keys import is_within_scope
from context_curator.models import Chunk, utcnow_iso
from context_curator.store.interface import Store


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class InMemoryStore(Store):
    def __init__(self, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        self._data: dict[str, Chunk] = {}
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix

    def store(self, key, content, tags=None, ttl_s=86400, pin=False,
              source="tool:read", provenance=None):
        self._data[key] = Chunk(
            key=key,
            content=content,
            tags=list(tags or []),
            ttl_s=ttl_s,
            pin=pin,
            source=source,
            provenance=provenance,
            created_at=utcnow_iso(),
            embedding=self._embedder.embed(content),
        )
        return key

    def retrieve(self, key):
        c = self._data.get(key)
        if c is None or not is_within_scope(key, self._allowed_prefix):
            return None
        return c

    def query(self, task_context, tags=None, k=10, token_budget=None):
        cands = [
            c for c in self._data.values()
            if is_within_scope(c.key, self._allowed_prefix)
            and (tags is None or set(tags).issubset(set(c.tags)))
        ]
        cands.sort(key=lambda c: c.created_at, reverse=True)  # recency (M3 adds similarity)
        cands = cands[:k]
        if token_budget is not None:
            out, used = [], 0
            for c in cands:
                t = _estimate_tokens(c.content)
                if used + t > token_budget:
                    break
                out.append(c)
                used += t
            return out
        return cands

    def list(self, prefix):
        return [
            key for key in self._data
            if (key == prefix or key.startswith(prefix))
            and is_within_scope(key, self._allowed_prefix)
        ]

    def evict(self, key):
        return self._data.pop(key, None) is not None

    def pin(self, key):
        c = self._data.get(key)
        if c is None:
            return False
        c.pin = True
        return True
```

- [ ] **Step 6: Run the contract suite — verify it passes against memory**

Run: `uv run pytest tests/test_store_contract.py -v`
Expected: all tests pass with the `[memory]` parametrization (e.g. `test_store_returns_key_and_retrieve_roundtrips[memory] PASSED`).

- [ ] **Step 7: Commit**

```bash
git add src/context_curator/store/ tests/conftest.py tests/test_store_contract.py
git commit -m "feat: freeze Store interface (DESIGN §6) + InMemoryStore + contract suite"
```

---

### Task 6: Substrate config — empty hooks, Context7, CLAUDE.md rule

This is non-code project substrate (M0). No tests; verified by inspection and a JSON-validity check.

**Files:**
- Create: `.claude/settings.json`
- Create: `CLAUDE.md`

- [ ] **Step 1: Write `.claude/settings.json` with an empty (registered-but-inert) hook set**

```json
{
  "hooks": {
    "SessionStart": [],
    "UserPromptSubmit": [],
    "PostToolUse": [],
    "PreToolUse": [],
    "SubagentStop": [],
    "Stop": []
  }
}
```

- [ ] **Step 2: Write `CLAUDE.md` with the Context7 rule**

```markdown
# ContextCurator — project instructions

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
```

- [ ] **Step 3: Verify the settings JSON is valid**

Run: `uv run python -c "import json; json.load(open('.claude/settings.json')); print('valid')"`
Expected: prints `valid`.

> **Note for the implementer:** Context7 is already available in this environment as an MCP plugin, so no install step is needed; the CLAUDE.md rule above is the M0 "adopt Context7" deliverable. Compaction and subagent-model selection are runtime/CLI settings, not repo files — no action needed in this plan beyond documenting them here.

- [ ] **Step 4: Commit**

```bash
git add .claude/settings.json CLAUDE.md
git commit -m "chore: register empty hook set and Context7 usage rule (M0 substrate)"
```

---

# M1 — Working-memory store (SQLite)

### Task 7: SqliteStore — store + retrieve, wired into the contract suite

**Files:**
- Create: `src/context_curator/store/sqlite_store.py`
- Modify: `src/context_curator/store/__init__.py`
- Modify: `tests/conftest.py` (append `SqliteStore` to `STORE_FACTORIES`)

- [ ] **Step 1: Write `src/context_curator/store/sqlite_store.py` (schema + store + retrieve)**

```python
"""SQLite-backed Store (DESIGN.md §4.1, §5). Embedded, no daemon. Tenant scope
is enforced in SQL; tag filtering happens in Python (adequate at single-machine
scale). Embeddings serialize as JSON text (cosine ranking is M3)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from context_curator.embeddings import Embedder
from context_curator.keys import is_within_scope
from context_curator.models import Chunk, utcnow_iso
from context_curator.store.interface import Store

_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    key              TEXT PRIMARY KEY,
    content          TEXT NOT NULL,
    tags             TEXT NOT NULL,          -- JSON array
    source           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_onloaded_at TEXT,
    pin              INTEGER NOT NULL,       -- 0/1
    ttl_s            INTEGER,                -- nullable
    provenance       TEXT,
    embedding        TEXT,                   -- JSON array
    expires_at       TEXT                    -- precomputed, nullable; NULL when pinned or ttl_s NULL
);
"""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _compute_expires_at(created_at: str, ttl_s: int | None, pin: bool) -> str | None:
    if pin or ttl_s is None:
        return None
    base = datetime.fromisoformat(created_at)
    from datetime import timedelta

    return (base + timedelta(seconds=ttl_s)).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SqliteStore(Store):
    def __init__(self, db_path: str, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_DDL)
        self._conn.commit()
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix

    # --- helpers -----------------------------------------------------------
    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            key=row["key"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            source=row["source"],
            created_at=row["created_at"],
            last_onloaded_at=row["last_onloaded_at"],
            pin=bool(row["pin"]),
            ttl_s=row["ttl_s"],
            provenance=row["provenance"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
        )

    def _is_expired(self, row: sqlite3.Row) -> bool:
        if row["expires_at"] is None:
            return False
        return datetime.fromisoformat(row["expires_at"]) <= _now()

    # --- interface ---------------------------------------------------------
    def store(self, key, content, tags=None, ttl_s=86400, pin=False,
              source="tool:read", provenance=None):
        created_at = utcnow_iso()
        chunk = Chunk(
            key=key, content=content, tags=list(tags or []), ttl_s=ttl_s, pin=pin,
            source=source, provenance=provenance, created_at=created_at,
            embedding=self._embedder.embed(content),
        )
        self._conn.execute(
            """INSERT INTO chunks
               (key, content, tags, source, created_at, last_onloaded_at, pin,
                ttl_s, provenance, embedding, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 content=excluded.content, tags=excluded.tags, source=excluded.source,
                 created_at=excluded.created_at, pin=excluded.pin, ttl_s=excluded.ttl_s,
                 provenance=excluded.provenance, embedding=excluded.embedding,
                 expires_at=excluded.expires_at""",
            (
                key, content, json.dumps(chunk.tags), source, created_at, None,
                1 if pin else 0, ttl_s, provenance, json.dumps(chunk.embedding),
                _compute_expires_at(created_at, ttl_s, pin),
            ),
        )
        self._conn.commit()
        return key

    def retrieve(self, key):
        if not is_within_scope(key, self._allowed_prefix):
            return None
        row = self._conn.execute("SELECT * FROM chunks WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        if self._is_expired(row):
            self.evict(key)  # lazy delete
            return None
        return self._row_to_chunk(row)

    def query(self, task_context, tags=None, k=10, token_budget=None):
        # tenant scope enforced in SQL
        if self._allowed_prefix is None:
            rows = self._conn.execute(
                "SELECT * FROM chunks ORDER BY created_at DESC"
            ).fetchall()
        else:
            p = self._allowed_prefix
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE key = ? OR key LIKE ? ORDER BY created_at DESC",
                (p, p + ":%"),
            ).fetchall()
        out: list[Chunk] = []
        used = 0
        for row in rows:
            if self._is_expired(row):
                continue
            c = self._row_to_chunk(row)
            if tags is not None and not set(tags).issubset(set(c.tags)):
                continue
            if token_budget is not None:
                t = _estimate_tokens(c.content)
                if used + t > token_budget:
                    break
                used += t
            out.append(c)
            if len(out) >= k:
                break
        return out

    def list(self, prefix):
        rows = self._conn.execute(
            "SELECT key FROM chunks WHERE key = ? OR key LIKE ?", (prefix, prefix + "%")
        ).fetchall()
        return [r["key"] for r in rows if is_within_scope(r["key"], self._allowed_prefix)]

    def evict(self, key):
        cur = self._conn.execute("DELETE FROM chunks WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    def pin(self, key):
        cur = self._conn.execute(
            "UPDATE chunks SET pin = 1, expires_at = NULL WHERE key = ?", (key,)
        )
        self._conn.commit()
        return cur.rowcount > 0
```

- [ ] **Step 2: Export `SqliteStore` from `src/context_curator/store/__init__.py`**

```python
from context_curator.store.interface import Store
from context_curator.store.memory import InMemoryStore
from context_curator.store.sqlite_store import SqliteStore

__all__ = ["Store", "InMemoryStore", "SqliteStore"]
```

- [ ] **Step 3: Append `SqliteStore` to the contract parametrization in `tests/conftest.py`**

Add the import and a factory, and extend `STORE_FACTORIES`:
```python
from context_curator.store.sqlite_store import SqliteStore


def _sqlite_factory(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=64))


STORE_FACTORIES = [
    pytest.param(_memory_factory, id="memory"),
    pytest.param(_sqlite_factory, id="sqlite"),
]
```

- [ ] **Step 4: Run the contract suite — verify BOTH implementations pass**

Run: `uv run pytest tests/test_store_contract.py -v`
Expected: every contract test now reports both `[memory]` and `[sqlite]` PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/store/sqlite_store.py src/context_curator/store/__init__.py tests/conftest.py
git commit -m "feat: add SqliteStore satisfying the §6 contract suite"
```

---

### Task 8: TTL expiry and pin-survives-eviction-pressure

**Files:**
- Test: `tests/test_sqlite_ttl.py`

- [ ] **Step 1: Write the failing test**

`tests/test_sqlite_ttl.py`:
```python
import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))


def test_expired_chunk_reads_as_absent(sqlite_store):
    sqlite_store.store("k1", "transient", ttl_s=0)  # expires immediately
    assert sqlite_store.retrieve("k1") is None


def test_unexpired_chunk_is_returned(sqlite_store):
    sqlite_store.store("k1", "durable", ttl_s=3600)
    assert sqlite_store.retrieve("k1").content == "durable"


def test_pinned_chunk_never_expires(sqlite_store):
    sqlite_store.store("k1", "pinned", ttl_s=0, pin=True)
    assert sqlite_store.retrieve("k1").content == "pinned"


def test_pinning_after_store_clears_expiry(sqlite_store):
    sqlite_store.store("k1", "becomes pinned", ttl_s=0)
    # would be expired, but pin() clears expires_at
    assert sqlite_store.pin("k1") is True
    assert sqlite_store.retrieve("k1").content == "becomes pinned"


def test_none_ttl_means_no_expiry(sqlite_store):
    sqlite_store.store("k1", "forever", ttl_s=None)
    assert sqlite_store.retrieve("k1").content == "forever"


def test_expired_chunk_excluded_from_query(sqlite_store):
    sqlite_store.store("live", "a", ttl_s=3600, tags=["t"])
    sqlite_store.store("dead", "b", ttl_s=0, tags=["t"])
    keys = {c.key for c in sqlite_store.query("x", tags=["t"], k=10)}
    assert keys == {"live"}
```

- [ ] **Step 2: Run test to verify behavior**

Run: `uv run pytest tests/test_sqlite_ttl.py -v`
Expected: all PASS (the TTL/pin logic was implemented in Task 7; these tests pin the behavior down). If `test_pinned_chunk_never_expires` or `test_pinning_after_store_clears_expiry` fails, fix `_compute_expires_at` / `pin()` in `sqlite_store.py` so that pin always nulls `expires_at`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sqlite_ttl.py
git commit -m "test: TTL expiry and pin-overrides-expiry for SqliteStore"
```

---

### Task 9: Tenant isolation — security-critical fuzz (100% pass)

**Files:**
- Test: `tests/test_tenant_isolation.py`

- [ ] **Step 1: Write the failing test**

`tests/test_tenant_isolation.py`:
```python
import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


def _scoped_store(tmp_path, prefix):
    return SqliteStore(
        db_path=str(tmp_path / "cc.db"),
        embedder=HashingEmbedder(dim=32),
        allowed_prefix=prefix,
    )


def test_query_never_crosses_tenant_boundary(tmp_path):
    # Seed many tenants via an unscoped writer sharing the same db file.
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    for t in range(20):
        for i in range(5):
            seed.store(f"proj:acme:tenant:t{t}:offloaded:{i}", f"secret-{t}-{i}", tags=["s"])
    # Also a prefix-collision tenant: t1 vs t10/t11...
    target = "proj:acme:tenant:t1"
    scoped = _scoped_store(tmp_path, target)

    results = scoped.query("anything", tags=["s"], k=1000)
    assert results, "scoped query should see its own tenant's chunks"
    for c in results:
        assert c.key.startswith(target + ":"), f"LEAK: {c.key} escaped scope {target}"


def test_retrieve_blocks_out_of_scope_key(tmp_path):
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store("proj:acme:tenant:t99:secret", "nope", tags=["s"])
    scoped = _scoped_store(tmp_path, "proj:acme:tenant:t1")
    assert scoped.retrieve("proj:acme:tenant:t99:secret") is None


def test_list_blocks_out_of_scope_keys(tmp_path):
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store("proj:acme:tenant:t1:a", "x")
    seed.store("proj:acme:tenant:t10:a", "y")  # prefix collision
    seed.store("proj:acme:tenant:t99:a", "z")
    scoped = _scoped_store(tmp_path, "proj:acme:tenant:t1")
    keys = scoped.list("proj:acme:tenant:t1")
    assert keys == ["proj:acme:tenant:t1:a"]


@pytest.mark.parametrize("attacker", [
    "proj:acme:tenant:t10",
    "proj:acme:tenant:t100",
    "proj:acme:tenant:t1x",
])
def test_prefix_collision_does_not_leak(tmp_path, attacker):
    seed = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    seed.store(f"{attacker}:data", "leak?", tags=["s"])
    seed.store("proj:acme:tenant:t1:data", "mine", tags=["s"])
    scoped = _scoped_store(tmp_path, "proj:acme:tenant:t1")
    for c in scoped.query("x", tags=["s"], k=1000):
        assert c.key.startswith("proj:acme:tenant:t1:")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_tenant_isolation.py -v`
Expected: all PASS. **This is security-critical — zero leaks tolerated.** If `test_list_blocks_out_of_scope_keys` fails because `list()`'s SQL `LIKE prefix||'%'` over-matches `t10`/`t100`, the Python `is_within_scope` filter in `list()` must catch it (it does, via boundary-aware matching). If any query test leaks, fix the SQL scope clause in `query()` before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tenant_isolation.py
git commit -m "test: tenant-isolation fuzz for SqliteStore (security-critical, 100% pass)"
```

---

### Task 10: MCP server adapter exposing `cc_*` tools

The server is a thin adapter — it wires the `SqliteStore` to MCP tool names. All logic is already tested via the store; here we add a smoke test that the tools are registered and callable.

**Files:**
- Create: `src/context_curator/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Confirm the current FastMCP API via Context7**

Per `CLAUDE.md`, before writing MCP code, run `resolve-library-id` + `get-library-docs` for the `mcp` Python SDK to confirm the `FastMCP` import path and `@tool` decorator signature. The code below targets `mcp.server.fastmcp.FastMCP`; adjust if the docs differ.

- [ ] **Step 2: Write the failing smoke test**

`tests/test_mcp_server.py`:
```python
from context_curator.embeddings import HashingEmbedder
from context_curator.mcp_server import build_store_facade
from context_curator.store.sqlite_store import SqliteStore


def test_facade_exposes_all_cc_operations(tmp_path):
    store = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=32))
    facade = build_store_facade(store)
    # store
    assert facade.cc_store("shared:contracts:auth", "POST /login", tags=["auth"]) == "shared:contracts:auth"
    # retrieve
    assert facade.cc_retrieve("shared:contracts:auth")["content"] == "POST /login"
    # query
    res = facade.cc_query("auth", tags=["auth"], k=5)
    assert res and res[0]["key"] == "shared:contracts:auth"
    # list
    assert "shared:contracts:auth" in facade.cc_list("shared:contracts")
    # pin + evict
    assert facade.cc_pin("shared:contracts:auth") is True
    assert facade.cc_evict("shared:contracts:auth") is True
    assert facade.cc_retrieve("shared:contracts:auth") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_store_facade'`.

- [ ] **Step 4: Write `src/context_curator/mcp_server.py`**

```python
"""context-curator-mcp — thin MCP adapter over a Store (DESIGN.md §4.1).

`build_store_facade` returns a plain object whose methods mirror the cc_* tools;
it is the unit-testable seam. `build_mcp` registers those methods as MCP tools.
The facade returns JSON-serializable dicts (chunks as dicts), since MCP tool
results cross a process boundary."""
from __future__ import annotations

import os

from context_curator.embeddings import Embedder, HashingEmbedder
from context_curator.store.interface import Store
from context_curator.store.sqlite_store import SqliteStore


class _StoreFacade:
    def __init__(self, store: Store) -> None:
        self._store = store

    def cc_store(self, key, content, tags=None, ttl_s=86400, pin=False,
                 source="tool:read", provenance=None):
        return self._store.store(key, content, tags=tags, ttl_s=ttl_s, pin=pin,
                                 source=source, provenance=provenance)

    def cc_retrieve(self, key):
        c = self._store.retrieve(key)
        return c.model_dump() if c is not None else None

    def cc_query(self, task_context, tags=None, k=10, token_budget=None):
        return [c.model_dump() for c in
                self._store.query(task_context, tags=tags, k=k, token_budget=token_budget)]

    def cc_list(self, prefix):
        return self._store.list(prefix)

    def cc_evict(self, key):
        return self._store.evict(key)

    def cc_pin(self, key):
        return self._store.pin(key)


def build_store_facade(store: Store) -> _StoreFacade:
    return _StoreFacade(store)


def build_default_store() -> Store:
    db_path = os.environ.get("CC_DB_PATH", "context-curator.db")
    allowed_prefix = os.environ.get("CC_ALLOWED_PREFIX") or None
    embedder: Embedder = HashingEmbedder(dim=256)
    return SqliteStore(db_path=db_path, embedder=embedder, allowed_prefix=allowed_prefix)


def build_mcp():
    """Register the facade methods as MCP tools and return the FastMCP server.
    Confirm the FastMCP API via Context7 (CLAUDE.md) before relying on this."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("context-curator-mcp")
    facade = build_store_facade(build_default_store())

    mcp.tool(name="cc_store")(facade.cc_store)
    mcp.tool(name="cc_retrieve")(facade.cc_retrieve)
    mcp.tool(name="cc_query")(facade.cc_query)
    mcp.tool(name="cc_list")(facade.cc_list)
    mcp.tool(name="cc_evict")(facade.cc_evict)
    mcp.tool(name="cc_pin")(facade.cc_pin)
    return mcp


def main() -> None:
    build_mcp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: 1 passed.

> If `mcp.tool(name=...)` is not the current registration API, the smoke test still passes (it only exercises `build_store_facade`, not `build_mcp`). Fix `build_mcp` per the Context7 docs; optionally add a test that `build_mcp()` returns without error once the API is confirmed.

- [ ] **Step 6: Run the FULL suite**

Run: `uv run pytest -v`
Expected: every test passes (models, keys, embeddings, contract×{memory,sqlite}, ttl, tenant isolation, mcp facade).

- [ ] **Step 7: Lint**

Run: `uv run ruff check .`
Expected: no errors (fix any import-order/unused warnings).

- [ ] **Step 8: Commit**

```bash
git add src/context_curator/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add context-curator-mcp adapter exposing cc_* tools over the store"
```

---

## Self-review (completed by plan author)

**Spec coverage (M0/M1 rows of DESIGN.md §8):**
- M0 "Python/UV project + MCP skeleton + §6 interface + contract tests" → Tasks 1, 5, 10. ✅
- M0 "confirm compaction + subagent model selection; empty hook set" → Task 6 (documented; runtime settings noted). ✅
- M0 "adopt Context7 + CLAUDE.md rule" → Task 6. ✅
- M0 replay harness → **intentionally deferred to the next plan** per the §8 sequencing note (store-first). Documented in Scope. ✅
- M1 "store/retrieve/list/evict/pin against SQLite + §5 schema, contract tests green" → Tasks 7, 8. ✅
- M1 "store embeddings at write time" → Tasks 4, 7. ✅
- M1 "tenant-isolation enforcement in query/retrieve, server-side" → Tasks 3, 9. ✅

**Type/signature consistency:** `Store` methods in `interface.py` (Task 5) match `InMemoryStore` (Task 5) and `SqliteStore` (Task 7) exactly: `store(key, content, tags, ttl_s, pin, source, provenance)`, `query(task_context, tags, k, token_budget)`. `Chunk` fields (Task 2) are used identically in both stores and the facade. `is_within_scope`/`tenant_prefix` (Task 3) used in both stores and isolation tests. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows full assertions. The one external-API caveat (FastMCP registration) is explicitly bounded with a Context7 verification step and a fallback. ✅

**Note on `query` ranking:** v1 is recency-only by design (the §10.4 arm-2 baseline). M3 replaces the `sort by created_at` line with embedding-cosine scoring — the interface and stored embeddings already support it with no signature change.
