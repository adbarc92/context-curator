"""PreToolUse guardrail hook (design §3.3-3.4). Blocks sensitive-path writes and secret
inputs (exit 2); never opens the store; fails open with a distinct marker on crash."""
from __future__ import annotations

import re

from context_curator.guard.config import load_config
from context_curator.guard.paths import is_sensitive_path
from context_curator.guard.secrets import scan_secrets
from context_curator.hooks._io import ALERT, HookResult, log, run_hook

_REDIRECT = re.compile(r"(?:>>?|\btee\b)\s*(\S+)")
_GUARDED = {"Write", "Edit", "MultiEdit", "Bash"}


def _paths_and_texts(tool_name: str, ti: dict) -> tuple[list[str], list[str]]:
    """(sensitive-path candidates, secret-scan texts) per the §3.3 table."""
    if tool_name == "Write":
        return ([ti.get("file_path", "")], [ti.get("content", "")])
    if tool_name == "Edit":
        return ([ti.get("file_path", "")], [ti.get("new_string", "")])
    if tool_name == "MultiEdit":
        texts = [e.get("new_string", "") for e in ti.get("edits", []) if isinstance(e, dict)]
        return ([ti.get("file_path", "")], texts)
    if tool_name == "Bash":
        cmd = ti.get("command") or ""
        return (_REDIRECT.findall(cmd), [cmd])   # path = redirect targets only
    return ([], [])


def handle(event: dict) -> HookResult:
    tool_name = event.get("tool_name", "")
    ti = event.get("tool_input") or {}
    if tool_name not in _GUARDED:
        log(f"{ALERT} unhandled tool {tool_name!r}, allowing")
        return HookResult(0)
    cfg = load_config()
    paths, texts = _paths_and_texts(tool_name, ti)
    for p in paths:
        try:
            if p and is_sensitive_path(p, cfg.sensitive_globs):
                return HookResult(2, f"blocked: sensitive path '{p}'")
        except Exception as e:
            log(f"{ALERT} path check errored (allowing this check): {e}")
    for t in texts:
        try:
            hit = scan_secrets(t, cfg.secret_patterns)
            if hit:
                return HookResult(2, f"blocked: secret pattern '{hit}' in tool input")
        except Exception as e:
            log(f"{ALERT} secret scan errored (allowing this check): {e}")
    return HookResult(0)


def main() -> None:
    run_hook(handle, needs_store=False)


if __name__ == "__main__":
    main()
