from context_curator.models import Chunk
from context_curator.onload.format import format_block


def _c(key, content, source="tool:read"):
    return Chunk(key=key, content=content, source=source)


def test_empty_returns_empty_string():
    assert format_block([], title="Anything") == ""


def test_names_each_key_marker_and_body():
    out = format_block([_c("k1", "hello"), _c("k2", "world")], title="Ctx")
    assert "## Ctx" in out
    assert "_(auto-onloaded by ContextCurator)_" in out
    assert "[k1]" in out and "[k2]" in out
    assert "hello" in out and "world" in out


def test_truncates_overlong_content():
    out = format_block([_c("k", "x" * 5000)], title="T", per_chunk_chars=100)
    assert "…" in out
    assert ("x" * 5000) not in out
    assert "x" * 100 in out          # the per_chunk_chars prefix is preserved


def test_content_at_exact_boundary_not_truncated():
    out = format_block([_c("k", "x" * 100)], title="T", per_chunk_chars=100)
    assert "…" not in out
    assert "x" * 100 in out
