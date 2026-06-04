# M6 — Decision Log + Statusline Indicator

## 1. Purpose
Make the live curator **observable**. Every turn the `UserPromptSubmit` onload hook injects a slice of
prior context (policy selection when the flag is on; recency-fallback otherwise). M6 records each such
decision to a durable, inspectable **per-session decision log**, and surfaces it via a Claude Code
**statusLine** indicator. Useful immediately even with the policy dark — it records the
recency-fallback injections that already happen every turn.

**Indicator semantics (injection window, not store eviction — §11 has no per-chunk eviction):** the
"working set" is the slice injected **this** turn; **page-in/out** is the turn-over-turn delta of that
injected set (keys that entered vs left the window since the previous turn); plus a `[curator]` /
`[recency]` source tag.

## 2. Scope
**In scope:** an `observe/` package with `decision_log.py` (record/read/delta/path + an inspect CLI)
and `statusline.py` (the statusLine command); a best-effort, fail-open capture call wired into the
onload hook; deterministic tests; a short docs note for configuring the statusLine.

**Out of scope (deferred):** the remote-control sanity pass / ultraplan-ultracode channel-conflict
note (a Claude Code ergonomics aside, not curator code); any change to the selection policy or the
chunk store; M7 packaging.

## 3. Decision record + sidecar log
New package `observe/` (NOT `inspect/` — that shadows the stdlib `inspect` module). `observe/decision_log.py`:
```python
@dataclass
class DecisionRecord:
    ts: str                  # iso8601 UTC, e.g. "2026-06-04T12:00:00Z"
    session_id: str
    prompt_preview: str      # first 80 chars of the prompt
    source: str              # "curator" | "recency" | "none"
    injected_keys: list[str]
    working_set_size: int    # == len(injected_keys)
    paged_in: list[str]      # injected_keys − previous turn's injected_keys (order-stable)
    paged_out: list[str]     # previous turn's injected_keys − injected_keys (order-stable)
```
- **Storage:** one JSONL file per session, append-only: `decisions-<session_id>.jsonl` in a
  `decisions/` dir beside the SQLite db — `Path(resolve_db_path()).parent / "decisions"`. Because the
  default db is `<project>/.context-curator/store.db` and `.context-curator/` is already gitignored,
  the log is local-only with no new ignore rule (§8). **It never touches the chunk store**, so the
  "reconcile thread is the sole writer" invariant (`handler.py`) is preserved.
- `record_decision(session_id, prompt_preview, source, injected_keys) -> None`: get `prev_keys` via
  the **same tail-read helper** the reader uses (round-1 I1 — a crash-torn last line must be tolerated
  here too, not only in `read_recent`); compute `paged_in = [k for k in injected_keys if k not in
  prev_set]`, `paged_out = [k for k in prev_keys if k not in cur_set]` (order-stable lists, not sets,
  so the record is deterministic); append via **`with open(path, "a", encoding="utf-8", newline="")
  as f: f.write(json.dumps(rec) + "\n")`** (round-2 I2: build the full newline-terminated line, write
  it, close-flushes; `newline=""` keeps the file byte-exact `\n` on Windows instead of `\r\n`). The
  only well-formed line ends in `\n`, so a crash-torn write leaves a non-`\n`-terminated final fragment
  that the reader discards (see `_tail_lines`). First turn (no file / empty) → `prev_keys = []` →
  `paged_in = injected_keys`, `paged_out = []`. `prompt_preview` is whitespace-normalized
  (`" ".join(prompt[:80].split())`, round-2 M1 — strip embedded newlines so the JSONL/inspect line
  stays one line). **Fail-open:** the whole body is wrapped so it can never raise.
  `ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")` (round-1 M2 — plain `isoformat()` yields
  `+00:00`, not `Z`).
- `_tail_lines(path, n) -> list[str]`: **bounded tail-read** (round-1 I2) returning the last `n`
  **complete (newline-terminated)** lines. Read the last ~64 KB in binary, decode utf-8 (errors
  ignored). **Discard rules (round-2 I1):** (a) if the read did NOT start at byte 0, drop everything
  before the first `\n` (the sliced partial first line); (b) **a final fragment not followed by `\n`
  is a torn partial → drop it** (only `\n`-terminated lines count as complete). Concretely: split the
  decoded text on `\n`; the trailing element after the last `\n` is always dropped (it is either `""`
  for a clean file or a torn partial for a crashed write). Both `read_recent` AND `record_decision`'s
  `prev_keys` read use this, so the writer's delta is genuinely torn-tolerant. Cost is O(window), not
  O(file) — the per-render statusline read stays cheap regardless of session length. **Window cap
  (round-3 M-R3a):** ~64 KB ≈ the last ~300 lines, so the inspect CLI's `--tail N` returns at most
  ~300 even for larger N — acceptable for an inspect affordance; documented, not grown (YAGNI).
  **Writer invariant (round-3 M-R3c):** `record_decision` ALWAYS appends a trailing `\n`, which is
  precisely why the reader may treat any unterminated final fragment as torn and drop it.
- `read_recent(session_id, n) -> list[DecisionRecord]`: `_tail_lines` then parse oldest→newest,
  **skipping** any malformed/partial line.
- **Growth (round-1 I3):** files are append-only and uncapped — one ~200-byte line per turn, so even a
  1000-turn session is ~200 KB, and tail-reading makes statusline cost independent of size. Accepted
  as unbounded by design (no rotation); documented here rather than capped (YAGNI).
- `_newest_session_file() -> Path | None` (round-1 M1): the `decisions-*.jsonl` with the newest mtime;
  shared by the inspect-CLI default (§6) and the statusline `session_id`-missing fallback (§5).

## 4. Hook capture point
Modify `hooks/user_prompt_submit.py::handle` ONLY. **Exact insertion point (round-2 C1):** between the
end of the existing `if keys: … else: …` block (the `else` branch ends at the `log(... [recency])`
line) and the `block = format_block(...)` line. At that point BOTH `keys` and `chunks` are bound on
every path (`keys` is set in both the `try`-success and `except CuratorUnavailable` branches; `chunks`
is set in both `if`/`else` branches) — and any *other* exception from `request_onload` propagates out
of `handle` and is fail-opened by `run_hook`, so the snippet never runs unbound. Insert:
```python
# source reflects what was ACTUALLY injected, not just what the curator returned (round-1 C1):
# curator can return keys that by_key filters to empty (store rotated them out) -> chunks == [].
source = "curator" if (keys and chunks) else ("recency" if chunks else "none")
try:
    record_decision(event.get("session_id", "") or "unknown",
                    prompt[:80], source, [c.key for c in chunks])
except Exception:               # fail-open: observability must never break injection
    pass
```
(`keys` = the curator result; `chunks` = the final injected list, post-`by_key` filter; the delta is
computed on the injected `chunks`, which is correct.) The existing `log()` lines stay; the only new
import is `record_decision`. Latency: one short append; negligible against the §8 budget, and guarded. **`session_id` is verified present** on the `UserPromptSubmit`
event (snake_case — confirmed against the Claude Code hook schema); the `or "unknown"` is pure defense
(if ever absent, records co-mingle in `decisions-unknown.jsonl` rather than crash — an acceptable
degraded mode, not the expected path).

## 5. Statusline command
`observe/statusline.py`, run as `python -m context_curator.observe.statusline`. Claude Code's
`statusLine` setting feeds the command a **JSON object on stdin** that includes **`session_id`
(snake_case — verified against the Claude Code statusLine schema, identical to the hook's)** plus
`cwd`, `model`, `context_window`, etc. (only `session_id` is used in v1; `context_window.used_percentage`
is a noted future enrichment, out of scope). It renders the single line printed to **stdout**.

**Command form (round-2 C2 — critical, or the statusline silently no-ops).** Claude Code runs the
statusLine command via the shell (Git Bash / PowerShell on Windows) from the **session cwd**, NOT the
project venv — so a bare `python -m context_curator.observe.statusline` cannot import the package under
`uv`. M6 (a) adds a console-script entry point in `pyproject.toml`
(`[project.scripts] cc-statusline = "context_curator.observe.statusline:main"`) — verified to
materialize as `.venv/Scripts/cc-statusline(.exe)` on `uv sync` (hatchling backend, editable install);
and (b) documents the `.claude/settings.json` command. **Primary (recommended) form (round-3 I-R3a):**
the direct venv executable — `"<ABS_PROJECT_DIR>/.venv/Scripts/cc-statusline.exe"` (Windows) /
`"<ABS_PROJECT_DIR>/.venv/bin/cc-statusline"` (POSIX) — ~64 ms, no re-sync, no stderr noise. **Fallback
form:** `uv run --project <ABS_PROJECT_DIR> cc-statusline` (~110 ms warm, resolves the venv from any
cwd, but **re-syncs and stalls ~0.8 s on the render after any `pyproject.toml` edit** — so it's the
fallback for users who haven't run `uv sync`, not the default). Forward slashes in paths on Windows.
The setup note states both verbatim; a wrong command = a blank statusline with no error.
- Read stdin JSON → `session_id`; `read_recent(session_id, 1)` → last record; render
  `f"CC ws:{r.working_set_size} +{len(r.paged_in)}/-{len(r.paged_out)} [{r.source}]"`
  (e.g. `CC ws:8 +2/-1 [recency]`). Plain ASCII, one line, no ANSI (portable). Emit via
  **`sys.stdout.write(line)`** (NOT `print()`, which appends `\r\n` on Windows — round-3 I-R3b); the
  test asserts the exact string with no trailing CR.
- **Fallback precedence (round-2 I3 — order matters):** (1) `session_id` present but its file doesn't
  exist yet (brand-new session, before the first prompt) → print `CC ·`. Do NOT fall through to the
  newest file — that would show ANOTHER session's working set under this one. (2) `session_id`
  absent/blank (shouldn't happen — it's verified present) → `_newest_session_file()` as best-effort
  (note: ambiguous if two Claude windows share the project — round-2 M2). (3) malformed stdin / no
  record / ANY error → `CC ·`. Exit 0 always; **never** raise or exit nonzero (statuslines render on
  every assistant message, debounced 300 ms, in-flight renders cancelled — so it must also be fast,
  which the §3 bounded tail-read guarantees).

## 6. Inspect command
`observe/decision_log.py` gets a `main()` (`python -m context_curator.observe.decision_log`) with
argparse: `--session <id>` (default: the most-recently-modified `decisions-*.jsonl` in the decisions
dir) and `--tail N` (default 10). Prints recent records one per line:
`<ts> [<source>] ws:N +<in>/-<out>  "<prompt_preview>"`. Read-only; the "read the decision log"
affordance. If the decisions dir is empty/missing → print a friendly "no decisions recorded yet" and
exit 0.

## 7. Testing (deterministic; no live curator/daemon/bge)
All tests monkeypatch the decisions-dir resolver to a `tmp_path`.
- **decision_log:** `record_decision` → `read_recent` round-trip; the page-in/out **delta** against a
  seeded previous record (`in = cur − prev`, `out = prev − cur`, order-stable lists); the first-record
  case (`paged_in == injected_keys`, `paged_out == []`); **fail-open** (an unwritable/locked path →
  returns without raising); per-session isolation (two `session_id`s write distinct files, no
  cross-contamination); `read_recent` skips a malformed trailing line.
- **`_tail_lines` torn-line:** a file whose last write is torn (no trailing `\n`, e.g.
  `b'{"a":1}\n{"b":2'`) → the torn fragment is DISCARDED, `read_recent` returns the prior complete
  record, and `record_decision`'s `prev_keys` read does not raise (round-2 I1).
- **statusline:** a seeded session log + stdin JSON `{"session_id": ...}` → asserts the EXACT rendered
  line; **`session_id` present but no file yet → `CC ·` (NOT the newest other-session file, round-2
  I3)**; missing/blank `session_id` → newest-file fallback; malformed stdin (`not json`) → `CC ·`,
  exit 0, no raise.
- **hook integration:** `user_prompt_submit.handle` against a seeded `InMemoryStore` with an event
  that **includes `session_id`** (flag dark → recency path) writes a record whose `injected_keys` ==
  the injected chunk keys and `source == "recency"`; monkeypatch `record_decision` to raise → `handle`
  still returns `HookResult(0)` and still injects (fail-open proven). Extends `tests/test_hooks_onload.py`
  (note: its existing events omit `session_id`, so the new test seeds it). Also a unit test for the
  `source` labeller: `keys` truthy but `chunks` empty → `source == "curator"` is NOT produced (it's
  `"none"`) — guards round-1 C1.

## 8. Privacy (§9 boundary)
Decision records hold prompt previews + chunk keys (keys may encode session ids / file paths) —
**local-only**. They live under `.context-curator/decisions/`, beside the SQLite db; `.context-curator/`
is already gitignored, so nothing is committed or transmitted. Same boundary as the chunk store; no
external calls.

## 9. Path resolution
`decisions_dir() -> Path` = `Path(resolve_db_path()).parent / "decisions"` (created best-effort on
write). `decision_log_path(session_id) -> Path` = `decisions_dir() / f"decisions-{session_id}.jsonl"`.
Reusing `store.paths.resolve_db_path()` (which honors `$CC_DB_PATH`) co-locates the log with whatever
db the curator already uses, so hook subprocess and inspect CLI always resolve to the same file. A
`session_id` is sanitized for filesystem safety (replace any non-`[A-Za-z0-9._-]` with `_`).

**`$CC_DB_PATH` caveat (round-1 I4):** `resolve_db_path` is `__file__`-anchored and cwd-independent, so
the hook and statusLine subprocesses resolve to the **same** `decisions/` dir by default. The only way
they diverge is if `$CC_DB_PATH` is set for one subprocess's environment and not the other's — the
same constraint that already governs the hook↔server db path. Documented in the statusLine setup note:
if you override `$CC_DB_PATH`, ensure the statusLine command inherits it identically.

## 10. File structure
- **New:** `src/context_curator/observe/__init__.py`; `observe/decision_log.py`
  (`DecisionRecord`, `record_decision`, `read_recent`, `decisions_dir`, `decision_log_path`, inspect
  `main`); `observe/statusline.py` (render + `main`).
- **Modify:** `src/context_curator/hooks/user_prompt_submit.py` (best-effort `record_decision` call +
  `source` label).
- **Modify:** `pyproject.toml` — add `[project.scripts] cc-statusline =
  "context_curator.observe.statusline:main"` (round-2 C2) so the statusLine command resolves under uv.
- **New tests:** `tests/observe/test_decision_log.py`, `tests/observe/test_statusline.py`; extend
  `tests/test_hooks_onload.py` (the fail-open + record integration test).
- **New docs:** a short section (the `.claude/settings.json` `statusLine` snippet + the inspect
  command usage).

## Design Critique Log
_(populated by 3 adversarial critique rounds before finalize.)_

### Critique Round 1
Critic flagged two make-or-break unverified assumptions + correctness/robustness gaps:
- **C2/C3 (Critical, both VERIFIED via the Claude Code docs):** `session_id` is present in BOTH the
  `UserPromptSubmit` hook stdin AND the statusLine command stdin, snake_case, identical — so keying
  the per-session file off it across hook and statusline is sound. Recorded in §4/§5 with the
  verification + a `_newest_session_file()` mtime fallback for defense.
- **C1 (Critical):** the `source` labeller mislabelled a curator-returned-but-store-filtered-empty
  injection as `"curator"`. **Fixed (§4):** `source = "curator" if (keys and chunks) else ("recency"
  if chunks else "none")`, with a guarding unit test (§7).
- **I1:** a crash-torn last line corrupts the delta read in `record_decision`, not just `read_recent`.
  **Fixed (§3):** both use the same tolerant `_tail_lines` helper; append is one atomic newline-
  terminated `write`.
- **I2:** per-render full-file read at unbounded size. **Fixed (§3):** bounded `_tail_lines` (seek to
  EOF, last ~64 KB), O(window) not O(file).
- **I3:** unbounded growth. **Decided (§3):** accepted + documented (≈200 B/turn; tail-read makes size
  irrelevant to the statusline), no cap (YAGNI).
- **I4:** `$CC_DB_PATH` env divergence between hook and statusLine subprocesses. **Documented (§9)** +
  the setup note tells the user to inherit it identically.
- **M1/M2/M3:** shared `_newest_session_file()`; `ts` via `strftime("%Y-%m-%dT%H:%M:%SZ")` (not
  `isoformat`); tests seed `session_id` and cover the C1 labeller.

### Critique Round 2
Critic traced the real `handle` body + verified the statusLine schema/runtime; two correctness
breakers + spec-tightening:
- **C2 (Critical):** Claude Code runs the statusLine command via the shell from the **session cwd**,
  not the project venv, so a bare `python -m …` silently no-ops under uv (Windows-primary). **Fixed
  (§5/§10):** add `[project.scripts] cc-statusline` and document the command as
  `uv run --project <abs> cc-statusline`.
- **I1 (Important):** the torn-final-line **discard rule** was implied, not stated — without it
  `record_decision`'s delta read hands a torn partial to `json.loads`. **Fixed (§3):** `_tail_lines`
  drops the post-last-`\n` trailing element (clean `""` or torn partial) and the sliced partial first
  line; both reader and writer use it.
- **C1 (Critical, snippet/real-code mismatch):** **Fixed (§4):** pinned the exact insertion point
  (after the `if keys/else` block, before `format_block`, where `keys`+`chunks` are both bound) and
  noted non-`CuratorUnavailable` errors fail-open via `run_hook` before the snippet.
- **I2:** committed to `with open(...,"a",encoding="utf-8",newline=""): f.write(line)` (close-flushes,
  no `\r\n`) instead of claiming syscall atomicity; the real guarantee = sequential single-writer +
  torn-final-line discard.
- **I3:** statusline fallback precedence pinned — `session_id` present but file-missing → `CC ·` (never
  another session's newest file).
- **M1:** `prompt_preview` whitespace-normalized. M2/M3/M4 (mtime best-effort note, vestigial
  sanitization, dataclass-not-pydantic) acknowledged; no change needed.

### Critique Round 3
Verdict: **PROCEED**. The critic **empirically verified** the round-2 C2 fix end-to-end — the
`cc-statusline` entry point materializes as `.venv/Scripts/cc-statusline.exe` on `uv sync` (hatchling,
editable install), runs correctly from a foreign cwd, and the `VIRTUAL_ENV` warning goes to stderr (not
stdout). The `_tail_lines` discard rule was traced correct for all cases (empty / single-no-newline /
single-with-newline / n>available / torn-final). The hook insertion point was re-verified (`keys`,
`chunks`, `prompt` all in scope). No Critical. Five items folded in inline:
- **I-R3a:** `uv run` re-syncs and stalls ~0.8 s on the render after any `pyproject.toml` edit (~110 ms
  warm otherwise) vs ~64 ms for the direct exe → §5 now recommends the **direct
  `.venv/Scripts/cc-statusline.exe`** as primary, `uv run --project` as fallback.
- **I-R3b:** `print()` emits `\r\n` on Windows → §5 pins `sys.stdout.write(line)` + an exact-bytes test.
- **M-R3a:** the 64 KB ≈ ~300-line `--tail` cap documented (§3).
- **M-R3c:** the "writer always appends `\n`" invariant made explicit (§3).
- **M-R3b:** this duplicated round-3 heading collapsed to one.
