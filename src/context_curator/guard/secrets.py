"""Secret-pattern scan (design §3.3). Capped input; returns the matched pattern name."""
from __future__ import annotations

import re

from context_curator.guard.config import GUARD_MAX_SCAN


def scan_secrets(text: str, patterns: list[tuple[str, str]]) -> str | None:
    window = text[:GUARD_MAX_SCAN]
    for name, pat in patterns:
        if re.search(pat, window):
            return name
    return None
