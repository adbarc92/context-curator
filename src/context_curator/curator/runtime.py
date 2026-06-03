"""Curator runtime primitives: token/HMAC handshake, atomic runtime file, liveness discover,
non-blocking single-instance lock, detached spawn (design §5.1, §6.1, §6.4, §7)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def new_token() -> str:
    return secrets.token_hex(32)               # 64-char hex; HMAC key = its UTF-8 bytes


def new_nonce() -> str:
    return secrets.token_hex(16)               # fresh per connection (replay protection)


def make_proof(token: str, nonce: str) -> str:
    return hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_proof(token: str, nonce: str, proof: str) -> bool:
    return hmac.compare_digest(make_proof(token, nonce), proof or "")


def write_runtime(path: Path, info: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(info), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)                      # atomic publish


def read_runtime(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def remove_runtime(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class Discovery:
    state: str | None           # ready | warming | provisioning | None(absent)
    info: dict | None
    respawn: bool               # hook should spawn a curator


def discover(
    path: Path,
    *,
    age_s,
    deadlines: dict[str, int],
    alive=pid_alive,
) -> Discovery:
    """`age_s(started_at_iso) -> float` lets tests inject the clock. A ready file -> connect
    (respawn False). A live, in-deadline warming/provisioning file -> recency, no respawn.
    Absent OR dead-pid OR stale warming -> respawn True (round-2 C1 liveness bound)."""
    info = read_runtime(path)
    if info is None:
        return Discovery(None, None, respawn=True)
    state = info.get("state")
    if state == "ready":
        return Discovery("ready", info, respawn=False)
    if state in ("warming", "provisioning"):
        deadline = deadlines.get(state, 0)
        if alive(int(info.get("pid", 0))) and age_s(info.get("started_at", "")) < deadline:
            return Discovery(state, info, respawn=False)
    return Discovery(state, info, respawn=True)        # stale/dead/unknown -> respawn


def try_lock(lock_path: str):
    """Non-blocking exclusive lock. Returns an opaque handle or None if already held."""
    f = open(lock_path, "a+")  # noqa: SIM115
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def release_lock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def spawn_detached(db_path: str) -> None:
    """Fire-and-forget detached curator; child fd 1 = DEVNULL (protects the hook stdout-only
    inject contract). Does NOT wait. (design §6.4)"""
    env = {**os.environ, "CC_DB_PATH": db_path}
    kwargs: dict = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=env,
    )
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "context_curator.curator"], **kwargs)
