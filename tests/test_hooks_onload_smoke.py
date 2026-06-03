import json
import os
import pathlib
import subprocess
import sys
import time

from context_curator.embeddings import HashingEmbedder
from context_curator.hooks import user_prompt_submit as ups
from context_curator.store.sqlite_store import SqliteStore


def _run(module, event, env):
    return subprocess.run([sys.executable, "-m", module], input=json.dumps(event),
                          capture_output=True, text=True, env=env)


def test_settings_registers_both_onload_hooks():
    # The red->green anchor for this task: empty arrays -> IndexError before registration.
    # Anchor the path to the repo root (parents[1]) so CWD doesn't matter.
    settings_path = pathlib.Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    hooks = settings["hooks"]
    ups_cmd = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    ss_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "user_prompt_submit" in ups_cmd
    assert "session_start" in ss_cmd


def test_user_prompt_submit_stdout_is_exactly_inject_json(tmp_path):
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store(
        "session:s:tool:c", "authenticate authorize user session token")
    r = _run("context_curator.hooks.user_prompt_submit",
             {"prompt": "authenticate authorize user session",
              "hook_event_name": "UserPromptSubmit"}, env)
    assert r.returncode == 0
    obj = json.loads(r.stdout)                       # parses cleanly => stdout is pure JSON
    assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "session:s:tool:c" in obj["hookSpecificOutput"]["additionalContext"]
    assert r.stdout == json.dumps(obj)               # exact bytes: no prefix/suffix/newline
    # the breadcrumb + DB-path diagnostics go to STDERR, never polluting the stdout inject
    assert "context-curator: onloaded" in r.stderr


def test_user_prompt_submit_recency_fallback_injects_any_session_chunk(tmp_path):
    # M4b: curator is unavailable in subprocess (no running curator) -> recency fallback runs.
    # Recency-only has no cosine gate, so off-topic session chunks are injected regardless.
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store(
        "session:s:tool:c", "quarterly financial revenue spreadsheet")
    r = _run("context_curator.hooks.user_prompt_submit",
             {"prompt": "authenticate authorize user session",
              "hook_event_name": "UserPromptSubmit"}, env)
    assert r.returncode == 0
    # recency fallback: no cosine gate -> off-topic chunk is injected
    obj = json.loads(r.stdout)                      # parses cleanly => no leading garbage
    assert r.stdout == json.dumps(obj)              # EXACT bytes: no prefix/suffix/trailing newline
    assert "session:s:tool:c" in obj["hookSpecificOutput"]["additionalContext"]
    assert "[recency]" in r.stderr


def test_session_start_stdout_is_exactly_inject_json(tmp_path):
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store("p", "a pinned decision", pin=True)
    r = _run("context_curator.hooks.session_start",
             {"hook_event_name": "SessionStart", "source": "startup"}, env)
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert r.stdout == json.dumps(obj)
    assert "p" in obj["hookSpecificOutput"]["additionalContext"]


def test_onload_latency_ceiling_1000_chunks(tmp_path):
    # round-3 I3: declared ceiling — at 1000 live chunks UserPromptSubmit p50 < 300ms on the
    # dev reference machine. Generous; if a slow CI trips it, convert to xfail rather than
    # weakening the budget.
    s = SqliteStore(db_path=str(tmp_path / "big.db"), embedder=HashingEmbedder())
    for i in range(1000):
        s.store(f"session:x:tool:c{i}", f"chunk {i} authenticate authorize user session")
    event = {"prompt": "authenticate authorize user session", "hook_event_name": "UserPromptSubmit"}
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        ups.handle(event, s)
        times.append(time.perf_counter() - t0)
    times.sort()
    p50 = times[len(times) // 2]
    assert p50 < 0.3, f"p50={p50*1000:.0f}ms exceeds 300ms at 1000 chunks"
