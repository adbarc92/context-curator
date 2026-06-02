"""UserPromptSubmit onload hook (design §3.5): inject the task-relevant slice of the live
store. Uniform HashingEmbedder live path (no model load); fail-open; stdout-only inject."""
from __future__ import annotations

from context_curator.embeddings import HashingEmbedder
from context_curator.hooks._io import HookResult, log, run_hook
from context_curator.onload.format import format_block
from context_curator.onload.select import ONLOAD_K, ONLOAD_TOKEN_BUDGET, onload_select
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import ONLOAD_COSINE_THRESHOLD, ONLOAD_WEIGHTS
from context_curator.store.interface import Store

_TITLE = "Relevant context from earlier in this project"


def handle(event: dict, store: Store) -> HookResult:
    prompt = (event.get("prompt") or "").strip()
    if not prompt:                                   # whitespace embeds to the zero vector
        log("context-curator: onloaded 0 (empty prompt)")
        return HookResult(0)
    policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)   # gate floor == score floor
    chunks = onload_select(policy, prompt, store.all_live_chunks(),
                           cos_threshold=ONLOAD_COSINE_THRESHOLD, k=ONLOAD_K,
                           token_budget=ONLOAD_TOKEN_BUDGET)
    log(f"context-curator: onloaded {len(chunks)} chunk(s)" if chunks
        else "context-curator: onloaded 0 (off-topic)")
    block = format_block(chunks, title=_TITLE)
    return HookResult(0, additional_context=block or None)


def main() -> None:
    run_hook(handle, needs_store=True, fail_label="onload")


if __name__ == "__main__":
    main()
