# M6 — Decision Log + Statusline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live curator observable — a durable, fail-open per-session decision log of what the onload hook injects each turn, plus a `cc-statusline` Claude Code statusLine indicator (`CC ws:8 +2/-1 [recency]`).

**Architecture:** A new decoupled `observe/` package: `decision_log.py` (a `DecisionRecord` dataclass + append-and-close JSONL writer with a torn-line-tolerant bounded tail-read + an inspect CLI) and `statusline.py` (stdin `session_id` → rendered line, never raises). A best-effort, fail-open `record_decision(...)` call is wired into the onload hook. The log is a sidecar under `.context-curator/decisions/` — it never touches the chunk store (preserving the reconcile single-writer invariant).

**Tech Stack:** Python + UV; pytest; ruff (`E,F,I,UP,B`, ≤100); stdlib only (json, dataclasses, datetime, pathlib). hatchling build backend (`[project.scripts]` entry point).

**Spec:** `docs/superpowers/specs/2026-06-04-m6-decision-log-design.md` (hardened through 3 critique rounds).

**Branch:** `feat/m6-decision-log` (already checked out, off `main`).

---

## Conventions
- Run everything via `uv run`; ignore the `VIRTUAL_ENV` mismatch warning (it goes to stderr). Lint: `uv run ruff check <files>`. TDD; commit per task.
- **Verified facts (spec §3–§9):** `store.paths.resolve_db_path() -> str` (default `<project>/.context-curator/store.db`, honors `$CC_DB_PATH`). `.context-curator/` is already gitignored. Both the `UserPromptSubmit` hook stdin and the statusLine stdin carry `session_id` (snake_case, verified). The hook `handle(event, store)` lives in `hooks/user_prompt_submit.py`; at the insertion point `keys`, `chunks`, `prompt`, `event` are all in scope.
- **Tests** monkeypatch `decision_log.decisions_dir` to a `tmp_path` (the clean seam — every path goes through it).

---

## Task 1: `observe/decision_log.py` — record, paths, tail-read, writer/reader

**Files:** Create `src/context_curator/observe/__init__.py` (empty), `src/context_curator/observe/decision_log.py`. Test: `tests/observe/__init__.py` (empty), `tests/observe/test_decision_log.py`.

- [ ] **Step 1: failing test** — `tests/observe/test_decision_log.py`:
```python
import pytest

from context_curator.observe import decision_log as dl


@pytest.fixture
def decisions(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    monkeypatch.setattr(dl, "decisions_dir", lambda: d)
    return d


def test_record_then_read_roundtrip(decisions):
    dl.record_decision("s1", "do the thing", "recency", ["a", "b"])
    recs = dl.read_recent("s1", 5)
    assert len(recs) == 1
    r = recs[0]
    assert r.session_id == "s1" and r.source == "recency"
    assert r.injected_keys == ["a", "b"] and r.working_set_size == 2
    assert r.paged_in == ["a", "b"] and r.paged_out == []      # first turn: all in, none out
    assert r.prompt_preview == "do the thing" and r.ts.endswith("Z")


def test_page_in_out_delta_vs_previous(decisions):
    dl.record_decision("s1", "p1", "recency", ["a", "b", "c"])
    dl.record_decision("s1", "p2", "recency", ["b", "c", "d"])   # a leaves, d enters
    r = dl.read_recent("s1", 1)[0]
    assert r.paged_in == ["d"] and r.paged_out == ["a"]


def test_per_session_isolation(decisions):
    dl.record_decision("s1", "p", "recency", ["a"])
    dl.record_decision("s2", "p", "recency", ["z"])
    assert dl.read_recent("s1", 5)[0].injected_keys == ["a"]
    assert dl.read_recent("s2", 5)[0].injected_keys == ["z"]


def test_tail_read_discards_torn_final_line(decisions):
    decisions.mkdir(parents=True)
    p = dl.decision_log_path("s1")
    # a complete record + a torn (no trailing newline) partial write
    good = '{"ts":"2026-06-04T00:00:00Z","session_id":"s1","prompt_preview":"p","source":"recency","injected_keys":["a"],"working_set_size":1,"paged_in":["a"],"paged_out":[]}'
    p.write_bytes((good + "\n" + '{"ts":"torn').encode("utf-8"))
    recs = dl.read_recent("s1", 5)
    assert len(recs) == 1 and recs[0].injected_keys == ["a"]    # torn fragment dropped
    # and the writer's prev-read survives the torn tail (uses the last COMPLETE record)
    dl.record_decision("s1", "p2", "recency", ["a", "b"])
    assert dl.read_recent("s1", 1)[0].paged_in == ["b"]


def test_record_decision_is_fail_open(decisions, monkeypatch):
    # point decisions_dir UNDER a regular file so mkdir/open fails -> must be swallowed
    bad_parent = decisions.parent / "not_a_dir"
    bad_parent.write_text("x")                                  # a file, not a dir
    monkeypatch.setattr(dl, "decisions_dir", lambda: bad_parent / "sub")
    dl.record_decision("s1", "p", "recency", ["a"])             # must NOT raise


def test_newest_session_file(decisions):
    import os
    dl.record_decision("old", "p", "recency", ["a"])
    dl.record_decision("new", "p", "recency", ["b"])
    # pin mtimes deterministically (sequential writes can tie at coarse fs resolution)
    os.utime(dl.decision_log_path("old"), (1000, 1000))
    os.utime(dl.decision_log_path("new"), (2000, 2000))
    newest = dl._newest_session_file()
    assert newest is not None and "new" in newest.name
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/observe/test_decision_log.py -v` → ImportError.

- [ ] **Step 3: implement** — `src/context_curator/observe/__init__.py` (empty file), then `src/context_curator/observe/decision_log.py`:
```python
"""Per-session decision log (design §3): a durable, fail-open JSONL record of what the onload hook
injects each turn. Sidecar to the SQLite db — it never touches the chunk store, preserving the
reconcile single-writer invariant. Injection-window semantics: page-in/out = turn-over-turn delta of
the injected key set. The writer ALWAYS appends a trailing newline, so the reader may treat any
unterminated final fragment as torn and drop it."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from context_curator.store.paths import resolve_db_path

_WINDOW_BYTES = 64 * 1024            # bounded tail-read (~300 lines); statusline cost O(window)
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class DecisionRecord:
    ts: str
    session_id: str
    prompt_preview: str
    source: str                     # "curator" | "recency" | "none"
    injected_keys: list[str]
    working_set_size: int
    paged_in: list[str]
    paged_out: list[str]


def decisions_dir() -> Path:
    return Path(resolve_db_path()).parent / "decisions"


def decision_log_path(session_id: str) -> Path:
    safe = _SAFE.sub("_", session_id) or "unknown"
    return decisions_dir() / f"decisions-{safe}.jsonl"


def _tail_lines(path: Path, n: int) -> list[str]:
    """Last `n` COMPLETE (newline-terminated) lines, reading only the trailing ~64 KB."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - _WINDOW_BYTES)
            f.seek(start)
            blob = f.read()
    except OSError:
        return []
    text = blob.decode("utf-8", errors="ignore")
    if start > 0:                               # sliced mid-line -> drop the partial first line
        nl = text.find("\n")
        text = text[nl + 1:] if nl >= 0 else ""
    parts = text.split("\n")
    complete = parts[:-1]                        # drop trailing element (clean "" or torn partial)
    return complete[-n:]


def _records_from(path: Path, n: int) -> list[DecisionRecord]:
    out: list[DecisionRecord] = []
    for line in _tail_lines(path, n):
        try:
            out.append(DecisionRecord(**json.loads(line)))
        except Exception:
            continue                             # skip malformed/partial
    return out


def read_recent(session_id: str, n: int) -> list[DecisionRecord]:
    return _records_from(decision_log_path(session_id), n)


def _newest_session_file() -> Path | None:
    try:
        files = list(decisions_dir().glob("decisions-*.jsonl"))
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def record_decision(session_id: str, prompt_preview: str, source: str,
                    injected_keys: list[str]) -> None:
    """Append one DecisionRecord line for this turn. Fail-open: never raises into the hook."""
    try:
        prev = read_recent(session_id, 1)
        prev_keys = prev[0].injected_keys if prev else []
        prev_set, cur_set = set(prev_keys), set(injected_keys)
        rec = DecisionRecord(
            ts=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            session_id=session_id,
            prompt_preview=" ".join(prompt_preview.split()),
            source=source,
            injected_keys=list(injected_keys),
            working_set_size=len(injected_keys),
            paged_in=[k for k in injected_keys if k not in prev_set],
            paged_out=[k for k in prev_keys if k not in cur_set],
        )
        path = decision_log_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(asdict(rec)) + "\n")
    except Exception:
        pass
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/observe/test_decision_log.py -v`; `uv run ruff check src/context_curator/observe/ tests/observe/`.

- [ ] **Step 5: commit** — `git add src/context_curator/observe/ tests/observe/ && git commit -m "feat(m6): per-session decision-log writer/reader (tail-read, torn-tolerant, fail-open)"`

---

## Task 2: `observe/statusline.py` + the `cc-statusline` entry point

**Files:** Create `src/context_curator/observe/statusline.py`. Modify `pyproject.toml`. Test: `tests/observe/test_statusline.py`.

- [ ] **Step 1: failing test** — `tests/observe/test_statusline.py`:
```python
import json

from context_curator.observe import decision_log as dl
from context_curator.observe import statusline as sl


def _patch(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    monkeypatch.setattr(dl, "decisions_dir", lambda: d)
    return d


def test_render_exact_line(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    dl.record_decision("s1", "p1", "recency", ["a", "b", "c"])
    dl.record_decision("s1", "p2", "recency", ["b", "c", "d"])   # +d / -a -> +1/-1, ws 3
    out = sl._line_for(json.dumps({"session_id": "s1"}))
    assert out == "CC ws:3 +1/-1 [recency]"


def test_session_present_but_no_file_is_idle_not_newest(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    dl.record_decision("other", "p", "recency", ["z"])           # a different session's file exists
    out = sl._line_for(json.dumps({"session_id": "s1"}))         # s1 has no file yet
    assert out == "CC ·"                                         # NOT other's data


def test_blank_session_falls_back_to_newest(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    dl.record_decision("only", "p", "curator", ["a"])
    out = sl._line_for(json.dumps({"session_id": ""}))
    assert out == "CC ws:1 +1/-0 [curator]"


def test_malformed_stdin_is_idle(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    assert sl._line_for("not json at all") == "CC ·"
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/observe/test_statusline.py -v` → ImportError.

- [ ] **Step 3: implement** — `src/context_curator/observe/statusline.py`:
```python
"""Claude Code statusLine command (design §5): read session_id from stdin JSON, print the curator's
last-decision indicator (`CC ws:N +i/-o [src]`). Never raises; always exits 0; writes with
sys.stdout.write (no print -> no Windows \\r\\n)."""
from __future__ import annotations

import json
import sys

from context_curator.observe.decision_log import (
    DecisionRecord,
    _newest_session_file,
    _records_from,
    read_recent,
)

_IDLE = "CC ·"


def _render(r: DecisionRecord) -> str:
    return f"CC ws:{r.working_set_size} +{len(r.paged_in)}/-{len(r.paged_out)} [{r.source}]"


def _line_for(stdin_text: str) -> str:
    try:
        payload = json.loads(stdin_text)
    except Exception:
        return _IDLE
    sid = (payload.get("session_id") or "").strip() if isinstance(payload, dict) else ""
    if sid:                                          # present -> its file or idle (NOT newest)
        recent = read_recent(sid, 1)
        return _render(recent[0]) if recent else _IDLE
    newest = _newest_session_file()                  # blank session -> best-effort newest file
    if newest is not None:
        recent = _records_from(newest, 1)
        if recent:
            return _render(recent[0])
    return _IDLE


def main() -> None:
    try:
        line = _line_for(sys.stdin.read())
    except Exception:
        line = _IDLE
    sys.stdout.write(line)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/observe/test_statusline.py -v`; ruff.

- [ ] **Step 5: add the entry point** — in `pyproject.toml`, add a `[project.scripts]` table (if none exists) right after the `[project]` table's end:
```toml
[project.scripts]
cc-statusline = "context_curator.observe.statusline:main"
```
Then materialize + smoke it:
```bash
uv sync
echo '{"session_id":"x"}' | uv run cc-statusline    # prints "CC ·" (no x file) and exits 0
```
Expected: `CC ·`, exit 0.

- [ ] **Step 6: commit** — `git add src/context_curator/observe/statusline.py tests/observe/test_statusline.py pyproject.toml uv.lock && git commit -m "feat(m6): cc-statusline statusLine command (window+churn indicator, never-raise)"`

---

## Task 3: inspect CLI (`python -m context_curator.observe.decision_log`)

**Files:** Modify `src/context_curator/observe/decision_log.py` (add `inspect_lines` + `main`). Test: `tests/observe/test_decision_log.py` (append).

- [ ] **Step 1: failing test** — append:
```python
def test_inspect_lines_formats_recent(decisions):
    dl.record_decision("s1", "first task", "recency", ["a"])
    dl.record_decision("s1", "second task", "curator", ["a", "b"])
    lines = dl.inspect_lines("s1", 10)
    assert len(lines) == 2
    assert "[curator] ws:2 +1/-0" in lines[1] and "second task" in lines[1]


def test_inspect_lines_empty_dir_message(decisions):
    assert dl.inspect_lines(None, 10) == ["no decisions recorded yet"]


def test_inspect_lines_default_uses_newest(decisions):
    import os
    dl.record_decision("a", "pa", "recency", ["x"])
    dl.record_decision("b", "pb", "recency", ["y"])     # newest
    os.utime(dl.decision_log_path("a"), (1000, 1000))   # deterministic mtimes (avoid tie-flake)
    os.utime(dl.decision_log_path("b"), (2000, 2000))
    lines = dl.inspect_lines(None, 10)
    assert any("pb" in ln for ln in lines)
```

- [ ] **Step 2: run, expect fail** — `uv run pytest tests/observe/test_decision_log.py -k inspect -v` → fail.

- [ ] **Step 3: implement** — append to `decision_log.py`:
```python
def inspect_lines(session_id: str | None, tail: int) -> list[str]:
    """Human-readable recent decisions. Default (session_id None) -> the newest session file."""
    path = decision_log_path(session_id) if session_id else _newest_session_file()
    if path is None or not path.exists():
        return ["no decisions recorded yet"]
    return [
        f'{r.ts} [{r.source}] ws:{r.working_set_size} '
        f'+{len(r.paged_in)}/-{len(r.paged_out)}  "{r.prompt_preview}"'
        for r in _records_from(path, tail)
    ]


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m context_curator.observe.decision_log")
    ap.add_argument("--session", default=None, help="session id (default: newest log)")
    ap.add_argument("--tail", type=int, default=10, help="how many recent decisions (max ~300)")
    args = ap.parse_args()
    for line in inspect_lines(args.session, args.tail):
        print(line)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: run, expect pass** — `uv run pytest tests/observe/test_decision_log.py -v`; ruff. Smoke: `uv run python -m context_curator.observe.decision_log --tail 3` (prints recent or "no decisions recorded yet").

- [ ] **Step 5: commit** — `git add src/context_curator/observe/decision_log.py tests/observe/test_decision_log.py && git commit -m "feat(m6): decision-log inspect CLI (--session/--tail)"`

---

## Task 4: wire `record_decision` into the onload hook

**Files:** Modify `src/context_curator/hooks/user_prompt_submit.py`. Test: `tests/test_hooks_onload.py` (append — mirror its existing store-seeding + `client.request_onload` monkeypatch pattern; READ that file first).

- [ ] **Step 1: read the existing hook test** — open `tests/test_hooks_onload.py` and note how it (a) seeds an `InMemoryStore` with chunks, (b) monkeypatches `client.request_onload`, and (c) calls `user_prompt_submit.handle(event, store)`. Reuse that exact setup.

- [ ] **Step 2: failing test** — append to `tests/test_hooks_onload.py` (adapt the seeding to match the file's existing helper; the assertions below are the contract):
```python
def test_hook_records_decision(tmp_path, monkeypatch):
    from context_curator.hooks import user_prompt_submit as ups
    from context_curator.observe import decision_log as dl

    monkeypatch.setattr(dl, "decisions_dir", lambda: tmp_path / "decisions")
    # dark/empty curator -> recency path. Mirror this file's existing client monkeypatch:
    monkeypatch.setattr(ups.client, "request_onload", lambda *a, **k: [])
    store = _seed_store(["k1", "k2"])               # <- use this file's existing seeding helper
    ups.handle({"prompt": "hello world", "session_id": "sess-A"}, store)
    recs = dl.read_recent("sess-A", 1)
    assert recs and recs[0].source == "recency"
    assert set(recs[0].injected_keys) <= {"k1", "k2"} and recs[0].working_set_size >= 1


def test_hook_record_failure_is_fail_open(monkeypatch):
    from context_curator.hooks import user_prompt_submit as ups
    monkeypatch.setattr(ups.client, "request_onload", lambda *a, **k: [])
    monkeypatch.setattr(ups, "record_decision",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    store = _seed_store(["k1"])
    res = ups.handle({"prompt": "hi", "session_id": "s"}, store)
    assert res.code == 0                            # injection still succeeds despite logging failure
```
(If `tests/test_hooks_onload.py` has no `_seed_store` helper, build the store inline the same way its existing tests do — the point is a store with ≥1 live chunk so the recency path injects something. `res.code` is the `HookResult` exit code field — match its actual attribute name from `_io.py`.)

- [ ] **Step 3: run, expect fail** — `uv run pytest tests/test_hooks_onload.py -k "records_decision or fail_open" -v` → fail (`record_decision` not imported/called).

- [ ] **Step 4: implement** — in `src/context_curator/hooks/user_prompt_submit.py`: add the import at the top (with the other imports) `from context_curator.observe.decision_log import record_decision`, and insert this block in `handle` **after the `if keys: … else: …` block and before `block = format_block(...)`**:
```python
    # M6 decision log: source reflects what was ACTUALLY injected (curator keys can be filtered to
    # empty by by_key). Fail-open: observability must never break injection.
    source = "curator" if (keys and chunks) else ("recency" if chunks else "none")
    try:
        record_decision(event.get("session_id", "") or "unknown",
                        prompt[:80], source, [c.key for c in chunks])
    except Exception:
        pass
```

- [ ] **Step 5: run, expect pass** — `uv run pytest tests/test_hooks_onload.py -v` (existing + new pass); `uv run pytest tests -k "hook or onload" -q` (no regressions); ruff.

- [ ] **Step 6: commit** — `git add src/context_curator/hooks/user_prompt_submit.py tests/test_hooks_onload.py && git commit -m "feat(m6): record onload decisions from the hook (fail-open)"`

---

## Task 5: statusline setup docs

**Files:** Create `docs/statusline.md` (or append to the project README/docs index if one exists — check first).

- [ ] **Step 1: write the doc** — `docs/statusline.md`, covering:
  - What the indicator shows: `CC ws:<injected this turn> +<paged-in>/-<paged-out> [curator|recency|none]`.
  - The `.claude/settings.json` config, **primary (recommended) direct-exe form**:
    ```json
    { "statusLine": { "type": "command",
      "command": "D:/MajorProjects/INFRASTRUCTURE/context-curator/.venv/Scripts/cc-statusline.exe" } }
    ```
    (POSIX: `.../.venv/bin/cc-statusline`.) Note: forward slashes on Windows; assumes `uv sync` has been run.
  - The **fallback form** (no `uv sync` needed, slower, re-syncs ~0.8 s after a `pyproject.toml` edit):
    `uv run --project <ABS_PROJECT_DIR> cc-statusline`.
  - The `$CC_DB_PATH` caveat (if you override the db path, the statusLine command must inherit it).
  - The inspect command: `uv run python -m context_curator.observe.decision_log --tail 20`.
  - Privacy: records live under the gitignored `.context-curator/decisions/`; local-only.

- [ ] **Step 2: commit** — `git add docs/statusline.md && git commit -m "docs(m6): statusLine setup + inspect usage"`

---

## Final verification
- [ ] `uv run pytest -q` green (the pre-existing `test_curator_lifecycle_and_handshake` timing flake passes in isolation); `uv run ruff check .` clean.
- [ ] **No selection/store change:** `git diff main --stat -- src/context_curator/policy/ src/context_curator/curator/ src/context_curator/store/` is EMPTY (M6 only adds `observe/` + the hook call + pyproject entry).
- [ ] `echo '{"session_id":"x"}' | uv run cc-statusline` → `CC ·`, exit 0.
- [ ] Then the final whole-branch review → PR.

## Spec coverage map (self-review)
| Spec § | Task |
|---|---|
| §3 record + paths + tail-read + writer/reader (torn-tolerant, fail-open, atomic append, ts format, prompt normalize) | 1 |
| §3 window cap + writer-\n invariant | 1 |
| §4 hook capture point (source labeller, fail-open, exact insertion) | 4 |
| §5 statusline render + fallback precedence + never-raise + stdout.write | 2 |
| §5/§10 cc-statusline entry point + command form | 2, 5 |
| §6 inspect CLI | 3 |
| §7 testing (all cases incl. torn-line, present-but-no-file, fail-open) | 1, 2, 4 |
| §8 privacy (gitignored .context-curator/decisions/) | 1 (path), Final verification |
| §9 path resolution + $CC_DB_PATH caveat | 1, 5 (doc) |
| §10 file structure | all |
