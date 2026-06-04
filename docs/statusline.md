# ContextCurator statusline + decision log

The curator records every onload injection to a durable, per-session **decision log**, and ships a
Claude Code **statusLine** indicator that surfaces the latest decision. Both are local-only.

## What the indicator shows

```
CC ws:8 +2/-1 [recency]
   │     │  │   └─ source: curator (bge policy) | recency (fallback) | none (nothing injected)
   │     │  └───── paged-out: keys that left the injected window since the previous turn
   │     └──────── paged-in: keys that entered the window since the previous turn
   └────────────── working-set: how many chunks were injected THIS turn
```

"Working set" and "page-in/out" are about the **injection window** (the slice the curator injects each
turn), not the persistent store — there is no per-chunk eviction in the CLI. `CC ·` means idle (no
decision recorded for the current session yet).

## Enable the statusLine

Add to `.claude/settings.json`. **Recommended (direct venv executable — fastest, no re-sync):**

```json
{
  "statusLine": {
    "type": "command",
    "command": "D:/MajorProjects/INFRASTRUCTURE/context-curator/.venv/Scripts/cc-statusline.exe"
  }
}
```

- POSIX: use `.../.venv/bin/cc-statusline`.
- Use **forward slashes** in the path on Windows (Claude Code runs the command via Git Bash/PowerShell).
- Assumes you have run `uv sync` (which creates the `cc-statusline` entry point in `.venv`).

**Fallback (no `uv sync` needed; slower, and re-syncs ~0.8 s on the render after any `pyproject.toml`
edit):**

```json
{
  "statusLine": {
    "type": "command",
    "command": "uv run --project D:/MajorProjects/INFRASTRUCTURE/context-curator cc-statusline"
  }
}
```

> **`$CC_DB_PATH` caveat:** if you override the curator db path with `$CC_DB_PATH`, the statusLine
> command's environment must inherit the same value, or it will read a different `decisions/` dir and
> show `CC ·`. Without the override, the hook and statusLine resolve to the same path automatically.

## Inspect the decision log

```
uv run python -m context_curator.observe.decision_log --tail 20
# or a specific session:
uv run python -m context_curator.observe.decision_log --session <session_id> --tail 50
```

Each line: `<ts> [<source>] ws:N +<in>/-<out>  "<prompt preview>"`. Default (no `--session`) reads the
most-recently-active session's log. `--tail` returns at most ~300 records (the read is bounded).

## Privacy

Decision records (prompt previews + chunk keys) live under `.context-curator/decisions/` beside the
SQLite store — already gitignored, local-only, never committed or transmitted. The decision log is a
sidecar; it never writes to the chunk store.
