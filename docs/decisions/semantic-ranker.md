# Decision — the onload ranker: BM25 over semantic (bge)

**Status:** Decided 2026-06-25. **Supersedes** the "semantic differentiator pending real-data verdict"
holding pattern. **Owner:** eval/policy.

## Context
ContextCurator's pre-registered thesis was that a **semantic (bge) relevance gate** would select
re-onload context better than cheap baselines, by a practically-meaningful margin (pre-registered
**MEI = +0.10 nDCG@10**, 90% CI). Two powered keystone evaluations now exist:

| Keystone | Corpus | Semantic vs strongest cheap baseline | Verdict |
|----------|--------|--------------------------------------|---------|
| M4c synthetic ([keystone-powered.md](../superpowers/keystone-powered.md)) | 26 fair, blind-judged fixtures | **+0.056** nDCG, 90% CI [+0.009, +0.102] | NEGATIVE-powered (CI excludes 0 but < MEI) |
| M4d real-data ([keystone-real.md](../superpowers/keystone-real.md)) | 154 fixtures, 4 test sessions, 5 projects | **−0.053** nDCG, clustered 90% CI [−0.085, −0.041] | **BASELINE WINS — powered** (CI entirely < 0) |

On real transcripts the semantic path is not merely sub-MEI — it is **beaten by plain BM25**
(arms: BM25 0.100 > recency 0.090 ≈ semantic 0.091). Both fairness diagnostics are clean
(lexical-bias non-degenerate; recency distribution healthy), so the result is a real property of the
comparison, not an artifact. Growing the synthetic corpus is non-productive (the effect shrank
pilot→powered), and the real verdict is decisive — there is no remaining lever that rescues semantic.

## Decision
1. **Do not ship the semantic/bge onload as the ranker.** It does not earn its complexity or cost
   (a warm bge process + the `[embed]` extra + write/read-time embeddings) — and on real data it is
   the *worst* arm. Keep it **optional and dark** behind `CC_CURATOR_ONLOAD` for research/repro only;
   it is no longer presented as the product differentiator. (Removing it outright is acceptable too —
   see follow-ups; demote-not-delete is the conservative default to preserve the eval harness.)
2. **Make BM25 the onload ranker.** Among the cheap baselines BM25 ≥ recency on both corpora and is
   the strongest cheap arm on real data. It needs no model, no warm process, no extra dependency.
   *(Implementation is a follow-up — see below; BM25 currently exists only in `eval/`, not the live
   onload path.)*

## What stays (validated — do not throw the baby out)
The negative is **specifically semantic-vs-BM25**. Everything else the project built is sound and keeps:
- The **store + capture hooks + re-onload loop** — the recency arm (0.090) crushes the empty/no-store
  condition; persisting state so compaction can drop it and re-injecting a relevant slice is the real,
  working value proposition.
- The **guardrails** (prod-path / secret-scan PreToolUse), the **per-project SQLite store**, the
  **plugin packaging** (M7), and the **eval harness itself** (it produced an honest, powered,
  self-correcting verdict — that is the delivered scientific value of M3b–M4d).

## The honest framing
The differentiating *thesis* (semantic relevance beats cheap ranking) did **not** survive its own
pre-registered bar. ContextCurator's shippable value is a **cheap, durable, per-project context
store with guardrails and a BM25-ranked re-onload** — not a semantic policy engine. Marketing/DESIGN
framing should follow the evidence.

## Follow-ups (not done here — new work)
- [ ] Wire `eval.bm25` / a `Bm25Target`-equivalent into the live onload path
      (`onload/select.py` + `hooks/user_prompt_submit.py`); make BM25 the default ranker over recency.
- [ ] Decide demote-vs-delete for the `curator/` semantic process + `[embed]` extra once BM25 onload ships.
- [ ] Update `DESIGN.md` §1 thesis / §10 to record the settled NEGATIVE and the BM25 decision.
- [ ] Re-confirm the decision if a much larger/different real corpus ever materially changes the picture
      (unlikely given two concordant powered runs).
