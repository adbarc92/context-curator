# Retrospective — the semantic-relevance thesis, how we tested it, and what we learned

**Date:** 2026-06-25 · **Status:** post-verdict retrospective · pairs with the decision
[`docs/decisions/semantic-ranker.md`](decisions/semantic-ranker.md) and the verdicts-of-record
[`docs/superpowers/keystone-powered.md`](superpowers/keystone-powered.md) (synthetic) +
[`docs/superpowers/keystone-real.md`](superpowers/keystone-real.md) (real).

## TL;DR
We set out to prove that a **semantic relevance gate** (bge embeddings + cosine) selects re-onload
context better than cheap baselines (recency, BM25), by a pre-registered **+0.10 nDCG@10** margin.
Two powered evaluations say the opposite: synthetic **+0.056** (half the bar), real-data **−0.053**
(semantic *loses* to BM25). The differentiator named in DESIGN §1 — "the entire differentiated
build" — did not survive its own success criterion. The substrate (durable store, capture/re-onload
hooks, guardrails) is independently validated and keeps; the ranker becomes **BM25**. The episode is
a clean instance of the **bitter lesson**, with one instructive twist (§4).

---

## 1. The thesis we set out to prove
DESIGN §1: *"What no off-the-shelf tool provides is the **policy**: a per-task decision about what to
page in… That policy layer is the entire differentiated build. Everything else… is substrate we
adopt."* The policy's heart was the **similarity term**: embed each stored chunk with
`BAAI/bge-small-en-v1.5`, embed the current subtask, score `w_s·cos(chunk, task)`, and re-onload the
top-k. The hypothesis: meaning-similarity is the right model of "what context is relevant now."

## 2. How we tested
The methodology is the part we got right, so it's worth recording in full.

- **Replay harness + downstream-use gold (M3b–M4).** We replay real/synthetic sessions turn-by-turn.
  A prior context chunk is labelled **gold** for a turn iff its file-path entity is **re-fetched**
  (Read/Grep/Glob) within a window of **W=5** turns — excluding verify-Read-after-Edit. Gold is thus
  defined by *what the session actually went back to*, not by anyone's opinion of relevance.
- **Three arms, one headline.** recency-only, BM25-lexical, semantic-policy — scored by nDCG@10 on a
  held-out **split-by-session** test set. The headline is *semantic vs the strongest cheap baseline
  per fixture* (so we never flatter semantic by averaging away a baseline's wins).
- **Pre-registration (the discipline that saved us).** Before seeing data we fixed: **MEI = +0.10**
  (minimum effect of interest), a **90% session-clustered CI**, an **n_sessions ≥ 3** floor, a
  **precision gate** (render a verdict only when CI width ≤ MEI; else "underpowered" with a
  computed needed-N), a **lexical-bias guard** (is gold secretly a BM25 proxy?), and a **recency
  audit** (is gold just "the newest"?).
- **Synthetic corpus (M4c), built *favorable to semantic*.** Blind gold-judge (dropped 5/40),
  hard-negative controls (0/80 false positives), recency-mixed, **paraphrased gold** (to strip
  BM25's lexical overlap) and **lexically-tempting negatives** (to bait BM25). The deck was stacked
  *for* the semantic arm.
- **Real corpus (M4d).** 5 real Claude Code work-sessions across 5 different projects (game-dev,
  infra tool, infra web, experiment, client app), harvested into 195 fixtures (154 test / 4 test
  sessions). Local-only, gitignored, aggregate-only (privacy §9). bge run with a content-memoizing
  embedder (deterministic per text) so the whole pipeline runs in ~2 min.

## 3. What we found
| Corpus | Semantic | BM25 | Recency | Headline (semantic − strongest cheap) | Verdict |
|---|---|---|---|---|---|
| Synthetic M4c (n=26) | 0.880 | 0.811 | 0.428 | **+0.056**, 90% CI [+0.009, +0.102] | NEGATIVE-powered (real but < MEI) |
| Real M4d (n=154, 4 sess) | 0.091 | **0.100** | 0.090 | **−0.053**, clustered CI [−0.085, −0.041] | **BASELINE WINS — powered** |

The pilot (n=8) had looked **GREEN** at +0.129 — the textbook underpowered false positive. Power
regressed it to +0.056; real data pushed it below zero.

**Why semantic loses — four compounding mechanisms:**
1. **The gold is lexical by construction.** "Should have re-onloaded" ⇔ "this *file* came back."
   That's an *identity* signal — a path, a symbol, `Warehouse.restock`. BM25 matches identifiers
   exactly; bge-small smears them into a 384-dim space, discarding the very signal that predicts re-fetch.
2. **Code isn't prose, and the model is.** `bge-small-en-v1.5` is a small, general, **English-prose**
   embedder. The candidates are paths, signatures, diffs, tool output. A prose ruler measuring code.
3. **Needle-in-haystack pools.** Median **~460 candidates** for ~2 gold. All arms score low
   (0.09–0.10); crisp lexical matching degrades more gracefully than fuzzy cosine over hundreds of blobs.
4. **The synthetic→real flip is the tell.** We *rigged* the synthetic corpus for semantic (paraphrased
   gold, lexical-bait negatives) and it still only reached +0.056. Remove the handicap — let real gold
   be naturally identifier-aligned — and BM25 overtakes. The lexical-bias guard confirms real gold is
   **not** a BM25 proxy (gold R@3 0.017 vs control 0.012), so BM25 isn't cheating; it's genuinely better.

Both fairness diagnostics came back clean on real data (lexical-bias non-degenerate; recency thirds
≈ even), so the negative is a property of the task, not a measurement artifact.

## 4. The bitter lesson — and the twist
Sutton's bitter lesson: *general methods that leverage computation and data beat methods that bake in
human assumptions about a problem's structure; the hand-crafted prior gives short-term gains, then
plateaus and loses.* Our result is an instance — **but the surface mapping is inverted, and the
inversion is the lesson.**

- **Surface read (tempting, half-right):** "the fancy neural embedding lost to dumb BM25." True, but
  it makes bge sound like the scale/learning method. It isn't.
- **The twist:** `bge-small-en` is a **frozen, small, off-the-shelf featurizer**. It does no
  task-specific learning, leverages no scale at runtime, and adapts to nothing about coding sessions.
  It is a *fixed encoding of a human hypothesis* — "relevance = semantic similarity." That hypothesis
  **is** the baked-in human prior. BM25 is the method that assumes *less* about meaning and instead
  tracks the data's actual statistical structure (identifier co-occurrence), which is what the task
  rewards.
- **So the real bitter lesson here:** we encoded a human theory of relevance and a more
  assumption-free method that matched the data won — exactly the bitter-lesson pattern, just with the
  "AI-looking" component playing the role of the human prior.
- **The move we did *not* make** (and the bitter lesson's actual prescription): we have **hundreds of
  real sessions** and a harness that harvests true re-fetch labels at scale. The
  scale/learning-leveraging approach is to **learn the re-onload policy from that data**, not to hand-pick
  a frozen embedding and sweep one weight over 5 values. We tested a prior; we never tested *learning*.

The honest synthesis: the bitter lesson doesn't say "always reach for the neural net." It says **let
the data, at scale, choose the representation — don't hand-encode what you think relevance is.** bge
was a hand-encoded guess wearing an ML costume. BM25 won because it's a better-calibrated dumb prior
for *this* task. Whether a *genuinely learned* ranker can beat BM25 is the one question the eval
hasn't answered — and it's the next bet.

## 5. What we keep / what we retire
**Keep (validated):** the durable per-project SQLite store, the capture + survive-compaction +
re-onload hook loop (recency alone crushes the no-store condition), the prod-path/secret guardrails,
the plugin packaging (M7), and — emphatically — **the eval harness itself**, which did the rarest
thing in applied ML: it told its author the thesis was wrong, with a powered, pre-registered, self-
correcting verdict.

**Retire / demote:** the semantic differentiator framing; the warm bge curator subprocess and the
`[embed]` dependency as production requirements. They bought the *worst* arm at the *highest* cost.

## 6. Next steps
Three tracks, ordered by confidence. Each "research" item must clear the **same pre-registered bar**
(MEI +0.10 vs BM25, session-clustered CI, n_sessions ≥ 3) — we do not get to move the goalposts.

### A. Ship the decided result (high confidence, do now)
1. **Wire BM25 into the live onload path.** It exists only in `eval/` today; make a `Bm25` ranker the
   default in `onload/select.py` + `hooks/user_prompt_submit.py`, replacing recency as the default.
   Respect the p50<300ms / p95<600ms hook budget (BM25 over the live candidate set is cheap).
2. **Demote the semantic path to dark/optional** behind `CC_CURATOR_ONLOAD`; keep it runnable for
   research repro. Decide demote-vs-delete for the `curator/` subprocess + `[embed]` extra once BM25 ships.
3. **Amend DESIGN.md §1 and §10** to record the settled NEGATIVE and the BM25 decision — the doc must
   follow the evidence; the "policy is the differentiator" thesis is retired in favor of
   "durable store + guardrails + cheap-but-honest re-onload."

### B. Give *learning* a fair, pre-registered shot (medium confidence, the real bitter-lesson test)
4. **Train a re-onload ranker on harvested real transcripts.** We already harvest true re-fetch labels
   at scale; learn a lightweight model (e.g. logistic / GBDT over cheap features: BM25 score, recency,
   path-overlap, same-dir, tool-type, turn-distance — and optionally a code-trained embedding as *one*
   feature, not the whole model). This is the data-leveraging method the thesis skipped.
5. **Pre-register it like M4c/M4d** and run it through the *same* keystone. Two outcomes, both useful:
   it beats BM25 by ≥ MEI → we have a real differentiator built the bitter-lesson way; it doesn't →
   the simple+general method is the final answer and we stop with confidence.

### C. Cheap side-probes (low cost, only inside track B's harness)
6. **One code-trained embedding arm** (vs bge-small-en) as a *feature/ablation* in B — to confirm the
   "wrong model family" mechanism (§3.2) and bound the upside of a better frozen encoder. Not a
   standalone product bet; a frozen featurizer is still a prior.

### D. Closeout (independent of the above)
7. Merge PR #15 (carries the M7 close-out + this verdict). 8. #12 residual console flash stays open.

> Decision rule for B: if a *learned* ranker on real data cannot beat BM25 by the pre-registered
> margin, the bitter lesson has spoken twice and we ship BM25 permanently. No third attempt on the
> same question without a fundamentally new data source or task definition.
