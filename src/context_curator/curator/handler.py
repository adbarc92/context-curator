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
    if not config.CURATOR_ONLOAD_ENABLED:               # dark default -> hook uses recency
        return {"ok": True, "keys": []}
    prompt = req.get("prompt") or ""
    k = req.get("k", 10)
    token_budget = req.get("token_budget")
    candidates = read_store.all_live_chunks()           # READ ONLY

    # On-demand IN-MEMORY embed of the most-recent NULL candidates (round-2 C3 / round-3 C-3):
    # mutate the in-memory frozen Chunks with fresh vectors so scoring SEES them; do NOT persist
    # (reconcile backfills within RECONCILE_INTERVAL_S). Older NULLs beyond the cap score 0.
    embedded = 0
    out: list[Chunk] = []
    for c in candidates:
        if c.embedding is None and embedded < config.ONDEMAND_EMBED_CAP:
            vec = embedder.embed(c.content)
            embedded += 1
            out.append(c.model_copy(update={"embedding": vec}) if vec is not None else c)
        else:
            out.append(c)

    policy = RelevancePolicy(embedder, ONLOAD_BGE_WEIGHTS)
    chosen = onload_select(
        policy, prompt, out,
        cos_threshold=ONLOAD_BGE_COSINE_THRESHOLD, k=k, token_budget=token_budget,
    )
    return {"ok": True, "keys": [c.key for c in chosen]}
