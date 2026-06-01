"""Shared hook plumbing (design §3.4). Thin: parse stdin event JSON, optionally open the
store, call the handler, emit the exit code. FAIL-OPEN on any error (exit 0); the guard
emits a distinct marker so a fail-open bypass is visible."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from context_curator.embeddings import HashingEmbedder
from context_curator.store.interface import Store
from context_curator.store.paths import resolve_db_path
from context_curator.store.sqlite_store import SqliteStore, sweep_expired

ALERT = "[context-curator GUARD-FAILOPEN]"   # distinct, greppable marker


@dataclass
class HookResult:
    exit_code: int          # 0 allow, 2 block
    message: str = ""       # -> stderr when blocking


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def read_event() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def open_store() -> Store:
    store = SqliteStore(db_path=resolve_db_path(), embedder=HashingEmbedder())
    log(f"context-curator: capture DB = {resolve_db_path()}")
    try:
        sweep_expired(store)
    except Exception as e:        # sweep is best-effort
        log(f"context-curator: sweep skipped ({e})")
    return store


def run_hook(handler: Callable[..., HookResult], *, needs_store: bool) -> None:
    try:
        event = read_event()
        if needs_store:
            result = handler(event, open_store())
        else:
            result = handler(event)
    except Exception as e:        # FAIL-OPEN
        if not needs_store:       # the guard: make the bypass visible
            log(f"{ALERT} guard crashed, allowing tool: {e}")
        else:
            log(f"context-curator: capture failed: {e}")
        sys.exit(0)
        return
    if result.message:
        log(result.message)
    sys.exit(result.exit_code)
