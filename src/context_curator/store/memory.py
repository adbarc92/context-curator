"""In-memory reference Store. Proves the wire format; not used in production."""
from __future__ import annotations

from context_curator.embeddings import Embedder
from context_curator.keys import is_within_scope
from context_curator.models import Chunk, utcnow_iso
from context_curator.store.interface import Store


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class InMemoryStore(Store):
    def __init__(self, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        self._data: dict[str, Chunk] = {}
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix

    def store(self, key, content, tags=None, ttl_s=86400, pin=False,
              source="tool:read", provenance=None):
        self._data[key] = Chunk(
            key=key,
            content=content,
            tags=list(tags or []),
            ttl_s=ttl_s,
            pin=pin,
            source=source,
            provenance=provenance,
            created_at=utcnow_iso(),
            embedding=self._embedder.embed(content),
        )
        return key

    def retrieve(self, key):
        c = self._data.get(key)
        if c is None or not is_within_scope(key, self._allowed_prefix):
            return None
        return c

    def query(self, task_context, tags=None, k=10, token_budget=None):
        cands = [
            c for c in self._data.values()
            if is_within_scope(c.key, self._allowed_prefix)
            and (tags is None or set(tags).issubset(set(c.tags)))
        ]
        cands.sort(key=lambda c: c.created_at, reverse=True)  # recency (M3 adds similarity)
        cands = cands[:k]
        if token_budget is not None:
            out, used = [], 0
            for c in cands:
                t = _estimate_tokens(c.content)
                if used + t > token_budget:
                    break
                out.append(c)
                used += t
            return out
        return cands

    def list(self, prefix):
        return [
            key for key in self._data
            if (key == prefix or key.startswith(prefix))
            and is_within_scope(key, self._allowed_prefix)
        ]

    def evict(self, key):
        return self._data.pop(key, None) is not None

    def pin(self, key):
        c = self._data.get(key)
        if c is None:
            return False
        c.pin = True
        return True
