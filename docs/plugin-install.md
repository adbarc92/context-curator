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

**Concrete hazard (not just double-firing):** if the dev `settings.json` hooks are active *while*
the plugin is installed and its `cc-mcp` MCP server is running, the dev hooks' `uv run` tries to
rebuild the editable install and fails to replace the now-locked `cc-mcp.exe`
(`os error 32: The process cannot access the file because it is being used by another process`).
On `PreToolUse` that error is **blocking** — it can wedge every Write/Edit/Bash in the session.
Keep `.claude/settings.json` hooks empty (`{}`) whenever the plugin is enabled; if you get wedged,
restore `{}` from a shell *outside* Claude, then reload or restart.

## 4. Smoke test in a throwaway scratch repo

In a fresh scratch repo (with the plugin enabled + Claude restarted):
- SessionStart fires.
- A prompt injects context (or shows `CC ·`).
- A decision record lands under `<scratch>/.context-curator/decisions/`.
- The MCP tool `cc_query` (called through Claude via the connected `cc-mcp` server, not a shell command) returns.
- `cc-statusline` renders (manual statusLine config — see `docs/statusline.md`).

## 5. Recorded exit criteria (the milestone is "done" only when BOTH are observed)

These are Claude-runtime behaviors the spec cannot prove on its own. Fill in the outcomes.
**Tracked in [#14](https://github.com/adbarc92/context-curator/issues/14)** (the remaining
out-of-session verification: criterion (a) below, plus confirming the gui-script hooks stop the
Windows console-window flashing — see [#12](https://github.com/adbarc92/context-curator/issues/12)).

### (a) Does `CLAUDE_PROJECT_DIR` reach the `cc-mcp` server's `os.environ`?
- **How checked (2026-06-25):** settled on two independent grounds, so the temporary stderr diagnostic
  was redundant and has been reverted:
  1. **Authoritative docs** — the [official Claude Code MCP reference](https://code.claude.com/docs/en/mcp.md)
     states Claude Code sets `CLAUDE_PROJECT_DIR` in the **spawned stdio server's environment**, and
     *"Plugin-provided MCP configurations substitute `${CLAUDE_PROJECT_DIR}` directly and don't need
     the default"* — i.e. a bare-`command` plugin server (`{"command": "cc-mcp"}`) receives it
     automatically. This reached parity with hooks in **v2.1.139**; this machine runs **v2.1.190**.
  2. **On-disk proof** — every project with the plugin enabled has its store at
     `<project>\.context-curator\store.db` (e.g. `…\CURRENT\audience`, `…\CURRENT\command-center`,
     `…\INFRASTRUCTURE\halyard`), and **no** `store.db` exists inside the uv-tool venv — which is
     exactly where `resolve_db_path()`'s fallback walk would land if the env var were *not* reaching
     the processes.
- **Outcome:** **YES.** `CLAUDE_PROJECT_DIR` reaches `cc-mcp`; `resolve_db_path()` → the project's
  `.context-curator\store.db`, the **same** store the hooks write. No `CC_DB_PATH` fallback needed.
- **If NO (not the case):** the MCP store would diverge from the hooks'. Fallback would be to pin
  `CC_DB_PATH` in the project `.mcp.json`/env, or accept a global MCP store.

### (b) Does the marketplace/cache install resolve bare `cc-*` on PATH?
- **How checked (2026-06-25):** the plugin is installed and has been exercised across ~8 real
  projects; their MCP logs (`…\claude-cli-nodejs\Cache\<project>\mcp-logs-plugin-context-curator-…`)
  show **18 `"Successfully connected (transport: stdio)"`** records — i.e. Claude resolved the bare
  `cc-mcp` command on PATH and the server connected, repeatedly, via the plugin path (not the dev
  shortcut). The capture hooks also resolved (per-project `store.db` files exist, criterion (a)).
- **Outcome:** **YES.** Bare `cc-*` shims resolve on PATH under the real plugin install.
- **If NO (not the case):** PATH was not inherited — re-run `uv tool update-shell`, restart, or use
  the escape hatch.

### (c) Did the gui-script (pythonw) mitigation stop the Windows console-window flashing (#12)?
- **How checked (2026-06-25):** user observation after weeks of live use across ~8 projects.
- **Outcome:** **PARTIAL — still flashing sometimes.** Moving hooks to `[project.gui-scripts]`
  (pythonw) reduced but did **not** fully eliminate the console-window flash. The residual flicker is
  the upstream-bug surface tracked in **[#12](https://github.com/adbarc92/context-curator/issues/12)**
  (Claude Code spawns the exes without `windowsHide`); **#14 stays open** on this sub-item even though
  exit criteria (a) and (b) are settled. Next investigation lives in #12, not here.

## Privacy
Each repo's `.context-curator/` (store + `decisions/`) is gitignored locally. The committed plugin
files contain only commands + metadata, no content. The MCP + store are local-only — no outbound
calls.
