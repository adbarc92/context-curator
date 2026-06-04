# M4c — Powered Corpus + bge Threshold Verdict (production flip deferred to M4d) — Design

**Status:** Hardened through 3 critique rounds (log below)
**Parent design:** `DESIGN.md` §10 (eval keystone / §10.8 differentiator), §4.2 (latency). Builds on M3b's eval harness (`src/context_curator/eval/*`, on `main`) and M4b-1's curator + `ONLOAD_BGE_*` constants + `CC_CURATOR_ONLOAD` flag.
**Milestone:** M4c (the keystone-validation milestone). Turns the live semantic differentiator ON **iff** a powered eval earns it.
**Stack:** Python + UV. bge-small via the optional `embed` extra (`FastEmbedEmbedder`, dim 384); `HashingEmbedder`/`KeywordEmbedder` for deterministic baselines + tests.

---

## 1. Purpose

M4b-1 shipped the curator **dark**: live onload is semantic-*capable* but gated OFF behind `CC_CURATOR_ONLOAD`, because the gate threshold (`ONLOAD_BGE_COSINE_THRESHOLD = 0.55`) was an unmeasured placeholder and the eval corpus (n_test=4) was underpowered. M4c builds a **powered** (n set by a §6 power estimate for a pre-registered minimum effect — NOT a folk n=30), **fair** synthetic eval corpus, adds a **BM25 strong baseline** + threshold sweep, runs a 3-arm keystone (recency, BM25, bge), and **records an honest synthetic verdict**: if bge beats the *stronger* of {recency, BM25} it sets the tuned threshold constant. **M4c does NOT flip the live `CC_CURATOR_ONLOAD` default** (round-2 C8) — a synthetic verdict cannot license a production change for real users when this spec itself defers real-context validity (§9). The **production flip is a separate milestone, M4d, gated on a real-data keystone**. M4c's deliverable is the powered harness + the honest synthetic finding; the flip is earned later by *flip-relevant* (real) data.

**Honesty boundaries (locked — TWO, after round-1 critique):**
1. **Synthetic vs real:** the corpus is **LLM-generated planted-synthetic** (user decision; not blended with mined-real). External validity to real captured-context distributions (transcript mining via `parse_transcript`) is an explicit follow-up.
2. **Fair vs rigged (round-1 C1/C2 — the deeper boundary):** the original design used a *bge-blind validity gate* that admitted a fixture only if recency AND lexical baselines FAILED. Round-1 critique proved this is **circular**: selecting "gold is lexically-disjoint AND positionally non-recent" selects *exactly* bge's operating regime, so "does bge win?" is pre-ordained and recency loses by construction (forcing arm-2 nDCG ≤ τ arithmetically forces gold to recency-rank ≥ 3, i.e. always old). **That gate is REMOVED.** M4c instead earns the verdict on a **fair** corpus where neither recency nor lexical methods are crippled, and requires bge to beat the **strongest** cheap baseline — **BM25** — not just recency. A fair corpus where a strong lexical IR method survives and bge still beats it is a real signal; a rigged corpus where the only surviving method is bge is not.

## 2. Scope & decisions

**In scope (M4c):**
- A **powered, FAIR synthetic corpus** `fixtures/powered/` (n ≥ the power-derived target, §6; not a cargo-culted 30) in the existing `Fixture` JSON schema, generated under a committed protocol with **genuinely mixed gold-recency** and **hard negatives**.
- A **fairness/non-triviality check** (`eval/corpus_audit.py`) — NOT a per-fixture admission gate: it characterizes the corpus (recency-nDCG spread, hard-negative presence) and FAILS the corpus only if it's degenerate (recency trivially wins/loses everywhere). It does not select fixtures to make any method win.
- A **BM25 baseline** (`eval/bm25.py` + a `Bm25Target`) — the strong lexical comparator bge must beat.
- An **independent gold judge** (`eval/gold_judge.py`) — drops fixtures whose planted gold a blind judge disagrees with (reduces generator self-bias).
- A **threshold sweep** (`eval/threshold_sweep.py`) choosing `ONLOAD_BGE_COSINE_THRESHOLD` by the **max-threshold-subject-to-recall-floor** rule (not F1-argmax), with per-cell CIs.
- A **powered keystone run** comparing **bge vs the strongest of {recency, BM25}** → CI-backed verdict + a **frozen per-fixture cosine matrix** for reproducibility, recorded in `docs/.../keystone-powered.md`.
- An **honest synthetic verdict** (GREEN / powered-NEGATIVE / underpowered-INCONCLUSIVE) recorded in the verdict doc. If GREEN, the verdict doc **records the tuned threshold value** for M4d to apply.

**Out of scope (deferred to M4d — round-2 C8 / round-3 I1):**
- **The production flag flip** (`CC_CURATOR_ONLOAD` default `0→1`) and the **`ONLOAD_BGE_COSINE_THRESHOLD` constant edit**. M4c touches NEITHER `curator/config.py` NOR `policy/weights.py`: the flag gates the whole semantic path before the threshold is read (round-3 C3 — the constant edit would be a runtime no-op while dark), and a synthetic verdict cannot license a production change (round-2 C8). M4d flips the flag AND the constant together, gated on a **real-data** keystone (mined transcripts run through the production-capped `handle_onload` path).

**Out of scope (deferred):**
- **eviction-regret** metric — that's offload/M5, not onload tuning.
- **Mined-real corpus / external validity** — the follow-up; this milestone is generated-only.
- Any curator/runtime code change beyond the threshold constant + the flag default (the machinery already consumes both).
- packed-BLOB, dedup (M4b's other sub-projects).

**Locked decisions (brainstorming + round-1 critique):**
1. **Conditional flip earned by a powered keystone**, not assumed (the project's honesty discipline).
2. **No bge-flattering selection (round-1 C1/C2).** There is NO per-fixture admission gate that selects on baseline failure. The corpus is FAIR (mixed gold-recency, hard negatives); validity is enforced by (a) a corpus-level *audit* that only rejects a degenerate corpus, and (b) requiring bge to beat **BM25**, a strong lexical baseline the design does not cripple. bge must have a genuine opportunity to lose.
3. **bge vs the STRONGEST cheap baseline** (max of recency + BM25), not vs recency alone — recency alone is a weak comparator and any recency-targeting would be circular.
4. **Generated-only** corpus; external validity explicitly deferred.

## 3. Fairness, not rigging (centerpiece — rebuilt after round-1 C1/C2)

The original "validity gate" (admit a fixture only if recency+lexical fail) is **removed**: round-1 proved it selects bge's exact operating regime (lexically-disjoint + non-recent gold) and forces recency to lose by construction, making the verdict circular. Instead M4c earns the verdict on a **fair** corpus and a **strong** comparator.

**Three mechanisms replace the gate:**

1. **A FAIR corpus (generation constraints, §4).** Each fixture: **≥3 gold keys** (round-2 I5 — so recall has resolution, not a 2-valued step) that are semantically relevant with *realistic, not stripped* lexical overlap; **≥2 hard negatives** (topically-adjacent-but-irrelevant; these are what make the task non-trivial); **genuinely mixed gold-recency** — across the corpus, gold sits at *all* recency positions (newest, middle, oldest) in a roughly balanced spread (audited by the gold-position histogram, §3.2), so recency is neither a free win nor a free loss; realistic chunk types (tool outputs / file-edit summaries / decisions, the §5 capture shapes), ~12–20 chunks/fixture.

2. **A corpus AUDIT, not an admission gate (`eval/corpus_audit.py`).** It *characterizes* the assembled corpus and FAILS only a **degenerate** corpus — it does NOT drop fixtures to make a method win. **Round-2 I2 fix — no magic nDCG band:** the audit checks the **gold-recency-position histogram directly** (each fixture's gold chronological rank), requiring coverage across newest/middle/oldest thirds (a stated target distribution, e.g. roughly uniform), NOT a reverse-engineered recency-nDCG band (which was a re-introduced magic constant that biased gold position). It also checks: every fixture has ≥2 hard negatives whose lexical overlap with the prompt is ≥ the gold's (so lexical methods are genuinely tempted, not strawmanned). A corpus failing the audit is regenerated/rebalanced.

3. **BM25 as the strong comparator (`eval/bm25.py`, `Bm25Target`) — round-2 C3 + round-3 I4.** BM25 ranks *this fixture's* chunks for *this prompt* (the real per-prompt retrieval setting) with a **smoothed IDF** `log((M − df + 0.5)/(df + 0.5) + 1)` (df = chunk-frequency within the fixture, M = chunk count) + standard k1/b term saturation. The **add-one smoothing makes IDF well-defined over the ~12–20-chunk fixture** (round-2 worried per-fixture IDF is noisy; round-3 noted whole-corpus IDF imports cross-task contamination — smoothed task-local IDF avoids BOTH horns and matches how a per-conversation retriever actually works). This is a *genuinely strong* lexical baseline, not a strawman. The keystone reports **BM25's own recall on gold per fixture**: if BM25 is near-floor across the corpus, "bge beats BM25" is *vacuous* and the verdict says so. The differentiator is validated only if bge beats the **stronger** of {recency, BM25} (the §6 rule); **bge beating only recency is NOT sufficient**; bge ≈ BM25 → WASH.

This is the round-1 C1 + round-2 C3 fix: bge competes against a method that can genuinely win, on a corpus not built to eliminate its competitors, with BM25's strength verified by its own recall.

## 4. Generation & validation pipeline

`fixtures/powered/*.json`, `Fixture` schema (`name, chunks[], prompt, recent_tools[], gold_keys[], split`). Target n_test = the power-derived target (§6), ~2:1 train:test.

- **Generation** (subagent-driven during the build, NOT CI): batches produced by Claude subagents following a **committed protocol** (`docs/.../2026-06-03-corpus-generation-protocol.md`) encoding the §3 fairness constraints + worked examples. The protocol is the reproducibility artifact; the *output* (JSON fixtures) is committed frozen data.
- **Independent gold judge** (`eval/gold_judge.py`; subagent pass during build, blind to the planted gold) — **round-2 C1: the judge is a transformer-semantic model in bge's family, so it can re-introduce the circularity by systematically dropping BM25-favorable (lexically-obvious, semantically-near-miss) fixtures.** Two safeguards make it method-neutral:
  1. **The judge ONLY rejects fixtures where the planted gold is *flat-out wrong* / genuinely irrelevant** — a **yes/no** "is this chunk a correct answer to the prompt?" per gold key, with a written rationale. It does **NOT** adjudicate gold-vs-hard-negative *ranking* (that adjudication is the bge-favoring step). A fixture is dropped only if the judge says a planted gold is not actually relevant, never because "a paraphrase is slightly closer."
  2. **The circularity is detected WITHOUT a judge ranking (round-3 C1 — a yes/no judge can't emit a ranking, and eliciting one would re-introduce the bge-favoring step).** Instead of a Kendall-τ over judge rankings, compute the **judge drop-rate conditioned on each fixture's BM25-recall tercile** (BM25 recall is already computed per fixture, §3.3/§5). If the judge drops **disproportionately many high-BM25-recall fixtures** (the lexically-obvious-but-maybe-semantic-near-miss ones BM25 wins), the survivor set is being stripped of BM25-favorable cases → bge-aligned → the keystone verdict is **RIGGED / not interpretable** (§6). This uses only the yes/no decisions the judge actually makes — no ranking, no contradiction — and is a sharper circularity signal. It gates the verdict's validity in `keystone-powered.md`.
  A deterministic stub judge backs the unit tests. **Round-2 I6 — auditability of the *decision*, not just the arithmetic:** commit the **raw pre-judge corpus**, every dropped fixture with the judge's rationale, and BM25's per-fixture recall — so a reviewer can inspect the survival filtering, not just re-add the winners' numbers.
- **Corpus audit** (`eval/corpus_audit.py`, §3.2): run AFTER assembly over the whole corpus; FAILS a degenerate corpus (recency trivially wins/loses; missing hard negatives) → rebalance/regenerate. It drops nothing to favor a method.
- **Bounded loop (round-1 I3):** generate → judge → audit, with a **hard generation budget** `MAX_GEN_BATCHES`. A `results/corpus-build.md` records target-vs-achieved n + judge-rejection rate. **Fallback ladder:** power-target n (favored) → a smaller n with explicitly-wider-CI honest under-power framing → if even that is unreachable within budget, ship the machinery + whatever corpus passed, keep the flag **dark**, and document. Never pad to hit n with trivial fixtures.

## 5. Eval extensions

- **BM25 baseline** (`eval/bm25.py` + `Bm25Target` in `replay/target.py` style): a standard BM25 ranker with **per-fixture smoothed IDF** (§3.3 — `log((M−df+0.5)/(df+0.5)+1)`, k1/b defaults), exposed as a `ReplayTarget` so the keystone scores it as a third arm. Pure-Python, deterministic, CI-testable. `test_bm25.py` pins the fixture df explicitly so "rare/common" is unambiguous (round-3 M4).
- **Threshold sweep** (`eval/threshold_sweep.py`): `sweep_threshold(train, embedder, grid, k, recall_floor) -> ThresholdSweepResult`. Gated precision is **monotone-increasing in the threshold** (round-1 C3), so F1-argmax is degenerate/noisy. Instead the criterion is **the highest threshold whose recall lower-CI stays ≥ `recall_floor`**. **Round-2 I5 fixes:** (a) `recall_floor` is set from a **stated product requirement** ("tolerate dropping at most X% of relevant context"), not a default knob — its value + rationale are recorded. (b) Recall is **micro-averaged** (pooled gold-hits / total-gold across fixtures), NOT a macro-average of the 2–3-valued per-fixture recall — and fixtures plant **≥3 gold keys** (§3.1) so recall has real resolution. (c) The recall **denominator is stated explicitly** (gold recovered in the top-k *after* the cosine gate — the quantity a production gate actually controls). Each grid cell carries a bootstrap CI; the curve ships with error bars (mirroring `sweep.py`'s "neighbors not distinguishable" honesty). The sweep runs the **fully-embedded** path; this differs from the production `reembed_cap=0`/`ONDEMAND_EMBED_CAP=12` path — which is **fine for M4c because M4c does not flip production** (§6); a real-data, production-capped sweep is M4d's job.

- **Power estimate (round-2 I7 — pre-registered, not pipeline-chosen):** the corpus n is sized to detect a **pre-registered minimum effect of practical interest** (e.g. +0.10 nDCG, fixed *before* the run), at ~90% probability the 90% CI excludes 0 — NOT sized to the effect the pilot happens to produce (which would be circular: a biased pipeline inflates the effect, the power calc says "small n suffices," the n confirms the inflated effect). The pilot is used only to estimate **variance**, and the bge-vs-baseline delta is reported **pre-judge AND post-judge** so any judge-inflation (round-2 C1) is visible. Capped by the §4 generation budget.
- **Powered keystone**: `run_keystone(fixtures/powered, FastEmbedEmbedder())` extended to score **three arms** (recency, BM25, bge) → bge-vs-(strongest baseline) nDCG delta + 90% bootstrap CI. **Reproducibility (round-1 I4):** the run also writes a **frozen per-fixture bge cosine matrix** (`fixtures/powered/_bge_cosines.json` + a content hash) so a reviewer can re-derive the nDCG/CI **without** the (machine-sensitive, non-reproducible) bge model. The verdict of record + the frozen matrix hash + fastembed/onnxruntime versions go to `docs/.../keystone-powered.md` (committed; §7).

## 6. The synthetic verdict — and why M4c does NOT flip production (round-2 C8)

**M4c does not flip the live `CC_CURATOR_ONLOAD` default.** Round-2 critique established the decisive point: a GREEN verdict on a *synthetic* corpus does NOT answer DESIGN §10.8 ("does relevance beat recency on **real** captured context") — and this spec's own §1/§9 honesty boundary explicitly defers real-context validity to a mining follow-up. Flipping the **production** default for real users on synthetic-only evidence would contradict that boundary. Worse, the eval runs the **fully-embedded** path while production runs `reembed_cap=0` + `ONDEMAND_EMBED_CAP=12` (handler.py) — the tuned threshold would be derived in a regime production never executes, and the ≤20-chunk fixtures never even exercise the 12-cap.

**So M4c's deliverable is the powered synthetic HARNESS + an honest synthetic verdict, not a production flip:**

The keystone reports bge vs **the stronger of {recency, BM25}** on **test**, with the bootstrap CI on that delta + the §5 recall-floor threshold + the audit numbers (the §4 judge-drop-rate-by-BM25-tercile circularity check, BM25's own recall, the recency-position histogram). **Decision rule (round-3 I5 — pin the CI semantics):** "favorable" = the **two-sided 90% bootstrap CI's lower bound > 0** (≡ a one-sided 95% exceedance at this α; matches the existing `keystone.py` `lo > 0`); `stats.bootstrap_ci` needs no change. **MEI** = a pre-registered minimum effect of interest (+0.10 nDCG, §5). **Four honest outcomes** (round-3 I5 distinguishes the two "not-green" cases), all recorded in `docs/superpowers/keystone-powered.md` — M4c changes **no production code** in any of them:

- **GREEN** — CI lower bound > 0 **and** the point estimate ≥ MEI **and** the circularity check passes (judge does not disproportionately drop high-BM25-recall fixtures) **and** BM25 is non-vacuous (its recall isn't near-floor): the synthetic differentiator holds. **Record the tuned `ONLOAD_BGE_COSINE_THRESHOLD` value in the verdict doc** as "M4d should set this when flipping." **Do NOT edit `weights.py` (runtime no-op while dark — round-3 C3) or `config.py`.**
- **NEGATIVE (powered)** — CI excludes 0 but |effect| < MEI, at the §5 power-target n: a **valid conclusion** = "no *practically-meaningful* semantic advantage on this corpus." Not a failure; a real finding. Keep 0.55, change nothing.
- **INCONCLUSIVE (underpowered)** — CI straddles 0 **and** achieved n < the power-target (e.g. the §4 budget hit the fallback ladder): cannot conclude. Change nothing; document.
- **RIGGED (circularity check fails)** — the judge dropped disproportionately many BM25-favorable fixtures (§4): the survivor corpus is bge-aligned → the verdict is **not interpretable**; report it and treat as inconclusive.

Either way the corpus + harness + verdict ship as durable value. **The production flip is its own milestone — M4d** — gated on a *real-data* keystone (mine transcripts via `parse_transcript` → gold-label → re-run through the **production-capped** `handle_onload` path), flipping the constant + the flag together. **The flip is a function of the *flip-relevant* (real) data, which M4c does not collect.**

## 7. Architecture / file structure

```
src/context_curator/eval/
  bm25.py               # NEW: BM25 ranker + Bm25Target (the strong lexical comparator)
  corpus_audit.py       # NEW: audit_corpus (characterize; FAIL only a degenerate corpus; drops nothing)
  threshold_sweep.py    # NEW: sweep_threshold (max-threshold s.t. recall-CI >= floor; per-cell CIs)
  gold_judge.py         # NEW: judge interface + deterministic stub; real LLM judge run at build
  keystone.py           # MODIFY: score 3 arms (recency, BM25, bge); write frozen cosine matrix;
                        #         REWRITE verdict/header strings to cite the §6 power-derived n +
                        #         pre-registered +0.10 MEI, REMOVING legacy "grow corpus to n>=~30" (R3 C2)
  stats.py              # MODIFY: docstring — adequacy = the §6 power estimate, NOT "n>=~30" (R3 C2)
fixtures/powered/*.json # NEW: the generated, judged, audited FAIR corpus (committed frozen data)
fixtures/powered/_bge_cosines.json   # NEW: frozen per-fixture bge cosine matrix (reviewer repro)
fixtures/powered/_raw_pregenerated/  # NEW: pre-judge raw corpus + judge drop rationales (R2 I6 audit)
docs/superpowers/specs/2026-06-03-corpus-generation-protocol.md  # NEW: generation protocol
docs/superpowers/keystone-powered.md    # NEW: verdict of record (committed under docs/) — verdict +
                        #         tuned-threshold-value-for-M4d + _bge_cosines hash + fastembed/onnx versions
tests/eval/
  test_bm25.py             # NEW: BM25 (smoothed per-fixture IDF) ranks a rare-term match above a common one
  test_corpus_audit.py     # NEW: audit FAILS a recency-trivial corpus, PASSES a mixed one (pinned predicate)
  test_threshold_sweep.py  # NEW: recall-floor rule + per-cell CIs (fake embedder, deterministic)
  test_gold_judge.py       # NEW: stub judge accepts matching gold, drops flat-wrong gold
  test_powered_corpus.py   # NEW: committed corpus loads, n>=target, audit PASSES, >=2 hard negs + >=3 gold each
```

**M4c edits NO production code (round-3 C3/I1):** it does NOT touch `policy/weights.py`, `curator/config.py`, `test_onload_weights.py`, or `test_curator_bge.py` — the threshold-constant edit is a runtime no-op while the flag is dark, and the flag flip is M4d. The tuned value (if GREEN) is recorded in the verdict doc for M4d to apply. The verdict of record lives at `docs/superpowers/keystone-powered.md` (committed under `docs/`, avoiding a fragile `results/` `.gitignore` negation).

## 8. Testing

Deterministic (no bge in CI):
- **bm25** — BM25 ranks a chunk matching a rare high-IDF prompt term above one matching only a common low-IDF term (proves it's stronger than bag-of-words hashing).
- **corpus_audit** — a recency-trivial corpus (gold always newest) FAILS the audit; a mixed-recency + hard-negative corpus PASSES; a corpus with a fixture lacking ≥2 hard negatives FAILS. The audit drops nothing — it returns pass/fail + stats.
- **threshold_sweep** — with a fake embedder giving known cosines, the sweep applies the **recall-floor** rule (highest threshold with recall-CI ≥ floor), attaches per-cell CIs, and the test-set eval uses the train-chosen threshold (selection on train only).
- **gold_judge** (stub) — accepts a fixture whose planted gold is the judge's top pick; drops one where a hard negative outranks gold.
- **powered_corpus** — the committed `fixtures/powered/` loads under the `Fixture` schema, has n ≥ the recorded target, **passes `audit_corpus`**, and every fixture has ≥2 hard negatives (the corpus is self-certifying for *fairness*, not for any method winning). Runs in CI **without bge**.
- **bge keystone proxy** — the existing embed-gated smoke test, pointed at `fixtures/powered/`, scores all 3 arms; skips without the extra.
- **flag-on regression — deferred to M4d** (round-1 M1 + round-3 I1): M4c does NOT flip the flag, so the curator flag-on test is M4d's. When M4d adds it, it must assert a **behavioral contract** (the gate EXCLUDES a hand-authored known-irrelevant chunk *outside* the corpus; the handler returns keys in score order) — NOT "gold from the corpus is selected" (self-fulfilling against the corpus that justified the flip).
- **No regression** — M3b eval + curator suites stay green; M4c touches no production code so nothing in the live path changes.

## 9. How this connects forward

- **External validity** — mine real Claude Code transcripts (`replay/capture/transcript.py`) into gold-labeled fixtures; re-run the keystone on real data to confirm the synthetic verdict transfers. The remaining honesty gap this milestone names.
- **eviction-regret + offload** — M5 (subagent offload loop), where the write-half of curation is evaluated.
- **Continuous tuning** — once flipped (M4d), the threshold/weights become things M-future re-tunes as the corpus grows.

---

## Design Critique Log

Three independent adversarial rounds (fresh opus subagent each, each seeing the prior round's revision). This was the most consequential gate yet: each round found a *methodological circularity* the prior design hid, and the milestone's scope contracted twice as a result — the honest M4c is much smaller and does NOT flip production.

### Critique Round 1

**The original design was fundamentally circular (C1).** The "validity gate" admitted a fixture only if recency AND lexical baselines FAILED — but that selects *exactly* bge's operating regime (gold lexically-disjoint + positionally non-recent), so "does bge win?" was pre-ordained, not tested. **C2:** the arithmetic of requiring recency-nDCG ≤ τ forces gold to recency-rank ≥ 3 (always old), making arm-2 lose by construction and contradicting the "mixed recency" claim. **C3:** the gated-precision threshold sweep is degenerate (precision monotone in τ → F1-argmax is noise), and the "held-out test" was contaminated because the gate touched every fixture. **I1:** "n≥30" is a cargo-culted t-test rule that doesn't guarantee a bootstrap CI excludes 0. **I2–I4/M1–M3:** GATE_NDCG_MAX=0.5 was a new magic constant; unbounded generation loop; non-reproducible bge verdict; self-fulfilling regression test.
**Resolution:** removed the bge-blind admission gate entirely; rebuilt around a **FAIR corpus** (genuinely mixed gold-recency, hard negatives) audited (not gated); added **BM25** as a strong comparator bge must beat; recall-floor sweep with CIs; power-derived n; bounded generation + fallback ladder; frozen cosine matrix.

### Critique Round 2

**The circularity was relocated, not removed (C1).** The gold-judge is itself a transformer-semantic model in bge's family → it systematically drops BM25-favorable (lexically-obvious, semantically-near-miss) fixtures, re-enriching the survivor set for bge's regime; "blind to the label" ≠ "method-neutral." **C3:** BM25 over ~8–20 docs has noise-degenerate IDF (a strawman), and its corpus (per-fixture vs whole) was undefined. **C8 (the deepest):** even a perfect *synthetic* GREEN does NOT answer §10.8 ("does relevance beat recency on **real** captured context") — and the spec's own §9 defers real validity — so flipping the **production** default on synthetic evidence is indefensible; worse, the eval runs the fully-embedded path while production runs `reembed_cap=0`/`ONDEMAND_EMBED_CAP=12`. **I2/I5/I6/I7:** the recency-nDCG audit band was the killed magic-constant reborn; recall_floor undefined + 2-valued; frozen matrix audits arithmetic not the decision; pilot-power is circular.
**Resolution (scope contracted):** **M4c no longer flips production** — it builds the harness, records an honest synthetic verdict, and the production flip becomes its own milestone **M4d** gated on a real-data keystone run through the production-capped path. The judge now ONLY rejects flat-wrong gold (never adjudicates ranking) + a circularity check; BM25 corpus defined; audit checks the gold-position histogram directly (no nDCG band); micro-averaged recall + ≥3 gold/fixture + product-derived floor; commit the raw pre-judge corpus + judge rationales; power for a **pre-registered** +0.10 MEI (pilot for variance only).

### Critique Round 3

Final implementation-readiness pass; caught that the section-by-section edits hadn't fully propagated. **C1:** the round-2 judge-bias measurement was self-contradictory — a yes/no judge can't emit the ranking a Kendall-τ needs, and eliciting one re-introduces the bge-favoring step → replaced with **judge-drop-rate conditioned on BM25-recall tercile** (uses only the yes/no decisions). **C2:** the existing `keystone.py`/`stats.py` still hard-code "grow corpus to n≥~30," which would make the verdict-of-record contradict its own power-derived methodology → added explicit MODIFY notes to scrub them. **C3:** the conditional `weights.py` threshold edit is a confirmed **runtime no-op** (the flag gates the path before the threshold is read) → M4c now edits **no production code**; the tuned value is recorded in the verdict doc for M4d. **I1:** the title, §2 in-scope bullet, and §7 `config.py` line still advertised the flip → retitled + moved to deferred/M4d. **I4:** §3/§5 disagreed on BM25 IDF, and whole-corpus IDF imports cross-task contamination → **per-fixture smoothed IDF** (task-local, avoids both the noise and the contamination horns). **I5:** split the not-green outcome into **powered-NEGATIVE** (effect < MEI, a valid conclusion) vs **underpowered-INCONCLUSIVE**, and pinned the CI semantics (two-sided 90% lower-bound > 0 ≡ one-sided 95%; `bootstrap_ci` unchanged). **I2/I3/M1–M4:** noted the harness-vs-run two-phase decomposition, generation-targeting + pinned audit predicate (for a deterministic committed-corpus test), recall_floor value, and the bm25 test df.
**Resolution:** all must-fixes (C1/C2/C3/I1) and the strong recommendations (I4a/I5) applied above; the two-phase build (deterministic harness extensions, then the non-deterministic generate→judge→audit→keystone run) is reflected in the plan handoff.
