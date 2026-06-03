import json

import pytest

import context_curator.hooks._io as io
from context_curator.hooks._io import HookResult, run_hook


def _noexit(monkeypatch):
    monkeypatch.setattr(io.sys, "exit", lambda code: None)


def test_inject_emits_exact_json_no_trailing_newline(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "UserPromptSubmit"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(0, additional_context="HELLO"), needs_store=False)
    out = capsys.readouterr()
    expected = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "HELLO"}}
    )
    assert out.out == expected          # EXACT bytes — no prefix, no trailing newline
    assert out.err == ""                # nothing leaked to stderr


def test_no_additional_context_writes_nothing_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "UserPromptSubmit"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(0), needs_store=False)
    assert capsys.readouterr().out == ""


def test_message_goes_to_stderr_not_stdout(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "X"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(0, message="note", additional_context="CTX"), needs_store=False)
    out = capsys.readouterr()
    assert "note" in out.err and "note" not in out.out
    assert "CTX" in out.out          # the inject still goes to stdout (exit 0)


def test_onload_failure_logs_distinct_label(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {})
    monkeypatch.setattr(io, "open_store", lambda: object())   # avoid touching a real DB
    _noexit(monkeypatch)

    def boom(event, store):
        raise RuntimeError("x")

    run_hook(boom, needs_store=True, fail_label="onload")
    assert "onload failed" in capsys.readouterr().err         # greppable, distinct from "capture"


def test_blocking_result_does_not_emit_inject(monkeypatch, capsys):
    # exit 2 (block) must not write the inject JSON to stdout even if additional_context is set
    monkeypatch.setattr(io, "read_event", lambda: {"hook_event_name": "X"})
    _noexit(monkeypatch)
    run_hook(lambda e: HookResult(2, "blocked", additional_context="SHOULD_NOT_APPEAR"),
             needs_store=False)
    out = capsys.readouterr()
    assert out.out == ""
    assert "SHOULD_NOT_APPEAR" not in out.out


def _sqlite(tmp_path):
    # real backend that ships (round-3 I2): exercises seq-DESC order + JSON deserialize.
    # Local imports — this block is appended BELOW Task 5's tests, so module-level imports
    # here would trip ruff E402/I (the project lints E + I).
    from context_curator.embeddings import HashingEmbedder
    from context_curator.store.sqlite_store import SqliteStore
    return SqliteStore(db_path=str(tmp_path / "o.db"), embedder=HashingEmbedder())


def test_ups_curator_up_uses_keys_in_rank_order(tmp_path, monkeypatch):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:a", "alpha alpha")
    s.store("session:x:tool:b", "beta beta")          # b is NEWER -> all_live_chunks = [b, a]
    # curator ranks a BEFORE b (the REVERSE of recency) -> the block must follow the curator,
    # not seq order; a seq-order bug would put b first and fail this.
    monkeypatch.setattr(ups.client, "request_onload",
                        lambda prompt, *, k, token_budget: ["session:x:tool:a", "session:x:tool:b"])
    r = ups.handle({"prompt": "anything", "hook_event_name": "UserPromptSubmit"}, s)
    ctx = r.additional_context
    assert ctx.index("session:x:tool:a") < ctx.index("session:x:tool:b")


def test_ups_curator_down_falls_back_to_recency_and_spawns(tmp_path, monkeypatch, capsys):
    from context_curator.curator.client import CuratorUnavailable
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:a", "alpha")

    def boom(prompt, *, k, token_budget):
        raise CuratorUnavailable("down", respawn=True)

    spawned = {}
    monkeypatch.setattr(ups.client, "request_onload", boom)
    monkeypatch.setattr(ups.runtime, "spawn_detached", lambda db: spawned.setdefault("yes", True))
    r = ups.handle({"prompt": "anything"}, s)
    assert "session:x:tool:a" in (r.additional_context or "")    # recency fallback injected
    assert spawned.get("yes") is True
    assert "[recency]" in capsys.readouterr().err


def test_ups_warming_falls_back_without_spawn(tmp_path, monkeypatch):
    from context_curator.curator.client import CuratorUnavailable
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:a", "alpha")

    def warming(prompt, *, k, token_budget):
        raise CuratorUnavailable("warming", respawn=False)

    spawned = {}
    monkeypatch.setattr(ups.client, "request_onload", warming)
    monkeypatch.setattr(ups.runtime, "spawn_detached", lambda db: spawned.setdefault("yes", True))
    ups.handle({"prompt": "anything"}, s)
    assert spawned.get("yes") is None                            # warming -> NO respawn


def test_ups_empty_prompt_no_injection(tmp_path, capsys):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    assert ups.handle({"prompt": "   "}, s).additional_context is None
    assert "empty prompt" in capsys.readouterr().err


def test_session_start_seeds_pins_and_conventions(tmp_path):
    from context_curator.hooks import session_start as ss
    s = _sqlite(tmp_path)
    s.store("pinnedkey", "an important pinned decision", pin=True)
    s.store("proj:myapp:conventions", "the project conventions body")
    s.store("session:x:tool:c1", "ordinary captured output", pin=False)
    r = ss.handle({"hook_event_name": "SessionStart", "source": "compact"}, s)
    ctx = r.additional_context
    assert ctx is not None
    assert "pinnedkey" in ctx and "proj:myapp:conventions" in ctx
    assert "session:x:tool:c1" not in ctx          # non-pin non-convention not seeded


def test_session_start_empty_store_no_injection(tmp_path):
    from context_curator.hooks import session_start as ss
    s = _sqlite(tmp_path)
    r = ss.handle({"hook_event_name": "SessionStart", "source": "startup"}, s)
    assert r.additional_context is None


def test_session_start_breadcrumb_reports_count(tmp_path, capsys):
    from context_curator.hooks import session_start as ss
    s = _sqlite(tmp_path)
    s.store("pinnedkey", "a pinned decision", pin=True)
    ss.handle({"hook_event_name": "SessionStart", "source": "startup"}, s)
    assert "seeded 1 pinned/convention chunk" in capsys.readouterr().err


@pytest.mark.parametrize("source", ["startup", "resume", "compact", "clear"])
def test_session_start_ignores_source(tmp_path, source):
    # round-3 I1: the durable set is re-seeded regardless of source (incl. compact, which is
    # exactly when the window was just trimmed). Same output for every source value.
    from context_curator.hooks import session_start as ss
    s = _sqlite(tmp_path)
    s.store("pinnedkey", "a pinned decision", pin=True)
    r = ss.handle({"hook_event_name": "SessionStart", "source": source}, s)
    assert r.additional_context is not None
    assert "pinnedkey" in r.additional_context


def test_ups_curator_returns_empty_falls_back_to_recency(tmp_path, monkeypatch):
    # the DARK DEFAULT: a warm curator with the flag off returns {keys:[]} (no exception);
    # the hook must inject the recency floor, NOT nothing (final-review C1)
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:a", "alpha")
    monkeypatch.setattr(ups.client, "request_onload", lambda prompt, *, k, token_budget: [])
    r = ups.handle({"prompt": "anything", "hook_event_name": "UserPromptSubmit"}, s)
    assert "session:x:tool:a" in (r.additional_context or "")    # recency floor injected
