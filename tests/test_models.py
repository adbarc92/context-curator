from datetime import UTC, datetime

from context_curator.models import Chunk, utcnow_iso


def test_chunk_defaults():
    c = Chunk(key="shared:contracts:auth", content="POST /login -> {token}")
    assert c.key == "shared:contracts:auth"
    assert c.tags == []
    assert c.source == "tool:read"
    assert c.pin is False
    assert c.ttl_s == 86400
    assert c.last_onloaded_at is None
    assert c.embedding is None
    # created_at is a valid ISO8601 timestamp
    datetime.fromisoformat(c.created_at)


def test_chunk_explicit_fields_roundtrip():
    c = Chunk(
        key="shared:decisions:1",
        content="Use SQLite for v1",
        tags=["backend", "decision"],
        source="decision",
        pin=True,
        ttl_s=None,
        provenance="session-abc",
        embedding=[0.1, 0.2, 0.3],
    )
    dumped = c.model_dump()
    restored = Chunk(**dumped)
    assert restored == c
    assert restored.pin is True
    assert restored.ttl_s is None


def test_utcnow_iso_is_timezone_aware():
    ts = utcnow_iso()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed) == UTC.utcoffset(parsed)
