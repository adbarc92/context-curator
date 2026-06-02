from datetime import UTC, datetime, timedelta

from context_curator.store.expiry import is_expired


def _ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def test_pinned_never_expires():
    assert is_expired(_ago(10_000), ttl_s=1, pin=True) is False


def test_none_ttl_never_expires():
    assert is_expired(_ago(10_000), ttl_s=None, pin=False) is False


def test_past_ttl_is_expired():
    assert is_expired(_ago(100), ttl_s=10, pin=False) is True


def test_future_ttl_is_live():
    assert is_expired(_ago(1), ttl_s=3600, pin=False) is False
