# ContextCurator — project instructions

> M7 shipped (`v0.0.2`, [PR #13](https://github.com/adbarc92/context-curator/pull/13) merged to `main`).
> Both #14 exit criteria are verified **YES** (`CLAUDE_PROJECT_DIR` reaches `cc-mcp`; plugin resolves
> bare `cc-*` on PATH — [`docs/plugin-install.md`](docs/plugin-install.md) §5), but **#14 stays open**:
> the gui-script mitigation only *partially* stopped the Windows console flash (still flashing
> sometimes → tracked in [#12](https://github.com/adbarc92/context-curator/issues/12)).
> The one remaining product lever is the **real-data keystone (M4d) verdict** (semantic vs BM25), blocked
> on capturing ≥2 more sessions — decomposed in [`docs/handoff/handoff-2026-06-25-swarm.md`](docs/handoff/handoff-2026-06-25-swarm.md).
> The current eval status and next-steps map live in [`docs/CODEBASE-DIGEST.md`](docs/CODEBASE-DIGEST.md).

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
