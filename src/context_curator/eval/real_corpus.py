"""Real-transcript corpus harvest (design §3). Turns a parsed `Trace` into per-turn `Fixture`s
with DOWNSTREAM-USE gold (a prior chunk is gold iff its file-path entity is re-FETCHED by a call
within W turns, excluding verify-Read-after-Edit). Deterministic; no bge, no LLM. Entities are
extracted from each chunk's producing `ToolCall.args` (recovered via the call_id embedded in the
chunk key)."""
from __future__ import annotations

import os

from context_curator.replay.schema import ToolCall

_PATH_ARGS = ("file_path", "notebook_path", "path")        # structured path args (Bash deferred)
_RETRIEVAL = {"read", "grep", "glob", "notebookread"}      # lowercased; re-fetch = needed/absent
_EDIT = {"edit", "write", "multiedit", "notebookedit"}     # edits never generate gold (churn)


def _canon(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def extract_entities(call: ToolCall) -> set[str]:
    """Canonical absolute file-path entities from a tool call's args. Pattern-only / path-less
    calls yield the empty set."""
    out: set[str] = set()
    for key in _PATH_ARGS:
        v = call.args.get(key)
        if isinstance(v, str) and v:
            out.add(_canon(v))
    return out


def _contains(dir_: str, file_: str) -> bool:
    return file_.startswith(dir_.rstrip(os.sep) + os.sep)


def entities_match(a: set[str], b: set[str]) -> bool:
    """True iff some entity in `a` equals, contains, or is contained by some entity in `b`.
    Empty sets never match (pattern-only Glob / path-less calls)."""
    for x in a:
        for y in b:
            if x == y or _contains(x, y) or _contains(y, x):
                return True
    return False
