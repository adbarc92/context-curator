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


def test_plugin_registers_both_onload_hooks():
    # M7: the plugin (hooks/hooks.json) is the single source of hook wiring; .claude/settings.json
    # no longer carries hooks (it would double-fire alongside the plugin). Assert the plugin wires
    # the two onload events to their cc-* console scripts.
    hooks_path = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"
    hooks = json.loads(hooks_path.read_text())["hooks"]
    ups_cmd = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    ss_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert ups_cmd == "cc-hook-user-prompt"
    assert ss_cmd == "cc-hook-session-start"


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


def test_user_prompt_submit_stickiness_floor_injects_any_session_chunk(tmp_path):
    # M4b: curator is unavailable in subprocess (no running curator) -> the floor runs.
    # Track A: the floor is stickiness; it has no relevance gate, so off-topic chunks still inject.
    db = str(tmp_path / "s.db")
    env = {**os.environ, "CC_DB_PATH": db}
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store(
        "session:s:tool:c", "quarterly financial revenue spreadsheet")
    r = _run("context_curator.hooks.user_prompt_submit",
             {"prompt": "authenticate authorize user session",
              "hook_event_name": "UserPromptSubmit"}, env)
    assert r.returncode == 0
    # floor: no relevance gate -> off-topic chunk is injected
    obj = json.loads(r.stdout)                      # parses cleanly => no leading garbage
    assert r.stdout == json.dumps(obj)              # EXACT bytes: no prefix/suffix/trailing newline
    assert "session:s:tool:c" in obj["hookSpecificOutput"]["additionalContext"]
    assert "[stickiness]" in r.stderr


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


def _realistic_store(tmp_path, n_chunks=900, seed=0):
    # Real stores hold ~900 chunks / ~3-4 M chars (most of them entity-tagged Reads/Edits).
    import random

    from context_curator.embeddings import NullEmbedder
    from context_curator.entities import entity_tags
    rng = random.Random(seed)
    s = SqliteStore(db_path=str(tmp_path / "real.db"), embedder=NullEmbedder())
    total = 0
    for i in range(n_chunks):
        body = " ".join(rng.choice(_WORDS) for _ in range(min(int(rng.lognormvariate(7.6, 1.1)),
                                                              32_768) // 6))
        total += len(body)
        tags = ["read", *entity_tags({rng.choice(_PATHS)})] if rng.random() < 0.6 else ["bash"]
        s.store(f"session:x:tool:t{i}", body, tags=tags)
    return s, total


_WORDS = [f"w{i}" for i in range(5000)] + ["def", "return", "import", "class", "self", "the"]
_PATHS = [f"/repo/src/mod{i}/file{j}.py" for i in range(30) for j in range(6)]


def _p50(event, store, runs=5):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        ups.handle(event, store)
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[len(times) // 2]


def test_onload_latency_ceiling_realistic_store(tmp_path, monkeypatch):
    # Track A: the stickiness floor runs BM25 over the store each prompt. Hold the same declared
    # ceiling (p50 < 300 ms) at real-store size.
    monkeypatch.setattr(ups.client, "request_onload", lambda *a, **k: [])
    s, total = _realistic_store(tmp_path)
    assert total > 3_000_000
    event = {"prompt": "fix the w12 w400 import in mod3", "hook_event_name": "UserPromptSubmit"}
    p50 = _p50(event, s)
    assert p50 < 0.3, f"p50={p50*1000:.0f}ms exceeds 300ms on a realistic-size store"


def test_onload_latency_ceiling_long_prompt(tmp_path, monkeypatch):
    # A pasted log or stack trace is a prompt with thousands of distinct words; BM25 cost must
    # not scale with prompt vocabulary x store size.
    import random
    monkeypatch.setattr(ups.client, "request_onload", lambda *a, **k: [])
    s, _ = _realistic_store(tmp_path)
    prompt = " ".join(random.Random(1).choices(_WORDS, k=3000))
    p50 = _p50({"prompt": prompt, "hook_event_name": "UserPromptSubmit"}, s, runs=3)
    assert p50 < 0.3, f"p50={p50*1000:.0f}ms exceeds 300ms on a 3000-word prompt"


def test_onload_latency_ceiling_oversized_store(tmp_path, monkeypatch):
    # Every tool call is its own row (keyed on tool_use_id), so a busy day can outgrow the
    # realistic store several times over; the hook must stay in budget anyway (uncapped, ~6x the
    # realistic store measured 438 ms on the dev machine).
    monkeypatch.setattr(ups.client, "request_onload", lambda *a, **k: [])
    s, total = _realistic_store(tmp_path, n_chunks=6000)
    assert total > 18_000_000
    event = {"prompt": "fix the w12 w400 import in mod3", "hook_event_name": "UserPromptSubmit"}
    p50 = _p50(event, s)
    assert p50 < 0.3, f"p50={p50*1000:.0f}ms exceeds 300ms on a {total/1e6:.0f} M-char store"
