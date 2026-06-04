# Keystone — Powered Synthetic Verdict (M4c)

**Verdict of record.** Whether the bge onload gate (semantic policy) beats cheap baselines at
context selection, measured on a **fair, powered, blind-judged synthetic corpus** with a **strong
BM25 baseline**. This is the M4c deliverable. **It does NOT flip production** — the
`CC_CURATOR_ONLOAD` flag and `ONLOAD_BGE_COSINE_THRESHOLD` are unchanged; the production decision is
M4d, on real mined transcripts (design §6 C8, §9).

> bge floats are machine-sensitive. REGENERATE, do not diff. `seed` fixes bootstrap resampling only.

## Verdict
**NEGATIVE (powered).** The tuned semantic arm beats the strongest cheap baseline by
**+0.056 nDCG@10** (90% CI **[0.009, 0.102]**, n_test=26). The CI excludes 0 — the advantage is
*real and statistically detectable* — but it is **below the pre-registered +0.10 minimum effect of
interest (MEI)**. On fair synthetic data, bge's edge over a strong lexical baseline is not
practically meaningful. **Recommendation: do not flip the onload gate on this evidence; defer to
M4d real-data keystone.**

## Three-arm results (held-out test split, n=26, k=10, seed=0)
| Arm | nDCG@10 | R@3 |
|---|---|---|
| semantic (tuned bge, `w_similarity=1.0`) | **0.880** | 0.596 |
| BM25 (lexical, per-fixture smoothed IDF) | 0.811 | 0.541 |
| recency-only | 0.428 | 0.147 |

- **Headline** = semantic − **strongest cheap baseline per fixture** (max of recency, BM25), so bge
  must beat whichever cheap method wins each task. mean **+0.0562**, sd 0.150, 90% CI [0.009, 0.102].
- BM25 is a genuinely strong comparator here (0.811): on paraphrase-realistic fixtures, lexical
  retrieval captures most of the signal, leaving bge a small margin.
- The semantic arm's weight was grid-swept on the train split → `w_similarity=1.0`.

### Pilot vs powered (why we grew the corpus)
A 16-fixture pilot (n_test=8) showed +0.129 (CI [0.034, 0.226]) — apparently GREEN. The powered
n_test=26 run regressed it to +0.056 (sub-MEI). The small pilot over-estimated the effect; the
power-sized run is the trustworthy number. This is exactly why M4c grew the corpus rather than
flipping on the directional first-look.

## Corpus fairness (the result is only meaningful because the corpus is fair)
- **35 fixtures** (9 train / 26 test), assembled from 40 LLM-generated raw fixtures minus 5 the
  blind gold-judge dropped. Each: 12–20 chronological chunks, ≥3 paraphrased gold, ≥2 lexically-
  tempting hard negatives (tagged `hard_neg`). Generation protocol:
  `docs/superpowers/specs/2026-06-03-corpus-generation-protocol.md`.
- **Fairness audit** (`eval/corpus_audit.py`): `ok=True` ("fair"). Gold-position histogram across
  recency thirds **(oldest 12, middle 12, newest 11)** — gold is genuinely mixed, NOT recency-
  trivial. (Recency-only's low 0.428 confirms this.)
- **Hard-negative discrimination:** the blind judge marked **0 of 80** hard negatives relevant
  (0% false-positive) — the decoys are genuinely off-topic AND the judge discriminates rather than
  rubber-stamps.

## Circularity guard (is the survivor set secretly bge-aligned?)
The independent blind gold-judge (`eval/gold_judge.py`) dropped 5/40 fixtures whose planted gold it
judged non-relevant. Guarding against the judge stripping BM25-favorable cases (which would rig the
corpus toward bge): **drop-rate by BM25-recall tercile = high 0.136 vs low 0.071** (~1.9×, both on
small counts). Below the >2× rigging signal → **no rigging detected**. (Borderline given only 5
drops; M4d's larger real corpus will tighten this.)

## Threshold sweep (informational — NOT applied, verdict is not GREEN)
Over 125 gold bge-cosines (range 0.48–0.818), the recall-floor (≥0.80 gold recall) rule
(`eval/threshold_sweep.py`) picks **0.60**. At the current production `ONLOAD_BGE_COSINE_THRESHOLD
= 0.55`, gold recall is **0.98**. Recorded for M4d reference only; **no production constant is
changed in M4c.**

## Power
Pre-registered **MEI = +0.10 nDCG**, two-sided 90% CI decision rule (≡ one-sided 95%). Observed
per-fixture-delta sd = **0.150**; the 90%-power target for MEI=0.10 was **n_test ≈ 25**, and the
corpus delivers **n_test = 26** — adequately powered. The NEGATIVE verdict is therefore a *powered*
conclusion (effect < MEI with the CI excluding 0), not an underpowered "inconclusive."

## Reproducibility
- Frozen per-fixture bge cosine matrix: `fixtures/powered/_bge_cosines.json`, sha256 `f7481196f19ec655`.
- Blind judge verdicts + drop summary: `fixtures/powered/_raw_pregenerated/_judge/`.
- `fastembed 0.8.0`, `onnxruntime 1.26.0`, bge-small-en-v1.5 (dim 384), `seed=0`.
- Regenerate: `KEYSTONE_CORPUS=src/context_curator/eval/fixtures/powered uv run python -m context_curator.eval.keystone`
  (bge floats are machine-sensitive; expect small drift, regenerate the matrix rather than diffing).

## What M4c did NOT do
No production/runtime code changed — `git diff main` on `policy/weights.py` and `curator/config.py`
is empty. The `CC_CURATOR_ONLOAD` flag stays at its dark default. The production flip + any threshold
edit are **M4d**, gated on a real-data keystone (mined transcripts), per design §6/§9.
