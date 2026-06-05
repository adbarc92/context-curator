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
- The MCP tool `cc_query` (called through Claude via the connected `cc-mcp` server, not a shell command) returns.
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
