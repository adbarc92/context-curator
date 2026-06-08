# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/).

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
