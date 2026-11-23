"""File-path entities of a tool call — one definition shared by live capture and the eval harvest,
so the stickiness feature the hook ranks on is the feature the eval measured (Track A, design D2).

An entity is a canonical absolute path taken from a tool call's structured path args. Live chunks
carry their entities as `entity:<path>` tags (design D1); the eval reads them from fixtures."""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

PATH_ARGS = ("file_path", "notebook_path", "path")       # structured path args (Bash deferred)
ENTITY_TAG_PREFIX = "entity:"


def canon_path(p: str) -> str:
    """Absolute, normalized, case-normalized path. Relative paths resolve against the CWD (the
    project dir for hooks; the harvest root for the eval)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def entities_from_args(args: Mapping[str, object] | None) -> set[str]:
    """Canonical path entities from a tool call's args. Pattern-only / path-less calls -> set()."""
    out: set[str] = set()
    for key in PATH_ARGS:
        v = (args or {}).get(key)
        if isinstance(v, str) and v:
            out.add(canon_path(v))
    return out


def _contains(dir_: str, file_: str) -> bool:
    return file_.startswith(dir_.rstrip(os.sep) + os.sep)


def entities_match(a: Iterable[str], b: Iterable[str]) -> bool:
    """True iff some entity in `a` equals, contains, or is contained by some entity in `b`.
    Empty sets never match (pattern-only Glob / path-less calls)."""
    b = list(b)
    for x in a:
        for y in b:
            if x == y or _contains(x, y) or _contains(y, x):
                return True
    return False


def entity_tags(entities: Iterable[str]) -> list[str]:
    return sorted(ENTITY_TAG_PREFIX + e for e in entities)


def entities_from_tags(tags: Iterable[str]) -> set[str]:
    n = len(ENTITY_TAG_PREFIX)
    return {t[n:] for t in tags if t.startswith(ENTITY_TAG_PREFIX) and len(t) > n}
