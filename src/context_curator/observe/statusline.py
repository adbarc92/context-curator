"""Claude Code statusLine command (design §5): read session_id from stdin JSON, print the curator's
last-decision indicator (`CC ws:N +i/-o [src]`). Never raises; always exits 0; writes via
sys.stdout.write (no print -> no Windows CRLF)."""
from __future__ import annotations

import json
import sys

from context_curator.observe.decision_log import (
    DecisionRecord,
    _newest_session_file,
    _records_from,
    read_recent,
)

_IDLE = "CC ·"


def _render(r: DecisionRecord) -> str:
    return f"CC ws:{r.working_set_size} +{len(r.paged_in)}/-{len(r.paged_out)} [{r.source}]"


def _line_for(stdin_text: str) -> str:
    try:
        payload = json.loads(stdin_text)
    except Exception:
        return _IDLE
    raw = payload.get("session_id") if isinstance(payload, dict) else None
    sid = raw.strip() if isinstance(raw, str) else ""    # tolerate non-string/absent session_id
    if sid:                                          # present -> its file or idle (NOT newest)
        recent = read_recent(sid, 1)
        return _render(recent[0]) if recent else _IDLE
    newest = _newest_session_file()                  # blank session -> best-effort newest file
    if newest is not None:
        recent = _records_from(newest, 1)
        if recent:
            return _render(recent[0])
    return _IDLE


def main() -> None:
    try:
        line = _line_for(sys.stdin.read())
    except Exception:
        line = _IDLE
    sys.stdout.write(line)


if __name__ == "__main__":
    main()
