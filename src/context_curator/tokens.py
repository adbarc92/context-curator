"""Token estimation. Crude heuristic for v1; replace with a real tokenizer in M3."""
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Rough token count (1 token ~= 4 chars)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)
