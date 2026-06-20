# ContextCurator — project instructions

> M7 shipped (`v0.0.2`, [PR #13](https://github.com/adbarc92/context-curator/pull/13) merged to `main`).
> Remaining work is out-of-session only, tracked in [#14](https://github.com/adbarc92/context-curator/issues/14)
> with steps in [`docs/M7-runbook.md`](docs/M7-runbook.md) Phase 5. The current eval status and
> next-steps map live in [`docs/CODEBASE-DIGEST.md`](docs/CODEBASE-DIGEST.md).

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
