# Plan — Track A: stickiness-ranked live onload

Design: [`../specs/2026-11-22-track-a-stickiness-onload-design.md`](../specs/2026-11-22-track-a-stickiness-onload-design.md).
Branch `feat/track-a-stickiness-ranker` off `origin/main`; one PR, never merged by the agent.
Every task is test-first; full suite + ruff green after each commit.

| # | Task | Files | Proving test |
|---|---|---|---|
| 1 | Shared entity module; `eval/real_corpus.py` imports it (no behaviour change) | `src/context_curator/entities.py`, `eval/real_corpus.py` | `tests/test_entities.py` (canonicalisation, match rules, empty-set never matches); existing eval tests unchanged |
| 2 | `Counter`-based BM25, bit-identical | `eval/bm25.py` | `tests/eval/test_bm25_equivalence.py` — frozen copy of the old function vs new over randomized docs/prompts, `==` on the dicts |
| 3 | Entity tags on captured tool results | `capture/tool_result.py` (`entities=` kwarg), `hooks/post_tool_use.py` | `tests/test_capture_tool_result.py`, `tests/test_hooks_capture.py` — Read/Edit/Grep get `entity:` tags; replay path (no kwarg) byte-identical |
| 4 | Ranker: indexed refetch count + `stickiness_rank` | `onload/stickiness.py` | `tests/test_onload_stickiness.py` — randomized equivalence vs eval `prior_refetch_scores`; ordering (count, BM25, newer); entity-tag parsing |
| 5 | `stickiness_select` (durable exclusion, rendered-size budget, k) | `onload/select.py`, `onload/format.py` (`rendered_body`) | same file — big sticky chunk is injected (old full-size budget would inject 0); pins/conventions excluded |
| 6 | Hook wiring + decision-log source + latency | `hooks/user_prompt_submit.py`, `observe/decision_log.py` comment | `tests/test_hooks_onload_smoke.py` — `[stickiness]` floor; realistic-size 1 000-chunk p50 < 300 ms |
| 7 | Freeze the powered analysis (before any run) | `docs/superpowers/stickiness-powered-prereg.md` | committed before task 8 runs on any data |
| 8 | Eval module: gap-parameterised harvest, gap sweep, live-ranker arm, clustered CI, corpus-size gate | `eval/real_corpus.py` (`gap=`), `eval/stickiness.py` (`python -m`) | `tests/eval/test_stickiness_eval.py` — gap=0 identical to old harvest; tiny synthetic trace end-to-end; refuses a "powered" label below 45 sessions |
| 9 | Smoke-run task 8 on the local harvestable sessions (labelled n=2 smoke, not evidence); end-to-end hook benchmark on a real store copy | scratchpad only | numbers into the PR body |
| 10 | Docs: decision follow-up ticked, digest flows/status, CLAUDE.md, CHANGELOG | docs | — |
