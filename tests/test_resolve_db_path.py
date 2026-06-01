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
