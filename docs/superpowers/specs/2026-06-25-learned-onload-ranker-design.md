# Design — Learned re-onload ranker (track B: the bitter-lesson test)

**Date:** 2026-06-25 · **Status:** approved design (passed 3 adversarial rounds — see Design Critique Log)
**Owner:** eval/policy · **Context:**
[`docs/retrospective-semantic-relevance.md`](../../retrospective-semantic-relevance.md) §6 track B ·
[`docs/decisions/semantic-ranker.md`](../../decisions/semantic-ranker.md) ·
[`keystone-real.md`](../keystone-real.md).

## 1. Problem & goal
bge semantic onload is settled NEGATIVE — BM25 wins. The retrospective's bitter-lesson reading: we
tested a **frozen human prior** (cosine of an off-the-shelf embedding), never a method that **learns the
re-onload signal from the real-session data we already harvest**. Track B asks: does a **learned ranker
over cheap features** beat BM25 by **+0.10 nDCG@10** on held-out real sessions?

**The round-3 restructure (R3 holistic verdict):** ~70% of a full learned-ranker build only pays off on a
GREEN that this design rates *unlikely*. So track B is split into two cycles, and **only Cycle 1 is
in scope now**:

| Cycle | Scope | Pays off |
|---|---|---|
| **1 — Feasibility (THIS spec's deliverable)** | eval-only: does a learned Tier-1 logistic move vs BM25 on real fixtures, and is the question powerable? | always — a cheap go/no-go answer |
| **2 — Full build + wiring (GATED, sketched in §4)** | `LearnedTarget`, parity-safe serve featurizer, artifact, ≥8-session powered keystone, production wiring | only if Cycle 1 says go |

**Definition of done (Cycle 1):** a committed feasibility finding — effect sign + rough magnitude of
`learned − BM25` on the staged 5-session corpus (via leave-one-session-out), an order-of-magnitude
needed-N, and the circularity audit — with a clear go/no-go on harvesting dozens of sessions for Cycle 2.

**Non-goals:** beating bge (done); deep/neural models; ANY production change, `LearnedTarget`, artifact,
serve featurizer, or schema migration **this cycle**; moving goalposts.

## 2. Why eval-only Cycle 1 dissolves the hardest problems
The round-2/3 parity bugs (recency direction, TTL set-mismatch, tool-tag casing, `norm_stats` replay) are
all **train-*serve* skew** problems. Cycle 1 never touches serve — it trains and evaluates entirely on
harvested fixtures — so **parity is not in scope for Cycle 1**. The featurizer reads the fixture's own
candidate set with one consistent ordering; there is no second pipeline to diverge from. Those problems
move to Cycle 2 (§4), on the far side of the gate, where they're only worth solving if Cycle 1 says go.

## 3. Cycle 1 — feasibility (the deliverable)

### 3.1 Features (Tier-1, eval-only)
Four cheap features per candidate, computed from the harvested fixture:
| Feature | Definition |
|---|---|
| `bm25_score` | BM25(prompt, content), in-set, normalized |
| `recency_rank` | normalized position in the fixture's candidate list (one consistent orientation) |
| `chunk_log_len` | `log1p(len(content))` |
| `tool_type` | one-hot of producing tool, **canonicalized `.lower()`** (so it's Cycle-2-serve-ready) |

`tool_type` is the orthogonal signal that makes Tier-1 more than a reweighting of BM25+recency (R2/M1).
It requires a small **eval-side** change: add `FixtureChunk.producing_tool` and populate it in
`harvest_trace` from the producing `ToolCall.name` (already in scope at `real_corpus.py:89`, currently
discarded), then re-harvest. No store/serve change (that's Cycle 2). **Excluded as circular** (R1/C3):
`prior_refetch_count`, `same_dir_recent` — used only in the §3.3 audit. **Cut** (R1/m7):
`gold_rate_prior_by_tool` (collinear with the one-hot).

### 3.2 Power dry-run — real model, not a proxy (R2/M4, R3/M3,M4)
Fit the **actual Tier-1 logistic** on the staged 5-session corpus via **leave-one-session-out**: for each
session, fit on the other 4 (with feature `norm_stats` and any L2 fixed a-priori — 4 sessions is too few
to tune, R2/M5), score the held-out session, collect its per-fixture `learned − BM25` deltas; pool all 5
held-out sessions' deltas, clustered by session, into `cluster_bootstrap_ci`. Report:
- **(a) effect sign + rough magnitude** of mean `learned − BM25` — the primary signal.
- **(b) order-of-magnitude needed-N** via `precision_gate`'s `needed_n = ceil(n·(width/MEI)²)`, with the
  variance **bootstrapped to a range**, explicitly labeled order-of-magnitude (the metric is near-binary
  at ~2 gold/fixture, R2/m3; 5 clusters give a noisy width, R3/M3).

**Decision rule (R3/M3,M4): build/no-build keys off (a) the effect sign + the harvestability of ~20–40
sessions, NOT a precise needed-N.** needed-N is a sanity check on order of magnitude, never a hard gate.
- learned clearly *moves* vs BM25 (right sign, non-trivial magnitude) AND ~20–40 sessions plausibly
  resolve it → **GO to Cycle 2**.
- learned ties/loses BM25 on 5 sessions → strong NEGATIVE signal → **ship BM25, stop** (bitter lesson
  twice). - needed-N is astronomically large → record "impractical", stop.

### 3.3 Circularity / stickiness audit (R1/C3)
Score the held-out fixtures by `prior_refetch_count` alone and `same_dir_recent` alone (locality window
`W_loc ≠ W`). If either solo ranker lands within MEI of the learned model, those features are circular
proxies for the refetch-defined gold and are barred from any Cycle-2 production model. Pre-registered.

### 3.4 Components (Cycle 1 only — deliberately small)
```
src/context_curator/eval/learned/
  features.py   # featurize(fixture) -> (X, y, FEATURE_NAMES); tool one-hot lowercased; z-score norm (train-only stats)
  feasibility.py  # LOSO fit (logistic, fixed L2), learned-BM25 deltas, cluster_bootstrap_ci,
                  # precision_gate needed-N range, circularity audit  -> prints + writes the report
src/context_curator/eval/fixtures.py   # + FixtureChunk.producing_tool (optional field)
src/context_curator/eval/real_corpus.py # harvest_trace populates producing_tool
docs/superpowers/learned-feasibility-prereg.md   # pre-registration — commit BEFORE fitting
docs/superpowers/keystone-learned.md             # the feasibility verdict-of-record
```
No `LearnedTarget`, no `model.py` artifact, no `keystone_learned` runner, no `ranker_weights.json`, no
production wiring, no sklearn-pinned shipped model. Reuse `bm25_scores`, `ndcg_at_k`,
`cluster_bootstrap_ci`, `precision_gate`, `lexical_bias` verbatim; the deployable baseline is computed
net-new (do **not** copy `keystone.py:81`'s per-fixture oracle `max`, R2/M2,R3/M2).

### 3.5 Baseline (R2/M2, R3/M2)
Headline = `learned − BM25`. BM25 is the strongest *single deployable* baseline (it beat recency in M4d),
so it is the right comparator; recency is reported for context. The non-deployable per-fixture
`max(BM25,recency)` oracle is **not** used (a NEGATIVE vs an oracle is uninterpretable).

### 3.6 Pre-registration (commit BEFORE fitting)
MEI +0.10 · 90% session-clustered CI · seed 0 · L2 fixed a-priori (record the value) · feature list +
`.lower()` tool canon + z-score norm (train-fold stats only) · **the 5 session ids pinned by sha**
(R3 reproducibility) · `harvest_root` pinned and recorded (R2/m4: `extract_entities` uses
`os.path.abspath`, so gold labels are CWD-dependent) · circularity + lexical-bias + recency audits ·
decision rule per §3.2 · stopping rule: learned ⊀ BM25 → ship BM25, retire the question.

### 3.7 Testing (TDD)
- `features.py`: known fixture → known matrix; tool one-hot lowercases; z-score uses train-fold stats only.
- `feasibility.py`: LOSO touches each session as test exactly once; no session in both folds of a rotation;
  end-to-end on the committed `_traces` samples emits a well-formed report (harness-works).
- Reuse existing `precision_gate`/`cluster_bootstrap_ci`/`metrics` tests unchanged.

### 3.8 Dependencies
`scikit-learn>=1.4` in an optional/dev group (`[dependency-groups] learn`), `random_state` pinned.
**Zero production runtime deps** (Cycle 1 ships nothing to production).

## 4. Cycle 2 — full build (GATED on Cycle 1; sketch only, not in scope)
Built **only if Cycle 1 says GO**. Documented here so the gate has a target, not to be implemented now.
- **Harvest dozens of sessions**; hold out **≥8 whole test sessions**; select L2 by **session-level
  K-fold CV within train** (R2/M5). Runner is **new** (`keystone_learned`), null-embedder store with
  `ttl_s` set (R3/m3), clustered CI, arms [learned, BM25, recency], deployable + oracle baselines.
- **Serve parity (the hard part, R2/C1,C2 + R3/C1):** a shared `Candidate` builder enforcing one
  ordering + content truncation + a **turn-based** candidate window. The eval/serve bound is
  **approximate, not identical** — fixtures have no wall-clock, only a turn index (which must be added to
  `FixtureChunk` and threaded through `schema.ToolResult`/`parse_transcript`); serve's `CAPTURE_TTL_S` is
  seconds. Document the residual gap honestly; a **multi-chunk** parity test asserts the feature *matrix*
  (incl. recency order), not a single row.
- **`tool_type` serve parity (R3/M1):** pin a `tool_vocab` in the artifact; lowercase both sides; unseen
  tool → defined fallback (not silent all-zeros).
- **`LearnedTarget` (R1/M1):** `decide` returns `Decision.candidates` = the **full pool pre-sorted by
  learned score** (the runner does not re-sort, `runner.py:45`); the test asserts *order* (R3/m4).
- **Artifact:** `{model, feature_names, tool_vocab, coefs, intercept, norm_stats (train-only z-score),
  ttl_window, harvest_root, trained_on, sha}`. **GBDT** is a train-CV-only diagnostic, no verdict, no
  action (R1/M3).
- **Verdict→action:** GREEN (≥MEI vs deployable baseline, CI>0, no circular feature load-bearing) → wire
  on, ship artifact, amend DESIGN §1/§10; else dark/revert + ship BM25. Add a zero-variance/`needed_n=None`
  degenerate branch (R3/m2).
- **Deferred Tier-2:** `path_overlap`/entities = `Chunk` schema field + sqlite migration + capture-time
  `extract_entities` + backfill (R2/M3) — a later cycle, not Cycle 2's first cut.

## 5. Risks & mitigations (Cycle 1)
| Risk | Mitigation |
|---|---|
| Underpowered feasibility read on 5 sessions (R3/M3,M4) | decision keys off effect **sign** + harvestability, not a precise needed-N; needed-N reported as a range |
| Gold-as-refetch circularity (R1/C3) | §3.3 audit bars circular features from Cycle 2 |
| `tool_type` not in fixtures (R1/C1) | small eval-side `FixtureChunk.producing_tool` + re-harvest |
| Oracle-baseline bias (R2/M2) | headline is `learned − BM25` (deployable), not the per-fixture max |
| Copy-paste the oracle from `keystone.py:81` (R3/M2) | baseline computed net-new; explicitly do not reuse that line |
| Label reproducibility (R2/m4, R3) | pin + record `harvest_root` and the 5 session shas in the prereg |
| Scope creep back into the full build (R3 holistic) | §4 is a gated sketch; Cycle 1 ships nothing to production |

## 6. Out of scope (this cycle)
Everything in §4 (the full build, serve parity, `LearnedTarget`, artifact, production wiring, ≥8-session
keystone); Tier-2 entities/migration; embedding arms (track C); bge `curator/` demote-vs-delete; track-A
BM25-onload wiring (separate; the fallback if Cycle 1 says stop); DESIGN §1/§10 amendment.

---

## Design Critique Log

### Critique Round 1
Independent agent vs the design + `real_corpus.py`, `keystone.py`, `fixtures.py`, `target.py`,
`runner.py`, `select.py`, `user_prompt_submit.py`, store models. **C1 (Critical)** 6/9 features absent
from the harvested `Fixture` → tiered features + eval-side fields. **C2 (Critical)** absent from the live
`Chunk` too → reframed/parity-tested. **C3 (Critical)** gold is refetch-defined; refetch-features fake a
win → circularity audit; those features excluded. **M1/M2** `LearnedTarget` full ranked candidates;
`keystone_learned` is a new runner, clustered CI, null embedder. **M3** GBDT no-verdict. **M4** added the
Phase-0 power gate. **M5** 3-way split. Minors: cut collinear prior, `W_loc`, determinism, small-pool
nDCG, dropped model card.

### Critique Round 2
Fresh agent + the capture path. **C1 (Critical)** `recency_rank` inverted (fixtures oldest-first vs
`all_live_chunks` newest-first); single-chunk parity test blind to set-relative features → shared
`Candidate` builder + multi-chunk parity test. **C2 (Critical)** TTL makes live set ≠ eval set → window
the eval set. **M1** Tier-1 can't beat the baseline → promoted captured `tool_type`. **M2**
`max(BM25,recency)` is an oracle → deployable baseline for the verdict. **M3** `tool_type` *is* captured
(corrected); `path_overlap` entities = migration → deferred. **M4** wrong variance proxy → fit the real
Tier-1 model. **M5** tiny val fold → session-CV. Minors: post-truncation length, floor
necessary-not-sufficient, near-binary metric, pin harvest root.

### Critique Round 3
Fresh agent; holistic readiness judgment. **C1 (Critical)** the TTL-window-on-eval fix is *uncomputable*
— fixtures carry no timestamp and no per-chunk turn; `ToolResult` drops time (`schema.py`) — so the R2/C2
parity claim was still open. **M1** `tool_type` eval value won't match serve tag without `.lower()` + a
pinned `tool_vocab` (unseen→all-zeros skew). **M2** the deployable baseline is net-new; `keystone.py:81`
is the wrong oracle template (copy-paste trap). **M3/M4** Phase-0 needed-N from 5 sessions is
order-of-magnitude uncertain and the split arithmetic was inconsistent (4-test/1-train under the default)
— a hard gate on it is not a gate. Minors: `harvest_corpus` random split, `needed_n=None` degenerate
branch, store `ttl_s` reuse, `LearnedTarget` ordering contract, undefined `norm_stats`/`W_ttl`, pin
session shas. **Holistic verdict: NOT READY as a single full build → split into two cycles.**
**Resolution:** adopted the restructure wholesale — **Cycle 1 = eval-only feasibility** (this spec's
deliverable), which *dissolves* the train-serve parity criticals (C1/M1) by never touching serve; uses
**LOSO on the 5 pinned sessions**, an effect-**sign** decision rule (needed-N only as an order-of-mag
sanity check), a net-new deployable `learned − BM25` baseline, and an a-priori L2. **Cycle 2 = the full
build**, gated on Cycle 1, with the parity/`tool_vocab`/`LearnedTarget`/artifact work sketched in §4 to be
done only once a measured signal justifies it.
