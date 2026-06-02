"""Relevance policy (design §3.3). Pure: operates on Chunk lists + task text + tags;
no replay/JSON knowledge. Single scoring pass per call; task embedded once."""
from __future__ import annotations

import math
from hashlib import sha1

from context_curator.embeddings import Embedder
from context_curator.models import Chunk
from context_curator.policy.weights import PolicyWeights
from context_curator.tokens import estimate_tokens

_DEFAULT_WEIGHTS = PolicyWeights()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class RelevancePolicy:
    def __init__(self, embedder: Embedder, weights: PolicyWeights = _DEFAULT_WEIGHTS) -> None:
        self._embedder = embedder
        self._w = weights

    def scored_with_similarity(
        self, task_text: str, candidates: list[Chunk], query_tags: list[str] | None = None
    ) -> list[tuple[Chunk, float, float]]:
        """Embed task ONCE; score every candidate (candidates MUST be recency newest-first);
        return (chunk, score, RAW_COSINE) sorted by (-score, incoming_index). The raw cosine
        is the value BEFORE the affine rescale — the onload gate thresholds on it (design §3.2).
        It is 0.0 whenever there is no comparable embedding (over reembed_cap / dim mismatch with
        no re-embed): no comparison -> cosine 0 -> gate-excluded (round-2 I4). reembed_cap is the
        per-pass budget; mismatched candidates beyond it score similarity 0."""
        task_emb = self._embedder.embed(task_text)
        qtags = set(query_tags or [])
        cache: dict[str, list[float]] = {}
        reembed_used = 0
        w = self._w
        results: list[tuple[Chunk, float, float, int]] = []
        for i, c in enumerate(candidates):
            recency = math.exp(-w.decay_lambda * i)
            emb = c.embedding
            if emb is None or len(emb) != self._embedder.dim:
                h = sha1(c.content.encode("utf-8")).hexdigest()
                if h in cache:
                    emb = cache[h]
                elif reembed_used < w.reembed_cap:
                    emb = self._embedder.embed(c.content)
                    cache[h] = emb
                    reembed_used += 1
                else:
                    emb = None  # over cap -> similarity 0
            cos = 0.0
            if emb is None:
                sim = 0.0
            else:
                cos = _cosine(task_emb, emb)
                denom = max(1e-9, 1.0 - w.sim_floor)
                sim = min(1.0, max(0.0, (cos - w.sim_floor) / denom))
            tag = (len(qtags & set(c.tags)) / len(qtags)) if qtags else 0.0
            score = (w.w_recency * recency + w.w_similarity * sim
                     + w.w_tag * tag + (w.pin_bias if c.pin else 0.0))
            results.append((c, score, cos, i))
        # t = (chunk, score, cos, incoming_index); sort by (-score, incoming_index)
        results.sort(key=lambda t: (-t[1], t[3]))
        return [(c, s, cos) for (c, s, cos, _i) in results]

    def scored(self, task_text: str, candidates: list[Chunk],
               query_tags: list[str] | None = None) -> list[tuple[Chunk, float]]:
        """(chunk, score) view of scored_with_similarity — the single scoring impl (round-1 M4).
        Keeps query_tags so the tag term is preserved (round-3 C2)."""
        return [(c, s) for c, s, _cos in
                self.scored_with_similarity(task_text, candidates, query_tags)]

    def pick(self, scored_pairs: list[tuple[Chunk, float]], k: int = 10,
             token_budget: int | None = None) -> list[Chunk]:
        out: list[Chunk] = []
        used = 0
        for c, _ in scored_pairs:
            if token_budget is not None:
                t = estimate_tokens(c.content)
                if used + t > token_budget:
                    break                       # first-fit BREAK (matches arm-2)
                used += t
            out.append(c)
            if len(out) >= k:
                break
        return out

    def offload_keys(self, scored_pairs: list[tuple[Chunk, float]]) -> list[str]:
        return [c.key for c, s in scored_pairs
                if not c.pin and s < self._w.eviction_threshold]

    def select_onload(self, task_text: str, candidates: list[Chunk],
                      query_tags: list[str] | None = None, k: int = 10,
                      token_budget: int | None = None) -> list[Chunk]:
        return self.pick(self.scored(task_text, candidates, query_tags), k, token_budget)

    def select_offload(self, task_text: str, candidates: list[Chunk],
                       query_tags: list[str] | None = None) -> list[str]:
        return self.offload_keys(self.scored(task_text, candidates, query_tags))
