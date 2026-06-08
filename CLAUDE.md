# ContextCurator — project instructions

<!-- BEGIN: ACTIVE-SESSION-PICKUP — remove this block when feat/m7-plugin-package merges to main -->
## Active session pickup

If the current branch is `feat/m7-plugin-package` (check with `git rev-parse --abbrev-ref HEAD`), read [`docs/Session-Pickup-2026-06-04.md`](docs/Session-Pickup-2026-06-04.md) before doing anything else. It documents:

- where we are: **M7 is implemented, validated end-to-end, and shipped as `v0.0.2`** — branch pushed, [PR #13](https://github.com/adbarc92/context-curator/pull/13) open to `main`. Nothing else is actionable in-session,
- what's left (all out-of-session / user-driven, tracked in [#14](https://github.com/adbarc92/context-curator/issues/14)): merge PR #13; after a Claude reinstall+restart, confirm the gui-script hooks stop the Windows flashing and record exit criterion (a) — `CLAUDE_PROJECT_DIR`→`cc-mcp` — in [`docs/plugin-install.md`](docs/plugin-install.md) §5,
- key non-obvious context the diff doesn't carry: the Windows console-flicker root cause + the `gui-scripts` fix ([#12](https://github.com/adbarc92/context-curator/issues/12)), the "never run both wirings" lock hazard, and to use `uv run --no-sync` while the plugin's `cc-mcp` is alive.

If the branch has changed (e.g. PR #13 merged to `main`), this section and the linked status doc are stale — skip them and delete this block.
<!-- END: ACTIVE-SESSION-PICKUP -->

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
