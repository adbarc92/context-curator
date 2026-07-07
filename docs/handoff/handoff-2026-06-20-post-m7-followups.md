# Handoff — 2026-06-20 — post-M7 follow-ups

> For a fresh agent the user will walk through this with later. M7 is shipped and merged;
> this session did cleanup + prepped the remaining out-of-session verification. The repo map is
> [`docs/CODEBASE-DIGEST.md`](../CODEBASE-DIGEST.md) — **read it first**, it has the full
> architecture, contracts, and the eval-status analysis. This doc only covers what's left.

## State at handoff
- **M7 (plugin packaging) is merged.** `v0.0.2` tag; PR #13 merged to `main` (`63afb5d`). Verified
  the merge landed (local `feat/m7-plugin-package@17c62c4` is an ancestor of `origin/main`).
- **Work branch: `chore/post-m7-followups`** (3 commits, **NOT pushed**, off the merged tree):
  - `bb0c082` docs: remove stale ACTIVE-SESSION-PICKUP block from CLAUDE.md + add CODEBASE-DIGEST.md
  - `990c9fb` chore(mcp): **temporary** #14 stderr diagnostic in `cc-mcp` (see ⚠️ below)
  - `11c01c6` test(curator): fix the `test_curator_lifecycle_and_handshake` idle-timeout flake
- Tests: full suite green, **3× full-suite + 3× isolation** runs (346 passed, 6 skipped). Run with
  `uv run --no-sync pytest -p no:cacheprovider` (the `--no-sync` avoids colliding with a locked
  `cc-mcp.exe` if the plugin is installed/running — see gotchas).
- Open issues: **#14** (out-of-session M7 verification) and **#12** (upstream Windows console-window bug).

## ⚠️ Load-bearing: the #14 diagnostic is TEMPORARY and must be reverted
Commit `990c9fb` added a clearly-delimited `>>> TEMP DIAGNOSTIC … REVERT` block to
[`src/context_curator/mcp_server.py`](../../src/context_curator/mcp_server.py) `main()`. It prints
one stderr line at `cc-mcp` startup: `[cc-mcp diag #14] CLAUDE_PROJECT_DIR=… db=…`. **Do not let
this branch merge with the diagnostic in place** — revert it (a `git revert 990c9fb` or delete the
block) once exit-criterion (a) is recorded.

## Remaining work (all needs the USER's hands — none is doable solo in-session)

### 1. Close #14 — out-of-session verification (the diagnostic is already staged)
Follow [`docs/M7-runbook.md`](../M7-runbook.md) **Phase 5**. Sequence:
1. **[USER]** `uv tool install --editable D:\MajorProjects\INFRASTRUCTURE\context-curator` (entry-point
   code changed, so a reinstall is required) → restart Claude in the scratch repo
   `D:\MajorProjects\SCRATCH` → trigger an MCP call → report the `[cc-mcp diag #14]` stderr line.
2. **[USER]** Also confirm the gui-script hooks stopped the Windows console-window flashing (#12) after
   the reinstall+restart.
3. **[AGENT]** Record both outcomes in [`docs/plugin-install.md`](../plugin-install.md) §5 (the §5(a)
   *How checked* line is already prepped with the YES/NO criterion). If `CLAUDE_PROJECT_DIR` does NOT
   reach `cc-mcp`, make pinning `CC_DB_PATH` the documented default. Then **revert the diagnostic**.
4. **[AGENT]** Per runbook Phase 6: once #14 is fully recorded, delete `docs/Session-Pickup-2026-06-04.md`
   and `docs/M7-runbook.md` (they were intentionally **left in place** this session because they are the
   live instructions for #14).

### 2. The real-data keystone (M4d) — the ONLY lever that can change the product decision
This is the important strategic item. The synthetic keystone is **settled NEGATIVE-powered** (semantic
beats BM25 by only +0.056 < +0.10 MEI on a fair, blind-judged, power-sized corpus —
[`docs/superpowers/keystone-powered.md`](../superpowers/keystone-powered.md)). **Growing the synthetic
corpus is NOT productive** (effect shrank pilot→powered). The real-data run
([`docs/superpowers/keystone-real.md`](../superpowers/keystone-real.md)) returned **HARNESS-ONLY (no
verdict)** because it had only 1 session (floor is ≥3).
- **[USER]** Capture ≥2 more real Claude Code work-sessions (ideally different projects) and drop the
  `.jsonl` transcripts into `src/context_curator/eval/fixtures/_real_local/` (gitignored, never committed).
- **[AGENT]** Re-run harvest → keystone → cluster_bootstrap → precision_gate (recipe in keystone-real.md
  §Reproducibility). The verdict then drives whether to ship BM25 as the ranker (and demote/dark the
  semantic path) or keep the semantic gate. Production stays dark (`CC_CURATOR_ONLOAD="0"`) until then.

### 3. Land this branch
Push `chore/post-m7-followups` and open a PR to `main` — **after** the diagnostic is reverted (or open
the PR but do not merge until reverted). User has not authorized push yet.

## Gotchas to carry forward (full list in the digest)
- **Never run both wirings:** dev `.claude/settings.json` hooks + installed plugin + running `cc-mcp`
  → blocks every Write/Edit/Bash via a locked-exe error. Keep `settings.json` = `{}`.
- **Hooks must stay `[project.gui-scripts]`** (pythonw) — console scripts reintroduce the #12 flicker.
- **No per-chunk eviction in the CLI** — `cc_evict` is store-only. Don't design around window eviction.

## Suggested skills for the next agent
- `superpowers:finishing-a-development-branch` — to push `chore/post-m7-followups` and open the PR.
- `superpowers:verification-before-completion` — before claiming #14 done or the diagnostic reverted.
- `verify` / `qa-runner` — if validating plugin behavior end-to-end in the scratch repo.
