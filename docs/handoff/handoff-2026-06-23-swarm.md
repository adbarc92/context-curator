# Swarm Handoff — 2026-06-23 — post-M7 remaining work

> Read [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) **first** — full architecture, contracts,
> build/run/test, and the eval-status analysis. This doc only decomposes *what's left* into
> dispatch-ready lanes. Supersedes [`handoff-2026-06-20-post-m7-followups.md`](handoff-2026-06-20-post-m7-followups.md)
> (same work, now carved into lanes + ownership).

## State at handoff (verified this session)
- **M7 shipped & merged.** `v0.0.2`; PR #13 → `main` (`63afb5d`).
- **Branch `chore/post-m7-followups` @ `1ff3ea2`** — 4 ahead / 1 behind `origin/main`, **NOT pushed**.
- **Tests green:** `uv run --no-sync pytest -p no:cacheprovider` → exit 0 (~346 passed / 6 skipped).
  The former `test_curator_lifecycle_and_handshake` flake was fixed in `11c01c6`.
- **Lint: 2 `E501` errors at HEAD**, both inside the temp #14 diagnostic block in
  [`src/context_curator/mcp_server.py`](../../src/context_curator/mcp_server.py) `main()`. They are
  load-bearing-to-revert, **not** to fix — they disappear when Lane A reverts `990c9fb`.
- **`cc-mcp` is installed via `uv tool` (`v0.0.1`)** → its exe is locked; **always test with
  `uv run --no-sync`** (a plain `uv run` collides with the locked exe: `os error 32`, which on
  `PreToolUse` blocks every Write/Edit/Bash).

## ⚠️ Honest scope note: nothing here is agent-doable *solo right now*
Both lanes are **blocked on a human gate** (a `uv tool install`, a Claude restart, or the user
capturing transcripts) that a single in-session agent cannot satisfy. This is a *small* swarm: the
value is the clean ownership map so the two agent-portions can run **in parallel** the moment their
gates clear — and the gates are independent of each other (clear in any order). Do **not** spawn
agents until a gate is actually cleared; producing this doc is the safe deliverable.

---

## Dependency graph
```
Lane A  (#14 close + land branch) ──┐  human gate: reinstall + restart Claude + report stderr line
                                    ├── independent file sets, separate PRs
Lane B  (real-data M4d verdict)  ───┘  human gate: drop ≥2 .jsonl sessions into _real_local/
```
- **A and B share no owned files** except the status files (`CLAUDE.md`, `CODEBASE-DIGEST.md`) →
  **Lane A owns those**; Lane B files a contract request (see Shared contract).
- A and B go to **separate PRs** (distinct concerns: packaging verification vs. eval verdict).
- If both land near-simultaneously: **land A first** (it reverts the diagnostic + owns status files),
  then B rebases and applies its contract request.

---

## Lane A — Close #14 + land the branch   ·   blocked on USER hardware gate
- **Scope:** Record M7 exit-criterion (a) (does `CLAUDE_PROJECT_DIR` reach `cc-mcp`?), revert the
  temporary diagnostic, delete the now-spent runbook docs, then push the branch and open the PR.
- **Owns (exclusive write):**
  - [`src/context_curator/mcp_server.py`](../../src/context_curator/mcp_server.py) — revert the
    `>>> TEMP DIAGNOSTIC … REVERT` block (`git revert 990c9fb` or delete the block in `main()`).
  - [`docs/plugin-install.md`](../plugin-install.md) §5 — fill the `PENDING` outcomes.
  - **Deletes:** [`docs/M7-runbook.md`](../M7-runbook.md),
    [`docs/Session-Pickup-2026-06-04.md`](../Session-Pickup-2026-06-04.md) (runbook Phase 6 — only
    *after* (a) is recorded; they are the live #14 instructions until then).
  - [`CLAUDE.md`](../../CLAUDE.md) header + [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) Status
    section — update once #14 closes (these are the shared status files; see Shared contract).
- **Reads (no write):** [`docs/handoff/`](.), this doc.
- **Shared contract:** owns `CLAUDE.md` + `CODEBASE-DIGEST.md`; applies Lane B's requested verdict
  line at integration if B is ready.
- **Human gate (USER, then AGENT):**
  1. **[USER]** `uv tool install --editable D:\MajorProjects\INFRASTRUCTURE\context-curator`
     (entry-point code changed → reinstall required) → fully restart Claude in `D:\MajorProjects\SCRATCH`
     → trigger an MCP call → report the startup stderr line `[cc-mcp diag #14] CLAUDE_PROJECT_DIR=… db=…`.
  2. **[USER]** Confirm the gui-script hooks stopped the Windows console-window flashing (#12).
  3. **[AGENT]** Record both in `plugin-install.md` §5. **YES** = `CLAUDE_PROJECT_DIR` non-`None`
     **and** `db=` points at `<scratch>\.context-curator\store.db`. **If NO:** make pinning
     `CC_DB_PATH` the documented default (MCP store diverges from the hooks' store).
  4. **[AGENT]** Revert the diagnostic; delete the two runbook docs.
- **Done when:** `plugin-install.md` §5(a) + flash-fix outcomes recorded; diagnostic gone; runbook
  docs deleted; branch pushed; PR to `main` open.
- **Verify:** `uv run --no-sync ruff check .` → **0 errors** (proves the diagnostic is fully reverted)
  AND `uv run --no-sync pytest -p no:cacheprovider` → exit 0. Then `gh pr view` shows the open PR.
- **Notes / open questions:** Phases 1–4 of the runbook (VERIFY PASS, `/plugin install`, scratch-repo
  smoke = criterion (b)) may already be observed — check `plugin-install.md` §5 for any remaining
  `PENDING` beyond (a) and record those too. Use `superpowers:finishing-a-development-branch` to land.

## Lane B — Real-data keystone (M4d) verdict   ·   blocked on USER capturing data
- **Scope:** Produce a *powered, real-data* verdict for the semantic-vs-BM25 question — the **only**
  lever that can still change the product decision. The synthetic keystone is settled NEGATIVE-powered
  (semantic beats BM25 by +0.056 < +0.10 MEI on a fair, blind-judged, power-sized corpus); **growing
  the synthetic corpus is NOT productive** (effect shrank pilot→powered). The real run so far is
  **HARNESS-ONLY (no verdict)** — it had 1 session, floor is ≥3.
- **Owns (exclusive write):**
  - [`docs/superpowers/keystone-real.md`](../superpowers/keystone-real.md) — record the verdict.
  - `results/` (e.g. `results/keystone-real.md`) — the generated report.
  - A short product-decision note (new file under `docs/`, e.g. `docs/decisions/semantic-ranker.md`)
    capturing ship-BM25-as-ranker **vs** keep-semantic-gate-but-dark.
  - Eval **fixtures**: `src/context_curator/eval/fixtures/_real_local/*.jsonl` (gitignored — **never
    commit transcripts**, DESIGN §9 privacy boundary).
- **Reads (no write):** [`src/context_curator/eval/`](../../src/context_curator/eval/)
  (`real_corpus.py`, `keystone.py`, `stats.py`, `precision_gate.py`), [`DESIGN.md`](../../DESIGN.md) §10.
- **Shared contract:** `CLAUDE.md` + `CODEBASE-DIGEST.md` are **owned by Lane A**. B must **not** edit
  them — instead file a contract request: *"Add a line recording the M4d verdict (`<verdict>`) and the
  resulting ranker decision (ship BM25 / keep semantic dark); update the digest's eval-status gotcha."*
- **Human gate (USER, then AGENT):**
  1. **[USER]** Capture ≥2 more real Claude Code work-sessions (ideally different projects) and drop the
     `.jsonl` transcripts into `src/context_curator/eval/fixtures/_real_local/`.
  2. **[AGENT]** Run the pipeline per `keystone-real.md` §Reproducibility: `harvest_corpus(_real_local/*)`
     → write a corpus dir → `KEYSTONE_CORPUS=<dir> uv run --no-sync python -m context_curator.eval.keystone`
     (needs the `[embed]` extra for `FastEmbedEmbedder`) → `cluster_bootstrap_ci` → `precision_gate`.
     Pre-registered config: MEI 0.10, 90% session-clustered CI, W=5, n_sessions floor 3, precision-gate
     width ≤ MEI, lexical-bias margin +0.15.
- **Done when:** `n_sessions ≥ 3` and the harness emits a real verdict (not HARNESS-ONLY); verdict +
  ranker decision recorded; contract request for the status files filed in the lane report.
- **Verify:** the generated `results/keystone-real.md` shows `verdict:` ≠ `HARNESS-ONLY` and a
  `n_sessions` ≥ 3; `uv run --no-sync pytest -p no:cacheprovider` still exit 0.
- **Notes / open questions:** Encouraging diagnostic from the 1-session run — on real data gold is
  **not** a BM25 proxy (lexical-bias guard non-degenerate), so the central methodological worry doesn't
  materialize. Production stays dark (`CC_CURATOR_ONLOAD="0"`) until a powered real verdict exists.
  `[embed]` install: `uv tool install --editable "D:\MajorProjects\INFRASTRUCTURE\context-curator[embed]"`.
  bge floats are machine-sensitive — **regenerate, don't diff**.

---

## Shared contract — `CLAUDE.md` + `docs/CODEBASE-DIGEST.md`
| File | Owner | Other lanes may request |
|------|-------|-------------------------|
| `CLAUDE.md` (header: "remaining work is out-of-session / #14"; eval status) | **Lane A** | Lane B: append the M4d verdict + ranker decision line |
| `docs/CODEBASE-DIGEST.md` (Status section, eval-status gotcha) | **Lane A** | Lane B: update the "NEGATIVE keystone" / "real-data M4d" gotchas with the verdict |

These are **single-owner** files. A lane that does not own a file records its change as an append-only
*contract request* in its final report; Lane A applies it at integration. (Both are repo files, so
worktrees isolate them — but single-owner still avoids a merge conflict.)

## Integration order
1. **Lane A** lands first when both are ready — it reverts the diagnostic (clears the 2 `E501`s) and
   owns the status files. PR to `main`.
2. **Lane B** rebases on the merged `main`, applies its own status-file edits (A already merged, so no
   live contention), PR to `main`.
3. **Reconcile:** on merged `main`, `uv run --no-sync pytest -p no:cacheprovider` exit 0 **and**
   `uv run --no-sync ruff check .` 0 errors. Confirm `CLAUDE.md` + digest hold the union of both lanes'
   status updates and the temp diagnostic is gone.

## Rules of the road (give to every dispatched agent verbatim)
1. **Stay in your lane** — write only files your lane owns; for anything else, file a contract request.
2. **Worktree/branch per lane** — never commit to `main`; one PR per lane.
3. **Shared status files are single-owner (Lane A)** — Lane B requests, never edits them.
4. **Don't widen scope** — build only your lane's items; report discoveries, don't fix them.
5. **Test with `uv run --no-sync`** (locked `cc-mcp.exe`); **never** run dev hooks + installed plugin
   together (locked-exe error blocks all writes — keep `.claude/settings.json` = `{}`).
6. **Verify before claiming done** — run the lane's Verify check and paste the real output.
7. **Privacy boundary** — never commit `_real_local/` transcripts; Context7 gets library names only.

## Suggested skills
- `superpowers:finishing-a-development-branch` — Lane A push + PR.
- `superpowers:verification-before-completion` — before claiming #14 done or the diagnostic reverted.
- `verify` / `qa-runner` — end-to-end plugin validation in the scratch repo.
