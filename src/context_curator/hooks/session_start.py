"""SessionStart seed hook (design §3.5): inject the durable pinned/convention set once.
`source` (startup/resume/compact/clear) is intentionally ignored — re-seeding on all of
them restores the durable set after a compaction trim (round-3 I1). No embedding."""
from __future__ import annotations

from context_curator.hooks._io import HookResult, log, run_hook
from context_curator.onload.format import format_block
from context_curator.onload.select import SEED_TOKEN_BUDGET, seed_select
from context_curator.store.interface import Store

_TITLE = "Project context: pinned decisions, contracts, conventions"


def handle(event: dict, store: Store) -> HookResult:
    chunks = seed_select(store, token_budget=SEED_TOKEN_BUDGET)
    log(f"context-curator: seeded {len(chunks)} pinned/convention chunk(s)")
    block = format_block(chunks, title=_TITLE)
    # format_block returns "" for no chunks; "" or None -> None suppresses the inject entirely
    return HookResult(0, additional_context=block or None)


def main() -> None:
    run_hook(handle, needs_store=True, fail_label="seed")


if __name__ == "__main__":
    main()
