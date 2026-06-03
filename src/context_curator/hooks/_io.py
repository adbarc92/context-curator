"""Shared hook plumbing (design §3.4). Thin: parse stdin event JSON, optionally open the
store, call the handler, emit the exit code. FAIL-OPEN on any error (exit 0); the guard
emits a distinct marker so a fail-open bypass is visible."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from context_curator.embeddings import NullEmbedder
from context_curator.store.interface import Store
from context_curator.store.paths import resolve_db_path
from context_curator.store.sqlite_store import SqliteStore, sweep_expired

ALERT = "[context-curator GUARD-FAILOPEN]"   # distinct, greppable marker


@dataclass
class HookResult:
    exit_code: int          # 0 allow, 2 block
    message: str = ""       # -> stderr when blocking
    additional_context: str | None = None   # -> stdout inject JSON on exit 0 (design §3.4)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def read_event() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def open_store() -> Store:
    store = SqliteStore(db_path=resolve_db_path(), embedder=NullEmbedder())
    log(f"context-curator: capture DB = {resolve_db_path()}")
    try:
        sweep_expired(store)
    except Exception as e:        # sweep is best-effort
        log(f"context-curator: sweep skipped ({e})")
    return store


def _emit_inject(event: dict, text: str) -> None:
    """STDOUT-ONLY contract (design §3.4): write EXACTLY the inject JSON and nothing else.
    json.dump emits no trailing newline (unlike print(json.dumps(...))) — do NOT switch to
    print() or add a newline; any stray byte silently breaks Claude Code's additionalContext
    parse."""
    name = event.get("hook_event_name") or event.get("hookEventName") or ""
    obj = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}
    json.dump(obj, sys.stdout)


def run_hook(handler: Callable[..., HookResult], *, needs_store: bool,
             fail_label: str = "capture") -> None:
    try:
        event = read_event()
        if needs_store:
            result = handler(event, open_store())
        else:
            result = handler(event)
    except Exception as e:        # FAIL-OPEN
        if not needs_store:       # the guard: make the bypass visible
            log(f"{ALERT} guard crashed, allowing tool: {e}")
        else:                     # distinct, greppable per-hook label (round-1 I3)
            log(f"context-curator: {fail_label} failed: {e}")
        sys.exit(0)
        return
    # inject ONLY on allow (exit 0): a blocking hook must not also write to stdout (design §3.4)
    if result.exit_code == 0 and result.additional_context is not None:
        _emit_inject(event, result.additional_context)
    if result.message:
        log(result.message)
    sys.exit(result.exit_code)
