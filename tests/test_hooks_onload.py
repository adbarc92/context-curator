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
