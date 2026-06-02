"""Shared chunk-expiry predicate (design §round-3 #2). One implementation for both
backends: InMemoryStore holds Chunks (no expires_at column), SqliteStore has rows."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


def is_expired(created_at: str, ttl_s: int | None, pin: bool,
               now: datetime | None = None) -> bool:
    """A chunk is expired iff it is not pinned, has a finite ttl_s, and
    created_at + ttl_s <= now. Pinned or ttl_s=None never expire."""
    if pin or ttl_s is None:
        return False
    expires_at = datetime.fromisoformat(created_at) + timedelta(seconds=ttl_s)
    return expires_at <= (now or datetime.now(UTC))
