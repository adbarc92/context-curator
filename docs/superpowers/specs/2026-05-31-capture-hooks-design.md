# M2 — Capture Path & Guardrail Hooks — Design

**Status:** Draft (pre-critique)
**Parent design:** `DESIGN.md` v1.3 §4.3 (hook layer), §8 M2, §6 (hook contract), §9 (security), §5 (keyspace)
**Milestone:** M2, after M0/M1 + the replay harness. The store, the frozen `Store` interface, and `replay/ingest.py` exist.
**Stack:** Python + UV. Hooks are Python entry points writing to the shared embedded SQLite store.

---

## 1. Purpose

Wire Claude Code's hook events to the curated store so the working set is **captured automatically** as a session runs, and add **guardrails** that block writes of secrets / to sensitive paths. This is the *write* half of the system (M4 adds the *read*/onload half).

Three hooks (DESIGN §8 M2):
- **PostToolUse** → capture file-touches + tool results into the store.
- **SubagentStop** → capture subagent summaries into `shared:exploration:*`.
- **PreToolUse** → guardrails: block sensitive-path writes and secret-bearing inputs.

## 2. Scope & decisions

**In scope (v1):**
- **Deterministic capture only** — a file-*write* ledger and raw tool-result candidate chunks. **No** heuristic "decision/contract" semantic extraction (deferred; it needs the M3 policy to be useful and is non-deterministic in a hook).
- Hooks are **Python entry points** (`python -m context_curator.hooks.<name>`) that read event JSON on stdin and write via `SqliteStore(db_path=$CC_DB_PATH, embedder=HashingEmbedder())` (the embedder is required — §3.1) — the same DB the MCP server uses. **WAL + `busy_timeout` + immediate-transaction seq allocation** make concurrent main-session + hook writes safe (§3.1); WAL alone does *not* (it serializes writers but neither prevents `seq` races nor eliminates `SQLITE_BUSY`).
- A **canonical `capture/` module** of pure functions is the single source of truth for "how an event becomes a chunk"; `replay/ingest.py` is refactored to a thin wrapper over it.
- **Capture hooks** (PostToolUse/SubagentStop) are best-effort and **fail-open** (exit 0 + loud log) on any internal error — a capture failure must never disrupt a session.
- **The guardrail** (PreToolUse) **blocks on match (exit 2)**. It touches **no store** (so it can't be wedged by DB contention — §3.4), each check is defensively isolated *and flagged on error* (never silently skipped), and inputs are size-capped to bound regex cost. On a guard crash it **fails open (exit 0) but emits a distinct, alertable marker** — an explicitly accepted risk for a dev tool (see DESIGN §9 security), not a silent bypass.
- Exit-code contract: **0 = allow, 2 = block** (verified against the CLI in DESIGN §11's spike); a nonzero-non-2 exit is a non-blocking error surfaced to the user. (DESIGN §6 was already corrected to read "exit 2 = block" — no further DESIGN edit needed.)

**Non-goals (v1):** semantic decision/contract extraction; capturing contract *bodies* (we only have names from a subagent summary, if any); the onload/UserPromptSubmit path (M4); networked store concurrency. The guardrail is **defense-in-depth, not a sandbox** — it does not resolve symlinks/TOCTOU, does not fully parse arbitrary Bash, and its residual bypass surface is stated explicitly (§3.3).

## 3. Architecture

```
                   ┌─────────────────────── shared SQLite (WAL), $CC_DB_PATH ───────────────────────┐
   tool runs ──▶ PostToolUse hook ──▶ capture.file_ledger + capture.tool_result ──▶ store           │
 subagent ends ─▶ SubagentStop hook ─▶ capture.subagent_summary ───────────────────▶ store          │
 before write ─▶ PreToolUse hook ──▶ guard.paths + guard.secrets ──▶ exit 2 (block) / exit 0 (allow) │
                   └──────────────────────────────────────────────────────────────────────────────┘
   (offline) replay engine ─▶ replay/ingest.py (thin wrapper) ─▶ capture.tool_result ─▶ store
```

Layers, each independently testable:
- **`capture/`** — pure functions `(store, **fields) -> key|None`. No hook/JSON knowledge.
- **`guard/`** — pure predicates over a path / a text blob + a config. No hook/JSON knowledge.
- **`hooks/`** — thin adapters: parse stdin event JSON → call capture/guard → emit the §6 exit code. The ONLY layer that knows the Claude Code event-JSON shape (isolated, like the transcript adapter).

### 3.1 Store change — concurrency-safe writes

Three connected changes to `SqliteStore`, because the MCP-server process and short-lived hook processes now write the same DB file concurrently. `SqliteStore.__init__` still takes the **required `embedder`** (every caller, including `open_store()`, must pass one — C1).

1. **WAL** — `PRAGMA journal_mode=WAL;` after connect. Allows concurrent readers alongside a writer; persists in the DB header (so per-connection setup is harmless). *WAL requires the DB and its `-wal` sidecar to be on the **same local filesystem*** — fine for the single-machine v1; the networked backend (DESIGN §12) will not use WAL.
2. **Lock-wait** — `PRAGMA busy_timeout=5000;` (and `sqlite3.connect(..., timeout=5)`). WAL still serializes *writers*; without a busy timeout the loser of a write race raises `SQLITE_BUSY`/`database is locked` immediately. (The §2 claim "WAL avoids `database is locked`" was wrong — busy_timeout is what handles it.)
3. **Race-free `seq` — explicit `BEGIN IMMEDIATE` (version-proof, rollback-safe).** `seq = (SELECT COALESCE(MAX(seq),0)+1 FROM chunks)` is a read-modify-write that, across *connections*, can compute the same `MAX` in two concurrent inserts → **duplicate `seq`** → nondeterministic recency (the entire v1 ranking signal). The fix must NOT rely on the Python `sqlite3` driver's implicit-`BEGIN` behavior, which **changed in 3.12** (the project supports `>=3.11`). Instead: connect with **`isolation_level=None` (autocommit)** and have `store()`:
   - **compute `self._embedder.embed(content)` BEFORE the transaction** (round-3 fix) — embedding up to `CAPTURE_MAX_CONTENT` must not run while holding the write lock, or it starves other writers for the `busy_timeout` window (cheap with `HashingEmbedder` today, but the embedder is M3-swappable);
   - then issue an **explicit transaction with rollback** — `try: BEGIN IMMEDIATE; INSERT/upsert; COMMIT  except: self._conn.rollback(); raise`. **The rollback is load-bearing (round-3 fix):** without it, an exception between `BEGIN` and `COMMIT` leaves the write transaction open on the connection. Hook processes self-heal on exit, but the **long-lived MCP server shares this code path** — one open transaction wedges the write lock for the server's lifetime, after which every hook writer hits `busy_timeout`, fails open, and silently drops captures (the exact failure WAL+busy_timeout exist to prevent).

   `BEGIN IMMEDIATE` takes the write lock *before* the `MAX` read; with `busy_timeout` a second writer waits then reads the committed `MAX`. Identical on every supported interpreter. Other single-statement writes (`pin`/`evict`/`sweep`) run as their own autocommit statements (no MAX-race, no wrapper needed). The existing `self._conn.commit()` calls become harmless no-ops under autocommit and are removed. A test asserts a `store()` whose embedder raises leaves the connection usable for the next write (no lingering lock).

`.gitignore` ignores `*-wal` and `*-shm` (sidecar names are `<dbfile>-wal`/`-shm`, so glob on the suffix, not `*.db-wal`).

**Lazy-expiry sweep (rate-limited).** Expiry is lazy (only checked on `retrieve`/`query`), so with hooks writing every turn the `chunks` table would grow unboundedly. But running a `DELETE` on **every** hook invocation is a writer on the capture hot path (serializes against the MCP server, up to a `busy_timeout` stall) — so it is **rate-limited**: a tiny `cc_meta(key TEXT PRIMARY KEY, value TEXT)` table (added to `_DDL`'s `CREATE TABLE IF NOT EXISTS` block, so two processes can't race to create it) holds `last_sweep` (epoch). `sweep_expired(store)` reads it and **returns immediately if a sweep ran within `SWEEP_INTERVAL_S` (default 300)**; otherwise it runs `DELETE FROM chunks WHERE expires_at IS NOT NULL AND expires_at <= <now>` and updates `last_sweep`. So at most one sweep per 5 min across all processes. The M3 `query` should additionally filter expired rows in SQL rather than load-all-then-filter; noted forward (not built here).

**Tests:** `test_store_wal.py` asserts `PRAGMA journal_mode` returns `wal`; **two interleaved writers produce NO duplicate `seq`** (the load-bearing concurrency assertion — a "both write without error" test would pass even with the race) — run it on the **min and max supported Python** in CI; a blocked writer waits rather than raising; the sweep deletes expired rows, spares live/pinned, is a no-op within `SWEEP_INTERVAL_S`, and is **correct under a concurrent inserter** (sweep + insert interleaved → no error, expired gone, concurrently-inserted live row survives).

### 3.2 Canonical capture module (`src/context_curator/capture/`)

Pure functions; operate on primitives, not on hook JSON or replay schema types.

**`tool_result.py`** — the shared mapping `replay/ingest.py` is refactored onto. The key's unique suffix is chosen so the function works whether or not a per-call id is in the payload (I2 — `call_id` presence in PostToolUse is **not** assumed):
```python
CAPTURE_MAX_CONTENT = 32_768  # bytes; larger tool outputs are head-truncated + marked

def capture_tool_result(store: Store, *, session_id: str, tool_name: str, content: str,
                        error: bool = False, call_id: str | None = None,
                        ordinal: int | None = None, ttl_s: int | None = None,
                        max_content: int | None = None) -> str | None:
    """Successful tool result -> candidate chunk. Returns the key, or None if skipped.
    `max_content` truncates oversized content; it is None on the replay path so replay
    output stays STRUCTURALLY byte-identical regardless of fixture size (I-3)."""
    if error:
        return None
    if ordinal is not None:                       # replay: deterministic ordinal key
        suffix = f"{ordinal:06d}:{call_id}"
    elif call_id:                                 # live, payload has a call id
        suffix = call_id
    else:                                         # live, no id: content-hash (dedups identical results)
        suffix = sha1(content.encode()).hexdigest()[:12]
    if max_content is not None and len(content) > max_content:
        content = content[:max_content] + "\n…[truncated]"
    key = f"session:{session_id}:tool:{suffix}"
    store.store(key, content, tags=[tool_name.lower()], source=f"tool:{tool_name}", ttl_s=ttl_s)
    return key
```
`replay/ingest.py` becomes a thin wrapper — `ordinal` given, `max_content=None` (no truncation), `ttl_s=None` → key `session:{sid}:tool:{ordinal:06d}:{call_id}`, **byte-identical and size-unconditional**, so the replay tests/keystone determinism stay green:
```python
def ingest_tool_result(result, call, session_id, ordinal, store):
    capture_tool_result(store, session_id=session_id, tool_name=call.name,
                        content=result.content, error=result.error,
                        call_id=result.call_id, ordinal=ordinal, ttl_s=None, max_content=None)
```
Live capture passes `ordinal=None`, the payload `call_id` if present (else content-hash), `ttl_s=CAPTURE_TTL_S`, and `max_content=CAPTURE_MAX_CONTENT`.

**`file_ledger.py`** — deterministic who-*wrote*-what (**write tools only** — `Read` is excluded so reads don't clobber write provenance, M4):
```python
def capture_file_write(store: Store, *, session_id: str, tool_name: str, path: str,
                       ttl_s: int | None = None) -> str:
    key = f"shared:file_ledger:{path}"
    store.store(key, f"{tool_name} wrote {path}", tags=["file-touch"],
                source="file-ledger", provenance=session_id or "unknown-session", ttl_s=ttl_s)
    return key
```
Latest-write-per-file (overwrite; `seq` keeps it recent; `provenance` records the session/agent, never silently `None` — DESIGN §9). **Keyspace note (M-2):** DESIGN §5 lists `shared:file_ledger` as a single key; this spec refines it to per-path keys `shared:file_ledger:{path}` (queryable per file). The tradeoff: only the *latest* writer per file is retained (no write history) — acceptable for the M3 relevance signal, but it means the §9 audit sees the last writer only. Intentional, documented divergence.

**`subagent.py`** — capture a subagent's final summary. **Important (I1):** DESIGN §6's `{summary, artifacts, contracts_touched, followups}` is the convention for what a `cc-*` subagent *returns in its final message* — it is **not** a structured field in the `SubagentStop` hook payload. So v1 captures the subagent's **final-message text** as the exploration summary (whatever the payload actually exposes — pinned at implementation time, §3.4). `contracts_touched` is parsed **only if** the final message contains a fenced ```json block matching the schema; otherwise it is empty. The function therefore takes already-extracted primitives:
```python
def capture_subagent_summary(store: Store, *, subagent_id: str, summary: str,
                             contracts_touched: list[str] | None = None,
                             ttl_s: int | None = None) -> str | None:
    if not summary:
        return None                               # nothing parseable -> no-op (hook still exits 0)
    tags = ["exploration", *(contracts_touched or [])]
    key = f"shared:exploration:{subagent_id}"
    store.store(key, summary, tags=tags, source="subagent:explore",
                provenance=subagent_id or "unknown-subagent", ttl_s=ttl_s)
    return key
```
It does NOT mint pinned `shared:contracts:*` chunks (the summary carries names, not bodies). Deferred until there's a body source.

### 3.3 Guard module (`src/context_curator/guard/`)

Pure, config-driven, unit-testable.

**`config.py`** — defaults, overridable by a JSON file at `$CC_GUARD_CONFIG` (or `.claude/cc-guard.json` if present). Also `CAPTURE_TTL_S = 86400` (live-capture TTL) and `GUARD_MAX_SCAN = 262_144` (bytes; inputs larger are head-scanned only, bounding regex cost — I5):
```python
DEFAULT_SENSITIVE_GLOBS = ["**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/id_rsa*",
                           "**/.aws/**", "**/.ssh/**", "**/secrets/**", "**/*secrets*",
                           "**/*.prod.*", "**/*prod*"]
DEFAULT_SECRET_PATTERNS = [
    ("aws-access-key-id", r"AKIA[0-9A-Z]{16}"),
    ("private-key-block", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("aws-secret-quoted", r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9/+=]{20,}['\"]"),
    # value must be QUOTED to avoid matching ordinary code like `token = make_token()` (I5):
    ("generic-secret",    r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]"),
]
```
`load_config()` merges file overrides (replace lists if present) over defaults. Patterns are simple/linear (no nested quantifiers → no catastrophic backtracking); scanning is still capped at `GUARD_MAX_SCAN`.

**`paths.py`** — `is_sensitive_path(path: str, globs: list[str]) -> bool`. Normalizes first: `os.path.expanduser` (resolve `~`), then `normpath`, then `\`→`/`. Matches each glob against **both the full normalized path and the basename** (so `secrets-prod` with no slash is caught by `*secrets*`/`*prod*`). Symlink/TOCTOU/`..`-escape resolution is **out of scope** (stated bypass — §"residual surface").

**`secrets.py`** — `scan_secrets(text: str, patterns: list[tuple[str,str]]) -> str | None` returns the **name** of the first matching pattern (so the block message says *what* matched) or `None`. Scans only the first `GUARD_MAX_SCAN` bytes.

**Per-tool guard extraction (`pre_tool_use` uses this — C4).** What gets path-checked vs. secret-scanned is tool-shaped:

| tool | sensitive-path check | secret-scan |
|---|---|---|
| `Write` | `tool_input.file_path` | `tool_input.content` |
| `Edit` | `tool_input.file_path` | `tool_input.new_string` |
| `MultiEdit` | `tool_input.file_path` | each `edits[].new_string` |
| `Bash` | **write-redirect targets only** — regex `(?:>>?|\btee\b)\s*(\S+)` over the command (note `\s*`, not `\s+` — must catch the no-space `>.env`), each captured target checked against the globs | the command string |

**Why Bash is redirect-only (I-1 fix):** glob-matching the *whole command string* against `*secrets*`/`*prod*` would block benign reads like `grep secrets file`, `ls prod/`, `git log prod-release`. So Bash path-checking is restricted to explicit write redirects (`>`, `>>`, `tee`) whose literal target matches a sensitive glob; reads that merely mention `secret`/`prod` are NOT blocked. The regex uses `\s*` so both `cat > .env` **and the no-space `cat >.env`/`cat>>.env`** are caught (round-3 fix — `\s+` would miss the common no-space form). Tested both ways and across the space/no-space variants (`>.env`, `>>.env` blocked; `grep secrets f` allowed).

`tool_input` field names (`file_path`, `content`, `new_string`, `edits[]`) are the assumed Claude Code tool-input schema — **pinned against real payloads at implementation time**, isolated to the hook adapter. If `tool_name` fires the PreToolUse matcher but is **not** in this table (e.g. a renamed tool in a future CLI), the guard emits the distinct alert marker and allows (visible recognition-miss, not silent — I-2).

**Residual bypass surface (explicit non-goal).** A `Bash` write whose target path is constructed/obfuscated (vars, `$()`, indirect redirects), symlinked sensitive paths, secrets read-from-file-and-written-by-subprocess, and non-quoted secrets are **not** reliably caught. The guard is defense-in-depth against the common/accidental cases (a literal `.env` write, a pasted AWS key), not a sandbox. Accepted for v1.

### 3.4 Hooks (`src/context_curator/hooks/`)

Each hook's *core* is a pure-ish testable function; the `__main__` does stdin/exit.

**`_io.py`** — shared plumbing:
```python
@dataclass
class HookResult:
    exit_code: int          # 0 allow, 2 block (verified CLI semantics; supersedes DESIGN §6's "1")
    message: str = ""       # -> stderr when blocking

def open_store() -> Store:
    # REQUIRED embedder (C1): HashingEmbedder is cheap (hash-based); fine on the capture
    # path. NOTE its embedding is currently *dead work* per capture — nothing reads it
    # until the M3 policy. The §3.2 content cap is what actually bounds the cost.
    store = SqliteStore(db_path=resolve_db_path(), embedder=HashingEmbedder())
    log(f"context-curator: capture DB = {resolve_db_path()}")  # surface misroutes (C2)
    sweep_expired(store)    # rate-limited lazy-expiry sweep (§3.1)
    return store

def read_event() -> dict:   # json.load(sys.stdin); {} on parse failure
def run_hook(handler, *, needs_store: bool) -> None:
    """Parse stdin; build a store only if needs_store; call handler; emit exit code.
    FAIL-OPEN: any exception -> exit 0. Capture hooks log quietly; the guardrail
    (needs_store=False) logs a DISTINCT, alertable marker on crash so a fail-open
    bypass is visible (I4)."""
```
Contract: handlers return `HookResult`; `run_hook` maps it to process exit. **Capture hooks** (`needs_store=True`) always return `0`. **The guardrail** (`needs_store=False` — it never opens the store, I3) returns `2` only on a definite match.

**Unified DB path (C2).** `resolve_db_path()` is the SINGLE source of the DB location, shared by `open_store()` AND `mcp_server.build_default_store()` (which is refactored to call it) so the capture-writer and the model-reader can never drift to different files. It returns `$CC_DB_PATH` if set, else an **absolute, CWD-independent** default: walk up from the module for a project marker (`.git`/`pyproject.toml`) → `<project_root>/.context-curator/store.db`; if no marker, `~/.context-curator/store.db`. (CWD-relative defaults silently sever capture from read when the hook's CWD ≠ the server's; the plugin's `settings.json` should also set `$CC_DB_PATH` explicitly.) Lives in `store/__init__.py` (or `store/paths.py`), imported by both sides. Tested: both call sites resolve to the same absolute path for the same env/root.

**`post_tool_use.py`** — `handle(event, store)` (the ONLY place PostToolUse field names live; isolated; pinned against a real payload at implementation time):
- Parse `tool_name`, `tool_input`, `tool_response`, `session_id`, and a `call_id` if present.
- **Coerce `tool_response` to text (I-2):** `content = resp if isinstance(resp, str) else json.dumps(resp, sort_keys=True)` — Claude Code `tool_response` is frequently a dict/list; without coercion `sha1(content.encode())`/`len(content)` throw → capture silently lost. (`sort_keys` keeps it deterministic.)
- If `tool_name` is a **write** tool (`Write/Edit/MultiEdit` — **not `Read`**, M4) with a `file_path` → `capture_file_write`.
- If a non-error `tool_response` is present → `capture_tool_result(content=content, call_id=…, ordinal=None, ttl_s=CAPTURE_TTL_S, max_content=CAPTURE_MAX_CONTENT)`.
- Always `HookResult(0)`. Registered with `needs_store=True`.

**`subagent_stop.py`** — extract the subagent's **final-message text** from the event (payload shape pinned at implementation time — it is text/transcript, NOT a structured schema, I1). Optionally parse a fenced ```json block for `contracts_touched`. → `capture_subagent_summary`. `HookResult(0)`; no parseable summary → no-op, still `0`. `needs_store=True`.

**`pre_tool_use.py`** — guardrail `handle(event)` (**no `store` param** — I3):
- Use the per-tool extraction table (§3.3) to get the path(s) and text-blob(s) for the tool.
- Each check is **defensively isolated** — but a check that swallows an exception MUST emit the distinct alert marker (I-5): "isolated" means *logged-and-flagged*, never *silently skipped*, or a secret could slip through a per-field error with zero observability.
- Any `is_sensitive_path(path)` → `HookResult(2, "blocked: sensitive path '<path>'")`.
- Any `scan_secrets(text)` hit → `HookResult(2, "blocked: secret pattern '<name>' in tool input")`.
- A `tool_name` that fired the matcher but is absent from the §3.3 table → emit the marker, `HookResult(0)` (visible recognition-miss, I-2).
- Else `HookResult(0)`. Registered with `needs_store=False`; a total crash → fail-open `0` + the distinct marker.

### 3.5 Settings registration

`.claude/settings.json` (M0 left the hook arrays empty) gets:
```json
{
  "hooks": {
    "PreToolUse":  [{"matcher": "Write|Edit|MultiEdit|Bash",
                     "hooks": [{"type": "command", "command": "python -m context_curator.hooks.pre_tool_use"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "python -m context_curator.hooks.post_tool_use"}]}],
    "SubagentStop":[{"hooks": [{"type": "command", "command": "python -m context_curator.hooks.subagent_stop"}]}]
  }
}
```
(Exact matcher/command syntax pinned against current Claude Code hook config at implementation time; isolated to this file. `$CC_DB_PATH` set in the environment or defaulted.)

## 4. Data model touchpoints (DESIGN §5)

- `session:{sid}:tool:{call_id | content-hash}` — captured tool results (live; TTL'd; suffix is the payload call id if present, else a content hash — I2).
- `shared:file_ledger:{path}` — latest *write* per file, `provenance=session` (never `None`).
- `shared:exploration:{subagent_id}` — subagent final-summary text.
No tenant prefixes here (these are session/shared, not tenant-scoped); tenant isolation is unaffected.

## 5. Testing (DESIGN §10.1 — deterministic, golden-file)

- **`guard/paths`, `guard/secrets`** — unit + property tests: each default glob/pattern matches a positive AND **rejects realistic code negatives** (e.g. `token = make_token()`, a UUID assigned to `id`, a 40-char git SHA — must NOT match, guarding against the I5 false-positive class); Windows `\` and `~` paths normalized; `secrets-prod` (no slash) is caught by basename match; a secret mid-blob is found; an over-`GUARD_MAX_SCAN` blob is head-scanned without hanging.
- **Per-tool extraction** — `MultiEdit` with a secret in `edits[1].new_string` → blocked; a `Bash` `cat > .env` (path in the command) → blocked.
- **`capture/*`** — write to an `InMemoryStore`, assert key/tags/source/provenance/ttl; `capture_tool_result(error=True)` → None; **two distinct tool results → two distinct keys** (no collision, I2); content over `CAPTURE_MAX_CONTENT` is truncated+marked; `capture_file_write` excludes `Read`.
- **Hook golden-file tests** — build event JSON dicts, call `handle(...)` directly: `Write` to `.env` → `HookResult(2)`; `Bash` with a planted AWS key → `HookResult(2)`; benign `Write` → `HookResult(0)` and the ledger + result chunks land; **two PostToolUse events for different tools → two result chunks** (multi-event, catches the I2 single-key bug); a `SubagentStop` final-text event → an exploration chunk; an event with no summary → no-op + exit 0.
- **`open_store()` construction test** — actually call `open_store()` and assert it returns a usable `SqliteStore` (catches the C1 missing-embedder bug, which a pre-built-store `handle` test masks).
- **DB-path unification (C2)** — `resolve_db_path()` returns the same absolute path for `open_store()` and `mcp_server.build_default_store()` given the same env/root; the default is absolute (not CWD-relative).
- **Dict-shaped `tool_response` (I-2)** — a PostToolUse event whose `tool_response` is a dict captures successfully (coerced via `json.dumps`), not a crash/silent-loss.
- **Fail-open** — a capture handler that raises → exit 0, quiet log; a **guard handler that raises → exit 0 + the distinct alert marker on stderr** (I4); assert no exception escapes.
- **Replay regression** — the full existing replay suite stays green after `ingest.py` is refactored onto `capture_tool_result` (key format + `ttl_s=None` byte-identical).
- **WAL/concurrency** — `PRAGMA journal_mode`==`wal`; **two interleaved writers produce no duplicate `seq`** (C2 — the load-bearing assertion, run on min+max supported Python); a blocked writer waits (busy_timeout) rather than raising; the expiry sweep deletes expired, spares pinned/live, is a no-op within `SWEEP_INTERVAL_S`, and is correct under a concurrent inserter; **a `store()` whose embedder raises rolls back and leaves the connection usable for the next write** (round-3 #1 — no lingering write lock).
- **Bash redirect variants** — `cat > .env`, `cat >.env`, `cat>>.env` all blocked (the `\s*` regex); `grep secrets f`, `ls prod/` allowed (round-3 #2).
- A couple of **subprocess smoke tests** (`echo '<json>' | python -m context_curator.hooks.pre_tool_use; echo $?`) confirm stdin→exit wiring end-to-end.

## 6. File structure

```
src/context_curator/
  capture/
    __init__.py
    tool_result.py     # capture_tool_result (canonical)
    file_ledger.py     # capture_file_write
    subagent.py        # capture_subagent_summary
  guard/
    __init__.py
    config.py          # defaults + override loader
    paths.py           # is_sensitive_path
    secrets.py         # scan_secrets
  hooks/
    __init__.py
    _io.py             # read_event, open_store, run_hook (fail-open), HookResult
    post_tool_use.py
    subagent_stop.py
    pre_tool_use.py
  store/
    sqlite_store.py     # MODIFY: WAL + busy_timeout + autocommit/explicit BEGIN IMMEDIATE; cc_meta; sweep_expired()
    paths.py            # NEW: resolve_db_path() — shared by hooks + mcp_server (C2)
  mcp_server.py         # MODIFY: build_default_store() uses resolve_db_path()
  replay/ingest.py      # MODIFY: thin wrapper over capture_tool_result (max_content=None)
.claude/settings.json   # MODIFY: register the three hooks
.gitignore              # MODIFY: *-wal, *-shm
tests/                  # guard/capture/hook/construction/failopen/concurrency tests as in §5
```
`sweep_expired(store)` lives in `store/sqlite_store.py` (it needs the connection); `_io.open_store()` calls it. `capture/tool_result.py` imports `sha1` from `hashlib`.
```

## 7. How this connects forward

- **M3 policy** queries these captured chunks (`file-touch`, `exploration`, `tool:*`) by relevance; the capture tags are the policy's retrieval signal.
- **M4 onload** (`UserPromptSubmit`/`SessionStart`) reads them back via `additionalContext` — the read half.
- The §9 poisoning audit (`cc-guard`) inspects `provenance`/`source` on these chunks; that's why `capture_tool_result` preserves canonical tool-name case in `source`.

---

## Design Critique Log

Three independent adversarial review rounds (fresh reviewer each round, each seeing the prior revision) before presentation.

### Critique Round 1
**Findings (Critical):** `SqliteStore(db_path=…)` omits the **required `embedder`** — every hook would `TypeError`, fail open, and silently disable all of M2 (C1); the `MAX(seq)+1` recency allocation **races under the concurrent writers M2 introduces**, and "WAL makes it safe" is false for both `seq` and `SQLITE_BUSY` (C2/C3); the guardrail's path/secret extraction **misses Bash, MultiEdit, path normalization** (C4). **Important:** the `SubagentStop` `{summary,…}` schema is the project's *own return convention*, **not a hook-payload field** (I1); `call_id` presence in PostToolUse is unverified — keys could collide (I2); the guard needlessly opens a writable store (I3); fail-open is an unweighed bypass (I4); secret regexes false-positive on ordinary code / risk ReDoS (I5); unbounded capture volume with lazy-only expiry (I6); exit-code contract contradicts DESIGN §6's stale "1=block" (I7).
**Resolved:** explicit `embedder=HashingEmbedder()` in `open_store()`; WAL + `busy_timeout` + race-free seq; content-hash key fallback (no `call_id` needed); SubagentStop reframed to capture final-message text (+ optional fenced-JSON); guard takes `needs_store=False`; per-tool extraction table + path normalization; quoted/tightened regexes + `GUARD_MAX_SCAN` cap; explicit live TTL + content cap + a lazy-expiry sweep; exit-2-block reconciled and DESIGN §6 corrected.

### Critique Round 2
**Findings (Critical):** the seq-fix reasoning (`isolation_level="IMMEDIATE"`) is **Python-version-dependent** across the supported `>=3.11` range (C-1, empirically verified working on 3.14 but fragile-by-reasoning); DB-path "unification" was **two copy-pasted CWD-relative literals** — a CWD mismatch silently severs capture from read (C-2). **Important:** the Bash whole-command path-glob **over-blocks** `grep secrets`/`ls prod/` — a new regression inverting I5 (I-1); unknown tool names + **dict-shaped `tool_response`** cause silent allow/crash (I-2); content truncation made replay byte-identity **size-conditional** (I-3); the per-hook sweep `DELETE` is an unrate-limited hot-path writer (I-4); per-check exception isolation is a **marker-less partial fail-open** (I-5). Minors: dangling `§9-risk` ref, `file_write`/`file_touch` naming, file_ledger keyspace divergence, embedder dead-work.
**Resolved:** explicit `BEGIN IMMEDIATE` on an autocommit connection (version-proof); shared absolute `resolve_db_path()` for hooks + MCP server; Bash reduced to secret-scan + narrow write-redirect; `tool_response` coercion + unknown-tool marker; truncation gated to the live path via `max_content=None`; `cc_meta`-gated rate-limited sweep + concurrency test; per-check swallow must flag; refs/naming fixed; keyspace divergence documented.

### Critique Round 3
**Verdict: implementation-ready after a small must-fix list.** The reviewer verified the autocommit change does NOT break the existing store/tests and the seq fix is sound. **Important:** the explicit `BEGIN IMMEDIATE` had **no rollback path** — an exception mid-transaction wedges the long-lived MCP server's write lock (round-2 fix introduced this) (#1); the Bash redirect regex `\s+` **misses the no-space `>.env`** (#2). Minor: stale "DESIGN §6 MODIFY" item (§6 already corrected) (#3); pin `cc_meta` into `_DDL` (#4); state that embedding is computed **before** `BEGIN IMMEDIATE` so the lock isn't held during embedding (#5).
**Resolved:** `try/BEGIN IMMEDIATE/COMMIT except: rollback; raise` + a test that an embedder error leaves the connection usable; regex `\s*` + space/no-space tests; dropped the stale DESIGN item; `cc_meta` added to `_DDL`; embedding pinned before the transaction. No design decision changed.
