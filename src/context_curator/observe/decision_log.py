"""Per-session decision log (design §3): a durable, fail-open JSONL record of what the onload hook
injects each turn. Sidecar to the SQLite db — it never touches the chunk store, preserving the
reconcile single-writer invariant. Injection-window semantics: page-in/out = turn-over-turn delta of
the injected key set. The writer ALWAYS appends a trailing newline, so the reader may treat any
unterminated final fragment as torn and drop it."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from context_curator.store.paths import resolve_db_path

_WINDOW_BYTES = 64 * 1024            # bounded tail-read (~300 lines); statusline cost O(window)
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class DecisionRecord:
    ts: str
    session_id: str
    prompt_preview: str
    source: str                     # "curator" | "recency" | "none"
    injected_keys: list[str]
    working_set_size: int
    paged_in: list[str]
    paged_out: list[str]


def decisions_dir() -> Path:
    return Path(resolve_db_path()).parent / "decisions"


def decision_log_path(session_id: str) -> Path:
    safe = _SAFE.sub("_", session_id) or "unknown"
    return decisions_dir() / f"decisions-{safe}.jsonl"


def _tail_lines(path: Path, n: int) -> list[str]:
    """Last `n` COMPLETE (newline-terminated) lines, reading only the trailing ~64 KB."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - _WINDOW_BYTES)
            f.seek(start)
            blob = f.read()
    except OSError:
        return []
    text = blob.decode("utf-8", errors="ignore")
    if start > 0:                               # sliced mid-line -> drop the partial first line
        nl = text.find("\n")
        text = text[nl + 1:] if nl >= 0 else ""
    parts = text.split("\n")
    complete = parts[:-1]                        # drop trailing element (clean "" or torn partial)
    return complete[-n:]


def _ends_torn(path: Path) -> bool:
    """True if the file exists, is non-empty, and its final byte is not a newline."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            if f.tell() == 0:
                return False
            f.seek(-1, 2)
            return f.read(1) != b"\n"
    except OSError:
        return False


def _records_from(path: Path, n: int) -> list[DecisionRecord]:
    out: list[DecisionRecord] = []
    for line in _tail_lines(path, n):
        try:
            out.append(DecisionRecord(**json.loads(line)))
        except Exception:
            continue                             # skip malformed/partial
    return out


def read_recent(session_id: str, n: int) -> list[DecisionRecord]:
    return _records_from(decision_log_path(session_id), n)


def _newest_session_file() -> Path | None:
    try:
        files = list(decisions_dir().glob("decisions-*.jsonl"))
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def record_decision(session_id: str, prompt_preview: str, source: str,
                    injected_keys: list[str]) -> None:
    """Append one DecisionRecord line for this turn. Fail-open: never raises into the hook."""
    try:
        prev = read_recent(session_id, 1)
        prev_keys = prev[0].injected_keys if prev else []
        prev_set, cur_set = set(prev_keys), set(injected_keys)
        rec = DecisionRecord(
            ts=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            session_id=session_id,
            prompt_preview=" ".join(prompt_preview.split()),
            source=source,
            injected_keys=list(injected_keys),
            working_set_size=len(injected_keys),
            paged_in=[k for k in injected_keys if k not in prev_set],
            paged_out=[k for k in prev_keys if k not in cur_set],
        )
        path = decision_log_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if _ends_torn(path) else ""   # isolate any torn fragment onto its own line
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(prefix + json.dumps(asdict(rec)) + "\n")
    except Exception:
        pass


def inspect_lines(session_id: str | None, tail: int) -> list[str]:
    """Human-readable recent decisions. Default (session_id None) -> the newest session file."""
    path = decision_log_path(session_id) if session_id else _newest_session_file()
    if path is None or not path.exists():
        return ["no decisions recorded yet"]
    return [
        f'{r.ts} [{r.source}] ws:{r.working_set_size} '
        f'+{len(r.paged_in)}/-{len(r.paged_out)}  "{r.prompt_preview}"'
        for r in _records_from(path, tail)
    ]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m context_curator.observe.decision_log")
    ap.add_argument("--session", default=None, help="session id (default: newest log)")
    ap.add_argument("--tail", type=int, default=10, help="how many recent decisions (max ~300)")
    args = ap.parse_args()
    for line in inspect_lines(args.session, args.tail):
        print(line)


if __name__ == "__main__":
    main()
