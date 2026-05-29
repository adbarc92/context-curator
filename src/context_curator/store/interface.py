"""Store interface — DESIGN.md §6. Frozen; implementations must satisfy the
contract suite in tests/test_store_contract.py."""
from __future__ import annotations

from abc import ABC, abstractmethod

from context_curator.models import Chunk


class Store(ABC):
    @abstractmethod
    def store(
        self,
        key: str,
        content: str,
        tags: list[str] | None = None,
        ttl_s: int | None = 86400,
        pin: bool = False,
        source: str = "tool:read",
        provenance: str | None = None,
    ) -> str:
        """Write/offload a chunk. Computes and stores its embedding. Returns the key."""

    @abstractmethod
    def retrieve(self, key: str) -> Chunk | None:
        """Exact fetch. Returns None if absent, expired, or outside the allowed scope."""

    @abstractmethod
    def query(
        self,
        task_context: str,
        tags: list[str] | None = None,
        k: int = 10,
        token_budget: int | None = None,
    ) -> list[Chunk]:
        """Ranked retrieval, with content. v1 ranks by recency (M3 adds similarity).
        Never returns chunks outside the allowed scope."""

    @abstractmethod
    def list(self, prefix: str) -> list[str]:
        """Enumerate keys under `prefix` (debug/inspection). Scope-constrained."""

    @abstractmethod
    def evict(self, key: str) -> bool:
        """Remove a chunk from the STORE (not the context window). True if removed."""

    @abstractmethod
    def pin(self, key: str) -> bool:
        """Mark a chunk pinned (never expires). True if the chunk exists."""
