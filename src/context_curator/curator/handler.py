"""Curator onload handler (design §5.4): READ-ONLY, in-memory on-demand embed, dark-flag gate.
Performs NO DB write — the reconcile thread is the sole writer (round-3 C-1/C-2)."""
from __future__ import annotations

from context_curator.curator import config
from context_curator.embeddings import Embedder
from context_curator.models import Chunk
from context_curator.onload.select import onload_select
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import ONLOAD_BGE_COSINE_THRESHOLD, ONLOAD_BGE_WEIGHTS
from context_curator.store.interface import Store


def handle_onload(read_store: Store, embedder: Embedder, req: dict) -> dict:
    """Read-only onload. req keys: prompt (str), k (int, default 10), token_budget (int|None).
    Returns {"ok": True, "keys": [...]}. Dark by default (CC_CURATOR_ONLOAD off) -> {"keys": []}."""
    if not config.CURATOR_ONLOAD_ENABLED:               # dark default -> hook uses recency
        return {"ok": True, "keys": []}
    prompt = req.get("prompt") or ""
    k = req.get("k", 10)
    token_budget = req.get("token_budget")
    candidates = read_store.all_live_chunks()           # READ ONLY

    # On-demand IN-MEMORY embed of the most-recent NULL candidates (design §5.4): a chunk captured
    # this turn is still NULL until a reconcile tick, so embed it here and thread the vector into
    # the in-memory frozen Chunk (model_copy) so scoring SEES it — WITHOUT persisting (the reconcile
    # thread is the sole writer). Bounded by ONDEMAND_EMBED_CAP to hold the §8 latency budget;
    # remaining NULLs stay cos-0 and are gate-excluded.
    embedded = 0
    out: list[Chunk] = []
    for c in candidates:
        if c.embedding is None and embedded < config.ONDEMAND_EMBED_CAP:
            vec = embedder.embed(c.content)
            if vec is not None:
                embedded += 1                       # count only real embeds against the cap
                out.append(c.model_copy(update={"embedding": vec}))
            else:
                out.append(c)                       # no vector (e.g. NullEmbedder) -> unchanged
        else:
            out.append(c)

    policy = RelevancePolicy(embedder, ONLOAD_BGE_WEIGHTS)
    chosen = onload_select(
        policy, prompt, out,
        cos_threshold=ONLOAD_BGE_COSINE_THRESHOLD, k=k, token_budget=token_budget,
    )
    return {"ok": True, "keys": [c.key for c in chosen]}
