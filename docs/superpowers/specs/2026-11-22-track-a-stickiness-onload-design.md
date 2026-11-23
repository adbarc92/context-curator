# Design — Track A: stickiness-ranked live onload

**Status:** Built 2026-11-22 (overnight, unattended); awaiting review in the Track-A PR.
**Evidence:** [`../stickiness-probe-prereg.md`](../stickiness-probe-prereg.md) (n=5 feasibility: refetch
count 0.445 nDCG@10 vs BM25 0.102, robust under a temporal-gap sweep).
**Decision it implements:** [`../../decisions/semantic-ranker.md`](../../decisions/semantic-ranker.md)
(ship a cheap ranker, keep semantic dark) as amended by the stickiness outcome (ship
stickiness-dominant, BM25 as tie-breaker, not a blend).

## Gate this work closes, and the one it does not
- **Closes (engineering gate):** the live `UserPromptSubmit` floor ranks by stickiness instead of
  recency, inside the hook budget (p50 < 300 ms, p95 < 600 ms), and the eval harness scores the
  *exact* function the hook runs, so a measured number is a shipped number.
- **Does not close (evidence gate):** the *final powered* stickiness verdict needs a corpus of at
  least ~45 harvestable sessions. On 2026-11-22 the available corpora were 5 sessions (the Windows
  machine, pinned) and 2 harvestable sessions on the Linux machine (13 main transcripts; the other
  111 `.jsonl` files are single-turn subagent transcripts, which yield no fixtures). The powered
  analysis is frozen now, before data exists, in
  [`../stickiness-powered-prereg.md`](../stickiness-powered-prereg.md), and runs as one command.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Entities travel as tags**: each live tool-result chunk gets `entity:<canonical path>` tags from `tool_input` keys `file_path` / `notebook_path` / `path`. No schema or `Store` interface change. | The `Store` interface is frozen behind a contract suite; tags are already a JSON list, `query(tags=…)` is a subset filter (extra tags are inert), and the policy's tag weight is 0. |
| D2 | **One entity module** (`context_curator/entities.py`) is used by both live capture and the eval harvest (`eval/real_corpus.py` re-exports it). | The live feature must be the measured feature; two copies of path canonicalisation would drift. |
| D3 | **Ranker = (refetch count desc, BM25 desc, newer first).** Refetch count of a candidate = number of *earlier* candidates whose entities match (equal, or one contains the other) — identical to the eval's `prior_refetch_scores`. | The evidence: pure refetch beats a rank-sum blend at every gap. BM25 only breaks ties (the eval's solo ranker broke ties by key, i.e. arbitrarily). |
| D4 | **Indexed refetch count** (group by distinct entity set; O(n·D)) with a randomized equivalence test against the eval's O(n²) reference. | Naive O(n²) at ~900 live chunks costs hundreds of ms in the hook. |
| D5 | **BM25 rewritten with `Counter`** — bit-identical scores (equivalence test against a frozen copy of the previous implementation), ~20% faster. | Real stores hold ~900 chunks / ~4.3 M chars; BM25 is the dominant hook cost (~85–105 ms). |
| D6 | **Stickiness path budgets on the rendered size** (`format_block` truncates each chunk to 1 200 chars) instead of the full content. First-fit break semantics otherwise unchanged. | Real stores: Edit chunks average ~2 850 tokens, Reads ~900–1 600. Sticky chunks are exactly the big ones, so full-size budgeting against 1 500 tokens rejects the rank-1 chunk and the break injects nothing. Budgeting on what is actually injected fixes that without changing the injected text. `recency_select` and the curator path keep their existing semantics. |
| D7 | **Wiring:** the hook's floor (curator down, flag off, or empty) becomes `stickiness_select`; the decision-log `source` becomes `"stickiness"`. Curator/semantic stays dark behind `CC_CURATOR_ONLOAD`. `recency_select` stays (tests, reference). | Smallest change that makes stickiness the default ranker. |
| D8 | **Cross-session pool:** the live candidate pool is every live chunk in the per-project store (24 h TTL), not just the current session; refetch counts span that pool. | That is what the store holds; the eval pool is single-session. Recorded as a known difference for the powered run to keep in mind. Pre-existing chunks (no entity tags) score 0 and fall back to BM25 order, which is still better than recency on the evidence. |

## Out of scope (recorded, not built)
- Deduplicating multiple copies of the same file in one selection (older copies are stale). Measure
  with the eval module before changing the measured ranker.
- Backfilling entity tags for existing chunks (they expire within 24 h).
- Removing the semantic curator / `[embed]` extra (decision follow-up, separate PR).
