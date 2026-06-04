# Powered corpus — build notes (M4c)

Reproducibility record for `fixtures/powered/`. See the verdict of record:
`docs/superpowers/keystone-powered.md`; generation recipe:
`docs/superpowers/specs/2026-06-03-corpus-generation-protocol.md`.

## Provenance
- **40** raw LLM-generated fixtures (batches p1–p5), each honoring the generation protocol
  (12–20 chunks, ≥3 paraphrased gold, ≥2 lexically-tempting `hard_neg`, first-gold recency-third
  targeted). Raw pre-judge corpus: `_raw_pregenerated/`.
- **Blind gold-judge** (no gold/negative labels, tags stripped, chunks key-sorted to remove recency
  leakage): dropped **5** fixtures with a non-relevant planted gold →
  `api-key-scoping-p5`, `circuit-breaker-tuning-p4`, `feature-flag-percentage-rollout-p4`,
  `json-schema-validation-p5`, `locale-fallback-p5`. Verdicts: `_raw_pregenerated/_judge/`.
- **35** kept fixtures → this directory, split **9 train / 26 test** (≈3 train per recency third).
- Hard-negative false-positive rate: **0/80 (0%)**. Circularity guard (drop-rate by BM25-recall
  tercile): high **0.136** vs low **0.071** (no rigging).

## Power sizing
- Pre-registered MEI = **+0.10 nDCG**; two-sided 90% CI rule.
- Pilot (n_test=8, grid-swept) gave per-fixture-delta sd ≈ 0.17 → 90%-power target **n_test ≈ 25**.
- Achieved **n_test = 26** (powered). Final observed sd = 0.150.

## Audit
`eval/corpus_audit.py` → `ok=True` ("fair"); gold-position thirds (oldest, middle, newest) =
**(12, 12, 11)**; every fixture ≥3 gold and ≥2 hard negatives.

## Headline result
semantic 0.880 vs BM25 0.811 vs recency 0.428; delta **+0.056**, 90% CI **[0.009, 0.102]** →
**NEGATIVE (powered)** (effect < MEI). No production flip (M4d).
