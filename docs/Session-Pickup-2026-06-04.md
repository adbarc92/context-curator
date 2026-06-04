# Session Pickup — 2026-06-04

**Branch:** `feat/m7-plugin-package` (off `main`)
**Active spec:** [`docs/superpowers/specs/2026-06-04-m7-plugin-package-design.md`](superpowers/specs/2026-06-04-m7-plugin-package-design.md) — committed `ef523f0`, hardened through 3 critique rounds.
**Active plan:** none yet — the next step is `superpowers:writing-plans` for M7.

## Where we are
M7 (the final roadmap milestone — package ContextCurator as a Claude Code plugin) is **at the
spec-review gate**. The spec is written + committed; **the implementation plan has not been written and
no code has been touched.** Resume sequence: (1) user reviews the spec, (2) `writing-plans`, (3)
subagent-driven implementation, (4) the manual/out-of-session verification (below).

**Roadmap:** M0–M6 are all **done and merged** — M4c #7, M4d #8, M5 #9, M6 #10. M7 is the last one.

## The M7 design in one breath (read the spec for detail)
The repo *becomes* the plugin: `.claude-plugin/plugin.json` + `hooks/hooks.json` + `.mcp.json` +
`marketplace.json` at the repo root, **pure config** that calls bare `cc-*` console scripts. The
curator is installed machine-globally via **`uv tool install --editable <checkout>`** (no PyPI),
adding **7 new `[project.scripts]`** (`cc-mcp`, `cc-inspect`, 5× `cc-hook-*`) beside the existing
`cc-statusline`. Per-project store via **one new branch in `resolve_db_path`** (`$CLAUDE_PROJECT_DIR`).
The dev `.claude/settings.json` hook block is **removed** (→ `{}`) so hooks don't double-fire.

## Three things that make M7 implementation non-trivial — do NOT forget
1. **Two RECORDED EXIT CRITERIA require a Claude Code restart + fresh shell** (inherently
   out-of-session, spec §7.4): (a) does `CLAUDE_PROJECT_DIR` actually reach the `cc-mcp` subprocess
   `os.environ`? (if not, the MCP store diverges from the hooks' → user must pin `CC_DB_PATH`); (b)
   does the **marketplace/cache** install (`~/.claude/plugins/cache`) resolve bare `cc-*` on **PATH**?
   M7 is "done" only when both are **observed and written into `docs/plugin-install.md`** — not assumed.
2. **M7 self-modifies the running session's own harness** — it edits `.claude/settings.json` (removes
   the hook block) and `src/context_curator/store/paths.py`. These are the hooks injecting context +
   writing the decision log for the live session. Do these edits deliberately at the start, expect the
   current session's hook behavior to shift, and re-verify.
3. **PATH is the load-bearing risk** (the M6 statusline lesson, relived in critique rounds 1–2). Bare
   `cc-*` names depend on `uv tool update-shell` + restart. The install procedure has a **hard
   fresh-shell resolution gate** (`Get-Command cc-mcp` / `command -v` — NOT `cc-mcp --help`, which
   blocks: FastMCP `run()` has no argparse). Document the absolute-shim escape hatch for locked-down envs.

## Critique-round reshaping (so the next session doesn't re-tread)
The spec's FINAL mechanism is **uv-tool console scripts** — NOT the `uv run --project
${CLAUDE_PLUGIN_ROOT}` framing from my first draft. Round 1 killed that (Claude copies marketplace
plugins to a cache dir without the gitignored `.venv` → cold-sync/fail). Round 2 dropped a poisoning
`.mcp.json` `CC_DB_PATH` override (literal `${CLAUDE_PROJECT_DIR}` if unexpanded) and caught that
removing the settings.json hooks breaks `tests/test_hooks_onload_smoke.py::test_settings_registers_both_onload_hooks`
(must be **repointed at `hooks/hooks.json`**). Round 3 fixed the broken `cc-mcp --help` probe and
promoted the two runtime facts to exit criteria. The Design Critique Log in the spec has the full trail.

## Known plan-shaping notes for writing-plans
- `mcp` is a **core** dep (not an extra) — `cc-mcp` runs from a plain install. `embed` (bge) stays optional.
- All 5 hook modules + `mcp_server` + `observe.decision_log` already have stdin/argv-driven `main()`s
  (verified) — the 7 entry points are pure wiring.
- The `resolve_db_path` test (`tests/test_resolve_db_path.py`) must `monkeypatch.delenv("CLAUDE_PROJECT_DIR")`
  in env-default cases (a Claude-session pytest inherits it); the `home` branch is unreachable in-repo
  → descope that assertion (YAGNI).
- No plan-vs-code drift or bugs this session — it was design-only.

## Deferred / future (recorded, not this branch)
- M4d real-data **production flip** (needs ≥3 captured sessions; harness is merged, verdict was
  harness-only on 1 session).
- M5 **real-session eviction-regret** (reuses M4d's labeling; deferred until enough sessions).
- M7 out-of-scope: `cc-explorer`/`cc-guard` subagents + orchestration skill (never built — the home for
  the M5-deferred offload loop); PyPI/`uvx` distribution; a plugin-provided statusLine (Claude Code
  doesn't allow it).

## What to pick up next
**User reviews the M7 spec → then run `superpowers:writing-plans`** to produce
`docs/superpowers/plans/2026-06-04-m7-plugin-package.md`, then subagent-driven implementation. Expect
the milestone to pause at the two out-of-session verification steps (§7.4) that need a Claude restart.

## Commands worth remembering
- Everything runs via `uv run` (e.g. `uv run pytest -q`, `uv run ruff check .`). Ignore the
  `VIRTUAL_ENV` mismatch warning (stderr).
- The local real-transcript corpus lives at `src/context_curator/eval/fixtures/_real_local/`
  (gitignored; 1 session so far — that's why M4d's real verdict was harness-only).
