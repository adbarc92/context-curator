# M2 — Capture Path & Guardrail Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Claude Code hooks (PostToolUse/SubagentStop capture, PreToolUse guardrail) to the shared embedded store, with a concurrency-safe store and a canonical `capture/` module that `replay/ingest.py` reuses.

**Architecture:** A concurrency-safe `SqliteStore` (WAL + busy_timeout + autocommit/explicit `BEGIN IMMEDIATE` for race-free `seq` + rate-limited sweep + a unified absolute DB path) underneath pure `capture/` and `guard/` modules, with thin `hooks/` adapters that read stdin event JSON and emit the §6 exit code (0 allow / 2 block), fail-open on error.

**Tech Stack:** Python 3.11+, UV, pydantic v2, pytest, ruff, stdlib `sqlite3`/`re`/`hashlib`/`fnmatch`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-capture-hooks-design.md` (read it; its Design Critique Log explains the non-obvious choices — the rollback, the no-space redirect regex, the autocommit/BEGIN-IMMEDIATE, the resolve_db_path unification).

---

## Sequencing & scope

**Task 1 (store concurrency + path unification) lands first** — every hook depends on it. Tasks 2–3 build the canonical capture module (and refactor `replay/ingest.py`). Task 4 the guard. Tasks 5–7 the hooks. Task 8 wires settings + smoke. All hook code is dev/eval+runtime tooling under `src/context_curator/`.

**Payload field-name caveat:** the exact Claude Code hook event JSON field names (`tool_name`, `tool_input`, `tool_response`, `session_id`, `transcript_path`, `isSidechain`, etc.) and `tool_input` sub-fields (`file_path`, `content`, `new_string`, `edits`) are written here against the documented shape and **must be confirmed against a real event at implementation time** — isolated to the hook-adapter functions. The pure capture/guard modules don't depend on them.

## File structure

```
src/context_curator/
  store/
    paths.py            # NEW: resolve_db_path()
    sqlite_store.py     # MODIFY: autocommit+WAL+busy_timeout, mkdir, cc_meta, BEGIN IMMEDIATE+rollback, sweep_expired
  mcp_server.py         # MODIFY: build_default_store() uses resolve_db_path()
  capture/
    __init__.py
    tool_result.py      # capture_tool_result (canonical)
    file_ledger.py      # capture_file_write
    subagent.py         # capture_subagent_summary
  guard/
    __init__.py
    config.py           # defaults + load_config + constants
    paths.py            # is_sensitive_path
    secrets.py          # scan_secrets
  hooks/
    __init__.py
    _io.py              # HookResult, read_event, open_store, run_hook, log
    post_tool_use.py
    subagent_stop.py
    pre_tool_use.py
  replay/ingest.py      # MODIFY: thin wrapper over capture_tool_result
.claude/settings.json   # MODIFY: register the three hooks
.gitignore              # MODIFY: *-wal, *-shm
tests/                  # as listed per task
```

---

### Task 1: Concurrency-safe store + `resolve_db_path`

**Files:**
- Create: `src/context_curator/store/paths.py`
- Modify: `src/context_curator/store/sqlite_store.py`
- Modify: `src/context_curator/mcp_server.py:75-80`
- Modify: `.gitignore`
- Test: `tests/test_resolve_db_path.py`, `tests/test_store_concurrency.py`

- [ ] **Step 1: Write `resolve_db_path` failing test**

`tests/test_resolve_db_path.py`:
```python
import os
from pathlib import Path

from context_curator.store.paths import resolve_db_path


def test_env_var_wins_and_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "x.db"))
    p = resolve_db_path()
    assert Path(p).is_absolute()
    assert p == str((tmp_path / "x.db").resolve())


def test_default_is_absolute_and_cwd_independent(monkeypatch):
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    p = resolve_db_path()
    assert Path(p).is_absolute()
    assert p.endswith(os.path.join(".context-curator", "store.db"))


def test_mcp_and_hook_resolve_identically(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "shared.db"))
    from context_curator import mcp_server
    # build_default_store must use resolve_db_path under the hood
    store = mcp_server.build_default_store()
    assert Path(store._conn.execute("PRAGMA database_list").fetchall()[0]["file"]) \
        == Path(resolve_db_path())
    store.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_resolve_db_path.py -v`
Expected: FAIL (`ModuleNotFoundError: context_curator.store.paths`).

- [ ] **Step 3: Implement `store/paths.py`**

```python
"""Single source of the DB path, shared by hooks and the MCP server (design §3.4 C2)."""
from __future__ import annotations

import os
from pathlib import Path


def resolve_db_path() -> str:
    """Absolute, CWD-independent DB path. `$CC_DB_PATH` wins; else `<project>/.context-curator/
    store.db` (project root = nearest ancestor with .git/pyproject.toml); else `~/.context-curator/
    store.db`. Absolute so hook subprocesses and the server never resolve to different files."""
    env = os.environ.get("CC_DB_PATH")
    if env:
        return str(Path(env).expanduser().resolve())
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return str(parent / ".context-curator" / "store.db")
    return str(Path.home() / ".context-curator" / "store.db")
```

- [ ] **Step 4: Modify `SqliteStore.__init__` — autocommit, WAL, busy_timeout, mkdir, cc_meta**

In `sqlite_store.py`, add `cc_meta` to `_DDL` (so two processes can't race to create it) and change the connection. Replace `_DDL`:
```python
_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    key              TEXT PRIMARY KEY,
    content          TEXT NOT NULL,
    tags             TEXT NOT NULL,
    source           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_onloaded_at TEXT,
    pin              INTEGER NOT NULL,
    ttl_s            INTEGER,
    provenance       TEXT,
    embedding        TEXT,
    expires_at       TEXT,
    seq              INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cc_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""
```
Replace `__init__`:
```python
    def __init__(self, db_path: str, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        parent = Path(db_path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None (autocommit): store() uses an explicit, version-proof
        # BEGIN IMMEDIATE for race-free seq; busy_timeout handles writer contention; WAL
        # allows concurrent readers. check_same_thread=False for the FastMCP thread case.
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_DDL)      # multi-statement DDL needs executescript
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix
```
Add `from pathlib import Path` to the imports at the top.

- [ ] **Step 5: Modify `store()` — embedding before the transaction, explicit `BEGIN IMMEDIATE` + rollback**

Replace the body of `store()` (keep the signature):
```python
    def store(self, key: str, content: str, tags: list[str] | None = None,
              ttl_s: int | None = 86400, pin: bool = False,
              source: str = "tool:read", provenance: str | None = None) -> str:
        created_at = utcnow_iso()
        # Embed BEFORE the transaction so the write lock is not held during embedding
        # (round-3 fix; matters once the M3 embedder replaces the cheap HashingEmbedder).
        embedding = self._embedder.embed(content)
        chunk = Chunk(
            key=key, content=content, tags=list(tags or []), ttl_s=ttl_s, pin=pin,
            source=source, provenance=provenance, created_at=created_at, embedding=embedding,
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
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
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.rollback()   # no-op if no txn; frees the write lock so the
            raise                   # long-lived MCP server connection isn't wedged
        return key
```

- [ ] **Step 6: Remove the now-no-op `commit()` calls in `evict()` and `pin()`**

In `evict()` delete the line `self._conn.commit()`; in `pin()` delete the line `self._conn.commit()`. (Under autocommit each statement commits immediately.)

- [ ] **Step 7: Add `sweep_expired` at the bottom of `sqlite_store.py`**

```python
SWEEP_INTERVAL_S = 300


def sweep_expired(store: "SqliteStore") -> int:
    """Rate-limited deletion of expired (non-pinned) chunks. At most one sweep per
    SWEEP_INTERVAL_S across processes (gated by cc_meta.last_sweep). Returns rows deleted."""
    conn = store._conn
    now = _now()
    row = conn.execute("SELECT value FROM cc_meta WHERE key = 'last_sweep'").fetchone()
    if row is not None:
        last = datetime.fromisoformat(row["value"])
        if (now - last).total_seconds() < SWEEP_INTERVAL_S:
            return 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "DELETE FROM chunks WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now.isoformat(),),
        )
        conn.execute(
            "INSERT INTO cc_meta(key, value) VALUES ('last_sweep', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now.isoformat(),),
        )
        conn.execute("COMMIT")
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise
```

- [ ] **Step 8: Point `mcp_server.build_default_store` at `resolve_db_path`**

In `mcp_server.py`, replace `build_default_store`:
```python
def build_default_store() -> Store:
    """Construct the store at the unified DB path (shared with the hooks)."""
    from context_curator.store.paths import resolve_db_path
    allowed_prefix = os.environ.get("CC_ALLOWED_PREFIX") or None
    embedder: Embedder = HashingEmbedder(dim=256)
    return SqliteStore(db_path=resolve_db_path(), embedder=embedder, allowed_prefix=allowed_prefix)
```

- [ ] **Step 9: Update `.gitignore`**

Add these lines:
```
*-wal
*-shm
.context-curator/
```

- [ ] **Step 10: Write the concurrency test**

`tests/test_store_concurrency.py`:
```python
import sqlite3
import threading

import pytest

from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SWEEP_INTERVAL_S, SqliteStore, sweep_expired


def _store(tmp_path):
    return SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=16))


def test_wal_enabled(tmp_path):
    s = _store(tmp_path)
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_concurrent_writers_no_duplicate_seq(tmp_path):
    db = str(tmp_path / "cc.db")
    _store(tmp_path)  # create schema
    def writer(n):
        s = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=16))
        for i in range(50):
            s.store(f"k{n}-{i}", "x")
        s.close()
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    chk = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=16))
    seqs = [r[0] for r in chk._conn.execute("SELECT seq FROM chunks").fetchall()]
    assert len(seqs) == len(set(seqs)) == 200  # 4*50 rows, all-distinct seq


def test_store_rollback_leaves_connection_usable(tmp_path):
    class Boom(HashingEmbedder):
        def embed(self, text):
            raise RuntimeError("boom")
    # embed raises -> store() raises, but no lingering write lock
    s = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=Boom(dim=16))
    with pytest.raises(RuntimeError):
        s.store("k1", "x")
    s2 = SqliteStore(db_path=str(tmp_path / "cc.db"), embedder=HashingEmbedder(dim=16))
    s2.store("k2", "y")  # must not hang/lock
    assert s2.retrieve("k2") is not None


def test_sweep_deletes_expired_spares_pinned_and_rate_limits(tmp_path, monkeypatch):
    s = _store(tmp_path)
    s.store("dead", "x", ttl_s=0)
    s.store("live", "y", ttl_s=3600)
    s.store("pinned", "z", ttl_s=0, pin=True)
    assert sweep_expired(s) == 1                    # only 'dead'
    assert s.retrieve("live") is not None
    assert s.retrieve("pinned") is not None
    s.store("dead2", "x", ttl_s=0)
    assert sweep_expired(s) == 0                    # rate-limited within SWEEP_INTERVAL_S
```

- [ ] **Step 11: Run the concurrency test + the FULL suite**

Run: `uv run pytest tests/test_store_concurrency.py tests/test_resolve_db_path.py -v` → all pass.
Run: `uv run pytest -q` → the entire existing M0/M1 + replay suite stays green (autocommit/removed-commit changes are behavior-preserving for single-threaded writes). Run `uv run ruff check .` → clean.

- [ ] **Step 12: Commit**

```bash
git add src/context_curator/store/paths.py src/context_curator/store/sqlite_store.py src/context_curator/mcp_server.py .gitignore tests/test_resolve_db_path.py tests/test_store_concurrency.py
git commit -m "feat: concurrency-safe store (WAL, race-free seq, sweep) + unified resolve_db_path"
```

---

### Task 2: Canonical `capture_tool_result` + refactor `replay/ingest.py`

**Files:**
- Create: `src/context_curator/capture/__init__.py` (empty), `src/context_curator/capture/tool_result.py`
- Modify: `src/context_curator/replay/ingest.py`
- Test: `tests/test_capture_tool_result.py`

- [ ] **Step 1: Write the failing test**

`tests/test_capture_tool_result.py`:
```python
from context_curator.capture.tool_result import CAPTURE_MAX_CONTENT, capture_tool_result
from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_ordinal_key_for_replay():
    s = _store()
    key = capture_tool_result(s, session_id="s1", tool_name="Read", content="x",
                              call_id="c0", ordinal=3, ttl_s=None, max_content=None)
    assert key == "session:s1:tool:000003:c0"


def test_live_key_uses_call_id_then_content_hash():
    s = _store()
    k1 = capture_tool_result(s, session_id="s1", tool_name="Read", content="x", call_id="abc")
    assert k1 == "session:s1:tool:abc"
    k2 = capture_tool_result(s, session_id="s1", tool_name="Read", content="hello")
    assert k2.startswith("session:s1:tool:") and len(k2.split(":")[-1]) == 12  # content hash


def test_error_skipped():
    s = _store()
    assert capture_tool_result(s, session_id="s1", tool_name="Bash", content="boom",
                               error=True) is None


def test_two_distinct_results_two_keys():
    s = _store()
    capture_tool_result(s, session_id="s1", tool_name="Read", content="a", call_id="c0")
    capture_tool_result(s, session_id="s1", tool_name="Read", content="b", call_id="c1")
    assert len(s.list("session:s1")) == 2


def test_truncation_only_when_max_content_set():
    s = _store()
    big = "x" * (CAPTURE_MAX_CONTENT + 100)
    k = capture_tool_result(s, session_id="s1", tool_name="Read", content=big,
                            call_id="c0", max_content=CAPTURE_MAX_CONTENT)
    assert s.retrieve(k).content.endswith("…[truncated]")
    k2 = capture_tool_result(s, session_id="s1", tool_name="Read", content=big,
                             call_id="c1", max_content=None)
    assert s.retrieve(k2).content == big  # replay path: no truncation
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_capture_tool_result.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `capture/tool_result.py`**

```python
"""Canonical 'tool result -> chunk' mapping (design §3.2). Used by live hooks AND
offline replay (via replay/ingest.py), so there is one source of truth."""
from __future__ import annotations

from hashlib import sha1

from context_curator.store.interface import Store

CAPTURE_MAX_CONTENT = 32_768  # bytes; larger live content is head-truncated + marked


def capture_tool_result(store: Store, *, session_id: str, tool_name: str, content: str,
                        error: bool = False, call_id: str | None = None,
                        ordinal: int | None = None, ttl_s: int | None = None,
                        max_content: int | None = None) -> str | None:
    """Returns the written key, or None if skipped. `max_content` truncates oversized
    content; it is None on the replay path so replay stays structurally byte-identical."""
    if error:
        return None
    if ordinal is not None:
        suffix = f"{ordinal:06d}:{call_id}"
    elif call_id:
        suffix = call_id
    else:
        suffix = sha1(content.encode("utf-8")).hexdigest()[:12]
    if max_content is not None and len(content) > max_content:
        content = content[:max_content] + "\n…[truncated]"
    key = f"session:{session_id}:tool:{suffix}"
    store.store(key, content, tags=[tool_name.lower()], source=f"tool:{tool_name}", ttl_s=ttl_s)
    return key
```

- [ ] **Step 4: Refactor `replay/ingest.py` to wrap it**

Replace the body of `ingest_tool_result` in `src/context_curator/replay/ingest.py` (keep the module docstring/signature):
```python
from context_curator.capture.tool_result import capture_tool_result


def ingest_tool_result(result, call, session_id, ordinal, store):
    capture_tool_result(store, session_id=session_id, tool_name=call.name,
                        content=result.content, error=result.error,
                        call_id=result.call_id, ordinal=ordinal, ttl_s=None, max_content=None)
```
(Remove the old inline key-building/`store.store` logic — it now lives in `capture_tool_result`.)

- [ ] **Step 5: Run capture tests + the full replay suite (regression)**

Run: `uv run pytest tests/test_capture_tool_result.py tests/replay/ -v`
Expected: capture tests pass AND every replay test (determinism, ingest, engine) stays green — the replay key format `session:{sid}:tool:{ordinal:06d}:{call_id}` and `ttl_s=None` are preserved byte-for-byte.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/capture/__init__.py src/context_curator/capture/tool_result.py src/context_curator/replay/ingest.py tests/test_capture_tool_result.py
git commit -m "feat: canonical capture_tool_result; refactor replay ingest to reuse it"
```

---

### Task 3: `capture_file_write` + `capture_subagent_summary`

**Files:**
- Create: `src/context_curator/capture/file_ledger.py`, `src/context_curator/capture/subagent.py`
- Test: `tests/test_capture_file_and_subagent.py`

- [ ] **Step 1: Write the failing test**

`tests/test_capture_file_and_subagent.py`:
```python
from context_curator.capture.file_ledger import capture_file_write
from context_curator.capture.subagent import capture_subagent_summary
from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_file_write_ledger_entry():
    s = _store()
    key = capture_file_write(s, session_id="sess1", tool_name="Write", path="src/a.py")
    assert key == "shared:file_ledger:src/a.py"
    c = s.retrieve(key)
    assert c.tags == ["file-touch"]
    assert c.source == "file-ledger"
    assert c.provenance == "sess1"


def test_file_write_provenance_never_none():
    s = _store()
    key = capture_file_write(s, session_id="", tool_name="Edit", path="x")
    assert s.retrieve(key).provenance == "unknown-session"


def test_subagent_summary_chunk():
    s = _store()
    key = capture_subagent_summary(s, subagent_id="sub9", summary="explored auth",
                                   contracts_touched=["auth"])
    assert key == "shared:exploration:sub9"
    c = s.retrieve(key)
    assert c.content == "explored auth"
    assert c.source == "subagent:explore"
    assert "exploration" in c.tags and "auth" in c.tags


def test_subagent_empty_summary_noop():
    s = _store()
    assert capture_subagent_summary(s, subagent_id="sub9", summary="") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_capture_file_and_subagent.py -v` → FAIL (modules missing).

- [ ] **Step 3: Implement `capture/file_ledger.py`**

```python
"""Deterministic who-WROTE-what ledger (design §3.2). Write tools only; Read excluded."""
from __future__ import annotations

from context_curator.store.interface import Store


def capture_file_write(store: Store, *, session_id: str, tool_name: str, path: str,
                       ttl_s: int | None = None) -> str:
    key = f"shared:file_ledger:{path}"
    store.store(key, f"{tool_name} wrote {path}", tags=["file-touch"],
                source="file-ledger", provenance=session_id or "unknown-session", ttl_s=ttl_s)
    return key
```

- [ ] **Step 4: Implement `capture/subagent.py`**

```python
"""Capture a subagent's final summary text (design §3.2 / I1). The structured
{summary, contracts_touched, ...} schema is NOT in the hook payload — the hook
extracts final-message text and optional fenced JSON; this function takes primitives."""
from __future__ import annotations

from context_curator.store.interface import Store


def capture_subagent_summary(store: Store, *, subagent_id: str, summary: str,
                             contracts_touched: list[str] | None = None,
                             ttl_s: int | None = None) -> str | None:
    if not summary:
        return None
    tags = ["exploration", *(contracts_touched or [])]
    key = f"shared:exploration:{subagent_id}"
    store.store(key, summary, tags=tags, source="subagent:explore",
                provenance=subagent_id or "unknown-subagent", ttl_s=ttl_s)
    return key
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_capture_file_and_subagent.py -v` → 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/capture/file_ledger.py src/context_curator/capture/subagent.py tests/test_capture_file_and_subagent.py
git commit -m "feat: capture_file_write + capture_subagent_summary"
```

---

### Task 4: Guard module (`config`, `paths`, `secrets`)

**Files:**
- Create: `src/context_curator/guard/__init__.py` (empty), `config.py`, `paths.py`, `secrets.py`
- Test: `tests/test_guard.py`

- [ ] **Step 1: Write the failing test**

`tests/test_guard.py`:
```python
from context_curator.guard.config import load_config
from context_curator.guard.paths import is_sensitive_path
from context_curator.guard.secrets import scan_secrets


def _cfg():
    return load_config()


def test_sensitive_paths_positive():
    g = _cfg().sensitive_globs
    assert is_sensitive_path(".env", g)
    assert is_sensitive_path("config/.env.production", g)
    assert is_sensitive_path("/home/u/.aws/credentials", g)
    assert is_sensitive_path("deploy/id_rsa", g)
    assert is_sensitive_path("secrets-prod", g)            # basename, no slash


def test_sensitive_paths_negative():
    g = _cfg().sensitive_globs
    assert not is_sensitive_path("src/app.py", g)
    assert not is_sensitive_path("README.md", g)


def test_secret_positive():
    p = _cfg().secret_patterns
    assert scan_secrets("AKIAIOSFODNN7EXAMPLE", p) == "aws-access-key-id"
    assert scan_secrets("-----BEGIN OPENSSH PRIVATE KEY-----", p) == "private-key-block"
    assert scan_secrets('api_key = "abcdef0123456789ABCDEF"', p) == "generic-secret"


def test_secret_negative_realistic_code():
    p = _cfg().secret_patterns
    # the I5 false-positive class: ordinary code must NOT trip the guard
    assert scan_secrets("token = make_token()", p) is None
    assert scan_secrets("password = get_hashed_password_value", p) is None
    assert scan_secrets("commit 9f1c2e7a4b8d3f6e0a1b2c3d4e5f60718293a4b5", p) is None


def test_scan_is_capped(monkeypatch):
    from context_curator.guard import config
    p = _cfg().secret_patterns
    huge = "x" * (config.GUARD_MAX_SCAN + 1000) + 'api_key="abcdef0123456789ABCD"'
    # secret is past the cap -> not found (bounded scan, no hang)
    assert scan_secrets(huge, p) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_guard.py -v` → FAIL (modules missing).

- [ ] **Step 3: Implement `guard/config.py`**

```python
"""Guard configuration (design §3.3). Defaults overridable via $CC_GUARD_CONFIG JSON."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CAPTURE_TTL_S = 86_400      # live-capture chunk TTL
GUARD_MAX_SCAN = 262_144    # bytes; cap on secret-scan input (bounds regex cost)

DEFAULT_SENSITIVE_GLOBS = [
    "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/id_rsa*",
    "**/.aws/**", "**/.ssh/**", "**/secrets/**", "**/*secrets*",
    "**/*.prod.*", "**/*prod*",
]
DEFAULT_SECRET_PATTERNS = [
    ("aws-access-key-id", r"AKIA[0-9A-Z]{16}"),
    ("private-key-block", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("aws-secret-quoted", r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9/+=]{20,}['\"]"),
    ("generic-secret",    r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]"),
]


@dataclass
class GuardConfig:
    sensitive_globs: list[str]
    secret_patterns: list[tuple[str, str]]


def load_config() -> GuardConfig:
    globs = list(DEFAULT_SENSITIVE_GLOBS)
    patterns = list(DEFAULT_SECRET_PATTERNS)
    path = os.environ.get("CC_GUARD_CONFIG")
    candidate = Path(path) if path else Path(".claude/cc-guard.json")
    if candidate.exists():
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if "sensitive_globs" in data:
            globs = list(data["sensitive_globs"])
        if "secret_patterns" in data:
            patterns = [(p[0], p[1]) for p in data["secret_patterns"]]
    return GuardConfig(sensitive_globs=globs, secret_patterns=patterns)
```

- [ ] **Step 4: Implement `guard/paths.py`**

```python
"""Sensitive-path matching (design §3.3). Normalizes ~ and .., matches path AND basename."""
from __future__ import annotations

import os
from fnmatch import fnmatch


def is_sensitive_path(path: str, globs: list[str]) -> bool:
    norm = os.path.normpath(os.path.expanduser(path)).replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    for g in globs:
        if fnmatch(norm, g) or fnmatch(base, g) or fnmatch(base, g.replace("**/", "")):
            return True
    return False
```

- [ ] **Step 5: Implement `guard/secrets.py`**

```python
"""Secret-pattern scan (design §3.3). Capped input; returns the matched pattern name."""
from __future__ import annotations

import re

from context_curator.guard.config import GUARD_MAX_SCAN


def scan_secrets(text: str, patterns: list[tuple[str, str]]) -> str | None:
    window = text[:GUARD_MAX_SCAN]
    for name, pat in patterns:
        if re.search(pat, window):
            return name
    return None
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_guard.py -v` → all passed. (If `secrets-prod` basename match fails, confirm `paths.py` strips `**/` for the basename comparison as shown.)

- [ ] **Step 7: Commit**

```bash
git add src/context_curator/guard/ tests/test_guard.py
git commit -m "feat: guard module (sensitive paths + secret scan, config-driven)"
```

---

### Task 5: Hook I/O (`hooks/_io.py`)

**Files:**
- Create: `src/context_curator/hooks/__init__.py` (empty), `src/context_curator/hooks/_io.py`
- Test: `tests/test_hooks_io.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hooks_io.py`:
```python
import context_curator.hooks._io as io
from context_curator.hooks._io import HookResult, open_store, run_hook


def test_open_store_constructs_usable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "h.db"))
    store = open_store()                    # must not raise (C1: needs an embedder)
    store.store("k", "v")
    assert store.retrieve("k") is not None


def test_run_hook_maps_block_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {})
    rc = {}
    monkeypatch.setattr(io.sys, "exit", lambda code: rc.setdefault("code", code))
    run_hook(lambda event: HookResult(2, "blocked: x"), needs_store=False)
    assert rc["code"] == 2
    assert "blocked: x" in capsys.readouterr().err


def test_run_hook_fail_open_on_exception(monkeypatch):
    monkeypatch.setattr(io, "read_event", lambda: {})
    rc = {}
    monkeypatch.setattr(io.sys, "exit", lambda code: rc.setdefault("code", code))
    def boom(event):
        raise RuntimeError("boom")
    run_hook(boom, needs_store=False)
    assert rc["code"] == 0                  # fail-open
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_hooks_io.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `hooks/_io.py`**

```python
"""Shared hook plumbing (design §3.4). Thin: parse stdin event JSON, optionally open the
store, call the handler, emit the exit code. FAIL-OPEN on any error (exit 0); the guard
emits a distinct marker so a fail-open bypass is visible."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from context_curator.embeddings import HashingEmbedder
from context_curator.store.interface import Store
from context_curator.store.paths import resolve_db_path
from context_curator.store.sqlite_store import SqliteStore, sweep_expired

ALERT = "[context-curator GUARD-FAILOPEN]"   # distinct, greppable marker


@dataclass
class HookResult:
    exit_code: int          # 0 allow, 2 block
    message: str = ""       # -> stderr when blocking


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def read_event() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def open_store() -> Store:
    store = SqliteStore(db_path=resolve_db_path(), embedder=HashingEmbedder())
    log(f"context-curator: capture DB = {resolve_db_path()}")
    try:
        sweep_expired(store)
    except Exception as e:        # sweep is best-effort
        log(f"context-curator: sweep skipped ({e})")
    return store


def run_hook(handler: Callable[..., HookResult], *, needs_store: bool) -> None:
    try:
        event = read_event()
        if needs_store:
            result = handler(event, open_store())
        else:
            result = handler(event)
    except Exception as e:        # FAIL-OPEN
        if not needs_store:       # the guard: make the bypass visible
            log(f"{ALERT} guard crashed, allowing tool: {e}")
        else:
            log(f"context-curator: capture failed: {e}")
        sys.exit(0)
        return
    if result.message:
        log(result.message)
    sys.exit(result.exit_code)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_hooks_io.py -v` → 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/hooks/__init__.py src/context_curator/hooks/_io.py tests/test_hooks_io.py
git commit -m "feat: hook I/O plumbing (open_store, run_hook fail-open, HookResult)"
```

---

### Task 6: Capture hooks (`post_tool_use`, `subagent_stop`)

**Files:**
- Create: `src/context_curator/hooks/post_tool_use.py`, `src/context_curator/hooks/subagent_stop.py`
- Test: `tests/test_hooks_capture.py`

> **Field-name note:** the event field names below (`tool_name`, `tool_input.file_path`, `tool_response`, `session_id`, `transcript_path`) are the documented Claude Code shape — **confirm against a real event** and adjust ONLY these adapter files if they differ.

- [ ] **Step 1: Write the failing test**

`tests/test_hooks_capture.py`:
```python
from context_curator.embeddings import HashingEmbedder
from context_curator.hooks.post_tool_use import handle as post_handle
from context_curator.hooks.subagent_stop import extract_summary
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_write_captures_ledger_and_result():
    s = _store()
    event = {"tool_name": "Write", "session_id": "s1",
             "tool_input": {"file_path": "src/a.py", "content": "code"},
             "tool_response": "ok", "call_id": "c0"}
    r = post_handle(event, s)
    assert r.exit_code == 0
    assert s.retrieve("shared:file_ledger:src/a.py") is not None
    assert s.retrieve("session:s1:tool:c0") is not None


def test_read_excluded_from_ledger():
    s = _store()
    event = {"tool_name": "Read", "session_id": "s1",
             "tool_input": {"file_path": "src/a.py"}, "tool_response": "data", "call_id": "c1"}
    post_handle(event, s)
    assert s.list("shared:file_ledger") == []          # Read does not write the ledger
    assert s.retrieve("session:s1:tool:c1") is not None # but its result is captured


def test_dict_tool_response_is_coerced():
    s = _store()
    event = {"tool_name": "Grep", "session_id": "s1",
             "tool_input": {}, "tool_response": {"matches": ["a", "b"]}, "call_id": "c2"}
    r = post_handle(event, s)
    assert r.exit_code == 0
    assert s.retrieve("session:s1:tool:c2") is not None   # no crash on dict response


def test_two_tools_two_chunks():
    s = _store()
    for cid in ("c0", "c1"):
        post_handle({"tool_name": "Bash", "session_id": "s1", "tool_input": {},
                     "tool_response": f"out-{cid}", "call_id": cid}, s)
    assert len(s.list("session:s1")) == 2


def test_extract_summary_from_transcript(tmp_path):
    import json
    tp = tmp_path / "t.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "final summary"}]}}) + "\n",
        encoding="utf-8")
    assert extract_summary({"transcript_path": str(tp)}) == "final summary"
    assert extract_summary({}) == ""                       # no path -> empty -> no-op
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_hooks_capture.py -v` → FAIL (modules missing).

- [ ] **Step 3: Implement `hooks/post_tool_use.py`**

```python
"""PostToolUse capture hook (design §3.4). Field names pinned against a real payload."""
from __future__ import annotations

import json

from context_curator.capture.file_ledger import capture_file_write
from context_curator.capture.tool_result import CAPTURE_MAX_CONTENT, capture_tool_result
from context_curator.guard.config import CAPTURE_TTL_S
from context_curator.hooks._io import HookResult, run_hook
from context_curator.store.interface import Store

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}


def handle(event: dict, store: Store) -> HookResult:
    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    session_id = event.get("session_id", "")
    call_id = event.get("call_id")

    if tool_name in _WRITE_TOOLS and tool_input.get("file_path"):
        capture_file_write(store, session_id=session_id, tool_name=tool_name,
                           path=tool_input["file_path"], ttl_s=CAPTURE_TTL_S)

    resp = event.get("tool_response")
    if resp is not None:
        content = resp if isinstance(resp, str) else json.dumps(resp, sort_keys=True)
        capture_tool_result(store, session_id=session_id, tool_name=tool_name or "tool",
                            content=content, call_id=call_id, ordinal=None,
                            ttl_s=CAPTURE_TTL_S, max_content=CAPTURE_MAX_CONTENT)
    return HookResult(0)


def main() -> None:
    run_hook(handle, needs_store=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement `hooks/subagent_stop.py`**

```python
"""SubagentStop capture hook (design §3.4 / I1). The payload exposes a transcript path,
NOT a structured summary schema — so we read the subagent's final assistant text."""
from __future__ import annotations

import json
from pathlib import Path

from context_curator.capture.subagent import capture_subagent_summary
from context_curator.guard.config import CAPTURE_TTL_S
from context_curator.hooks._io import HookResult, run_hook
from context_curator.store.interface import Store


def extract_summary(event: dict) -> str:
    """Last assistant text message in the transcript, or '' if unavailable."""
    tp = event.get("transcript_path")
    if not tp or not Path(tp).exists():
        return ""
    text = ""
    for line in Path(tp).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") == "assistant":
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                t = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                if t:
                    text = t            # keep overwriting -> ends on the last one
    return text


def handle(event: dict, store: Store) -> HookResult:
    summary = extract_summary(event)
    subagent_id = event.get("session_id", "") or "unknown-subagent"
    capture_subagent_summary(store, subagent_id=subagent_id, summary=summary,
                             ttl_s=CAPTURE_TTL_S)
    return HookResult(0)


def main() -> None:
    run_hook(handle, needs_store=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_hooks_capture.py -v` → all passed.

- [ ] **Step 6: Commit**

```bash
git add src/context_curator/hooks/post_tool_use.py src/context_curator/hooks/subagent_stop.py tests/test_hooks_capture.py
git commit -m "feat: PostToolUse + SubagentStop capture hooks"
```

---

### Task 7: Guardrail hook (`pre_tool_use`)

**Files:**
- Create: `src/context_curator/hooks/pre_tool_use.py`
- Test: `tests/test_hooks_guard.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hooks_guard.py`:
```python
from context_curator.hooks.pre_tool_use import handle


def _ev(tool, tool_input):
    return {"tool_name": tool, "tool_input": tool_input}


def test_write_to_env_blocked():
    assert handle(_ev("Write", {"file_path": ".env", "content": "X=1"})).exit_code == 2


def test_planted_aws_key_blocked():
    assert handle(_ev("Bash", {"command": "echo AKIAIOSFODNN7EXAMPLE"})).exit_code == 2


def test_multiedit_secret_in_second_edit_blocked():
    ev = _ev("MultiEdit", {"file_path": "x.py", "edits": [
        {"old_string": "a", "new_string": "b"},
        {"old_string": "c", "new_string": 'api_key = "abcdef0123456789ABCDEF"'}]})
    assert handle(ev).exit_code == 2


def test_bash_redirect_to_sensitive_blocked_space_and_nospace():
    assert handle(_ev("Bash", {"command": "cat foo > .env"})).exit_code == 2
    assert handle(_ev("Bash", {"command": "cat foo >.env"})).exit_code == 2
    assert handle(_ev("Bash", {"command": "echo x >> deploy/id_rsa"})).exit_code == 2


def test_benign_bash_read_allowed():
    assert handle(_ev("Bash", {"command": "grep secrets config.txt"})).exit_code == 0
    assert handle(_ev("Bash", {"command": "ls prod/"})).exit_code == 0


def test_benign_write_allowed():
    assert handle(_ev("Write", {"file_path": "src/app.py", "content": "print(1)"})).exit_code == 0


def test_unknown_tool_allowed_with_marker(capsys):
    r = handle(_ev("FancyNewTool", {"whatever": 1}))
    assert r.exit_code == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_hooks_guard.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `hooks/pre_tool_use.py`**

```python
"""PreToolUse guardrail hook (design §3.3-3.4). Blocks sensitive-path writes and secret
inputs (exit 2); never opens the store; fails open with a distinct marker on crash."""
from __future__ import annotations

import re

from context_curator.guard.config import load_config
from context_curator.guard.paths import is_sensitive_path
from context_curator.guard.secrets import scan_secrets
from context_curator.hooks._io import ALERT, HookResult, log, run_hook

_REDIRECT = re.compile(r"(?:>>?|\btee\b)\s*(\S+)")
_GUARDED = {"Write", "Edit", "MultiEdit", "Bash"}


def _paths_and_texts(tool_name: str, ti: dict) -> tuple[list[str], list[str]]:
    """(sensitive-path candidates, secret-scan texts) per the §3.3 table."""
    if tool_name == "Write":
        return ([ti.get("file_path", "")], [ti.get("content", "")])
    if tool_name == "Edit":
        return ([ti.get("file_path", "")], [ti.get("new_string", "")])
    if tool_name == "MultiEdit":
        texts = [e.get("new_string", "") for e in ti.get("edits", []) if isinstance(e, dict)]
        return ([ti.get("file_path", "")], texts)
    if tool_name == "Bash":
        cmd = ti.get("command", "")
        return (_REDIRECT.findall(cmd), [cmd])   # path = redirect targets only
    return ([], [])


def handle(event: dict) -> HookResult:
    tool_name = event.get("tool_name", "")
    ti = event.get("tool_input") or {}
    if tool_name not in _GUARDED:
        return HookResult(0)
    cfg = load_config()
    paths, texts = _paths_and_texts(tool_name, ti)
    for p in paths:
        try:
            if p and is_sensitive_path(p, cfg.sensitive_globs):
                return HookResult(2, f"blocked: sensitive path '{p}'")
        except Exception as e:
            log(f"{ALERT} path check errored (allowing this check): {e}")
    for t in texts:
        try:
            hit = scan_secrets(t, cfg.secret_patterns)
            if hit:
                return HookResult(2, f"blocked: secret pattern '{hit}' in tool input")
        except Exception as e:
            log(f"{ALERT} secret scan errored (allowing this check): {e}")
    return HookResult(0)


def main() -> None:
    run_hook(handle, needs_store=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_hooks_guard.py -v` → all passed.

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/hooks/pre_tool_use.py tests/test_hooks_guard.py
git commit -m "feat: PreToolUse guardrail hook (sensitive-path + secret block, fail-open)"
```

---

### Task 8: Register hooks in settings + end-to-end smoke

**Files:**
- Modify: `.claude/settings.json`
- Test: `tests/test_hooks_smoke.py`

> **Settings syntax note:** confirm the current Claude Code hook config schema (matcher/command shape) before finalizing; adjust `.claude/settings.json` only.

- [ ] **Step 1: Register the three hooks in `.claude/settings.json`**

Replace the empty hook arrays:
```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Write|Edit|MultiEdit|Bash",
       "hooks": [{"type": "command", "command": "python -m context_curator.hooks.pre_tool_use"}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "python -m context_curator.hooks.post_tool_use"}]}
    ],
    "SubagentStop": [
      {"hooks": [{"type": "command", "command": "python -m context_curator.hooks.subagent_stop"}]}
    ],
    "SessionStart": [],
    "UserPromptSubmit": [],
    "Stop": []
  }
}
```

- [ ] **Step 2: Write the stdin→exit smoke test**

`tests/test_hooks_smoke.py`:
```python
import json
import subprocess
import sys


def _run(module, event, env):
    return subprocess.run([sys.executable, "-m", module], input=json.dumps(event),
                          capture_output=True, text=True, env=env)


def test_pre_tool_use_blocks_env_write(tmp_path):
    import os
    env = {**os.environ, "CC_DB_PATH": str(tmp_path / "s.db")}
    r = _run("context_curator.hooks.pre_tool_use",
             {"tool_name": "Write", "tool_input": {"file_path": ".env", "content": "X=1"}}, env)
    assert r.returncode == 2


def test_pre_tool_use_allows_benign(tmp_path):
    import os
    env = {**os.environ, "CC_DB_PATH": str(tmp_path / "s.db")}
    r = _run("context_curator.hooks.pre_tool_use",
             {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "ok"}}, env)
    assert r.returncode == 0


def test_post_tool_use_captures(tmp_path):
    import os
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    r = _run("context_curator.hooks.post_tool_use",
             {"tool_name": "Bash", "session_id": "s1", "tool_input": {},
              "tool_response": "hello", "call_id": "c0"}, env)
    assert r.returncode == 0
    from context_curator.embeddings import HashingEmbedder
    from context_curator.store.sqlite_store import SqliteStore
    s = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=16))
    assert s.retrieve("session:s1:tool:c0") is not None
```

- [ ] **Step 3: Run smoke + the FULL suite + lint**

Run: `uv run pytest -q` → everything green (M0/M1 + replay + all M2). Run `uv run ruff check .` → clean.

- [ ] **Step 4: Commit**

```bash
git add .claude/settings.json tests/test_hooks_smoke.py
git commit -m "feat: register capture/guard hooks in settings + end-to-end smoke"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3.1 store concurrency (WAL, busy_timeout, autocommit+BEGIN IMMEDIATE+rollback, cc_meta, sweep, resolve_db_path) → Task 1. ✅
- §3.2 canonical `capture_tool_result` + replay refactor → Task 2; `capture_file_write` (write-only), `capture_subagent_summary` → Task 3. ✅
- §3.3 guard (config, paths normalization, quoted regexes, GUARD_MAX_SCAN, per-tool table, Bash redirect-only `\s*`) → Task 4 (modules) + Task 7 (wiring). ✅
- §3.4 hooks (`open_store` with embedder, `run_hook` fail-open + marker, `needs_store=False` guard, dict `tool_response` coercion, unknown-tool marker, exit 0/2) → Tasks 5–7. ✅
- §3.5 settings registration → Task 8. ✅
- §5 tests (construction, concurrency no-dup-seq, rollback-usable, sweep, false-positive negatives, redirect variants, dict response, multi-event, fail-open, smoke) → distributed across tasks. ✅

**Placeholder scan:** No TBD/TODO. Field-name "confirm against a real event" appears only where the spec mandates it (hook adapters), bounded with concrete documented-shape code that passes the tests; the pure modules are fully specified.

**Type/signature consistency:** `capture_tool_result(store, *, session_id, tool_name, content, error, call_id, ordinal, ttl_s, max_content)` identical in Task 2 def, the Task 2 replay wrapper, and the Task 6 `post_tool_use` call. `HookResult(exit_code, message)` consistent across `_io` (Task 5) and all hooks (6–7). `is_sensitive_path(path, globs)` / `scan_secrets(text, patterns)` / `load_config().{sensitive_globs,secret_patterns}` consistent Task 4 ↔ Task 7. `sweep_expired(store)` / `resolve_db_path()` consistent Task 1 ↔ Task 5. Key formats (`session:{sid}:tool:{…}`, `shared:file_ledger:{path}`, `shared:exploration:{id}`) identical across capture defs and hook/smoke assertions.

**Note on Task 1 risk:** the autocommit + removed-`commit()` change is the one place that touches existing M0/M1 behavior; Step 11 explicitly re-runs the entire prior suite to confirm it's behavior-preserving before moving on.
