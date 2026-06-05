# ContextCurator — project instructions

<!-- BEGIN: ACTIVE-SESSION-PICKUP — remove this block when feat/m7-plugin-package merges to main -->
## Active session pickup

If the current branch is `feat/m7-plugin-package` (check with `git rev-parse --abbrev-ref HEAD`), read [`docs/Session-Pickup-2026-06-04.md`](docs/Session-Pickup-2026-06-04.md) before doing anything else. It documents:

- where we are: the M7 plan is written **and all six in-session tasks are implemented, reviewed, and committed** (`7d37943`..`da8e631`); full suite 342 passed. **Only Tasks 7–8 remain — the two out-of-session install/verification gates** (need a `uv tool install` + fresh shell + Claude restart; both recorded exit criteria are still `PENDING` in [`docs/plugin-install.md`](docs/plugin-install.md) §5),
- the exact resume sequence for those gates, plan-vs-code adaptations already made (don't re-derive them), and that the milestone ends with `superpowers:finishing-a-development-branch`,
- a separate, already-fixed Windows console-flicker bug that lives on branch `fix/windows-console-flicker` (PR #11 to `main`), **not** on this branch — so M7 install testing here still flashes windows until #11 merges or `43aca4e` is cherry-picked.

If the branch has changed, this section and the linked status doc are stale — skip them and delete this block.
<!-- END: ACTIVE-SESSION-PICKUP -->

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
