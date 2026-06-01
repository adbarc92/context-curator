"""Pluggable embedder. v1 default is a deterministic hashing embedder; the real
model is a deferred M3 decision (DESIGN.md §12)."""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod


class Embedder(ABC):
    @property
    @abstractmethod
    def dim(self) -> int:
        """Embedding dimensionality."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a `dim`-length embedding for `text`."""


class HashingEmbedder(Embedder):
    """Deterministic bag-of-words hashing embedder. Cheap, dependency-free,
    good enough to validate the write-time embedding path and serve as a crude
    similarity placeholder until M3 selects a real model."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in text.lower().split():
            h = int(hashlib.sha1(tok.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]
