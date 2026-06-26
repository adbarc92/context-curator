# Learned ranker -- Cycle 1 feasibility (eval-only)

> Deterministic on a fixed corpus + seed; gold labels are CWD-dependent (os.path). No bge.

verdict: STRONG SIGNAL: learned - BM25 +0.203 >= MEI, CI>0 -> GO to cycle 2
mean(learned - BM25) nDCG@10: +0.2034; clustered 90% CI [+0.0732, +0.3722]
n_sessions=5 n_fixtures=195 per_session={'7684a379-a62e-49a7-b17d-86c7222de94a': 37, '883d757f-0b27-4b59-b38d-f7a90d42407a': 64, '95749f90-cc87-4362-bc63-7a91f6f6d249': 39, '9e9b47ce-eb96-4461-b379-6387b6c121c1': 41, 'b70c7330-ba02-4c92-a9d6-aa490d1eea7b': 14}
arms: learned nDCG@10=0.306 bm25 nDCG@10=0.102
precision gate: inconclusive-underpowered (needed_n=45, range~[1, 78])
circularity audit (solo nDCG@10): {'prior_refetch_solo_ndcg': 0.4454812854626555, 'same_dir_solo_ndcg': 0.3098089268111106} -- flag any within MEI of learned (0.306)
lexical-bias: gold_R@3=0.018 control=0.014 degenerate=False

---

## Interpretation & decision (2026-06-26)

**Decision: GO to Cycle 2.** Per the pre-registered rule (sign + magnitude + harvestability, not a
precise needed-N), the signal is strong and clean:

- **The learned ranker triples BM25 and bge.** Arms on real data: **learned nDCG@10 = 0.306** vs
  **BM25 0.102** vs (M4d) bge **0.091**. The headline `learned - BM25 = +0.203` (≈2× MEI), clustered
  90% CI **[+0.073, +0.372]** — the interval **excludes 0**, so the *sign* is confident.
- It uses **only cheap features** (bm25_score, recency_rank, chunk_log_len, tool_type) — no embeddings,
  no daemon. This is the bitter lesson pointing the other way: a model that **learns from the data**
  beats both the frozen embedding (bge) *and* the lexical baseline (BM25) it was supposed to dethrone.
- **needed_n ≈ 45** (range [1, 78]) to reach width ≤ MEI. The precision gate is therefore still
  **inconclusive-underpowered** at n=5 — this is a *feasibility* signal, not the final powered verdict.
  But ~45 sessions is well within reach (hundreds are available locally), so the question is resolvable.

### ⚠️ Critical caveat — the circularity audit fired loudly
A **solo `prior_refetch_count` ranker scores 0.445 — higher than the full learned model (0.306)** — and
`same_dir_recent` (0.310) matches it. Both are *within/above* MEI of the learned mean, so the
pre-registered gate marks them **circular** (proxies for the refetch dynamic that *defines* gold).

- **This does NOT taint the Cycle 1 verdict:** those two features are **excluded** from the model, so the
  +0.203 win is achieved *without* them. `recency_rank` (which the model does use) is past-production
  recency, not future-refetch, so it is predictive, not tautological.
- **It is the #1 watch-item for Cycle 2:** the gold is heavily refetch-history-driven, which makes the
  task partly "predict the sticky file." Cycle 2 must (a) keep `prior_refetch_count`/`same_dir_recent`
  **out** of the production model, and (b) sanity-check that the learned lift survives when the corpus is
  larger and the gold is stratified — i.e. confirm we're measuring relevance, not just file stickiness.

### Bottom line
The bitter-lesson test is **provisionally won by learning**: cheap, data-fit logistic >> frozen bge >>
nothing, and >> BM25. Strong enough to justify Cycle 2 (the full powered build on ≥~45 sessions), with
the refetch-circularity of the gold as the explicit methodological risk to control there.
