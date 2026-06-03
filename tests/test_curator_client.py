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
