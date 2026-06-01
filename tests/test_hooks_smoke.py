import json
import subprocess
import sys


def _run(module, event, env):
    return subprocess.run([sys.executable, "-m", module], input=json.dumps(event),
                          capture_output=True, text=True, env=env)


def test_pre_tool_use_blocks_env_write(tmp_path):
    import os
    env = {**os.environ, "CC_DB_PATH": str(tmp_path / "s.db")}
    r = _run("context_curator.hooks.pre_tool_use",
             {"tool_name": "Write", "tool_input": {"file_path": ".env", "content": "X=1"}}, env)
    assert r.returncode == 2


def test_pre_tool_use_allows_benign(tmp_path):
    import os
    env = {**os.environ, "CC_DB_PATH": str(tmp_path / "s.db")}
    r = _run("context_curator.hooks.pre_tool_use",
             {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "ok"}}, env)
    assert r.returncode == 0


def test_post_tool_use_captures(tmp_path):
    import os
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    r = _run("context_curator.hooks.post_tool_use",
             {"tool_name": "Bash", "session_id": "s1", "tool_input": {},
              "tool_response": "hello", "call_id": "c0"}, env)
    assert r.returncode == 0
    from context_curator.embeddings import HashingEmbedder
    from context_curator.store.sqlite_store import SqliteStore
    s = SqliteStore(db_path=db, embedder=HashingEmbedder(dim=16))
    assert s.retrieve("session:s1:tool:c0") is not None
