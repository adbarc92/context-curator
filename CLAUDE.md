# ContextCurator — project instructions

> M7 shipped (`v0.0.2`, [PR #13](https://github.com/adbarc92/context-curator/pull/13) merged to `main`).
> Both #14 exit criteria are verified **YES** (`CLAUDE_PROJECT_DIR` reaches `cc-mcp`; plugin resolves
> bare `cc-*` on PATH — [`docs/plugin-install.md`](docs/plugin-install.md) §5), but **#14 stays open**:
> the gui-script mitigation only *partially* stopped the Windows console flash (still flashing
> sometimes → tracked in [#12](https://github.com/adbarc92/context-curator/issues/12)).
> The real-data keystone (M4d) verdict is now **in and powered**: on 5 real sessions the semantic/bge
> onload **loses to BM25** (−0.053 nDCG, clustered 90% CI [−0.085, −0.041]) — so the decision is made:
> **ship BM25 as the ranker, demote semantic to dark** ([`docs/decisions/semantic-ranker.md`](docs/decisions/semantic-ranker.md)).
> The store + hooks + guardrails + plugin keep; the semantic *differentiator* did not clear its bar.
> The current eval status and next-steps map live in [`docs/CODEBASE-DIGEST.md`](docs/CODEBASE-DIGEST.md).

## Library documentation
When working with any external library, framework, SDK, or CLI tool (the `mcp`
Python SDK, pydantic, pytest, etc.), use the **Context7** MCP (`resolve-library-id`
then `get-library-docs`) to fetch current docs before relying on memory. Context7
receives only library names + topics — never project code (DESIGN.md §9 privacy boundary).

## Stack
Python + UV. Run everything via `uv run`. SQLite-backed store; no external daemon.
