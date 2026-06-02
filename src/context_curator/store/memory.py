"""In-memory reference Store. Proves the wire format; not used in production."""
from __future__ import annotations

from context_curator.embeddings import Embedder
from context_curator.keys import is_within_scope
from context_curator.models import Chunk, utcnow_iso
from context_curator.store.expiry import is_expired
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens


class InMemoryStore(Store):
    def __init__(self, embedder: Embedder, allowed_prefix: str | None = None) -> None:
        self._data: dict[str, Chunk] = {}
        self._seq: dict[str, int] = {}
        self._next_seq: int = 0
        self._embedder = embedder
        self._allowed_prefix = allowed_prefix

    def store(self, key: str, content: str, tags: list[str] | None = None,
              ttl_s: int | None = 86400, pin: bool = False,
              source: str = "tool:read", provenance: str | None = None) -> str:
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
        self._next_seq += 1
        self._seq[key] = self._next_seq
        return key

    def retrieve(self, key: str) -> Chunk | None:
        c = self._data.get(key)
        if c is None or not is_within_scope(key, self._allowed_prefix):
            return None
        if is_expired(c.created_at, c.ttl_s, c.pin):
            return None
        return c

    def query(self, task_context: str, tags: list[str] | None = None, k: int = 10,
              token_budget: int | None = None) -> list[Chunk]:
        cands = [
            c for c in self._data.values()
            if is_within_scope(c.key, self._allowed_prefix)
            and not is_expired(c.created_at, c.ttl_s, c.pin)
            and (tags is None or set(tags).issubset(set(c.tags)))
        ]
        cands.sort(key=lambda c: self._seq[c.key], reverse=True)  # write-order recency
        cands = cands[:k]
        if token_budget is not None:
            out, used = [], 0
            for c in cands:
                t = estimate_tokens(c.content)
                if used + t > token_budget:
                    break
                out.append(c)
                used += t
            return out
        return cands

    def all_live_chunks(self) -> list[Chunk]:
        cands = [
            c for c in self._data.values()
            if is_within_scope(c.key, self._allowed_prefix)
            and not is_expired(c.created_at, c.ttl_s, c.pin)
        ]
        cands.sort(key=lambda c: self._seq[c.key], reverse=True)  # recency newest-first
        return cands

    def list(self, prefix: str) -> list[str]:
        return [
            key for key in self._data
            if (key == prefix or key.startswith(prefix + ":"))
            and is_within_scope(key, self._allowed_prefix)
        ]

    def evict(self, key: str) -> bool:
        self._seq.pop(key, None)
        return self._data.pop(key, None) is not None

    def pin(self, key: str) -> bool:
        c = self._data.get(key)
        if c is None:
            return False
        self._data[key] = c.model_copy(update={"pin": True})
        return True
