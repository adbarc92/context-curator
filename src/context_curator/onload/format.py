"""Render selected chunks into an additionalContext block (design §3.3)."""
from __future__ import annotations

from context_curator.models import Chunk


def format_block(chunks: list[Chunk], *, title: str, per_chunk_chars: int = 1200) -> str:
    """Render selected chunks as an additionalContext block, or "" if empty. Each line names
    the source key/provenance so the model knows this is auto-onloaded curated context.

    NOTE (design §3.3): the caller budgets on estimate_tokens(content); per-line boilerplate
    and the per_chunk_chars truncation here make the rendered size only an approximation of
    that budget — acceptable at M4a's k<=10 / ~1500-token scale."""
    if not chunks:
        return ""
    lines = [f"## {title}", "_(auto-onloaded by ContextCurator)_"]
    for c in chunks:
        body = c.content if len(c.content) <= per_chunk_chars else c.content[:per_chunk_chars] + "…"
        lines.append(f"- **[{c.key}]** ({c.source}): {body}")
    return "\n".join(lines)
