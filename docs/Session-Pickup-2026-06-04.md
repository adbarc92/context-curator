# Session Pickup — 2026-06-04

**Branch:** `feat/m7-plugin-package` (off `main`)
**Active plan:** [`docs/superpowers/plans/2026-06-04-m7-plugin-package.md`](superpowers/plans/2026-06-04-m7-plugin-package.md) — committed `da8e631`.
**Active spec:** [`docs/superpowers/specs/2026-06-04-m7-plugin-package-design.md`](superpowers/specs/2026-06-04-m7-plugin-package-design.md) — committed `ef523f0`.

## Where we are
The plan was written (`writing-plans`) and executed **subagent-driven** (fresh implementer + spec
review + code-quality review per task). **All six in-session tasks are done, committed, and passed a
final whole-branch review.** Full suite **342 passed, 6 skipped**. What remains is **Tasks 7–8 — the
two out-of-session verification gates** that need a `uv tool install`, a fresh shell, and a Claude
Code restart. The milestone is "done" only when both recorded exit criteria are **observed and written
into [`docs/plugin-install.md`](plugin-install.md) §5** — they are currently `PENDING`.

## Plan progress

| Task | Status | Commit |
|---|---|---|
| 1 — `cc-*` console-script entry points | done | `7d37943` |
| 2 — `$CLAUDE_PROJECT_DIR` branch in `resolve_db_path` | done | `672ee7e` |
| 3 — 4 repo-as-plugin manifests | done | `13cf2c2` (+ test hardening `70c36dc`) |
| 4 — remove dev hook block (→ `{}`), repoint onload test | done | `289a797` |
| 5 — `scripts/verify-plugin.ps1` | done | `1de8658` (+ Fail-helper fix `3566910`) |
| 6 — `docs/plugin-install.md` + README pointer | done | `8600b21` (+ `cc_query` clarify `3a0c052`) |
| 7 — real marketplace/cache install → record exit criterion (b) | **pending (out-of-session)** | — |
| 8 — confirm `CLAUDE_PROJECT_DIR` reaches `cc-mcp` → record criterion (a) | **pending (out-of-session)** | — |

## Adaptations made vs. the plan
- **`resolve_db_path` test count:** the plan said "5 tests" in `tests/test_resolve_db_path.py`; the
  real count is **6** (the plan miscounted the pre-existing `test_mcp_and_hook_resolve_identically`).
  Implementer correctly kept all 3 originals + appended 3. No action needed — noted so the next
  session isn't surprised.
- **`marketplace.json` schema (the one field the plan flagged as unverified):** confirmed against the
  official Claude Code plugin-marketplace docs — `owner` is an object `{ "name": ... }` and
  `source: "./"` is a valid relative path (resolves to the repo/marketplace root). **No change** from
  the planned content; the flag is resolved.
- **Three review-driven fixes landed on top of their tasks** (not in the original plan, all small):
  manifest tests hardened to exact-event-set + all-required-keys (`70c36dc`); `verify-plugin.ps1`
  `Fail` helper made to exit deterministically via stderr (`3566910`); `plugin-install.md` reworded so
  `cc_query` reads as an MCP tool, not a shell command (`3a0c052`).

## Real bugs caught
- **None in the M7 implementation itself** (it was design-only drift; the final review found no
  Critical/Important integration issues).
- **Separate, pre-existing Windows bug found + fixed this session (NOT on this branch):** the curator
  flashed console windows during use. Root cause — `runtime.pid_alive`'s `tasklist` liveness probe ran
  with no `creationflags`, so Windows allocated a fresh console window on every onload while the
  curator warmed (`discover` → `pid_alive` → `tasklist`). Fixed with `CREATE_NO_WINDOW` on branch
  **`fix/windows-console-flicker`** (commit `43aca4e`) → **PR #11** to `main`.

## Known limitations
- **Two exit criteria are unobserved** (Tasks 7–8): (a) does `CLAUDE_PROJECT_DIR` reach the `cc-mcp`
  subprocess env? (if not, the MCP store diverges from the hooks' and pinning `CC_DB_PATH` becomes
  mandatory); (b) does the marketplace/**cache** install resolve bare `cc-*` on PATH? Both are
  Claude-runtime behaviors no static test or CI can settle — they require the out-of-session install.
- **The console-flicker fix is on its own branch, NOT on M7.** Running the Task 7 install verification
  on this branch will still flash windows until PR #11 merges to `main` (or you `git cherry-pick
  43aca4e` onto this branch). They touch disjoint files (`runtime.py` vs `paths.py`/manifests) — no
  merge conflict.

## What to pick up next
**Tasks 7–8 (out-of-session)** — the full step-by-step is in
[`docs/M7-runbook.md`](M7-runbook.md) (tailored YOU/CLAUDE checklist). In brief: restart Claude + a
fresh shell, then:
1. `uv tool install --editable <ABS_CHECKOUT>` → `uv tool update-shell` → restart →
   `scripts/verify-plugin.ps1` must print `VERIFY PASS` (the fresh-shell PATH gate).
2. `/plugin marketplace add <ABS_CHECKOUT>` → `/plugin install context-curator@context-curator` →
   restart; smoke the scratch repo (SessionStart, prompt-inject, a decision record under
   `<scratch>/.context-curator/decisions/`, `cc_query` via MCP). Record criterion (b) in
   `docs/plugin-install.md` §5(b).
3. Add the temporary stderr diagnostic to `cc-mcp` `main()` (plan Task 8 Step 1), reinstall, observe
   whether `CLAUDE_PROJECT_DIR` reaches the MCP env + whether its `db=` matches the hooks'; record
   criterion (a) in §5(a); **revert the diagnostic** + reinstall.
4. Both recorded → `superpowers:finishing-a-development-branch` (PR), and **delete this doc + the
   CLAUDE.md pickup block** (M7 lands).

## Servers / commands used
- Everything via `uv run` (e.g. `uv run pytest -p no:cacheprovider`, `uv run ruff check .`). Ignore the
  `VIRTUAL_ENV` mismatch warning on stderr.
- Known flake: `test_curator_lifecycle_and_handshake` occasionally fails under full-suite load
  (subprocess timing); passes in isolation — re-run it alone before treating it as a regression.
- Local real-transcript corpus: `src/context_curator/eval/fixtures/_real_local/` (gitignored, 1
  session) — why M4d's real verdict was harness-only.
