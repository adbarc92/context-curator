"""Curator server: single-instance lock, warming->ready lifecycle, accept loop with idle
timeout INSIDE it, per-connection HMAC handshake, reconcile thread, teardown (design §5.1-5.3)."""
from __future__ import annotations

import json
import os
import socket
import threading
import traceback

from context_curator.curator import config, reconcile, runtime
from context_curator.curator.handler import handle_onload
from context_curator.embeddings import Embedder, FastEmbedEmbedder, HashingEmbedder
from context_curator.models import utcnow_iso
from context_curator.store.sqlite_store import SqliteStore


def make_embedder() -> Embedder:
    return HashingEmbedder() if config.CURATOR_EMBEDDER == "hashing" else FastEmbedEmbedder()


def serve_connection(conn: socket.socket, token: str, read_store, embedder: Embedder) -> None:
    """One connection: hello -> proof -> {onload|ping}. The SERVER proves token-knowledge so the
    client trusts us; we accept any reader of our 0600-file's token (same-user). Reject a missing
    hello (no proof -> the client won't send a prompt anyway)."""
    try:
        conn.settimeout(config.REQUEST_DEADLINE_S * 4)
        f = conn.makefile("rwb")
        first = f.readline()
        if not first:
            return
        msg = json.loads(first)
        if msg.get("op") != "hello" or "nonce" not in msg:
            f.write((json.dumps({"ok": False, "error": "handshake"}) + "\n").encode())
            f.flush()
            return
        proof = runtime.make_proof(token, msg["nonce"])
        f.write((json.dumps({"server": "context-curator", "proof": proof}) + "\n").encode())
        f.flush()
        req = json.loads(f.readline() or "{}")
        op = req.get("op")
        if op == "ping":
            resp = {"ok": True}
        elif op == "onload":
            resp = handle_onload(read_store, embedder, req)
        else:
            resp = {"ok": False, "error": "unknown op"}
        f.write((json.dumps(resp) + "\n").encode())
        f.flush()
    except (OSError, ValueError):
        pass                                        # client bailed / malformed JSON -> drop conn
    except Exception:                               # a handler bug must NOT kill the accept loop
        if config.CURATOR_DEBUG:
            traceback.print_exc()
    finally:
        try:
            conn.close()
        except OSError:
            pass


def run(db_path: str) -> int:
    lock = runtime.try_lock(config.lock_path(db_path))
    if lock is None:
        return 0                                         # another curator owns this store
    rt = config.runtime_path(db_path)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    token = runtime.new_token()
    started = utcnow_iso()
    embedder = make_embedder()                        # cheap: model load is lazy (.dim known now)
    warming_state = "warming" if config.CURATOR_EMBEDDER == "hashing" else "provisioning"
    base = {"pid": os.getpid(), "port": port, "token": token,
            "dim": embedder.dim, "started_at": started, "embedder": config.CURATOR_EMBEDDER}
    runtime.write_runtime(rt, {"state": warming_state, **base})   # discoverable BEFORE load
    try:
        embedder.embed("warmup")                      # force model load now (download if needed)
    except Exception:
        if config.CURATOR_DEBUG:                      # a persistent load failure must be visible
            traceback.print_exc()
        srv.close()                                       # C2: don't leak the bound socket/port
        runtime.remove_runtime(rt)                        # crash-cleanup: no frozen warming file
        runtime.release_lock(lock)
        return 1
    read_store = SqliteStore(db_path=db_path, embedder=embedder)
    write_store = SqliteStore(db_path=db_path, embedder=embedder)
    reconcile.migrate_wrong_dim_once(write_store, dim=embedder.dim)
    runtime.write_runtime(rt, {"state": "ready", **base})
    stop = threading.Event()
    rec = threading.Thread(target=reconcile.reconcile_loop,
                           args=(write_store, embedder),
                           kwargs=dict(batch=config.RECONCILE_BATCH,
                                       interval_s=config.RECONCILE_INTERVAL_S, stop=stop),
                           daemon=True)
    rec.start()
    try:
        srv.settimeout(config.IDLE_TIMEOUT_S)
        while True:
            try:
                conn, _ = srv.accept()                    # idle INSIDE the loop (round-1 I3)
            except TimeoutError:
                break                                     # idle-exit
            serve_connection(conn, token, read_store, embedder)
    finally:
        stop.set()
        rec.join(timeout=2)
        srv.close()
        runtime.remove_runtime(rt)                        # remove file...
        runtime.release_lock(lock)                        # ...release lock LAST (round-1 I3)
    return 0
