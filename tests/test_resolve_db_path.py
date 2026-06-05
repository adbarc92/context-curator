import os
from pathlib import Path

from context_curator.store.paths import resolve_db_path


def test_env_var_wins_and_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "x.db"))
    p = resolve_db_path()
    assert Path(p).is_absolute()
    assert p == str((tmp_path / "x.db").resolve())


def test_default_is_absolute_and_cwd_independent(monkeypatch):
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)   # else the new branch fires (round-3 I2)
    p = resolve_db_path()
    assert Path(p).is_absolute()
    assert p.endswith(os.path.join(".context-curator", "store.db"))


def test_mcp_and_hook_resolve_identically(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "shared.db"))
    from context_curator import mcp_server
    # build_default_store must use resolve_db_path under the hood
    store = mcp_server.build_default_store()
    assert Path(store._conn.execute("PRAGMA database_list").fetchall()[0]["file"]) \
        == Path(resolve_db_path())
    store.close()


def test_claude_project_dir_branch(monkeypatch, tmp_path):
    # Plugin-across-repos: $CLAUDE_PROJECT_DIR (and not CC_DB_PATH) -> store under that repo.
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    p = resolve_db_path()
    assert p == str((tmp_path / ".context-curator" / "store.db").resolve())


def test_cc_db_path_still_wins_over_claude_project_dir(monkeypatch, tmp_path):
    # CC_DB_PATH must keep priority over the new branch (existing overrides intact).
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "explicit.db"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "other_repo"))
    assert resolve_db_path() == str((tmp_path / "explicit.db").resolve())


def test_claude_project_dir_matches_cc_db_path_for_same_dir(monkeypatch, tmp_path):
    # Locks hook<->MCP consistency (round-1 M1): the path the CLAUDE_PROJECT_DIR branch yields for
    # <proj> must byte-equal what an explicit CC_DB_PATH=<proj>/.context-curator/store.db yields.
    expected_db = tmp_path / ".context-curator" / "store.db"
    monkeypatch.delenv("CC_DB_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    via_branch = resolve_db_path()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CC_DB_PATH", str(expected_db))
    via_env = resolve_db_path()
    assert via_branch == via_env
