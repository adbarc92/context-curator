# Handoff — 2026-06-26 — post-M7 verdict + learned-ranker Cycle 1 (GO)

> Read [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) first for architecture/build/test. This doc
> captures *this session's* state and what to do next. It supersedes the planning view in
> [`handoff-2026-06-25-swarm.md`](handoff-2026-06-25-swarm.md) (that swarm's two lanes are now both done).

## Where things stand
The session's work is split across **two independent PRs off `main`** (done 2026-06-26):
- **PR [#15](https://github.com/adbarc92/context-curator/pull/15)** — branch `chore/post-m7-followups` @ `c7ac6f0` (7 commits). The **M7 close-out (#14) + M4d verdict** (BM25 beats bge) + decision doc.
- **PR [#16](https://github.com/adbarc92/context-curator/pull/16)** — branch `feat/learned-ranker-feasibility` @ `53aa8ac` (16 commits, rebased onto `main`). The **learned-ranker (track B): retrospective + spec + plan + Cycle-1 code + GO verdict + this handoff**.
- **Merge order: #15 first.** #16's docs cross-link to `docs/decisions/semantic-ranker.md` and the updated `keystone-real.md`, which land via #15; the links resolve on `main` once #15 merges. No *code* dependency between them. Both branches are green (`pytest` exit 0, `ruff` clean) on their own.
- **Neither PR is merged yet** — merging is the user's call (global rule: merge via PR, never push to `main`). That's the only open thread.

## What shipped this session (don't re-derive — read the artifacts)
1. **M7 close-out (#14).** Both exit criteria recorded **YES** in [`docs/plugin-install.md`](../plugin-install.md) §5: `CLAUDE_PROJECT_DIR` reaches `cc-mcp` (Claude Code MCP docs v2.1.139+ parity; machine on 2.1.190; corroborated by per-project `store.db` placement) and bare `cc-*` resolve on PATH (18 stdio connects across ~8 projects). Temp diagnostic reverted; `M7-runbook.md` + `Session-Pickup-2026-06-04.md` deleted. **#14 stays OPEN** on one sub-item: the gui-script mitigation only *partially* stops the Windows console flash (still flashes sometimes) → tracked in **#12**.
2. **M4d real-data keystone verdict.** Powered: **bge semantic LOSES to BM25** (−0.053 nDCG, clustered 90% CI [−0.085, −0.041], 5 sessions). Verdict-of-record [`docs/superpowers/keystone-real.md`](../superpowers/keystone-real.md); decision [`docs/decisions/semantic-ranker.md`](../decisions/semantic-ranker.md) (ship BM25, demote semantic).
3. **Retrospective** [`docs/retrospective-semantic-relevance.md`](../retrospective-semantic-relevance.md) — the bitter-lesson framing (bge was a frozen *human prior*; we never tested *learning*).
4. **Track B — learned ranker.** Design [`docs/superpowers/specs/2026-06-25-learned-onload-ranker-design.md`](../superpowers/specs/2026-06-25-learned-onload-ranker-design.md) (survived 3 adversarial critique rounds → restructured into a gated 2-cycle plan). Plan [`docs/superpowers/plans/2026-06-25-learned-ranker-feasibility.md`](../superpowers/plans/2026-06-25-learned-ranker-feasibility.md). **Cycle 1 implemented** (subagent-driven, ~13 commits on PR #16, `fb590a7..0c3bc02` after the rebase onto `main`, per-task + final whole-branch review all clean).

## The headline result — Cycle 1 says GO
Full numbers + interpretation in [`docs/superpowers/keystone-learned.md`](../superpowers/keystone-learned.md); pre-registration (committed before the run) in [`docs/superpowers/learned-feasibility-prereg.md`](../superpowers/learned-feasibility-prereg.md).

- A **learned logistic over only cheap features** (bm25, recency_rank, chunk_log_len, tool_type — no embeddings, no daemon) scores **nDCG@10 = 0.306 vs BM25 0.102 vs bge 0.091**. Headline **learned − BM25 = +0.203** (≈2× MEI), clustered 90% CI **[+0.073, +0.372]** (excludes 0 → sign confident). **The bitter lesson cuts the other way: learning beats both the frozen embedding and the lexical baseline.**
- **Precision gate: inconclusive-underpowered, needed_n ≈ 45** (range [1, 78]) at n=5. So this is a *feasibility* signal, not the final powered verdict — but ~45 sessions is harvestable (hundreds exist locally).
- **⚠️ Critical caveat — circularity audit fired:** a solo `prior_refetch_count` ranker scores **0.445 > the learned 0.306**; `same_dir_recent` 0.310 ≈ learned. These are **excluded** from the model (so the +0.203 win is clean), but the gold is heavily refetch-history-driven. **Cycle 2's #1 methodological job is to control for this** (keep those features out; confirm the lift is relevance, not file-stickiness, on a larger/stratified corpus). Lexical-bias guard non-degenerate (good).

## Next steps (in order)
1. **Merge the two PRs (the only open thread).** Review + merge **#15 first** (closeout), then **#16** (track-B) — #16's doc links resolve once #15's decision doc + `keystone-real.md` are on `main`. Merge via PR (global rule: never push to `main` directly). After both merge, the stale `feat/m7-plugin-package` branch can be deleted.
2. **Track A (ship the decided cheap win):** wire BM25 into the live onload path (`onload/select.py` + `hooks/user_prompt_submit.py`) as the default ranker over recency — it's eval-only today. See [`docs/decisions/semantic-ranker.md`](../decisions/semantic-ranker.md) follow-ups. *Note:* Cycle 1 suggests a **learned** ranker beats BM25, so consider whether to ship BM25 now or wait for the Cycle 2 verdict before wiring the live ranker once.
3. **Track B Cycle 2 (the full powered build) — GATED GO is met.** Harvest **≥~45 real sessions**, hold out ≥8 test sessions, K-fold session-CV for L2, build the parity-safe serve featurizer + `LearnedTarget` + artifact, run the powered keystone. **All the train-serve parity work deferred from Cycle 1 lives in §4 of the design spec** (recency direction, TTL set-alignment, `tool_vocab`/casing, turn-index on `FixtureChunk`, `norm_stats` replay). Re-pre-register. The reviewer also flagged a latent Cycle-2 item: `run_feasibility` passes `n_sessions=len(by)` to the gate even if `loso_deltas` skipped degenerate folds.
4. **Close #14 / progress #12:** the flash sub-item is the only thing keeping #14 open; #12 is the upstream-bug surface.

## Gotchas specific to this work (beyond the digest)
- **Always `uv run --no-sync`** — the installed `cc-mcp.exe` is locked; a plain `uv run` collides (`os error 32`) and on PreToolUse blocks every write.
- **The committed sample traces (`tests/eval/_traces/sample_a.jsonl`/`sample_b.jsonl`) are ALL-GOLD** (every candidate is gold), so they can't train a logistic — that's why `loso_deltas` skips single-class folds and the unit tests use mixed-class *inline* fixtures, not these traces. Don't "fix" the skip.
- **Report text must be ASCII** — the Windows cp1252 console can't print `−`/`→`/`—` (bit us once).
- **Real transcripts in `_real_local/` are gitignored** (DESIGN §9) and gold labels are **CWD-dependent** (`os.path.abspath`) — pin the harvest root; regenerate, don't diff.
- **SDD progress ledger** is at `.superpowers/sdd/progress.md` (git-ignored) — the recovery map of which tasks completed; trust it + `git log` after any compaction.

## Suggested skills
- `superpowers:writing-plans` then `superpowers:subagent-driven-development` — to plan + build **Cycle 2** (the full powered learned-ranker eval). Re-run `superpowers:brainstorming` first if the Cycle-2 scope needs re-opening (the spec already sketches it in §4).
- `superpowers:finishing-a-development-branch` — to resolve the open PR/merge decision (step 1).
- `verify` / `qa-runner` — if/when Track A wires a ranker into the live onload path and needs end-to-end validation.
