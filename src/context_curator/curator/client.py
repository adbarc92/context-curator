"""Curator client: discover -> handshake (verify server proof BEFORE sending the prompt) ->
onload, under one shared deadline (design §6.1). Raises CuratorUnavailable on any failure."""
from __future__ import annotations

import json
import socket
import time

from context_curator.curator import config, runtime
from context_curator.store.paths import resolve_db_path


class CuratorUnavailable(Exception):
    def __init__(self, msg: str = "", *, respawn: bool = True) -> None:
        super().__init__(msg)
        self.respawn = respawn


def _iso_age_s(started_at: str) -> float:
    from datetime import UTC, datetime

    try:
        return (datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds()
    except ValueError:
        return 1e9


def request_onload(prompt: str, *, k: int, token_budget) -> list[str]:
    path = config.runtime_path(resolve_db_path())
    d = runtime.discover(
        path,
        age_s=_iso_age_s,
        deadlines={
            "warming": config.WARMING_DEADLINE_S,
            "provisioning": config.PROVISION_DEADLINE_S,
        },
    )
    if d.state != "ready":
        raise CuratorUnavailable(f"state={d.state}", respawn=d.respawn)
    info = d.info or {}
    try:                                             # a corrupt 'ready' file (missing/bad port,
        port, token = int(info["port"]), info["token"]   # token) must fail-open, not raise raw
    except (KeyError, ValueError, TypeError) as e:
        raise CuratorUnavailable(f"bad runtime info: {e}", respawn=True) from e
    return request_onload_at(
        "127.0.0.1",
        port,
        token,
        prompt,
        k=k,
        token_budget=token_budget,
        deadline_s=config.REQUEST_DEADLINE_S,
    )


def request_onload_at(
    host: str,
    port: int,
    token: str,
    prompt: str,
    *,
    k: int,
    token_budget,
    deadline_s: float,
) -> list[str]:
    deadline = time.monotonic() + deadline_s

    def _to() -> float:
        return max(0.001, deadline - time.monotonic())

    s = None
    try:
        s = socket.create_connection((host, port), timeout=_to())
        s.settimeout(_to())
        f = s.makefile("rwb")
        nonce = runtime.new_nonce()
        f.write((json.dumps({"op": "hello", "nonce": nonce}) + "\n").encode())  # NO prompt/token
        f.flush()
        s.settimeout(_to())
        banner = json.loads(f.readline())
        if not runtime.verify_proof(token, nonce, banner.get("proof", "")):
            raise CuratorUnavailable("bad proof")  # stranger / wrong server -> NO prompt sent
        f.write(
            (
                json.dumps({"op": "onload", "prompt": prompt, "k": k, "token_budget": token_budget})
                + "\n"
            ).encode()
        )
        f.flush()
        s.settimeout(_to())
        resp = json.loads(f.readline())
        if not resp.get("ok"):
            raise CuratorUnavailable("not ok")
        return list(resp.get("keys", []))
    except CuratorUnavailable:
        raise
    except (OSError, ValueError) as e:
        raise CuratorUnavailable(str(e)) from e
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
