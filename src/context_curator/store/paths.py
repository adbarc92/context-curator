"""Single source of the DB path, shared by hooks and the MCP server (design §3.4 C2)."""
from __future__ import annotations

import os
from pathlib import Path


def resolve_db_path() -> str:
    """Absolute, CWD-independent DB path. `$CC_DB_PATH` wins; else (plugin-across-repos)
    `$CLAUDE_PROJECT_DIR/.context-curator/store.db`; else `<project>/.context-curator/store.db`
    (project root = nearest ancestor with .git/pyproject.toml); else `~/.context-curator/store.db`.
    Absolute so hook subprocesses and the server never resolve to different files. The
    $CLAUDE_PROJECT_DIR branch is load-bearing for the uv-tool install: the installed package's
    __file__ lives in the isolated tool venv, whose ancestors have their own pyproject.toml, so the
    walk below would otherwise resolve the store inside the tool dir, never the user's repo."""
    env = os.environ.get("CC_DB_PATH")
    if env:
        return str(Path(env).expanduser().resolve())
    proj = os.environ.get("CLAUDE_PROJECT_DIR")   # plugin runs across repos -> per-project store
    if proj:
        return str((Path(proj) / ".context-curator" / "store.db").resolve())
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return str(parent / ".context-curator" / "store.db")
    return str(Path.home() / ".context-curator" / "store.db")
