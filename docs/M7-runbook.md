# M7 — Out-of-Session Install & Verification Runbook

Step-by-step to finish M7 (package ContextCurator as a Claude Code plugin). These steps cannot run
inside a single Claude session — they need a `uv tool install`, fresh shells, and Claude Code
restarts. Steps are marked **[YOU]** (need your hands) or **[CLAUDE]** (the next Claude session does
them once you bring back observations).

- **Branch:** `feat/m7-plugin-package` (local-only — not pushed yet; pushed at PR time in Phase 6)
- **Repo:** `D:\MajorProjects\INFRASTRUCTURE\context-curator`
- **Env:** Windows 11, PowerShell 7+
- Canonical reference: [`docs/plugin-install.md`](plugin-install.md). Session state:
  [`docs/Session-Pickup-2026-06-04.md`](Session-Pickup-2026-06-04.md).

---

## ✅ Already done (no action)
- M7 branch synced with `main`, so the Windows console-flicker fix (`43aca4e`, PR #11) is included —
  install testing won't flash terminal windows. Tree clean, 343 tests green.

---

## Phase 1 — Install the console scripts + fix PATH  **[YOU]**

In any PowerShell, from anywhere:

```powershell
uv tool install --editable D:\MajorProjects\INFRASTRUCTURE\context-curator
uv tool update-shell
```

- `--editable` keeps `.py` edits live (no reinstall on code changes — **except** when entry points or
  deps change, e.g. the Phase 5 diagnostic, which requires a reinstall).
- Optional semantic (bge) path instead of recency-only:
  `uv tool install --editable "D:\MajorProjects\INFRASTRUCTURE\context-curator[embed]"`.

**Then fully restart (load-bearing — do not skip):**
1. Close **every** PowerShell window; open a brand-new one.
2. Quit and reopen **Claude Code** (so spawned hook/MCP subprocesses inherit the updated PATH).

---

## Phase 2 — Fresh-shell resolution gate  **[YOU]**

In a **brand-new** PowerShell (not one open before `update-shell`):

```powershell
Get-Command cc-mcp, cc-hook-user-prompt
powershell -NoProfile -ExecutionPolicy Bypass -File D:\MajorProjects\INFRASTRUCTURE\context-curator\scripts\verify-plugin.ps1
```

- **Success:** both commands resolve; the script prints three `OK:` lines then **`VERIFY PASS`**.
- **If `Get-Command` finds nothing / `VERIFY FAIL` at Gate 1:** PATH didn't update. Re-run
  `uv tool update-shell`, fully restart the shell, retry. Still failing → escape hatch: override with
  absolute shim paths in your own `.claude/settings.json` (see [`docs/statusline.md`](statusline.md)).
- ⚠️ Do **not** probe with `cc-mcp --help` — it has no argparse and will hang/error even on a correct
  install (false failure).

**→ Records exit criterion (b), part 1. Note whether you got `VERIFY PASS`.**

---

## Phase 3 — Install via the marketplace/cache path (the real success test)  **[YOU]**

Exercises the path real users hit (Claude copies the plugin to `~/.claude/plugins/cache`), not the
dev shortcut. Inside Claude Code:

```
/plugin marketplace add D:\MajorProjects\INFRASTRUCTURE\context-curator
/plugin install context-curator@context-curator
```

Then **restart Claude Code** once more.

**→ Records exit criterion (b), part 2. Note whether the install succeeded and the plugin enabled.**

---

## Phase 4 — Smoke test in a throwaway scratch repo  **[YOU]**

Create a disposable repo and open it in Claude Code (plugin enabled):

```powershell
mkdir $env:TEMP\cc-scratch; cd $env:TEMP\cc-scratch; git init
```

In a Claude session inside that scratch repo, confirm each surface and **note what you observe**:
- [ ] **SessionStart** fires (pinned-context injection may appear at session start).
- [ ] Submit a prompt → relevant context is injected (or the statusline shows `CC ·`).
- [ ] A decision-record file appears under `%TEMP%\cc-scratch\.context-curator\decisions\`.
- [ ] Ask Claude to call the **`cc_query`** MCP tool → it returns a result.
- [ ] (Optional) `cc-statusline` renders if wired manually (see [`docs/statusline.md`](statusline.md)).

---

## Phase 5 — Exit criterion (a): does `CLAUDE_PROJECT_DIR` reach the MCP?  **[CLAUDE + YOU]**

Determines whether the MCP server stores to the **same** DB as the hooks, or diverges (in which case
pinning `CC_DB_PATH` becomes the documented default). Needs a one-line temporary diagnostic in
`cc-mcp`, so it is collaborative:

1. **[CLAUDE, next session]** Add a temporary stderr line to `cc-mcp`'s `main()` logging
   `CLAUDE_PROJECT_DIR` + the resolved DB path (plan Task 8, Step 1).
2. **[YOU]** Re-run `uv tool install --editable D:\MajorProjects\INFRASTRUCTURE\context-curator`
   (entry-point code changed), restart Claude in the scratch repo, trigger an MCP call, and report what
   the MCP server's stderr line said — whether `CLAUDE_PROJECT_DIR` was set, and whether `db=` pointed
   at the scratch repo vs. the uv tool dir.
3. **[CLAUDE]** Record the outcome in [`docs/plugin-install.md`](plugin-install.md) §5(a), set the
   `CC_DB_PATH` fallback as default if needed, then **revert the diagnostic** (you reinstall once more
   to land the clean version).

---

## Phase 6 — Close out M7  **[CLAUDE]**

Once Phase 2–4 observations are back and Phase 5 is done:
- Fill in both `PENDING` outcomes in `docs/plugin-install.md` §5.
- `superpowers:finishing-a-development-branch` → push `feat/m7-plugin-package`, open the M7 PR.
- Remove the `ACTIVE-SESSION-PICKUP` block from `CLAUDE.md` and delete the pickup doc + this runbook
  (M7 lands).

---

## Minimum to report back to proceed
After Phases 1–4, tell the next Claude session:
1. Did **`VERIFY PASS`** print (Phase 2)?
2. Did **`/plugin install`** succeed and the plugin enable (Phase 3)?
3. In the scratch repo (Phase 4): did **context inject**, did a **decision-record file appear**, did
   **`cc_query`** return?

Then provide the Phase 5 observation. The cleanest moment to start is **right after a Claude Code
restart** — the next session reads the pickup doc and continues from here.
