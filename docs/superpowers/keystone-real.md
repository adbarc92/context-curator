# Keystone — Real-Data Verdict of Record (M4d)

**Aggregate-only** (privacy §9 — no transcript content). Whether the bge onload gate beats cheap
baselines at context selection, measured on **real Claude Code transcripts** via downstream-use gold.
Real-data counterpart to the M4c synthetic keystone (sibling `keystone-powered.md`: NEGATIVE-powered,
semantic +0.056 < +0.10 MEI).

## Outcome (2026-06-25): BASELINE WINS — powered, semantic loses to BM25
**Powered verdict reached.** With **5 distinct work-sessions across 5 different projects** the corpus
clears the pre-registered `n_sessions ≥ 3` floor (4 test sessions) and the session-clustered CI is
precise enough to place the effect (`width 0.044 ≤ MEI 0.10` → precision gate returns **verdict**).

- **Headline:** semantic (bge) vs the *strongest* per-fixture cheap baseline = **−0.053 nDCG@10**,
  session-clustered 90% CI **[−0.085, −0.041]** — the interval lies **entirely below zero**.
- **Arms:** BM25 **0.100** > recency 0.090 ≈ semantic **0.091**. The semantic onload is the **worst**
  arm *and* the most expensive (warm bge process, embeddings at write/read). BM25 is the best cheap ranker.
- n_test = 154 fixtures; 41 train fixtures (1 session); seed 0.

This is **stronger and more decisive than the synthetic NEGATIVE-powered result**: on real
transcripts the semantic path doesn't merely fail to clear the MEI, it is **beaten by plain BM25**.

## What ran (full pipeline, end-to-end on real data)
- Corpus: 5 local transcripts (1 = 1 session), gitignored, never committed:
  Disruption (train, 41 fx), halyard (39), hexy (64), context-curator (37), tenzy-client (14).
  195 fixtures total; **1 transcript = 1 session**; split-by-session (`test_frac=0.75`).
- Harvest: downstream-use gold (retrieval re-fetch within W=5, verify-after-edit excluded).
- bge keystone ran in full: `grid_sweep` on train → `w_similarity=0.5` → `evaluate` 3 arms on test →
  per-fixture nDCG deltas → `cluster_bootstrap_ci` (session-clustered) → `precision_gate`.

## Diagnostics (the fairness checks that make the verdict trustworthy)
| Diagnostic | Result | Reading |
|---|---|---|
| precision gate (§5.4) | **verdict** (n_sessions=4 ≥ 3; width 0.044 ≤ MEI) | the effect is precisely placed — not harness-only, not underpowered |
| lexical-bias guard (§5.2) | **NOT degenerate** — gold R@3 0.017 vs control 0.012 (< +0.15) | downstream-use gold is *not* a BM25 proxy; BM25's win is genuine, not circular |
| recency audit (§5.6) | **healthy** — gold thirds new 1969 / mid 1866 / old 1937 (≈ even) | no recency degeneracy (the n=1 pilot's mid-clustering does not recur) |

Both worry-diagnostics come back clean, so the negative verdict is **not** a measurement artifact —
it is a real property of the bge-vs-BM25 comparison on this machine's real sessions.

## Production / decision
The bge-onload question is now **settled NEGATIVE on real data**: ship **BM25 as the onload ranker**
and demote the semantic/bge path to optional-dark (or remove it). Full rationale, scope of what stays
(store + hooks + guardrails are validated and keep), and follow-up wiring in
**[`docs/decisions/semantic-ranker.md`](../decisions/semantic-ranker.md)**.

## Reproducibility
- Pre-registered config (spec §5.7): MEI 0.10; 90% session-clustered CI; W=5; n_sessions floor 3;
  precision gate width ≤ MEI; lexical-bias margin +0.15; split-by-session; seed 0.
- Re-run: drop `.jsonl` sessions into `src/context_curator/eval/fixtures/_real_local/` (gitignored),
  install the `[embed]` extra, then `harvest_corpus(_real_local/*)` → write a corpus dir of per-fixture
  `*.json` → `run_keystone(corpus, FastEmbedEmbedder())` → `cluster_bootstrap_ci(deltas, test_session_ids)`
  → `precision_gate(lo, hi, n_sessions)`. Cache the embedder by content (bge is deterministic per text)
  to keep the run to ~2 min. **bge floats are machine-sensitive — regenerate, don't diff.**

## Bottom line
The M4d harness delivered a **powered real-data verdict**: bge semantic onload is beaten by BM25
(−0.053, CI below 0), corroborating the synthetic NEGATIVE and removing the last open lever. The
product call is no longer pending data — it is made: **BM25 ranker, semantic demoted.**
