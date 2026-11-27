<!-- graph:state -->
# State

What survives between sessions. Read at the start of a session, append at the end.

## Current

- M7 shipped as v0.0.2; both #14 exit criteria verified YES, but #14 stays open for the Windows console-flash sub-item, tracked in #12. See [CLAUDE.md](../CLAUDE.md).
- Newest handoff: [handoff-2026-06-26-learned-ranker.md](handoff/handoff-2026-06-26-learned-ranker.md): PRs #15 and #16 unmerged, merge #15 first.
- Learned-ranker Cycle 1 verdict is GO: [keystone-learned.md](superpowers/keystone-learned.md).

## Decisions

- Ship BM25 as the onload ranker, demote semantic (bge) to dark: [semantic-ranker.md](decisions/semantic-ranker.md).
- Cycle 1 GO to Cycle 2 of the learned ranker: [keystone-learned.md](superpowers/keystone-learned.md).

## Dead ends

- Semantic bge onload gate did not beat BM25: synthetic [keystone-powered.md](superpowers/keystone-powered.md), real [keystone-real.md](superpowers/keystone-real.md). Reasoning in [retrospective-semantic-relevance.md](retrospective-semantic-relevance.md).
- gui-script mitigation only partly stopped the Windows console flash ([CODEBASE-DIGEST.md](CODEBASE-DIGEST.md), open questions).

## Open

- Merge PRs #15 then #16; wire BM25 into the live onload path; Cycle 2 needs about 45 real sessions: [handoff-2026-06-26-learned-ranker.md](handoff/handoff-2026-06-26-learned-ranker.md).
- Circularity caveat: a prior-refetch-count ranker outscored the learned model: [keystone-learned.md](superpowers/keystone-learned.md).
