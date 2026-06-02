import json

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


def test_ups_relevant_chunk_named_in_block(tmp_path):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:c1", "authenticate authorize user session token")
    r = ups.handle({"prompt": "authenticate authorize user session",
                    "hook_event_name": "UserPromptSubmit"}, s)
    assert r.additional_context is not None
    assert "session:x:tool:c1" in r.additional_context


def test_ups_offtopic_prompt_no_injection(tmp_path):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:c1", "quarterly financial revenue spreadsheet")
    r = ups.handle({"prompt": "authenticate authorize user session"}, s)
    assert r.additional_context is None


def test_ups_whitespace_prompt_no_injection_and_breadcrumb(tmp_path, capsys):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    r = ups.handle({"prompt": "   "}, s)
    assert r.additional_context is None
    assert "empty prompt" in capsys.readouterr().err


def test_ups_pins_and_conventions_excluded(tmp_path):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("pinnedkey", "authenticate authorize user session", pin=True)
    s.store("proj:myapp:conventions", "authenticate authorize user session")
    s.store("session:x:tool:c1", "authenticate authorize user session")
    r = ups.handle({"prompt": "authenticate authorize user session"}, s)
    ctx = r.additional_context or ""
    assert "session:x:tool:c1" in ctx
    assert "pinnedkey" not in ctx and "proj:myapp:conventions" not in ctx


def test_ups_breadcrumb_reports_count(tmp_path, capsys):
    from context_curator.hooks import user_prompt_submit as ups
    s = _sqlite(tmp_path)
    s.store("session:x:tool:c1", "authenticate authorize user session")
    ups.handle({"prompt": "authenticate authorize user session"}, s)
    assert "onloaded 1 chunk" in capsys.readouterr().err
