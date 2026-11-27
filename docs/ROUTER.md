<!-- graph:router -->
# context-curator — router

ContextCurator: a Python/UV relevance-driven working-set policy and durable SQLite context store for Claude Code, shipped as a plugin (v0.0.2).
M0-M7 are built; the decision is to ship BM25 as the onload ranker and keep semantic (bge) dark. See [state.md](state.md).

## Where things live

- [index.md](index.md) lists every doc with keywords and one sentence.
- [CODEBASE-DIGEST.md](CODEBASE-DIGEST.md): architecture, contracts, build/test, eval status.
- [decisions/semantic-ranker.md](decisions/semantic-ranker.md): why BM25 over bge.
- [handoff/](handoff/): dated session handoffs, newest 2026-06-26.
- [plugin-install.md](plugin-install.md) and [statusline.md](statusline.md): install and observability.
- [superpowers/specs/](superpowers/specs/) and [superpowers/plans/](superpowers/plans/): per-milestone design and plan.
- [superpowers/keystone-real.md](superpowers/keystone-real.md): the real-data verdict of record.

## Read first, by task

- Extend or own the code: [CODEBASE-DIGEST.md](CODEBASE-DIGEST.md)
- Why is the ranker BM25: [decisions/semantic-ranker.md](decisions/semantic-ranker.md)
- Pick up where the last session stopped: [handoff/](handoff/)
- Install the plugin: [plugin-install.md](plugin-install.md)
- Learned-ranker work: [superpowers/keystone-learned.md](superpowers/keystone-learned.md)

## Never

- Hard rules: [CLAUDE.md](../CLAUDE.md) (project instructions) and [DESIGN.md](../DESIGN.md) section 9 (privacy boundary).
