"""Policy scoring weights (design §3.2). Provisional defaults; M3b sweeps them."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyWeights:
    w_recency: float = 0.35
    w_similarity: float = 0.65
    w_tag: float = 0.0              # tags are tool-provenance not topic -> OFF by default
    pin_bias: float = 1000.0
    eviction_threshold: float = 0.15
    decay_lambda: float = 0.1       # recency = exp(-decay_lambda * rank)
    sim_floor: float = 0.5          # affine-rescale cosine above this floor
    reembed_cap: int = 128          # max inline re-embeds per scoring pass on dim mismatch


# Onload operating point (design §3.2). The threshold lives HERE next to ONLOAD_WEIGHTS so
# the two cannot drift and so neither side imports onload/select (round-3 C1: avoids a
# weights -> select -> relevance -> weights cycle). ONLOAD_WEIGHTS pins sim_floor to the gate
# threshold (round-2 C1) so an admitted chunk's similarity grows from 0 at the gate boundary.
# 0.15 is an UNTUNED placeholder for HashingEmbedder; re-derived on the bge swap (M4b).
ONLOAD_COSINE_THRESHOLD: float = 0.15
ONLOAD_WEIGHTS = PolicyWeights(sim_floor=ONLOAD_COSINE_THRESHOLD)
