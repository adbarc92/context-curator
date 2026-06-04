"""UserPromptSubmit onload hook (design §6.2): ask the curator for a bge selection; on any
failure, fall back to in-process recency-only and (unless 'warming') spawn the curator.
Constructs NO embedder/policy — embedding is the curator's job (round-1 I4)."""
from __future__ import annotations

from context_curator.curator import client, runtime
from context_curator.hooks._io import HookResult, log, run_hook
from context_curator.observe.decision_log import record_decision
from context_curator.onload.format import format_block
from context_curator.onload.select import ONLOAD_K, ONLOAD_TOKEN_BUDGET, recency_select
from context_curator.store.interface import Store
from context_curator.store.paths import resolve_db_path

_TITLE = "Relevant context from earlier in this project"


def handle(event: dict, store: Store) -> HookResult:
    prompt = (event.get("prompt") or "").strip()
    if not prompt:
        log("context-curator: onloaded 0 (empty prompt)")
        return HookResult(0)
    chunks_all = store.all_live_chunks()
    try:
        keys = client.request_onload(prompt, k=ONLOAD_K, token_budget=ONLOAD_TOKEN_BUDGET)
    except client.CuratorUnavailable as e:
        if e.respawn:
            runtime.spawn_detached(resolve_db_path())            # fire-and-forget; skip if warming
        keys = []                                                # -> recency floor below
    if keys:
        by_key = {c.key: c for c in chunks_all}
        chunks = [by_key[k] for k in keys if k in by_key]        # preserve curator RANK order
        log(f"context-curator: onloaded {len(chunks)} chunk(s) [curator]")
    else:
        # No keys: curator down/warming, OR the dark default (flag off) returns [], OR a genuinely
        # empty selection. In ALL these the always-fast recency floor applies (spec §8: dark default
        # = recency). NOTE for M3b: once CC_CURATOR_ONLOAD is on, a genuinely-empty SEMANTIC result
        # also lands here -> recency; revisit then if flag-on-empty should instead inject nothing.
        chunks = recency_select(chunks_all, k=ONLOAD_K, token_budget=ONLOAD_TOKEN_BUDGET)
        log(f"context-curator: onloaded {len(chunks)} chunk(s) [recency]")
    # M6 decision log: source reflects what was ACTUALLY injected (curator keys can be filtered to
    # empty by by_key). Fail-open: observability must never break injection.
    source = "curator" if (keys and chunks) else ("recency" if chunks else "none")
    try:
        record_decision(event.get("session_id", "") or "unknown",
                        prompt[:80], source, [c.key for c in chunks])
    except Exception:
        pass
    block = format_block(chunks, title=_TITLE)
    # format_block returns "" for no chunks; "" or None -> None suppresses the inject entirely
    return HookResult(0, additional_context=block or None)


def main() -> None:
    run_hook(handle, needs_store=True, fail_label="onload")


if __name__ == "__main__":
    main()
