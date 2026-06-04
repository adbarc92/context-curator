# Keystone — Real-Data Verdict of Record (M4d)

**Aggregate-only** (privacy §9 — no transcript content). Whether the bge onload gate beats cheap
baselines at context selection, measured on **real Claude Code transcripts** via downstream-use gold.
Real-data counterpart to the M4c synthetic keystone (`keystone-real.md`'s sibling
`keystone-powered.md`: NEGATIVE-powered, +0.056 < +0.10 MEI).

## Outcome: HARNESS-ONLY — insufficient independent sessions
**No verdict / no flip / no decommission attempted.** The corpus has **n_sessions = 1**, below the
pre-registered definitional floor (`n_sessions ≥ 3`, spec §5.4) required for the session-clustered
bootstrap to carry any between-session information. This is the **designed, self-correcting behavior**
— the harness refuses to render a verdict it cannot support, rather than fabricating one from a single
session's (autocorrelated) turns.

**To reach a verdict: capture ≥2 more distinct work-sessions** (ideally across different projects/
tasks for diversity) and re-run. The harness is re-runnable and converges as sessions accumulate.

## What ran (the full pipeline, end-to-end on real data)
- Corpus source: this project's own transcripts (user's choice). **1 transcript = 1 session**
  (15 MB; sha256 `47f3cfd7e9fb9c19`; local-only, gitignored, never committed).
- **Harvest works:** 81 user-prompt turns → **37 kept fixtures** (downstream-use gold via
  retrieval re-fetch, W=5, verify-after-edit excluded). Gold/fixture 1–18 (median 2); candidates/
  fixture 134–761 (median 460 — real long-session context pools).
- The bge keystone was **not run**: with 1 session the train/test split yields 0 train fixtures
  (the grid-sweep needs train), and the §5.4 floor short-circuits to harness-only regardless — so
  spending bge compute on an undecidable corpus is pointless.

## Diagnostics (deterministic, no bge — corpus characterization)
| Diagnostic | Result | Reading |
|---|---|---|
| precision gate (§5.4) | **harness-only** (n_sessions=1 < 3) | primary determinant; needed-N not extrapolable below the floor |
| selection bias (§5.5) | 81 turns → 37 kept, **drop rate 0.54** | kept corpus is the file-revisit-heavy subset, as predicted; pure-reasoning/first-try turns drop out |
| lexical-bias guard (§5.2) | **NOT degenerate** — BM25 R@3 on gold **0.035** vs control 0.0 | reassuring: downstream-use gold is *not* "whatever BM25 finds" — empirically allays the round-1 C1 lexical-circularity fear on real data |
| recency audit (§5.6) | **degenerate** — gold thirds (old 6 / mid 28 / new 3) | gold clusters mid-session; would independently downgrade any verdict toward INCONCLUSIVE |

The outcome is **multiply-determined** harness-only (n_sessions floor; 0 train fixtures; recency
degeneracy). Two diagnostics are genuinely informative for the future run: the **0.54 drop rate**
(the represented task class is narrow — scope any future GREEN per §5.5) and the **non-degenerate
lexical-bias** (the gold-labeling method is not secretly a BM25 proxy on real data — the central
methodological worry from critique round 1 does not materialize here).

## Production
**No change.** `CC_CURATOR_ONLOAD` stays dark (`"0"`); `ONLOAD_BGE_COSINE_THRESHOLD` unchanged.
`git diff main` on `curator/config.py` and `policy/weights.py` is empty. The flip (M4d GREEN path)
and the decommission recommendation (NEGATIVE path) both require a real verdict, which requires
≥3 sessions.

## Reproducibility
- Pre-registered config (spec §5.7): MEI 0.10; 90% session-clustered CI; W=5; n_sessions floor 3;
  precision gate width ≤ MEI; lexical-bias margin +0.15 over a same-count seeded random control;
  min candidates 5 / min gold 1; split-by-session.
- Transcript manifest (sha256, local path only): `47f3cfd7e9fb9c19`
  `src/context_curator/eval/fixtures/_real_local/7684a379-…-86c7222de94a.jsonl` (gitignored).
- Re-run: drop more `.jsonl` sessions into `_real_local/` and
  `uv run python -c "from context_curator.eval.real_corpus import harvest_corpus; ..."` then the
  keystone + `cluster_bootstrap_ci` + `precision_gate` (Phase B of the plan).

## Bottom line
The M4d **harness is the delivered value** and it works on real transcripts. On the available single
session it correctly declines to decide — and tells you exactly what's missing (≥2 more sessions).
The bge-onload question remains **open on real data**, consistent with the synthetic NEGATIVE-powered
result; nothing about production changes until a powered real verdict exists.
