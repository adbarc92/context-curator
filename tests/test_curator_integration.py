import json
import os
import socket
import subprocess
import sys
import threading
import time

from context_curator.curator import config, reconcile, runtime
from context_curator.embeddings import HashingEmbedder
from context_curator.store.sqlite_store import SqliteStore


def _env(db):
    return {**os.environ, "CC_DB_PATH": db, "CC_CURATOR_EMBEDDER": "hashing",
            "CC_CURATOR_IDLE_TIMEOUT_S": "2"}


def _poll(predicate, deadline_s=20.0, interval=0.05):
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _spawn(db):
    return subprocess.Popen([sys.executable, "-m", "context_curator.curator"], env=_env(db),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def test_curator_lifecycle_and_handshake(tmp_path):
    db = str(tmp_path / "i.db")
    SqliteStore(db_path=db, embedder=HashingEmbedder()).store("session:x:tool:a", "alpha")
    rt = config.runtime_path(db)
    proc = _spawn(db)
    try:
        assert _poll(
            lambda: (runtime.read_runtime(rt) or {}).get("state") == "ready"
        ), "never ready"
        info = runtime.read_runtime(rt)
        # real handshake + ping over loopback
        s = socket.create_connection(("127.0.0.1", info["port"]), timeout=2)
        f = s.makefile("rwb")
        nonce = runtime.new_nonce()
        f.write((json.dumps({"op": "hello", "nonce": nonce}) + "\n").encode())
        f.flush()
        banner = json.loads(f.readline())
        assert runtime.verify_proof(info["token"], nonce, banner["proof"])
        f.write((json.dumps({"op": "ping"}) + "\n").encode())
        f.flush()
        assert json.loads(f.readline())["ok"] is True
        s.close()
        # second spawn loses the single-instance lock and exits 0 quickly
        loser = _spawn(db)
        assert loser.wait(timeout=10) == 0
        # idle-exit (IDLE_TIMEOUT_S=2) removes the runtime file
        assert _poll(lambda: runtime.read_runtime(rt) is None, deadline_s=15.0), "file not removed"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_two_connections_reconcile_and_read_no_misuse(tmp_path):
    db = str(tmp_path / "c.db")
    s = SqliteStore(db_path=db, embedder=HashingEmbedder())
    for i in range(50):
        s.store(f"session:x:tool:{i}", f"content {i}")           # all embedded (hashing)
    write_store = SqliteStore(db_path=db, embedder=HashingEmbedder())
    read_store = SqliteStore(db_path=db, embedder=HashingEmbedder())
    errors = []

    def reader():
        try:
            for _ in range(200):
                read_store.all_live_chunks()
        except Exception as e:                                   # SQLITE_MISUSE etc.
            errors.append(e)

    def writer():
        try:
            for _ in range(50):
                reconcile.reconcile_once(write_store, HashingEmbedder(), batch=8)
        except Exception as e:
            errors.append(e)

    ts = [threading.Thread(target=reader), threading.Thread(target=writer)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
