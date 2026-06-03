import json
import socket
import threading

from context_curator.curator import runtime, server
from context_curator.embeddings import Embedder
from context_curator.store.sqlite_store import SqliteStore


class _Emb(Embedder):
    @property
    def dim(self):
        return 3

    def embed(self, text):
        return [1.0, 0.0, 0.0]


def test_serve_connection_handshake_then_ping(tmp_path):
    token = runtime.new_token()
    read_store = SqliteStore(db_path=str(tmp_path / "s.db"), embedder=_Emb())
    a, b = socket.socketpair()
    t = threading.Thread(target=server.serve_connection,
                         args=(a, token, read_store, _Emb()), daemon=True)
    t.start()
    f = b.makefile("rwb")
    nonce = runtime.new_nonce()
    f.write((json.dumps({"op": "hello", "nonce": nonce}) + "\n").encode())
    f.flush()
    banner = json.loads(f.readline())
    assert runtime.verify_proof(token, nonce, banner["proof"])
    f.write((json.dumps({"op": "ping"}) + "\n").encode())
    f.flush()
    assert json.loads(f.readline())["ok"] is True
    b.close()
    t.join(timeout=2)


def test_serve_connection_rejects_without_handshake(tmp_path):
    token = runtime.new_token()
    read_store = SqliteStore(db_path=str(tmp_path / "s.db"), embedder=_Emb())
    a, b = socket.socketpair()
    t = threading.Thread(target=server.serve_connection,
                         args=(a, token, read_store, _Emb()), daemon=True)
    t.start()
    f = b.makefile("rwb")
    # send onload WITHOUT a prior hello -> server must not answer keys
    f.write((json.dumps({"op": "onload", "prompt": "x"}) + "\n").encode())
    f.flush()
    line = f.readline()
    assert json.loads(line).get("ok") is False        # server replies {ok:false} before closing
    b.close()
    t.join(timeout=2)


def test_serve_connection_onload_dispatches_to_handler(tmp_path):
    token = runtime.new_token()
    read_store = SqliteStore(db_path=str(tmp_path / "s.db"), embedder=_Emb())
    read_store.store("session:x:tool:a", "alpha")
    a, b = socket.socketpair()
    t = threading.Thread(target=server.serve_connection,
                         args=(a, token, read_store, _Emb()), daemon=True)
    t.start()
    f = b.makefile("rwb")
    nonce = runtime.new_nonce()
    f.write((json.dumps({"op": "hello", "nonce": nonce}) + "\n").encode())
    f.flush()
    json.loads(f.readline())                            # banner
    f.write((json.dumps({"op": "onload", "prompt": "alpha", "k": 10,
                         "token_budget": None}) + "\n").encode())
    f.flush()
    resp = json.loads(f.readline())
    assert resp["ok"] is True and "keys" in resp        # reached handle_onload (dark -> keys [])
    b.close()
    t.join(timeout=2)


def test_serve_connection_unknown_op_returns_error(tmp_path):
    token = runtime.new_token()
    read_store = SqliteStore(db_path=str(tmp_path / "s.db"), embedder=_Emb())
    a, b = socket.socketpair()
    t = threading.Thread(target=server.serve_connection,
                         args=(a, token, read_store, _Emb()), daemon=True)
    t.start()
    f = b.makefile("rwb")
    nonce = runtime.new_nonce()
    f.write((json.dumps({"op": "hello", "nonce": nonce}) + "\n").encode())
    f.flush()
    json.loads(f.readline())
    f.write((json.dumps({"op": "bogus"}) + "\n").encode())
    f.flush()
    assert json.loads(f.readline()).get("ok") is False
    b.close()
    t.join(timeout=2)
