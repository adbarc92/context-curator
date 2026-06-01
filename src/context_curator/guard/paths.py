"""Sensitive-path matching (design §3.3). Normalizes ~ and .., matches path AND basename."""
from __future__ import annotations

import os
from fnmatch import fnmatch


def is_sensitive_path(path: str, globs: list[str]) -> bool:
    norm = os.path.normpath(os.path.expanduser(path)).replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    for g in globs:
        if fnmatch(norm, g) or fnmatch(base, g) or fnmatch(base, g.replace("**/", "")):
            return True
    return False
