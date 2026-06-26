# Pre-registration — Learned-ranker Cycle 1 feasibility run

> **Committed before any model is fit on the real corpus.** This freezes the analysis so the verdict
> cannot be reverse-engineered from the result (the failure mode the M3b–M4d evals were built to avoid).
> Spec: [`specs/2026-06-25-learned-onload-ranker-design.md`](specs/2026-06-25-learned-onload-ranker-design.md) §3.

## Question
Does a learned Tier-1 logistic ranker beat plain **BM25** at re-onload selection on real Claude Code
sessions, by the pre-registered **+0.10 nDCG@10** margin — enough to justify the full Cycle 2 build?

## Frozen analysis
- **Model:** L2 logistic regression, `class_weight="balanced"`, `C=1.0` (fixed a-priori — 5 sessions is
  too few to tune), `random_state=0`, `max_iter=1000`. Features z-scored on the fit set.
- **Features (Tier-1, cheap, eval-side):** `bm25_score`, `recency_rank`, `chunk_log_len`,
  `tool_type` one-hot (lowercased, fixed vocab + `other`). No embeddings. No refetch/locality features
  in the model (those are audit-only — see circularity gate).
- **Baseline (deployable):** plain **BM25** ranking. Headline = mean(`learned_nDCG@10 − BM25_nDCG@10`).
  We do **not** use a per-fixture `max(BM25, recency)` oracle.
- **Estimation:** **leave-one-session-out** over the 5 sessions — fit on 4, score the held-out 1,
  collect per-fixture deltas; pool all 5 held-out sessions, clustered by session, into a
  **90% session-clustered bootstrap CI** (`cluster_bootstrap_ci`).
- **MEI:** +0.10 nDCG@10. **Seed:** 0. **Gold:** downstream-use (file re-fetched within `W=5`,
  verify-after-edit excluded). **Metric:** nDCG@10 (near-binary at ~2 gold/fixture — expected).
- **Power read:** `needed_n_range` — bootstrap whole sessions, report needed-N as an **order-of-magnitude
  range**, never a precise gate.

## Pre-registered gates / audits
- **Circularity audit:** solo-ranker nDCG@10 for `prior_refetch_count` and `same_dir_recent`
  (locality window `W_loc=5`). Any feature whose solo ranker lands within MEI of the learned mean is
  **circular** (a proxy for the refetch-defined gold) and is barred from the Cycle 2 production model.
  (These features are NOT in the Cycle 1 model; this audit characterizes the risk for Cycle 2.)
- **Lexical-bias guard:** `lexical_bias` over all fixtures (margin +0.15) — gold must not be a BM25 proxy.

## Decision rule (anti-goalpost-moving)
The build/no-build call keys off the **effect sign + magnitude** and the harvestability of ~20–40
sessions — **NOT** a precise needed-N (5 sessions cannot estimate it precisely).
- **GO to Cycle 2** iff `learned − BM25` is **clearly positive** (right sign, non-trivial magnitude) AND
  ~20–40 sessions plausibly resolve it to width ≤ MEI.
- **NO-GO / ship BM25 (track A), retire the question** iff learned **ties or loses** BM25 on the 5
  sessions. The bitter lesson would then have spoken twice (synthetic + real for bge; now learned).
- **Reshape** (raise MEI / weaken claim / different data) iff the order-of-magnitude needed-N is
  astronomically large.

## Corpus (the exact 5 sessions, pinned by sha256[:16])
Local-only, gitignored, aggregate-only (DESIGN §9). Harvest root: the repo working dir on the run
machine (gold labels are CWD-dependent via `os.path.abspath`).

| sha256[:16] | session file | project |
|---|---|---|
| `47f3cfd7e9fb9c19` | `7684a379-…-86c7222de94a.jsonl` | context-curator |
| `c94080b5cfc8deb5` | `883d757f-…-f7a90d42407a.jsonl` | hexy |
| `529929af76991fe3` | `95749f90-…-7a91f6f6d249.jsonl` | halyard |
| `b183be9bdb7ea27a` | `9e9b47ce-…-6387b6c121c1.jsonl` | Disruption |
| `4fcc66d424b7e55d` | `b70c7330-…-aa490d1eea7b.jsonl` | tenzy-client |

## Outcome
Recorded in [`keystone-learned.md`](keystone-learned.md) after the run.
