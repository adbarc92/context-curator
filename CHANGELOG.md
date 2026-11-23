# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed
- **Onload ranks by refetch stickiness (Track A).** With the semantic curator dark, the
  `UserPromptSubmit` floor now ranks live chunks by how often their file was already fetched,
  BM25 breaking ties, newer first, instead of pure recency. It budgets on the rendered (≤ 1 200
  char) body, so large sticky files are injected rather than rejected, and drops byte-identical
  duplicates. Only the newest 4 M chars of the store compete (`ONLOAD_POOL_CHARS`), so hook
  latency stays bounded on busy days. The decision log records source `stickiness`.
- BM25 scoring moved to `context_curator.bm25`, bit-identical to the previous implementation.
  On a ~3 M-char store a short prompt scores in ~15-30 ms and a 3 000-word pasted prompt in
  ~150 ms; cost no longer scales with prompt vocabulary x store size.

### Added
- `PostToolUse` tags each captured tool result with `entity:<canonical path>`.
- `python -m context_curator.eval.stickiness`: the pre-registered confirmatory run for the
  shipped ranker (clustered CI, precision gate, 45-session floor, gap sweep).

### Fixed
- Live chunks are keyed on Claude Code's `tool_use_id`. The hook read `call_id`, which Claude
  Code never sends, so every key was a content hash and identical re-reads collapsed into one row.

## [0.0.2] - 2026-06-07

### Added
- **M7 — Claude Code plugin packaging.** The repo is now an installable plugin:
  `.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, and a local
  `.claude-plugin/marketplace.json`, all invoking bare `cc-*` console scripts installed via
  `uv tool install`. Seven new entry points (`cc-mcp`, `cc-inspect`, five `cc-hook-*`).
- Per-project store: `resolve_db_path` honours `$CLAUDE_PROJECT_DIR`, so the plugin stores under
  each user repo instead of inside the isolated tool venv.
- Install/verify docs (`docs/plugin-install.md`, `docs/M7-runbook.md`) and an end-to-end smoke
  (`scripts/verify-plugin.ps1`) that drives the real `cc-*` shims through the capture → store →
  inject → working-set loop plus MCP liveness — re-runnable, no Claude binary required.

### Changed
- The dev `.claude/settings.json` hook block is removed (`{}`); the plugin is the single source of
  hook wiring, preventing double-firing alongside an installed plugin.
- Hook entry points are now `[project.gui-scripts]` (pythonw / GUI subsystem) rather than console
  scripts, so Claude Code spawning them per event no longer flashes a console window on Windows;
  their stdin/stdout contract is preserved because Claude pipes those handles.

### Fixed
- Windows console-window flicker from the curator: the `tasklist` liveness probe runs with
  `CREATE_NO_WINDOW`, and the detached curator spawns with a hidden `STARTUPINFO` (`SW_HIDE`).
  The residual upstream Claude Code spawning behavior is tracked in #12.
