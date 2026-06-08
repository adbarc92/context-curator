# M7 — Package as a Claude Code Plugin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package ContextCurator's 5 hooks + the `cc-mcp` server as an installable Claude Code plugin (repo-as-plugin) that works end-to-end through the real marketplace/cache install path, with a per-project store.

**Architecture:** The repo *becomes* the plugin — pure-config manifests at the root (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, `.claude-plugin/marketplace.json`) that invoke bare `cc-*` console-script names. The curator is installed machine-globally via `uv tool install --editable <checkout>` (no PyPI, no venv in the plugin cache), which drops the console scripts on PATH. A single new `resolve_db_path` branch keyed on `$CLAUDE_PROJECT_DIR` gives every installed-into repo its own store. The dev `.claude/settings.json` hook block is removed so plugin + dev wirings never double-fire.

**Tech Stack:** Python 3.11+, UV (uv tool install), hatchling build backend, FastMCP (stdio MCP), pytest, PowerShell (verify script). Everything runs via `uv run`.

**Spec:** [`docs/superpowers/specs/2026-06-04-m7-plugin-package-design.md`](../specs/2026-06-04-m7-plugin-package-design.md) (committed `ef523f0`).

---

## Critical context for the implementer (read before Task 1)

1. **PATH is the load-bearing risk.** `uv tool install` does NOT add its bin dir to PATH; that needs a one-time `uv tool update-shell` + a **fresh shell / Claude Code restart**. The committed plugin config MUST use bare `cc-*` names (it can't hardcode a machine-specific absolute path). No static test can prove runtime PATH resolution — that's why Tasks 7–8 are out-of-session manual gates. A green manifest test ≠ "the plugin launches."

2. **This milestone self-modifies the running session's harness.** Task 4 removes the hook block from this repo's `.claude/settings.json`, and Task 2 edits `resolve_db_path`. These are the live hooks injecting context + writing the decision log for *this* session. Expect the current session's hook behavior to shift after those edits land. That is expected, not a bug.

3. **Two exit criteria are out-of-session** (Tasks 7–8): they need a Claude Code restart + fresh shell and their outcomes must be **observed and written into `docs/plugin-install.md`** — not assumed. The milestone is "done" only when both are recorded.

4. **`pytest` launched from inside a Claude session inherits `CLAUDE_PROJECT_DIR`.** After Task 2, env-default `resolve_db_path` tests MUST `monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)` or they'll hit the new branch instead of the `__file__`-walk.

5. **Run commands via `uv run`.** Ignore the `VIRTUAL_ENV` mismatch warning on stderr.

---

## File structure

**New (repo root):**
- `.claude-plugin/plugin.json` — plugin manifest (`name`, `version`, `description`, `author`).
- `.claude-plugin/marketplace.json` — one-entry local marketplace.
- `hooks/hooks.json` — the 5 hook events, each invoking a bare `cc-hook-*` command.
- `.mcp.json` — registers the `context-curator` stdio MCP via `cc-mcp`.
- `scripts/verify-plugin.ps1` — non-interactive verifier + fresh-shell resolution gate.
- `docs/plugin-install.md` — install/verify procedure + the two recorded exit criteria.
- `tests/test_plugin_manifests.py` — static validation of the 4 manifest files + command⊆scripts cross-check.

**Modify:**
- `pyproject.toml` — add 7 entries to `[project.scripts]`.
- `src/context_curator/store/paths.py` — one `$CLAUDE_PROJECT_DIR` branch + docstring.
- `.claude/settings.json` — remove the hook block (→ `{}`).
- `tests/test_resolve_db_path.py` — add the branch test; add `CLAUDE_PROJECT_DIR` delenv to the env-default test.
- `tests/test_hooks_onload_smoke.py` — repoint `test_settings_registers_both_onload_hooks` at `hooks/hooks.json`.
- `README.md` — install pointer to `docs/plugin-install.md`.

**No change** to hook/MCP/policy/store logic beyond the path branch. `.gitignore` already covers `.context-curator/`.

---

## Task 1: Console-script entry points

Adds the 7 new `[project.scripts]` shims (beside the existing `cc-statusline`) so the plugin's bare command names resolve. All 7 target modules already expose a `main()` (verified). The "test" is an importability/callability smoke test for the entry-point targets — no static test can prove PATH resolution (that's Task 8).

**Files:**
- Modify: `pyproject.toml:14-15` (the `[project.scripts]` table)
- Test: `tests/test_entry_points.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_entry_points.py`:

```python
"""Every cc-* console script declared in pyproject must resolve to an importable, callable
main(). Guards against a renamed/moved module silently breaking an entry point (the actual PATH
resolution is a runtime concern verified by scripts/verify-plugin.ps1, not here)."""
import importlib
import tomllib
from pathlib import Path

EXPECTED_SCRIPTS = {
    "cc-statusline": "context_curator.observe.statusline:main",
    "cc-mcp": "context_curator.mcp_server:main",
    "cc-inspect": "context_curator.observe.decision_log:main",
    "cc-hook-session-start": "context_curator.hooks.session_start:main",
    "cc-hook-user-prompt": "context_curator.hooks.user_prompt_submit:main",
    "cc-hook-pre-tool-use": "context_curator.hooks.pre_tool_use:main",
    "cc-hook-post-tool-use": "context_curator.hooks.post_tool_use:main",
    "cc-hook-subagent-stop": "context_curator.hooks.subagent_stop:main",
}


def _scripts() -> dict[str, str]:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]


def test_pyproject_declares_all_expected_scripts():
    assert _scripts() == EXPECTED_SCRIPTS


def test_every_script_target_is_importable_and_callable():
    for name, target in _scripts().items():
        module_path, func = target.split(":")
        module = importlib.import_module(module_path)
        assert callable(getattr(module, func)), f"{name} -> {target} is not callable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_entry_points.py -v`
Expected: FAIL on `test_pyproject_declares_all_expected_scripts` (only `cc-statusline` is declared today, so the dict comparison fails).

- [ ] **Step 3: Add the entry points**

Replace the `[project.scripts]` block in `pyproject.toml` (currently lines 14-15) with:

```toml
[project.scripts]
cc-statusline         = "context_curator.observe.statusline:main"
cc-mcp                = "context_curator.mcp_server:main"
cc-inspect            = "context_curator.observe.decision_log:main"
cc-hook-session-start = "context_curator.hooks.session_start:main"
cc-hook-user-prompt   = "context_curator.hooks.user_prompt_submit:main"
cc-hook-pre-tool-use  = "context_curator.hooks.pre_tool_use:main"
cc-hook-post-tool-use = "context_curator.hooks.post_tool_use:main"
cc-hook-subagent-stop = "context_curator.hooks.subagent_stop:main"
```

> Note: `cc-inspect` is a **CLI** (reads argv), not a hook. `cc-mcp` runs from a plain install because `mcp` is a core dependency (not an extra).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_entry_points.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_entry_points.py
git commit -m "feat(m7): add cc-* console-script entry points for hooks + MCP"
```

---

## Task 2: Per-project store branch in resolve_db_path

Adds the single `$CLAUDE_PROJECT_DIR` branch so the plugin (installed across repos) stores under the *user's* repo, not inside the isolated tool venv. Without this branch the uv-tool install is **actively broken** — the installed package's `__file__` lives under `~/AppData/Roaming/uv/tools/…`, whose ancestors have their own `pyproject.toml`, so the `__file__`-walk would resolve the store inside the tool dir.

**Files:**
- Modify: `src/context_curator/store/paths.py:8-19` (the function body + docstring)
- Test: `tests/test_resolve_db_path.py:14-18` (add delenv) + new test

- [ ] **Step 1: Write the failing test**

In `tests/test_resolve_db_path.py`, first **fix the existing env-default test** so it doesn't hit the new branch under a Claude-session pytest (which inherits `CLAUDE_PROJECT_DIR`). Replace lines 14-18:

```python
def test_default_is_absolute_and_cwd_independent(monkeypatch):
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)   # else the new branch fires (round-3 I2)
    p = resolve_db_path()
    assert Path(p).is_absolute()
    assert p.endswith(os.path.join(".context-curator", "store.db"))
```

Then **add** the new branch test at the end of the file:

```python
def test_claude_project_dir_branch(monkeypatch, tmp_path):
    # Plugin-across-repos: $CLAUDE_PROJECT_DIR (and not CC_DB_PATH) -> store under that repo.
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    p = resolve_db_path()
    assert p == str((tmp_path / ".context-curator" / "store.db").resolve())


def test_cc_db_path_still_wins_over_claude_project_dir(monkeypatch, tmp_path):
    # CC_DB_PATH must keep priority over the new branch (existing overrides intact).
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "explicit.db"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "other_repo"))
    assert resolve_db_path() == str((tmp_path / "explicit.db").resolve())


def test_claude_project_dir_matches_cc_db_path_for_same_dir(monkeypatch, tmp_path):
    # Locks hook<->MCP consistency (round-1 M1): the path the CLAUDE_PROJECT_DIR branch yields for
    # <proj> must byte-equal what an explicit CC_DB_PATH=<proj>/.context-curator/store.db yields.
    expected_db = tmp_path / ".context-curator" / "store.db"
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    via_branch = resolve_db_path()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CC_DB_PATH", str(expected_db))
    via_env = resolve_db_path()
    assert via_branch == via_env
```

> The spec descopes a `home`-branch test (YAGNI — unreachable in-repo, the walk always finds this repo's `pyproject.toml`; pre-existing unchanged behavior).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_resolve_db_path.py -v`
Expected: FAIL — `test_claude_project_dir_branch` and `test_claude_project_dir_matches_cc_db_path_for_same_dir` fail (the branch doesn't exist; `resolve_db_path` ignores `CLAUDE_PROJECT_DIR` and falls to the `__file__`-walk).

- [ ] **Step 3: Add the branch**

In `src/context_curator/store/paths.py`, insert the branch **after** the `CC_DB_PATH` check and **before** the `__file__`-walk, and update the docstring. The full function becomes:

```python
def resolve_db_path() -> str:
    """Absolute, CWD-independent DB path. `$CC_DB_PATH` wins; else (plugin-across-repos)
    `$CLAUDE_PROJECT_DIR/.context-curator/store.db`; else `<project>/.context-curator/store.db`
    (project root = nearest ancestor with .git/pyproject.toml); else `~/.context-curator/store.db`.
    Absolute so hook subprocesses and the server never resolve to different files. The
    $CLAUDE_PROJECT_DIR branch is load-bearing for the uv-tool install: the installed package's
    __file__ lives in the isolated tool venv, whose ancestors have their own pyproject.toml, so the
    walk below would otherwise resolve the store inside the tool dir, never the user's repo."""
    env = os.environ.get("CC_DB_PATH")
    if env:
        return str(Path(env).expanduser().resolve())
    proj = os.environ.get("CLAUDE_PROJECT_DIR")   # plugin runs across repos -> per-project store
    if proj:
        return str((Path(proj) / ".context-curator" / "store.db").resolve())
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return str(parent / ".context-curator" / "store.db")
    return str(Path.home() / ".context-curator" / "store.db")
```

- [ ] **Step 4: Run the file's tests, then the full suite**

Run: `uv run pytest tests/test_resolve_db_path.py -v`
Expected: PASS (all 5 tests).

Run: `uv run pytest -q`
Expected: PASS — full suite green. (If `test_hooks_onload_smoke.py::test_settings_registers_both_onload_hooks` is still green here, that's fine; Task 4 changes it.)

- [ ] **Step 5: Commit**

```bash
git add src/context_curator/store/paths.py tests/test_resolve_db_path.py
git commit -m "feat(m7): per-project store via \$CLAUDE_PROJECT_DIR branch in resolve_db_path"
```

---

## Task 3: Plugin manifest files (repo-as-plugin)

Creates the four pure-config files at the repo root. TDD order: write the static validation test first (it fails because the files don't exist), then create the files. The test also cross-checks that every hook/MCP command is one of the declared `cc-*` scripts (name-typo regression guard).

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `hooks/hooks.json`
- Create: `.mcp.json`
- Create: `.claude-plugin/marketplace.json`
- Test: `tests/test_plugin_manifests.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_manifests.py`:

```python
"""Static validation of the repo-as-plugin manifests. NOTE (round-2 M3): a green test here proves
the JSON is well-formed and the command names match declared entry points -- it does NOT prove the
plugin launches (PATH/exec resolution is runtime-only, verified by scripts/verify-plugin.ps1)."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOOK_EVENTS = {"PreToolUse", "PostToolUse", "SubagentStop", "SessionStart", "UserPromptSubmit"}


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _declared_scripts() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(pyproject["project"]["scripts"].keys())


def _hook_commands(hooks_json: dict) -> list[str]:
    cmds = []
    for event_groups in hooks_json["hooks"].values():
        for group in event_groups:
            for hook in group["hooks"]:
                cmds.append(hook["command"])
    return cmds


def test_plugin_manifest_has_required_name():
    manifest = _load(".claude-plugin/plugin.json")
    assert manifest["name"] == "context-curator"


def test_hooks_json_registers_all_five_events():
    hooks = _load("hooks/hooks.json")["hooks"]
    assert HOOK_EVENTS.issubset(hooks.keys())


def test_mcp_json_registers_cc_mcp():
    mcp = _load(".mcp.json")
    assert mcp["mcpServers"]["context-curator"]["command"] == "cc-mcp"


def test_marketplace_lists_the_plugin():
    market = _load(".claude-plugin/marketplace.json")
    names = {p["name"] for p in market["plugins"]}
    assert "context-curator" in names


def test_every_hook_and_mcp_command_is_a_declared_cc_script():
    # Regression guard: no `uv run`/cwd-dependent command, no missing entry point.
    declared = _declared_scripts()
    commands = _hook_commands(_load("hooks/hooks.json"))
    commands.append(_load(".mcp.json")["mcpServers"]["context-curator"]["command"])
    for cmd in commands:
        assert cmd in declared, f"{cmd!r} is not a declared [project.scripts] entry"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_manifests.py -v`
Expected: FAIL — `FileNotFoundError` (none of the manifest files exist yet).

- [ ] **Step 3: Create `.claude-plugin/plugin.json`**

```json
{
  "name": "context-curator",
  "version": "0.0.1",
  "description": "Relevance-driven working-set policy and curated context store for Claude Code.",
  "author": { "name": "Alex Barclay" }
}
```

- [ ] **Step 4: Create `hooks/hooks.json`**

Mirrors the 5 active events from `.claude/settings.json`, preserving the `PreToolUse` matcher, but with bare console-script commands (exec form — no `uv run`, no shell). The empty `Stop: []` event is dropped (it does nothing).

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [ { "type": "command", "command": "cc-hook-pre-tool-use" } ] }
    ],
    "PostToolUse": [
      { "hooks": [ { "type": "command", "command": "cc-hook-post-tool-use" } ] }
    ],
    "SubagentStop": [
      { "hooks": [ { "type": "command", "command": "cc-hook-subagent-stop" } ] }
    ],
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "cc-hook-session-start" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "cc-hook-user-prompt" } ] }
    ]
  }
}
```

- [ ] **Step 5: Create `.mcp.json`**

No `CC_DB_PATH` env override (round-2 I2: an unexpanded `${CLAUDE_PROJECT_DIR}` would poison `CC_DB_PATH`). The MCP relies on the same `resolve_db_path` `$CLAUDE_PROJECT_DIR` branch as the hooks.

```json
{
  "mcpServers": {
    "context-curator": {
      "command": "cc-mcp"
    }
  }
}
```

- [ ] **Step 6: Create `.claude-plugin/marketplace.json`**

One-entry local marketplace so `/plugin marketplace add <root>` → `/plugin install` works. `source` is relative to the **marketplace root** (the repo root — the dir containing `.claude-plugin/`), so `"./"` = the repo root, which is the plugin root (where `.claude-plugin/plugin.json` lives).

```json
{
  "name": "context-curator",
  "owner": { "name": "Alex Barclay" },
  "plugins": [
    {
      "name": "context-curator",
      "source": "./",
      "description": "Relevance-driven working-set policy and curated context store for Claude Code."
    }
  ]
}
```

> **Implementer check:** this is the one manifest whose exact schema the static test (`test_marketplace_lists_the_plugin`) only partially validates (name only) — its `owner`/`source` fields are exercised for real by `/plugin install` in Task 7. Before committing, cross-check the `owner` and `source` field shapes against the current Claude Code plugin-marketplace reference (the spec verified the repo-as-plugin *layout* against it; confirm these two field formats haven't drifted). If `/plugin install` fails in Task 7, this file is the first suspect.

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_manifests.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 8: Commit**

```bash
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json .mcp.json tests/test_plugin_manifests.py
git commit -m "feat(m7): add repo-as-plugin manifests (plugin/hooks/mcp/marketplace)"
```

---

## Task 4: Remove the dev hook block + repoint the onload smoke test

The plugin is now the single source of hook wiring. Claude Code **merges** plugin hooks with project hooks, so leaving the dev `.claude/settings.json` block in place would fire every event twice (double injection + double decision-log records). Remove the block (→ `{}`, kept as the seam for future non-hook settings). The existing test that asserted the block exists must be repointed at `hooks/hooks.json`.

> **Self-modification warning:** this edits the live session's own hooks. After this lands, this session's context-injection + decision-logging come only from the plugin (if enabled), not from `.claude/settings.json`. Expected.

**Files:**
- Modify: `.claude/settings.json` (replace entire contents)
- Modify: `tests/test_hooks_onload_smoke.py:18-27` (repoint the test)

- [ ] **Step 1: Repoint the failing test first**

In `tests/test_hooks_onload_smoke.py`, replace `test_settings_registers_both_onload_hooks` (lines 18-27) with a test that reads the **plugin** manifest:

```python
def test_plugin_registers_both_onload_hooks():
    # M7: the plugin (hooks/hooks.json) is the single source of hook wiring; .claude/settings.json
    # no longer carries hooks (it would double-fire alongside the plugin). Assert the plugin wires
    # the two onload events to their cc-* console scripts.
    hooks_path = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"
    hooks = json.loads(hooks_path.read_text())["hooks"]
    ups_cmd = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    ss_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert ups_cmd == "cc-hook-user-prompt"
    assert ss_cmd == "cc-hook-session-start"
```

- [ ] **Step 2: Run the repointed test to verify it passes**

Run: `uv run pytest tests/test_hooks_onload_smoke.py::test_plugin_registers_both_onload_hooks -v`
Expected: PASS (`hooks/hooks.json` from Task 3 already has these commands). This test passes *before* the settings.json edit because it no longer reads settings.json.

- [ ] **Step 3: Remove the hook block from `.claude/settings.json`**

Replace the entire contents of `.claude/settings.json` with:

```json
{}
```

- [ ] **Step 4: Verify the full suite stays green**

Run: `uv run pytest -q`
Expected: PASS — full suite green. Confirm no test still reads `.claude/settings.json`'s `hooks` key (grep guard below).

Run: `uv run pytest tests/test_hooks_onload_smoke.py -v`
Expected: PASS — all tests in the file (the other smoke tests drive the hooks via `subprocess`/`ups.handle` directly and are unaffected by the settings.json change).

- [ ] **Step 5: Commit**

```bash
git add .claude/settings.json tests/test_hooks_onload_smoke.py
git commit -m "feat(m7): remove dev hook block (plugin is sole wiring); repoint onload test"
```

---

## Task 5: Non-interactive verify script

`scripts/verify-plugin.ps1` automates the deterministic parts of the success test **without** the Claude binary, and doubles as the §7.1 fresh-shell resolution gate. It must: (1) gate on `cc-*` resolution via `Get-Command` (NOT `cc-mcp --help` — FastMCP `run()` has no argparse and would block/error even on a correct install); (2) pipe a minimal `UserPromptSubmit` event into `cc-hook-user-prompt` with `CLAUDE_PROJECT_DIR` set to a temp dir and assert exit 0 **AND** a record file at the *resolved* path; (3) start `cc-mcp` and assert process liveness (not a full JSON-RPC handshake — brittle in PowerShell, reserved for the manual smoke).

**Files:**
- Create: `scripts/verify-plugin.ps1`

- [ ] **Step 1: Create the script**

```powershell
#requires -Version 5.1
# scripts/verify-plugin.ps1
# Non-interactive M7 plugin verifier + fresh-shell PATH-resolution gate.
# Proves the deterministic surface WITHOUT the Claude binary. Run from a FRESHLY-SPAWNED shell
# (not the dev terminal that already has PATH) after: uv tool install --editable . ; uv tool
# update-shell ; restart. Exits nonzero on any failure.
$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Error "VERIFY FAIL: $msg"; exit 1 }

# --- Gate 1: PATH resolution (round-3 C1: resolution only, do NOT execute the server) ---
foreach ($cmd in @('cc-mcp', 'cc-hook-user-prompt')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "$cmd does not resolve on PATH. Run 'uv tool update-shell' and restart the shell, or use the absolute-shim escape hatch in docs/plugin-install.md."
    }
}
Write-Host "OK: cc-mcp and cc-hook-user-prompt resolve on PATH."

# --- Gate 2: the hook runs and writes a decision record at the RESOLVED path ---
$proj = Join-Path $env:TEMP ("cc-verify-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $proj -Force | Out-Null
try {
    $env:CLAUDE_PROJECT_DIR = $proj
    $sid = 'verify'
    $event = '{"prompt":"authenticate authorize user session","session_id":"' + $sid +
             '","hook_event_name":"UserPromptSubmit"}'
    $event | & cc-hook-user-prompt
    if ($LASTEXITCODE -ne 0) { Fail "cc-hook-user-prompt exited $LASTEXITCODE" }

    # Assert the resolved path deterministically (round-1 I4: not "a file appeared").
    $rec = Join-Path $proj ".context-curator/decisions/decisions-$sid.jsonl"
    if (-not (Test-Path $rec)) { Fail "no decision record at resolved path $rec" }
    Write-Host "OK: cc-hook-user-prompt exit 0 and wrote $rec"
} finally {
    Remove-Item Env:\CLAUDE_PROJECT_DIR -ErrorAction SilentlyContinue
}

# --- Gate 3: cc-mcp process liveness (round-2 M2: liveness, not a full handshake) ---
$mcp = Start-Process -FilePath 'cc-mcp' -PassThru -NoNewWindow `
        -RedirectStandardError (Join-Path $proj 'mcp.err.log')
Start-Sleep -Seconds 2
if ($mcp.HasExited) {
    Fail "cc-mcp exited prematurely (code $($mcp.ExitCode)); see $proj\mcp.err.log"
}
Stop-Process -Id $mcp.Id -Force
Write-Host "OK: cc-mcp stayed up (process liveness)."

Remove-Item -Recurse -Force $proj -ErrorAction SilentlyContinue
Write-Host "`nVERIFY PASS: deterministic plugin surface is healthy."
exit 0
```

- [ ] **Step 2: Run the script (requires `uv tool install` first)**

This step depends on the tool being installed and on PATH. If you have not yet run the install (Task 7 covers it), this gate will correctly fail at Gate 1 — that is the intended fresh-shell behavior, not a script bug. To run it meaningfully now, first run from a terminal:

```powershell
uv tool install --editable .
uv tool update-shell
```

Then **restart the shell** and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-plugin.ps1
```

Expected (after install + fresh shell): three `OK:` lines then `VERIFY PASS`. Exit code 0.

> If you cannot restart the shell within this session, mark this step as deferred to Task 7 and note it — do NOT claim the gate passed without seeing `VERIFY PASS`.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify-plugin.ps1
git commit -m "feat(m7): add verify-plugin.ps1 (resolution gate + hook/MCP smoke)"
```

---

## Task 6: Install/verify documentation + README pointer

`docs/plugin-install.md` documents the full install procedure (the **real** marketplace/cache path is the success criterion; `--plugin-dir` is only a dev convenience), the PATH requirement made loud, the absolute-shim escape hatch, and the two recorded exit criteria (with placeholders to be filled in Tasks 7–8). The README gets a one-line pointer.

**Files:**
- Create: `docs/plugin-install.md`
- Modify: `README.md` (add an install pointer)

- [ ] **Step 1: Create `docs/plugin-install.md`**

```markdown
# Installing ContextCurator as a Claude Code plugin

ContextCurator ships as a **repo-as-plugin**: the manifests at the repo root
(`.claude-plugin/`, `hooks/hooks.json`, `.mcp.json`) are pure config that call bare `cc-*`
console scripts. The curator itself is installed machine-globally with `uv tool install` — the
plugin carries no Python venv.

## 1. Install the console scripts (PATH is load-bearing)

```powershell
uv tool install --editable <ABS_CHECKOUT>   # e.g. the path to this repo
uv tool update-shell                          # one-time: puts uv's bin dir on PATH
# >>> RESTART your shell AND Claude Code <<<  so subprocesses inherit the updated PATH
```

`--editable` keeps `.py` edits live. **Re-run `uv tool install` whenever entry points or deps
change** (M7 added 7 scripts). `mcp` is a core dependency, so `cc-mcp` works from a plain install;
the semantic path needs the `embed` extra: `uv tool install --editable "<ABS_CHECKOUT>[embed]"`.

### Why the restart matters
`uv tool install` does NOT add its bin dir to PATH by itself. Claude Code runs hook/MCP commands
through a shell, so a bare `cc-*` name resolves via PATH (Windows: PATHEXT → `.exe`) — but only
after `uv tool update-shell` + a fresh shell. This is the M6 statusline lesson; do not skip it.

### Resolution gate (run from a FRESH shell, not your already-configured dev terminal)
```powershell
Get-Command cc-mcp, cc-hook-user-prompt        # PowerShell
# command -v cc-mcp cc-hook-user-prompt        # POSIX
```
Do NOT probe with `cc-mcp --help` — FastMCP `run()` has no argparse and will block/error even on a
correct install (a false red). `scripts/verify-plugin.ps1` automates this gate plus a hook + MCP
smoke; run it from a fresh shell and require `VERIFY PASS`.

### Escape hatch for locked-down environments
If you cannot get the uv bin dir on PATH, override in your own project `.claude/settings.json`
with absolute shim paths (the `docs/statusline.md` pattern) instead of the bare names. The
committed plugin cannot hardcode a machine-specific absolute path.

## 2. Enable the plugin via the marketplace/cache path (the real success criterion)

```
/plugin marketplace add <ABS_CHECKOUT>
/plugin install context-curator@context-curator
```
Then restart Claude Code. (`--plugin-dir <checkout>` works for dev but is NOT the success
criterion — it keeps the dev venv in reach and hides the cache-copy behavior real users hit.)

## 3. In-repo developers: never run both wirings

This repo's `.claude/settings.json` no longer wires hooks (it would double-fire alongside the
plugin). In-repo devs enable the plugin like everyone else (after `uv tool install --editable .`,
so edits stay live). `uv run pytest` is unaffected — tests import modules directly.

## 4. Smoke test in a throwaway scratch repo

In a fresh scratch repo (with the plugin enabled + Claude restarted):
- SessionStart fires.
- A prompt injects context (or shows `CC ·`).
- A decision record lands under `<scratch>/.context-curator/decisions/`.
- `cc_query` via the MCP returns.
- `cc-statusline` renders (manual statusLine config — see `docs/statusline.md`).

## 5. Recorded exit criteria (the milestone is "done" only when BOTH are observed)

These are Claude-runtime behaviors the spec cannot prove on its own. Fill in the outcomes:

### (a) Does `CLAUDE_PROJECT_DIR` reach the `cc-mcp` server's `os.environ`?
- **How checked:** _<observed under a real session — cc-mcp logs its env on startup to stderr>_
- **Outcome:** _<PENDING — fill in Task 8>_
- **If NO:** the MCP store diverges from the hooks'. Mandatory fallback — pin `CC_DB_PATH` in your
  project `.mcp.json`/env, or accept a global MCP store.

### (b) Does the marketplace/cache install resolve bare `cc-*` on PATH?
- **How checked:** _<`/plugin install` via cache, then fresh-shell Get-Command + a real hook fire>_
- **Outcome:** _<PENDING — fill in Task 7>_
- **If NO:** PATH was not inherited — re-run `uv tool update-shell`, restart, or use the escape hatch.

## Privacy
Each repo's `.context-curator/` (store + `decisions/`) is gitignored locally. The committed plugin
files contain only commands + metadata, no content. The MCP + store are local-only — no outbound
calls.
```

- [ ] **Step 2: Add the README pointer**

Add a line to `README.md` (under the install/usage section, or near the top if there is none):

```markdown
**Install as a Claude Code plugin:** see [docs/plugin-install.md](docs/plugin-install.md).
```

- [ ] **Step 3: Commit**

```bash
git add docs/plugin-install.md README.md
git commit -m "docs(m7): plugin install/verify procedure + recorded exit criteria + README pointer"
```

---

## Task 7: Out-of-session — real install via the marketplace/cache path (exit criterion b)

**This task requires a Claude Code restart + fresh shell and cannot complete inside the session that wrote the plan.** It exercises the real cache path (the actual success test) and records exit criterion (b).

**Files:**
- Modify: `docs/plugin-install.md` (fill in exit criterion (b) outcome)

- [ ] **Step 1: Full-suite green + clean working tree before leaving the session**

Run: `uv run pytest -q`
Expected: PASS. Then `git status` should be clean (all of Tasks 1–6 committed).

- [ ] **Step 2: Install the tool + update PATH**

```powershell
uv tool install --editable <ABS_CHECKOUT>
uv tool update-shell
```

- [ ] **Step 3: Restart the shell AND Claude Code.**

- [ ] **Step 4: Run the fresh-shell resolution gate**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-plugin.ps1
```
Expected: `VERIFY PASS`. If it fails at Gate 1, fix PATH (re-run `uv tool update-shell`, restart) or apply the escape hatch — do not proceed until it passes.

- [ ] **Step 5: Enable via the marketplace/cache path in a throwaway scratch repo**

```
/plugin marketplace add <ABS_CHECKOUT>
/plugin install context-curator@context-curator
```
Restart Claude Code. Open the scratch repo and confirm the §4 smoke list: SessionStart fires, a prompt injects context, a decision record lands under `<scratch>/.context-curator/decisions/`, `cc_query` via the MCP returns.

- [ ] **Step 6: Record exit criterion (b)**

In `docs/plugin-install.md` §5(b), replace the `PENDING` outcome with what you observed (bare `cc-*` resolved on PATH via the cache install: yes/no, and any fix needed).

- [ ] **Step 7: Commit**

```bash
git add docs/plugin-install.md
git commit -m "docs(m7): record exit criterion (b) — cache-path cc-* PATH resolution observed"
```

---

## Task 8: Out-of-session — confirm CLAUDE_PROJECT_DIR reaches the MCP (exit criterion a)

**Also requires a real running Claude session with the MCP live.** Confirms whether the MCP subprocess inherits `CLAUDE_PROJECT_DIR` (it does for hooks; unverified for MCP). Determines whether the §5 fallback (pin `CC_DB_PATH`) is mandatory.

**Files:**
- Modify: `docs/plugin-install.md` (fill in exit criterion (a) outcome)
- Possibly modify: `src/context_curator/mcp_server.py` (temporary startup stderr log — revert after observing)

- [ ] **Step 1: Add a temporary startup diagnostic to `cc-mcp`**

In `src/context_curator/mcp_server.py::main`, before `build_mcp().run()`, add a one-line stderr log of whether `CLAUDE_PROJECT_DIR` is present and the resolved DB path:

```python
def main() -> None:
    import os
    import sys
    from context_curator.store.paths import resolve_db_path
    print(f"cc-mcp startup: CLAUDE_PROJECT_DIR={os.environ.get('CLAUDE_PROJECT_DIR')!r} "
          f"db={resolve_db_path()}", file=sys.stderr, flush=True)
    build_mcp().run()
```

Re-run `uv tool install --editable <ABS_CHECKOUT>` (entry-point code changed → reinstall), restart Claude Code with the plugin enabled in a scratch repo.

- [ ] **Step 2: Observe the MCP server stderr under a real session**

Trigger an MCP tool call (e.g. `cc_query`) so the server is live, then inspect the MCP server log (Claude Code surfaces MCP stderr in its logs). Confirm whether `CLAUDE_PROJECT_DIR` is non-`None` and whether `db=` points at `<scratch>/.context-curator/store.db` (matching the hooks) vs. a path inside the uv tool dir.

- [ ] **Step 3: Record exit criterion (a) and decide on the fallback**

In `docs/plugin-install.md` §5(a), replace the `PENDING` outcome. If `CLAUDE_PROJECT_DIR` did NOT reach the MCP, make the "pin `CC_DB_PATH`" fallback the documented default and note it prominently in §2/§5.

- [ ] **Step 4: Revert the temporary diagnostic**

Remove the stderr `print` added in Step 1 (restore `main()` to `build_mcp().run()`), then re-run `uv tool install --editable <ABS_CHECKOUT>` so the clean version is installed.

- [ ] **Step 5: Final suite + commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add docs/plugin-install.md src/context_curator/mcp_server.py
git commit -m "docs(m7): record exit criterion (a) — CLAUDE_PROJECT_DIR-reaches-MCP observed"
```

- [ ] **Step 6: M7 complete — finish the branch**

Both exit criteria observed and recorded → M7 is done. Use `superpowers:finishing-a-development-branch` to open the PR. Remove the `ACTIVE-SESSION-PICKUP` block from `CLAUDE.md` (it's stale once this branch merges) and delete `docs/Session-Pickup-2026-06-04.md`.

---

## Spec coverage map (self-review)

| Spec section | Task |
|---|---|
| §2 console-script entry points | Task 1 |
| §4 per-project store branch | Task 2 |
| §3 plugin layout (4 manifests) | Task 3 |
| §5 MCP wiring (`.mcp.json`, no CC_DB_PATH override) | Task 3 (Step 5) + Task 8 (verify env reaches MCP) |
| §6 dev↔plugin coexistence (remove settings.json hooks) | Task 4 |
| §7.1 fresh-shell resolution gate | Task 5 + Task 7 (Step 4) |
| §7.2 marketplace/cache install (real success test) | Task 7 |
| §7.3 smoke each surface | Task 6 (doc) + Task 7 (Step 5) |
| §7.4 recorded exit criteria (a)+(b) | Task 8 (a) + Task 7 (b) |
| §7.5 verify-plugin.ps1 | Task 5 |
| §8 resolve_db_path test (named, delenv, descope home) | Task 2 |
| §8 test_plugin_manifests.py | Task 3 |
| §8 entry-point smoke | Task 1 |
| §8 retire/repoint settings.json test | Task 4 |
| §8 full suite stays green | Tasks 2, 4, 8 (Step 5) |
| §9 privacy | Task 6 (doc) |
| §10 file structure | all tasks |
| §11 out-of-scope | (not built — recorded only) |
```
