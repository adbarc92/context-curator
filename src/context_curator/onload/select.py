"""Onload selection (design §3.2): per-prompt raw-cosine gate + SessionStart seed set."""
from __future__ import annotations

import re

from context_curator.models import Chunk
from context_curator.policy.relevance import RelevancePolicy
from context_curator.store.interface import Store
from context_curator.tokens import estimate_tokens

# k / budgets live here (no cross-dependency); the gate threshold + ONLOAD_WEIGHTS live in
# policy/weights.py to avoid a circular import (round-3 C1). cos_threshold is a parameter, so
# this module does NOT import from weights.
ONLOAD_K = 10
ONLOAD_TOKEN_BUDGET = 1500
SEED_TOKEN_BUDGET = 1500

# proj:{project}:conventions are SessionStart's job -> excluded from per-prompt onload so a
# post-compaction turn doesn't double-inject the durable set (round-3 I1). Always applied with
# .fullmatch() (so no ^$ anchors here) -> 'shared:...:naming-conventions' must NOT match.
_CONV_RE = re.compile(r"proj:[^:]+:conventions")


def _is_durable(c: Chunk) -> bool:
    """Pins + proj:*:conventions are the SessionStart durable set — excluded from per-prompt
    onload AND the recency fallback (round-3 I1)."""
    return c.pin or bool(_CONV_RE.fullmatch(c.key))


def _first_fit(chunks: list[Chunk], k: int, token_budget: int | None) -> list[Chunk]:
    out: list[Chunk] = []
    used = 0
    for c in chunks:
        if token_budget is not None:
            t = estimate_tokens(c.content)
            if used + t > token_budget:
                break
            used += t
        out.append(c)
        if len(out) >= k:
            break
    return out


def recency_select(candidates: list[Chunk], *, k: int, token_budget: int | None) -> list[Chunk]:
    """No-embedding fallback (design §6.3, DESIGN §4.2 'tag+recency'): newest-first (candidates
    arrive seq DESC), pins/conventions excluded, first-fit under k+budget. Recency-only in
    practice — tags are tool-provenance not topic (w_tag=0)."""
    return _first_fit([c for c in candidates if not _is_durable(c)], k, token_budget)


def onload_select(policy: RelevancePolicy, task_text: str, candidates: list[Chunk], *,
                  cos_threshold: float, k: int, token_budget: int | None) -> list[Chunk]:
    """Per-prompt onload: candidates whose RAW COSINE >= cos_threshold (round-1 C3), ranked by
    full score, first-fit under k+budget. EXCLUDES pins AND proj:*:conventions (both seeded at
    SessionStart). `policy` carries ONLOAD_WEIGHTS (sim_floor == cos_threshold, round-2 C1) so
    the admitted band ranks by a real recency+similarity blend, not recency alone. No cross-turn
    dedup — the relevant slice is (re)injected every turn (round-2 M1)."""
    eligible = [(c, score)
                for c, score, cos in policy.scored_with_similarity(task_text, candidates)
                if not _is_durable(c) and cos >= cos_threshold]
    return policy.pick(eligible, k, token_budget)


def seed_select(store: Store, *, token_budget: int | None) -> list[Chunk]:
    """SessionStart durable set (no task signal, NO embedding): ALL pinned chunks (never
    budget-truncated — round-1 M2) + proj:{project}:conventions under the remaining budget."""
    chunks = store.all_live_chunks()                   # newest-first
    pins = [c for c in chunks if c.pin]                # always included
    conv = [c for c in chunks if not c.pin and _CONV_RE.fullmatch(c.key)]
    out, used = list(pins), sum(estimate_tokens(c.content) for c in pins)
    for c in conv:
        t = estimate_tokens(c.content)
        if token_budget is not None and used + t > token_budget:
            break
        out.append(c)
        used += t
    return out
