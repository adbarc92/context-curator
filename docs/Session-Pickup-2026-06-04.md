# Session Pickup — 2026-06-04 (updated 2026-06-07)

**Branch:** `feat/m7-plugin-package` (off `main`) — **pushed; [PR #13](https://github.com/adbarc92/context-curator/pull/13) open to `main` (release `v0.0.2`).**
**Plan:** [`docs/superpowers/plans/2026-06-04-m7-plugin-package.md`](superpowers/plans/2026-06-04-m7-plugin-package.md) · **Spec:** [`…/specs/2026-06-04-m7-plugin-package-design.md`](superpowers/specs/2026-06-04-m7-plugin-package-design.md)
**Out-of-session runbook:** [`docs/M7-runbook.md`](M7-runbook.md)

## Where we are
M7 (repo-as-plugin) is **implemented, validated end-to-end, and shipped as `v0.0.2`** (tag pushed,
PR #13 open). The plugin was confirmed working in a real scratch repo (per-project store, hooks
firing, capture, automatic injection with working-set paging) and by an automated smoke. The only
remaining work is **out-of-session and user-driven** — it cannot be done inside a Claude session.

## What's left (all tracked — nothing else actionable in-session)
1. **Merge [PR #13](https://github.com/adbarc92/context-curator/pull/13).** (Review/merge.)
2. **[#14](https://github.com/adbarc92/context-curator/issues/14) — out-of-session verification** (needs Claude quit/reinstall/restart; steps in the runbook):
   - Confirm the gui-script hooks stop the Windows console-window flashing (reinstall regenerates the
     `cc-hook-*` launchers as pythonw → no window).
   - **Exit criterion (a):** does `CLAUDE_PROJECT_DIR` reach the `cc-mcp` server's `os.environ`?
     (temp stderr diagnostic → observe → record in [`docs/plugin-install.md`](plugin-install.md) §5(a)
     → revert). If NO, pinning `CC_DB_PATH` becomes the documented default.
   - Both `PENDING`s in `plugin-install.md` §5 stay open until recorded; criterion (b) is effectively
     confirmed (verify-plugin.ps1 passes + scratch repo worked).
3. After #14 is recorded, M7 is fully done → it merges via PR #13.

## Key context the diff doesn't carry
- **The flicker root cause is upstream Claude Code** ([#12](https://github.com/adbarc92/context-curator/issues/12)):
  it spawns hook/MCP **console-subsystem** `.exe`s on Windows with no `windowsHide`, so each per-event
  hook flashes a console window. **Fix shipped:** the 5 `cc-hook-*` entry points are now
  `[project.gui-scripts]` (pythonw / GUI subsystem → no auto-console). Verified empirically that a
  pythonw process still reads piped stdin / writes stdout (Claude pipes those handles), so the hook
  contract holds. `cc-mcp`/`cc-inspect`/`cc-statusline` stay console scripts; move `cc-mcp` too if a
  persistent MCP window shows up (same proven-safe mechanism).
- **"Never run both wirings" is a hard hazard, not just double-firing:** with the dev
  `.claude/settings.json` hooks active *and* the plugin installed + `cc-mcp` running, the dev hooks'
  `uv run` can't replace the locked `cc-mcp.exe` (`os error 32`) and on `PreToolUse` that **blocks
  every Write/Edit/Bash**. Recover by restoring `settings.json` to `{}` from a shell *outside* Claude.
  (This is why M7's settings.json is `{}`, and why an "off main" branch is toxic in a live plugin
  session — branch any curator-runtime fixes off an M7-based tree, not main.)
- Injections show `[recency]`, not `[curator]` — that's the **expected dark default** (the semantic
  bge path needs the curator warmed-to-ready + flag/`[embed]`).
- For any `uv run` while the plugin's `cc-mcp` is alive, use `uv run --no-sync` to avoid the
  rebuild-vs-locked-`cc-mcp.exe` collision.

## Key commits this session
`7d37943..da8e631` M7 tasks 1–6 · `a17f87b` verify-plugin end-to-end smoke · `5b9bfab` curator
STARTUPINFO SW_HIDE · `0a39351` hooks → gui-scripts · `3dfba92` release v0.0.2 (tag) ·
`43aca4e` tasklist CREATE_NO_WINDOW (PR #11, already merged to `main`).

## Servers / commands
- `uv run --no-sync pytest -p no:cacheprovider` (346 passed, 6 skipped). Known flake:
  `test_curator_lifecycle_and_handshake` fails under full-suite load, passes in isolation.
- `pwsh -NoProfile -File scripts/verify-plugin.ps1` — repeatable installed-plugin smoke (PS 5.1 & 7).
- Scratch test repo: `D:\MajorProjects\SCRATCH`.
