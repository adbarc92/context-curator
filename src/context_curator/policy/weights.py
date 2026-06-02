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
