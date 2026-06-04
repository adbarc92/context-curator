# ContextCurator — project instructions

<!-- BEGIN: ACTIVE-SESSION-PICKUP — remove this block when feat/m7-plugin-package merges to main -->
## Active session pickup

If the current branch is `feat/m7-plugin-package` (check with `git rev-parse --abbrev-ref HEAD`), read [`docs/Session-Pickup-2026-06-04.md`](docs/Session-Pickup-2026-06-04.md) before doing anything else. It documents:

- where we are: the **M7 plugin-package spec is committed (`ef523f0`) and at the review gate** — no plan written, no code touched yet; next step is `superpowers:writing-plans`,
- the FINAL M7 mechanism (uv-tool console scripts — NOT `uv run --project ${CLAUDE_PLUGIN_ROOT}`, which critique round 1 killed),
- three things that make M7 non-trivial (two out-of-session exit criteria needing a Claude restart; it self-modifies this session's own hooks; PATH is load-bearing),
- and deferred/future items.

If the branch has changed, this section and the linked status doc are stale — skip them and delete this block.
<!-- END: ACTIVE-SESSION-PICKUP -->

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
