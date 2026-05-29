"""Keyspace grammar and tenant-scope enforcement helpers (DESIGN.md §5)."""
from __future__ import annotations

TENANT_SEGMENT = "tenant"


def tenant_prefix(key: str) -> str | None:
    """Return the `...:tenant:{id}` prefix of `key`, or None if it has no tenant."""
    parts = key.split(":")
    if TENANT_SEGMENT in parts:
        i = parts.index(TENANT_SEGMENT)
        if i + 1 < len(parts):
            return ":".join(parts[: i + 2])
    return None


def is_within_scope(key: str, allowed_prefix: str | None) -> bool:
    """True if `key` is inside `allowed_prefix`.

    `None` scope allows everything. Matching is boundary-aware: a scope of
    `proj:acme:tenant:t42` matches `...t42` and `...t42:child` but never `...t420`.
    """
    if allowed_prefix is None:
        return True
    return key == allowed_prefix or key.startswith(allowed_prefix + ":")
