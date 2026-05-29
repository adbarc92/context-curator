"""Chunk value schema (DESIGN.md §5)."""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    """Timezone-aware UTC timestamp in ISO 8601."""
    return datetime.now(UTC).isoformat()


class Chunk(BaseModel):
    """A stored unit of curated context.

    `key` is the canonical storage identifier (it subsumes §5's `id`).
    """

    key: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source: str = "tool:read"
    created_at: str = Field(default_factory=utcnow_iso)
    last_onloaded_at: str | None = None
    pin: bool = False
    ttl_s: int | None = 86400
    provenance: str | None = None
    embedding: list[float] | None = None
