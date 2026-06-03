import socket
import threading

import pytest

from context_curator.curator import client, runtime


def _fake_server(token, *, good_proof=True, then_keys=None, delay=0.0):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def serve():
        import json
        import time
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        hello = json.loads(f.readline())
        received["hello_has_prompt"] = "prompt" in hello
        if delay:
            time.sleep(delay)
        proof = runtime.make_proof(token, hello["nonce"]) if good_proof else "bad"
        f.write((json.dumps({"server": "context-curator", "proof": proof}) + "\n").encode())
        f.flush()
        try:
            line = f.readline()
            received["sent_prompt"] = bool(line) and "prompt" in json.loads(line)
        except Exception:
            received["sent_prompt"] = False
        f.write((json.dumps({"ok": True, "keys": then_keys or []}) + "\n").encode())
        f.flush()
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return port, received, srv


def test_good_proof_then_prompt_returns_keys():
    token = runtime.new_token()
    port, received, srv = _fake_server(token, then_keys=["k1", "k2"])
    keys = client.request_onload_at("127.0.0.1", port, token, "my prompt", k=10, token_budget=None,
                                    deadline_s=2.0)
    assert keys == ["k1", "k2"]
    assert received["hello_has_prompt"] is False        # hello carried NO prompt
    assert received["sent_prompt"] is True
    srv.close()


def test_bad_proof_never_sends_prompt():
    token = runtime.new_token()
    port, received, srv = _fake_server(token, good_proof=False)
    with pytest.raises(client.CuratorUnavailable):
        client.request_onload_at("127.0.0.1", port, token, "secret prompt", k=10,
                                 token_budget=None, deadline_s=2.0)
    assert received.get("sent_prompt", False) is False  # prompt NEVER transmitted (round-1 C1)
    srv.close()


def test_refused_raises_unavailable():
    with pytest.raises(client.CuratorUnavailable):
        client.request_onload_at(
            "127.0.0.1", 1, "tok", "p", k=10, token_budget=None, deadline_s=0.2
        )


def test_iso_age_s_valid_and_garbage():
    # regression: _iso_age_s must NOT raise (it ran datetime.now(datetime.UTC) -> AttributeError,
    # which escaped request_onload on the warming path and broke fail-open)
    age = client._iso_age_s("2020-01-01T00:00:00+00:00")
    assert age > 0                                   # a past timestamp -> positive age
    assert client._iso_age_s("not-a-date") == 1e9    # garbage -> huge age (treated stale)


def test_request_onload_warming_raises_unavailable_no_crash(tmp_path, monkeypatch):
    # a live 'warming' runtime file must yield CuratorUnavailable(respawn=False) via discover,
    # NOT an AttributeError out of _iso_age_s (fail-open regression guard)
    import os

    from context_curator.curator import config
    db = str(tmp_path / "s.db")
    info = {"state": "warming", "pid": os.getpid(), "port": 1, "token": "t", "dim": 384,
            "started_at": "2020-01-01T00:00:00+00:00", "embedder": "bge"}
    runtime.write_runtime(config.runtime_path(db), info)
    monkeypatch.setenv("CC_DB_PATH", db)
    with pytest.raises(client.CuratorUnavailable):
        client.request_onload("p", k=10, token_budget=None)


def test_server_ok_false_raises_unavailable():
    # a server that handshakes correctly but answers {ok: False} -> CuratorUnavailable
    token = runtime.new_token()
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0))
    s2.listen(1)
    p2 = s2.getsockname()[1]

    def serve():
        import json
        conn, _ = s2.accept()
        f = conn.makefile("rwb")
        hello = json.loads(f.readline())
        proof = runtime.make_proof(token, hello["nonce"])
        f.write((json.dumps({"server": "context-curator", "proof": proof}) + "\n").encode())
        f.flush()
        f.readline()
        f.write((json.dumps({"ok": False}) + "\n").encode())
        f.flush()
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(client.CuratorUnavailable):
        client.request_onload_at("127.0.0.1", p2, token, "p", k=10, token_budget=None,
                                 deadline_s=2.0)
    s2.close()


def test_single_deadline_times_out_within_budget():
    token = runtime.new_token()
    port, _received, srv = _fake_server(token, delay=0.5)   # server stalls 0.5s between round-trips
    import time
    t0 = time.monotonic()
    with pytest.raises(client.CuratorUnavailable):
        client.request_onload_at("127.0.0.1", port, token, "p", k=10, token_budget=None,
                                 deadline_s=0.1)             # budget 0.1s < 0.5s stall
    assert time.monotonic() - t0 < 0.4                       # one shared deadline, not 2x
    srv.close()


def test_request_onload_corrupt_ready_file_fails_open(tmp_path, monkeypatch):
    db = str(tmp_path / "s.db")
    from context_curator.curator import config
    # state 'ready' but missing port/token -> must raise CuratorUnavailable, not KeyError
    runtime.write_runtime(config.runtime_path(db),
                          {"state": "ready", "started_at": "2026-06-02T00:00:00+00:00"})
    monkeypatch.setenv("CC_DB_PATH", db)
    with pytest.raises(client.CuratorUnavailable):
        client.request_onload("p", k=10, token_budget=None)
