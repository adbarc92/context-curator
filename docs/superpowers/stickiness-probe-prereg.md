# Pre-registration — refetch-stickiness ranker (temporal-holdout disambiguation)

> **Committed before the temporal-holdout run.** Freezes the analysis so the verdict can't be
> reverse-engineered from the result. Pairs with [`learned-feasibility-prereg.md`](learned-feasibility-prereg.md)
> and the Cycle-1 verdict [`keystone-learned.md`](keystone-learned.md).
> Origin: the 2026-07-05 salvage probe (below) surfaced a lead the Cycle-1 docs frame as a *threat*.

## Why this exists — the salvage lead
Cycle 1 excluded `prior_refetch_count` and `same_dir_recent` as **circular** (proxies for the
refetch-defined gold) and kept them out of the model. A follow-up probe re-ran them as **solo,
parameter-free rankers under the same leave-one-session-out (LOSO)** protocol as the learned model:

| Ranker (LOSO, nDCG@10) | Score | vs BM25 |
|---|---|---|
| `bm25` | 0.102 | — |
| `learned_cheap` (Cycle-1 model) | 0.306 | +0.204 |
| `same_dir` solo (parameter-free) | 0.310 | +0.208 |
| **`prior_refetch` solo (parameter-free)** | **0.445** | **+0.343** |
| `learned_plus` (cheap + refetch + same_dir as model features) | 0.299 | +0.197 |

Two facts the Cycle-1 write-up undersells:
1. **The best cheap ranker in the whole study is a parameter-free heuristic** — "rank by how many
   times this file was already re-fetched this session" — at **0.445, ~4.4× BM25**, computable at
   serve time from the existing store. Its LOSO score equals its pooled score because it has no
   parameters to overfit.
2. **The learned logistic can't exploit the signal even when handed it** (`learned_plus` ≈
   `learned_cheap`). The value is the raw count, not a fitted combination — a second bitter-lesson
   beat inside the same corpus (dumb count > fitted model).

**But** `prior_refetch` is exactly the feature the circularity audit flagged: gold is *defined* by
refetch and the feature is *made of* past refetches — shared mechanism. LOSO rules out cross-session
overfit; it does **not** rule out shared-mechanism inflation. This probe settles that one question.

## Question
Is the refetch-stickiness lift a **deployable predictor** (past behaviour genuinely predicts future
re-fetch) or a **tautological artifact** of the gold definition — measured under a within-session
**temporal** holdout with strictly disjoint past/future windows?

## Frozen analysis
- **Feature under test:** `prior_refetch_count(chunk, t)` = number of times the chunk's file-entity
  was fetched in turns `[0, t]` of the session. Optional secondary: `same_dir_recent`.
- **Temporal split (the disambiguator):** for each session, pick split turn(s) `t`. Count refetches
  **only over turns `≤ t`**. Score against gold defined **only over turns `[t+1, t+W]`**, `W=5`.
  The counting window and the gold window are **disjoint** by construction — no shared refetch event
  can appear in both. (Cycle 1's audit used overlapping full-session windows; that's the confound.)
  Evaluate at multiple split points per session (e.g. quartile turns) and pool.
- **Rankers scored (same fixtures):** `bm25` (deployable baseline), `prior_refetch` solo,
  `bm25 + prior_refetch` blend (rank-sum or a 2-feature logistic), `learned_cheap` (reference).
- **Estimation:** LOSO across sessions × the temporal splits; **90% session-clustered bootstrap CI**
  (`cluster_bootstrap_ci`), clustered by session. Seed 0.
- **MEI:** +0.10 nDCG@10 vs BM25 (the same bar; no goalpost-moving). Metric nDCG@10.
- **Power:** `needed_n_range` as an order-of-magnitude range, never a precise gate. This corpus is 5
  sessions — treat as feasibility, harvest more before any final verdict.

## Decision rule (anti-goalpost-moving)
- **Stickiness is REAL → Track A ships a stickiness ranker (blended with BM25):** the temporal-holdout
  `prior_refetch` (or blend) − BM25 stays **clearly positive** (right sign, non-trivial magnitude)
  under the disjoint-window split. It becomes the default live-onload ranker over recency/BM25, gated
  on the p50<300ms / p95<600ms hook budget (a per-session refetch counter is trivially cheap).
- **Stickiness COLLAPSES → it was tautological; ship plain BM25:** the lift falls to ≈0 (or below MEI)
  once the windows are disjoint. BM25 remains the Track-A ship; refetch stays audit-only, as Cycle 1
  had it. The learned-ranker Cycle 2 proceeds on cheap features with refetch barred.
- **Ambiguous / underpowered:** harvest ≥~45 sessions (already the Cycle-2 prerequisite) and re-run
  before deciding; do not ship on 5.

## Relationship to the other tracks
- **Track A:** this probe decides *which* cheap ranker A ships (stickiness-blend vs plain BM25). Run
  it **before** wiring `onload/select.py`, so the live path is wired once.
- **Track B Cycle 2:** unchanged. Refetch/locality stay **out** of the learned production model
  regardless of this probe's outcome — this probe asks whether stickiness is worth shipping *as its
  own heuristic*, not whether to fold it into the learned model (the `learned_plus` result says
  folding it in doesn't help anyway).

## Corpus
Same 5 pinned real sessions as [`learned-feasibility-prereg.md`](learned-feasibility-prereg.md)
(local-only, gitignored, CWD-dependent gold). Feasibility only at n=5; harvest before final verdict.

## Outcome (2026-07-05) — STICKINESS IS REAL → ship it (Track A)

**Design note (honest amendment):** on reading the harvest code, the Cycle-1 windows turned out to
be **already disjoint** — a fixture at turn `i` counts refetches only over its candidate history
(turns `< i`), and gold is `[i, i+w]`. So 0.445 was never a temporal leak. The remaining doubt was
whether the lift is a *robust* prior or a trivial "the file open right now stays open." The frozen
test was therefore run in its **stronger form: a temporal-gap sweep** — push the gold window `g`
turns into the future (`[i+g, i+g+w]`) while the feature stays past-only. A tautology collapses as
`g` grows; a real prior survives.

| gap | bm25 | refetch | blend | refetch − bm25 (90% clustered CI) |
|---:|---:|---:|---:|---|
| 0 | 0.102 | 0.445 | 0.377 | +0.343 [+0.133, +0.580] |
| 1 | 0.097 | 0.447 | 0.371 | +0.350 [+0.133, +0.591] |
| 2 | 0.088 | 0.452 | 0.365 | +0.365 [+0.145, +0.623] |
| 3 | 0.082 | 0.459 | 0.362 | +0.377 [+0.163, +0.627] |
| 5 | 0.080 | 0.456 | 0.361 | +0.376 [+0.158, +0.634] |

- **Robust, not tautological.** refetch nDCG *holds and rises* (0.445 → 0.459) as the gold window
  moves 5 turns ahead, while BM25 *decays* (0.102 → 0.080). The lift vs BM25 excludes 0 at every gap
  and *widens* (+0.343 → +0.377). A tautology would have decayed toward BM25; this strengthens.
- **Decision rule → STICKINESS IS REAL.** Per the frozen rule, Track A ships a stickiness ranker.
- **Product correction: ship stickiness-dominant, NOT a 50/50 blend.** The rank-sum blend (0.36–0.38)
  is *worse* than pure refetch (0.45) at every gap — BM25 adds noise. Use past-refetch count as the
  primary sort; BM25 at most a tie-breaker. (The prereg had assumed a blend; the data overrides it.)
- **Scope honesty:** n=5 sessions — feasibility strength. The robustness dimension materially de-risks
  the tautology worry, but the ≥~45-session harvest still gates the *final* powered verdict. Absolute
  nDCG stays modest (~0.45) because the pools are needle-in-haystack (~460 cand / ~2 gold) — that's
  the task, not the ranker.

Run: `stickiness_gap.py` (scratchpad), `uv run --no-sync --group learn`. Deterministic, seed 0.

---

### Appendix — the salvage probe (2026-07-05, exploratory, pre-registration is above)
Script `salvage_probe.py` (scratchpad): re-ran the two excluded features as solo LOSO rankers and as
added model features. Produced the table at the top. This is the *exploratory* result that motivated
the frozen test above — it is **not** itself the verdict (it used overlapping windows, the very
confound this pre-registration removes).
