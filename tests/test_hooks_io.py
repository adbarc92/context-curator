import context_curator.hooks._io as io
from context_curator.hooks._io import HookResult, open_store, run_hook


def test_open_store_constructs_usable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_DB_PATH", str(tmp_path / "h.db"))
    store = open_store()                    # must not raise (C1: needs an embedder)
    store.store("k", "v")
    assert store.retrieve("k") is not None


def test_run_hook_maps_block_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(io, "read_event", lambda: {})
    rc = {}
    monkeypatch.setattr(io.sys, "exit", lambda code: rc.setdefault("code", code))
    run_hook(lambda event: HookResult(2, "blocked: x"), needs_store=False)
    assert rc["code"] == 2
    assert "blocked: x" in capsys.readouterr().err


def test_run_hook_fail_open_on_exception(monkeypatch):
    monkeypatch.setattr(io, "read_event", lambda: {})
    rc = {}
    monkeypatch.setattr(io.sys, "exit", lambda code: rc.setdefault("code", code))
    def boom(event):
        raise RuntimeError("boom")
    run_hook(boom, needs_store=False)
    assert rc["code"] == 0                  # fail-open
