"""SubagentStop capture hook (design §3.4 / I1). The payload exposes a transcript path,
NOT a structured summary schema — so we read the subagent's final assistant text."""
from __future__ import annotations

import json
from pathlib import Path

from context_curator.capture.subagent import capture_subagent_summary
from context_curator.guard.config import CAPTURE_TTL_S
from context_curator.hooks._io import HookResult, run_hook
from context_curator.store.interface import Store


def extract_summary(event: dict) -> str:
    """Last assistant text message in the transcript, or '' if unavailable."""
    tp = event.get("transcript_path")
    if not tp or not Path(tp).exists():
        return ""
    text = ""
    for line in Path(tp).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") == "assistant":
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                t = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                if t:
                    text = t            # keep overwriting -> ends on the last one
    return text


def handle(event: dict, store: Store) -> HookResult:
    summary = extract_summary(event)
    subagent_id = event.get("session_id", "") or "unknown-subagent"
    capture_subagent_summary(store, subagent_id=subagent_id, summary=summary,
                             ttl_s=CAPTURE_TTL_S)
    return HookResult(0)


def main() -> None:
    run_hook(handle, needs_store=True)


if __name__ == "__main__":
    main()
