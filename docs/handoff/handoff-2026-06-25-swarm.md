# Swarm Handoff — 2026-06-25 — what's left to ship ContextCurator

> Read [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) **first** — full architecture, contracts,
> build/run/test, and the eval-status analysis. This doc decomposes *what's left to ship* into
> dispatch-ready parallel lanes. Supersedes
> [`handoff-2026-06-23-swarm.md`](handoff-2026-06-23-swarm.md) (same two lanes; this revision is
> re-verified empirically on 2026-06-25 and adds the housekeeping loose-end + the explicit product
> ship-decision).

---

## Audit summary — verified this session (2026-06-25)

Every claim below was checked by running the command, not by trusting a doc.

| Check | Command | Result |
|-------|---------|--------|
| Tests | `uv run --no-sync pytest -p no:cacheprovider -q` | **exit 0** — ~346 passed / 6 skipped (no flake) |
| Lint | `uv run --no-sync ruff check .` | **2 E501 only**, both at [`mcp_server.py:105`](../../src/context_curator/mcp_server.py#L105) & `:107` — *inside the temp #14 diagnostic*. Load-bearing-to-revert, not to fix. |
| Diagnostic | `git show 990c9fb --stat` | Temp `[cc-mcp diag #14]` stderr block still present in `mcp_server.main()` (lines ~105–118). Must revert before merge. |
| Branch | `git status -sb` | `chore/post-m7-followups` @ `1ff3ea2` — **4 ahead / 1 behind** `origin/main` (`63afb5d`), **NOT pushed**. |
| Uncommitted | `git status` | [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) modified (un-staged) + this handoff dir untracked. **Not yet committed** — loose end, see Lane A. |
| Open issues | `gh issue list` | **#14** (out-of-session M7 verification) and **#12** (upstream Claude Code spawns exes without `windowsHide`). |
| Real-eval data | `ls …/_real_local/` | **1** `.jsonl` session present (15 MB). Verdict floor is **n_sessions ≥ 3** → need **≥2 more**. |
| Exit criteria | [`docs/plugin-install.md`](../plugin-install.md) §5 | Both **(a)** `CLAUDE_PROJECT_DIR`→`cc-mcp` and **(b)** marketplace-cache PATH resolve still `PENDING`. |

**Conclusion: the prior handoff is accurate.** The engineering is done and released (M0–M7,
`v0.0.2`, PR #13 merged). Nothing is broken. What remains to "ship" is *verification* and *a product
decision* — not code.

## What "ship" means here — two distinct things

1. **Ship the engineering closeout** (Lane A): prove the packaged plugin actually works on a clean
   install (the two `PENDING` exit criteria), revert the diagnostic, clean up the now-spent runbook
   docs, push the branch, open the PR, close #14. Mechanical once its human gate clears.

2. **Ship the product claim** (Lane B): the centerpiece — semantic relevance vs BM25 — came back
   **NEGATIVE-powered on synthetic data** (+0.056 nDCG@10 < the pre-registered +0.10 bar, on a fair,
   blind-judged, power-sized corpus). Growing the *synthetic* corpus is **not** productive (the effect
   *shrank* pilot→powered). The **only** lever left is a **powered real-data verdict (M4d)**, which is
   stuck at HARNESS-ONLY because it has 1 session and needs ≥3. The eventual product call — **ship
   BM25 as the ranker (demote/dark the semantic path) vs. keep semantic optional-but-dark pending the
   real verdict** — is a decision the user must make, and it cannot be made until Lane B produces a
   real verdict.

> **The honest state:** the plugin is technically shippable today as a recency/BM25 store; its
> *differentiator* has not cleared its own success bar. Lane A makes the plugin officially done;
> Lane B decides whether the differentiator lives.

## ⚠️ Honest scope note — neither lane is agent-doable *solo right now*

Both lanes are **blocked on a human gate** a single in-session agent cannot satisfy (a
`uv tool install` + Claude restart for A; the user capturing transcripts for B). This is a *small*
swarm — its value is the clean ownership map so the two agent-portions run **in parallel the moment
their gates clear**, in any order. **Do not spawn agents until a gate is actually cleared.** Producing
this doc is the safe deliverable.

---

## Dependency graph

```
Lane A (#14 close + land branch) ──┐  human gate: reinstall + restart Claude + report stderr line
                                   ├── independent file sets · separate PRs · clear in any order
Lane B (real-data M4d verdict) ────┘  human gate: drop ≥2 more .jsonl sessions into _real_local/
```

- A and B share **no owned files** except the status files (`CLAUDE.md`, `docs/CODEBASE-DIGEST.md`).
  **Lane A owns those;** Lane B files an append-only contract request (see Shared contract).
- A and B → **separate PRs** (distinct concerns: packaging verification vs. eval verdict).
- If both are ready together: **land A first** (it reverts the diagnostic + owns the status files),
  then B rebases on merged `main` and applies its contract request.

---

## Lane A — Close #14 + land the branch   ·   blocked on USER hardware gate

> **Update 2026-06-25 — Lane A executed; gate was redundant.** Exit criterion (a) was settled without
> the diagnostic (official Claude Code MCP docs: `CLAUDE_PROJECT_DIR` is set in the spawned stdio
> server's env, v2.1.139+ parity; this machine v2.1.190 — corroborated by per-project `store.db`
> placement and none in the tool venv). Criterion (b) = YES (18 stdio connects across ~8 projects).
> Diagnostic reverted, runbook docs deleted, status files updated, tree green (ruff 0 / pytest exit 0).
> **#14 stays OPEN:** user confirms the gui-script mitigation only *partially* stopped the console
> flash (still flashing sometimes) → residual flicker tracked in **#12**. The PR does **not** close #14.

- **Scope:** Record the two M7 exit criteria (does `CLAUDE_PROJECT_DIR` reach `cc-mcp`; does the
  marketplace-cache install resolve bare `cc-*` on PATH), confirm the gui-script hooks stopped the
  Windows console flashing, revert the temporary diagnostic, delete the now-spent runbook docs,
  commit the pending digest + this handoff, push the branch, and open the PR to `main`.
- **Owns (exclusive write):**
  - [`src/context_curator/mcp_server.py`](../../src/context_curator/mcp_server.py) — revert the
    `>>> TEMP DIAGNOSTIC … REVERT` block (lines ~105–118; `git revert 990c9fb` or delete the block in
    `main()`). This also clears the 2 `E501` lint errors.
  - [`docs/plugin-install.md`](../plugin-install.md) §5(a) + §5(b) — fill the two `PENDING` outcomes.
  - **Deletes:** [`docs/M7-runbook.md`](../M7-runbook.md),
    [`docs/Session-Pickup-2026-06-04.md`](../Session-Pickup-2026-06-04.md) — runbook Phase 6, **only
    after** (a) is recorded (they are the live #14 instructions until then).
  - [`CLAUDE.md`](../../CLAUDE.md) header + [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) Status
    section — update the "remaining work / #14" header (the runbook link dies when the runbook is
    deleted) once #14 closes. These are the shared status files (see Shared contract).
  - **Commit the loose ends:** the un-staged [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) edit
    and the untracked `docs/handoff/` docs are currently uncommitted on the branch — commit them.
- **Reads (no write):** [`docs/handoff/`](.) (this doc), [`scripts/verify-plugin.ps1`](../../scripts/verify-plugin.ps1).
- **Shared contract:** owns `CLAUDE.md` + `docs/CODEBASE-DIGEST.md`; applies Lane B's requested
  verdict line at integration if B is ready first.
- **Human gate (USER, then AGENT):**
  1. **[USER]** `uv tool install --editable D:\MajorProjects\INFRASTRUCTURE\context-curator`
     (entry-point/code changed → reinstall required) → fully restart Claude in a scratch repo
     (`D:\MajorProjects\SCRATCH`) → trigger an MCP call → report the startup stderr line
     `[cc-mcp diag #14] CLAUDE_PROJECT_DIR=… db=…`.
  2. **[USER]** Confirm the gui-script (pythonw) hooks stopped the Windows console-window flashing (#12),
     and that `/plugin install` via the marketplace/cache path succeeded + the plugin enabled (criterion b).
  3. **[AGENT]** Record (a) and (b) in `plugin-install.md` §5. **(a) YES** = `CLAUDE_PROJECT_DIR`
     non-`None` **and** `db=` points at `<scratch>\.context-curator\store.db`. **If (a) NO:** make
     pinning `CC_DB_PATH` the documented default (the MCP store otherwise diverges from the hooks' store).
  4. **[AGENT]** Revert the diagnostic; delete the two runbook docs; update the status-file headers;
     push the branch; open the PR.
- **Done when:** §5(a)+(b)+flash-fix outcomes recorded; diagnostic gone; runbook docs deleted; status
  headers updated; pending digest/handoff committed; branch pushed; PR to `main` open; **#14 closed**.
- **Verify:** `uv run --no-sync ruff check .` → **0 errors** (proves the diagnostic is fully reverted)
  **and** `uv run --no-sync pytest -p no:cacheprovider` → exit 0; then `gh pr view` shows the open PR
  and `gh issue view 14` shows it closed.
- **Notes / open questions:** Some of Phases 2–4 in the runbook (VERIFY PASS, `/plugin install`,
  scratch-repo smoke) may already have been observed — check `plugin-install.md` §5 for any
  `PENDING` and record those too. `#12` is an *upstream* Claude Code bug (no `windowsHide`); our
  gui-script workaround is the mitigation — don't try to "fix" #12 in this repo. Use
  `superpowers:finishing-a-development-branch` to land.

## Lane B — Real-data keystone (M4d) verdict + product decision   ·   blocked on USER capturing data

> **Update 2026-06-25 — Lane B executed; verdict reached.** Gate cleared from existing on-disk Claude
> Code transcripts (5 sessions across 5 projects, gitignored). Powered result: semantic/bge onload
> **loses to BM25** (−0.053 nDCG, session-clustered 90% CI [−0.085, −0.041], n=4 test sessions; gate =
> verdict). Diagnostics clean (lexical-bias non-degenerate, recency healthy). **Decision made:** ship
> BM25 ranker, demote semantic to dark → [`docs/decisions/semantic-ranker.md`](../decisions/semantic-ranker.md);
> verdict-of-record in [`docs/superpowers/keystone-real.md`](../superpowers/keystone-real.md). Landed on
> the same branch/PR as Lane A (one linear session, so the "separate PR + contract request" dance was
> unnecessary). Remaining is *implementation* (wire BM25 into live onload), tracked as a follow-up.

- **Scope:** Produce a *powered, real-data* verdict for the semantic-vs-BM25 question — the **only**
  lever that can still change the product decision — and record the resulting ranker call. The
  synthetic keystone is settled NEGATIVE-powered (+0.056 < +0.10 MEI on a fair, blind-judged corpus);
  **growing the synthetic corpus is NOT productive** (effect shrank pilot→powered). The real run so
  far is **HARNESS-ONLY (no verdict)** — 1 session, floor is ≥3.
- **Owns (exclusive write):**
  - [`docs/superpowers/keystone-real.md`](../superpowers/keystone-real.md) — record the verdict.
  - `results/` (e.g. `results/keystone-real.md`) — the generated report.
  - A new product-decision note (e.g. `docs/decisions/semantic-ranker.md`) capturing
    **ship-BM25-as-ranker vs. keep-semantic-gate-but-dark**, with the real verdict as its evidence.
  - Eval **fixtures**: `src/context_curator/eval/fixtures/_real_local/*.jsonl` (gitignored — **never
    commit transcripts**, DESIGN §9 privacy boundary; the dir is already in `.gitignore` line 20).
- **Reads (no write):** [`src/context_curator/eval/`](../../src/context_curator/eval/)
  (`real_corpus.py`, `keystone.py`, `stats.py`, `precision_gate.py`), [`DESIGN.md`](../../DESIGN.md) §10.
- **Shared contract:** `CLAUDE.md` + `docs/CODEBASE-DIGEST.md` are **owned by Lane A** — B must **not**
  edit them. File an append-only contract request instead (see Shared contract).
- **Human gate (USER, then AGENT):**
  1. **[USER]** Capture **≥2 more** distinct real Claude Code work-sessions (ideally different
     projects) and drop the `.jsonl` transcripts into
     `src/context_curator/eval/fixtures/_real_local/`. (1 session is already there → 3+ total.)
  2. **[AGENT]** Run the pipeline per `keystone-real.md` §Reproducibility:
     `harvest_corpus(_real_local/*)` → write a corpus dir →
     `KEYSTONE_CORPUS=<dir> uv run --no-sync python -m context_curator.eval.keystone`
     (needs the `[embed]` extra for `FastEmbedEmbedder`) → `cluster_bootstrap_ci` → `precision_gate`.
     Pre-registered config (spec §5.7): MEI 0.10, 90% session-clustered CI, W=5, n_sessions floor 3,
     precision-gate width ≤ MEI, lexical-bias margin +0.15.
- **Done when:** `n_sessions ≥ 3` and the harness emits a real verdict (≠ HARNESS-ONLY); verdict +
  ranker decision recorded; contract request for the status files filed in the lane report.
- **Verify:** the generated `results/keystone-real.md` shows `verdict:` ≠ `HARNESS-ONLY` **and**
  `n_sessions ≥ 3`; `uv run --no-sync pytest -p no:cacheprovider` still exit 0.
- **Notes / open questions:** Encouraging diagnostic from the 1-session run — on real data gold is
  **not** a BM25 proxy (lexical-bias guard non-degenerate), so the central methodological worry
  doesn't materialize. Production stays dark (`CC_CURATOR_ONLOAD="0"`) until a powered real verdict
  exists. `[embed]` install:
  `uv tool install --editable "D:\MajorProjects\INFRASTRUCTURE\context-curator[embed]"`. bge floats
  are machine-sensitive — **regenerate, don't diff**.

---

## Shared contract — `CLAUDE.md` + `docs/CODEBASE-DIGEST.md`

| File | Owner | Other lanes may request |
|------|-------|-------------------------|
| `CLAUDE.md` (header: "remaining work / #14 / runbook link"; eval status) | **Lane A** | Lane B: append the M4d verdict + ranker decision line |
| `docs/CODEBASE-DIGEST.md` (Status section; eval-status gotchas) | **Lane A** | Lane B: update the "NEGATIVE keystone" / "real-data M4d" gotchas with the verdict |

Both are **single-owner** files. A lane that does not own a file records its change as an
append-only *contract request* in its final report; **Lane A applies it at integration**. (Both are
repo files, so worktrees isolate them — single-owner still avoids the merge conflict. Note Lane A
must also rewrite the `CLAUDE.md` header's `M7-runbook.md` link, which dies when Lane A deletes that
runbook.)

## Integration order

1. **Lane A** lands first when both are ready — it reverts the diagnostic (clears the 2 `E501`s),
   owns the status files, and closes #14. PR to `main`.
2. **Lane B** rebases on merged `main`, applies its own status-file edits (A already merged → no live
   contention), PR to `main`.
3. **Reconcile:** on merged `main`, `uv run --no-sync pytest -p no:cacheprovider` exit 0 **and**
   `uv run --no-sync ruff check .` 0 errors. Confirm `CLAUDE.md` + digest hold the **union** of both
   lanes' status updates and the temp diagnostic is gone.

## Rules of the road (give to every dispatched agent verbatim)

1. **Stay in your lane** — write only files your lane owns; for anything else, file a contract request.
2. **Worktree/branch per lane** — never commit to `main`; one PR per lane.
3. **Shared status files are single-owner (Lane A)** — Lane B requests, never edits them.
4. **Don't widen scope** — build only your lane's items; report discoveries, don't fix them.
5. **Test with `uv run --no-sync`** (the installed `cc-mcp.exe` is locked; a plain `uv run` collides:
   `os error 32`, which on `PreToolUse` blocks every Write/Edit/Bash). **Never** run dev hooks +
   installed plugin together — keep dev `.claude/settings.json` = `{}`.
6. **Verify before claiming done** — run the lane's Verify check and paste the real output.
7. **Privacy boundary** — never commit `_real_local/` transcripts; Context7 gets library names only.

## Suggested skills

- `superpowers:finishing-a-development-branch` — Lane A push + PR.
- `superpowers:verification-before-completion` — before claiming #14 done or the diagnostic reverted.
- `verify` / `qa-runner` — end-to-end plugin validation in the scratch repo.
