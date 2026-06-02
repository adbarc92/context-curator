# M3b — Retrieval Eval & Weight Tuning — Design

**Status:** Draft (pre-critique)
**Parent design:** `DESIGN.md` v1.3 §10.2 (Layer 1 IR metrics), §10.4 (the arm-2-vs-arm-3 keystone), §10.8 (CI strategy), §12 (settle weights against the eval)
**Milestone:** M3b (second half of M3), after M3a (the policy engine + `PolicyTarget`). Uses the frozen `RelevancePolicy`, `PolicyWeights`, `RecencyOnlyTarget`/`PolicyTarget`, `Store.all_live_chunks`, `FastEmbedEmbedder`.
**Stack:** Python + UV. bge via the optional `embed` extra (the keystone command only).

---

## 1. Purpose & the question

Answer the project's central question (§10.4): **does the semantic policy (arm 3) actually retrieve better than recency-only (arm 2)?** Build the offline IR eval that measures it — labeled fixtures, precision@k / recall@k / nDCG, a weight sweep with held-out reporting — and produce the keystone result.

The eval is deliberately built to be able to **disprove** the centerpiece: if arm 3 is within noise of arm 2, that is a real, valuable finding, not a failure.

## 2. Scope & decisions

**In scope (M3b):**
- `eval/` package (dev/eval tooling, like `replay/`): IR **metrics**, **fixture** format + loader, an **eval runner**, a **grid weight sweep**, and the **keystone command**.
- Two fixture corpora (one format): `controlled/` (CI, deterministic fake embedder) and `realistic/` (the bge keystone job).
- A small change to `RecencyOnlyTarget` so arm-2 exposes a ranked `candidates` pool (for nDCG comparability).

**Out of scope (deferred):**
- Eviction-regret (§10.2) — needs M4 offload/active-window.
- Token-delta (§10.3) and the live Layer-4 end-to-end suite (§10.5) — separate.
- Embedding-model comparison beyond bge-small (the sweep tunes weights, not the model; a model bake-off is a later extension).
- **Arm 1 (native substrate — compaction + subagent isolation).** DESIGN §10.4's success bar is "arm 3 beats **both** arm 1 and arm 2," but arm 1 is a *live-substrate* behavior with **no offline IR analogue** (there is nothing to rank). It is validated in the Layer-4 live suite (§10.5), not here. **M3b therefore validates only the arm-2-vs-arm-3 slice of the success bar — not the whole bar.** "arm-3 beats arm-2 on retrieval" ≠ "the v1 success target is met"; that fuller claim awaits Layer-4.

**What M3b actually delivers (load-bearing reframe — round 2).** At a *starter* corpus size (n_test ≈ a handful), inferential statistics are **not powered** — a bootstrap CI over ~4 fixtures is so wide it is structurally pre-committed to "inconclusive," and an argmax over a weight grid scored on ~8 fixtures is selection-bias noise. So M3b's **primary deliverable is the validated eval machinery** (metrics + runner + the arm-2-vs-arm-3 comparison harness, proven correct deterministically) **plus an honest, explicitly-underpowered directional first-look** — NOT a statistically-significant keystone verdict. The conclusive keystone (a real CI-backed verdict, promoting swept weights to defaults) is **gated on growing the corpus to n ≳ 30** (a follow-up). The bootstrap/CV code ships but is labeled "meaningful once n is adequate"; the keystone command prints a **power caveat**, and any "arm-3 ahead" reading at this n is reported as *directional / underpowered*, never as "the centerpiece is validated."

**Locked decisions (from brainstorming):**
1. **Direct `decide()` per fixture** — a fixture is (chunks → fresh store, a task, gold keys); the eval calls `target.decide(signal, store)` and reads `Decision.candidates`. Uses the SAME targets the replay harness/M4 use (not a bespoke scorer), without wrapping a session Trace.
2. **Grid sweep on train, report on held-out** — split the corpus; grid-search weights maximizing mean nDCG on train; report the chosen weights + arm-2 on the untouched **held-out** split (avoids Goodharting, §10.2).
3. **CI gates the harness with a fake embedder; the bge keystone is a reported job** (§10.8). CI proves the comparison machinery detects a *planted* semantic win; the bge run answers the real question and is not CI-gated (bge floats are machine-sensitive; the model is a 130MB download).

## 3. Architecture

```
eval/                         (DEV/EVAL TOOLING — depends on policy/replay/store; not shipped)
  metrics.py     precision_at_k / recall_at_k / ndcg_at_k   (pure)
  fixtures.py    Fixture / FixtureChunk / load_fixtures      (pydantic + JSON)
  runner.py      evaluate(fixtures, target, embedder, k) -> ArmMetrics
  sweep.py       grid_sweep(train, embedder, grid, k) + DEFAULT_GRID
  keystone.py    run_keystone(...) + __main__ (the reported bge command)
  fixtures/controlled/*.json   # CI corpus (planted for the keyword fake embedder)
  fixtures/realistic/*.json     # bge keystone corpus
replay/target.py  MODIFY: RecencyOnlyTarget populates Decision.candidates (recency pool)
```

### 3.1 Metrics (`eval/metrics.py`)

Pure functions over `(ranked: list[str], gold: set[str], k: int)`, **binary relevance** (a deliberate v1 simplification — DESIGN §10.2's graded "weak positives" from retrospective mining are a future extension; named here, not assumed-obvious):
```python
def precision_at_k(ranked, gold, k) -> float:   # |top-k ∩ gold| / min(k, len(ranked))  (0 if empty)
def recall_at_k(ranked, gold, k) -> float:      # |top-k ∩ gold| / |gold|   (0.0 if no gold)
def ndcg_at_k(ranked, gold, k) -> float:
    # DCG = Σ rel_i / log2(i+2) over top-k; IDCG = ideal (min(|gold|,k) ones first); 0 if IDCG==0.
    # Robust to gold keys absent from `ranked` and to |gold| > len(ranked).
```
- **precision uses `/min(k, len(ranked))`** (M1): pools here are small/variable, so `/k` would depress a 3-chunk perfectly-ranked fixture to 0.3. (The mean-across-fixtures aggregation still weights fixtures equally regardless of size — a documented limitation.)
- **recall@k saturates and is NOT a primary metric (I3):** both arms' pools are the *full* live set and gold is always present, so for `k ≥ |pool|`, `recall@k = 1.0` for both arms regardless of ranking. It measures top-k *coverage*, not retrieval. The keystone's **primary metrics are nDCG@k and precision@k**; recall is reported only at a small `k` (`recall@3`) where `k < |pool|` makes it discriminating, with this caveat stated in the report.

**`test_metrics.py` is the authority on metric correctness (C1):** hand-computed **golden values** — perfect ranking → 1.0; gold absent → 0.0; a fixed `ranked`/`gold` with a hand-derived nDCG number (e.g. one gold at rank 0 and one at rank 2 → `(1 + 1/log2(4)) / (1 + 1/log2(3))`); `k > len(ranked)`; empty `gold`; `|gold| > k`. The keystone proxy (§4) does NOT carry metric correctness — it asserts exact values, so a metric bug that shifts both arms equally still fails.

### 3.2 Fixtures (`eval/fixtures.py` + JSON data)

```python
class FixtureChunk(BaseModel):
    key: str
    content: str
    tags: list[str] = []
    # NO `pin` (M3): pin_bias=1000 floats any pinned chunk to the top regardless of
    # similarity, which would corrupt the ranking-quality metric. Pinning is a budget/
    # eviction mechanism, not a relevance signal — out of scope for the IR eval.

class Fixture(BaseModel):
    name: str
    chunks: list[FixtureChunk]    # CHRONOLOGICAL: oldest first, newest last (defines recency)
    prompt: str
    recent_tools: list[str] = []  # tool NAMES; runner -> ToolRef(name=t, call_id=f"fixture:{i}")
    gold_keys: list[str]          # the relevant chunk keys (planted gold; blind-labeled — §3.2)
    split: Literal["train", "test"] = "train"   # Literal so a typo is rejected, not silently dropped (M3)

def load_fixtures(directory: str) -> list[Fixture]:   # parse every *.json under directory
```
**Recency convention (explicit):** `chunks` are listed oldest→newest; the runner stores them in that order so `seq` increases and `all_live_chunks` returns them newest-first. Gold can therefore be placed *recency-old* (early in the list) to create the arm-2-vs-arm-3 tension.

**Two corpora, one format:**
- `fixtures/controlled/` — small fixtures for CI with the deterministic **graded** `KeywordEmbedder` (I-1): NOT one-hot-per-keyword (which gives only cosine ∈ {0,1}, trivially separable so the asserts test only plumbing). Instead it embeds into a fixed small keyword space, each chunk a unit-normalized **bag of its keywords**, so two chunks sharing *some* keywords get an **intermediate** cosine (e.g. 0.3, 0.7). This makes the ranking arithmetic genuinely exercised and lets a near-margin flip be constructed.
  - Most fixtures: arm-3 *should* win (gold strongly topic-matched + recency-old; distractors weakly-matched + recency-new).
  - **At least one adversarial fixture where arm-2 STRICTLY wins (I-1):** gold is recency-**new** with *slightly lower* similarity than an older distractor, so recency tips arm-2's top rank to gold (correct) while arm-3 (similarity-dominant at default weights) ranks the higher-sim old distractor first (wrong) → **arm-2 nDCG > arm-3 nDCG** (a genuine negative, not a tie). The proxy asserts this strict inequality — proving the machinery can produce a negative; without it, "the eval can disprove the centerpiece" (§1) is unverified.
- `fixtures/realistic/` — realistic English, the bge keystone corpus. **Construct-validity requirements (I5), not authorial taste:**
  - **Hard negatives:** every fixture includes recency-NEW, lexically/topically *similar-but-wrong* distractors, so a dumb keyword or recency match is penalized.
  - **Mixed gold-recency:** gold must NOT be uniformly recency-old + semantically-distinct (the one regime arm-3 dominates) — include fixtures where gold is recency-new (recency would also find it) and where topicality is genuinely ambiguous, so the corpus does not pre-bake an arm-3 win.
  - **Blind gold labeling:** `gold_keys` are authored from the task alone, **before and independent of** running any arm's ranking (and ideally a second labeler / an LLM-judge calibrated to a human subset, per §10.5). Gold is the answer to the task, not "what the policy ranked high."
  - The corpus is the growable research artifact (§6); the starter set is explicitly directional.

### 3.3 Eval runner (`eval/runner.py`)

```python
@dataclass(frozen=True)
class ArmMetrics:
    ndcg_at_k: float          # primary, at k
    precision_at_k: float     # primary, at k
    recall_at_rk: float       # at the SMALLER recall_k (default 3) so k<|pool| keeps it informative (I1)
    selected_precision: float # production-faithful: precision over decision.selected (I2)
    n_fixtures: int

def evaluate(fixtures, target, embedder, k: int = 10, recall_k: int = 3) -> ArmMetrics:
    # per fixture: fresh InMemoryStore(embedder); store chunks in chronological order
    # (ttl_s=None); build TaskSignal(turn_index=0, prompt=fx.prompt, subtask_id=None,
    #   recent_tool_calls=[ToolRef(name=t, call_id=f"fixture:{i}") for i,t in enumerate(fx.recent_tools)]);
    # d = target.decide(signal, store);
    #   ranked    = [c.key for c in d.candidates]   -> ndcg_at_k(ranked,gold,k), precision_at_k(ranked,gold,k),
    #                                                   recall_at_k(ranked,gold,recall_k)
    #   selected  = [c.key for c in d.selected]     -> precision_at_k(selected,gold,k)   (production-faithful, I2)
    # accumulate; return the MEAN of each across fixtures.
```
`TaskSignal.turn_index` is required (use 0); `subtask_id=None`; `call_id` is irrelevant to scoring (only `ToolRef.name` enters `task_text` in `PolicyTarget.decide`) but is a required `ToolRef` field, hence the synthesized `fixture:{i}`. Both arms populate `selected` at default k/budget, so `selected_precision` is comparable across arms.
**Embedder consistency — a GUARD via a PUBLIC accessor (I1/I-4):** if the store is populated with embedder A but the `PolicyTarget` scores with embedder B, `RelevancePolicy.scored` silently hits the dim-mismatch re-embed path and (past `reembed_cap`) scores `similarity=0` — so a divergence quietly measures the *fallback*, not the policy. To guard without reaching through two `_private` layers, M3a's classes gain **public read-only accessors** — `RelevancePolicy.embedder` and `PolicyTarget.embedder` (returning the policy's embedder). `evaluate` asserts `if isinstance(target, PolicyTarget): assert target.embedder is embedder`. (`grid_sweep`/`run_keystone` build the `PolicyTarget` from the same embedder they pass to `evaluate`, so this catches a wiring bug, not a runtime condition.) The runner uses `Decision.candidates` (the full ranking) for metrics — NOT `selected` (the budget-limited onload slice); **ranking quality is the question.**

**Recency-tie invariant (M4):** `InMemoryStore._seq` strictly increases per `store()` call, so `all_live_chunks`' seq order is a total order — no recency ties, so arm-2's candidate order (and thus its nDCG) is deterministic. The runner stores fixture chunks in their chronological list order, giving the intended recency.

### 3.4 The arm-2-vs-arm-3 contest: pure ranking signal over an identical pool (C2)

**What the keystone measures, stated precisely (C-1):** *does ranking the live pool by semantic score beat ranking it by recency?* — i.e. **semantic ranking vs recency ranking.** Both arms rank the identical full pool (`all_live_chunks`); they differ ONLY in the ordering signal (arm-3 = `RelevancePolicy` score, arm-2 = recency/seq order). **This is NOT a strawman baseline:** "recency-only" *by definition* uses no semantic/topic pre-filter, so `RecencyOnlyTarget` is run with **`tags=None`** (its default) — and the top-k of its full recency ranking is *exactly* what production recency-only retrieves (`store.query(tags=None, k)` returns the k newest, identical to the top-k of `all_live_chunks`). So arm-2's eval ranking IS faithful to the shipped recency baseline; nothing production-relevant is stripped. (The `k`/token onload *budget* is a downstream slice applied equally to both arms via `selected`/`pick`; it is not a ranking signal and not part of the nDCG/precision metric.) **Production-faithful sanity row:** the keystone also reports both arms' `selected` (budget-limited onload) precision as a secondary row, so a reader sees the as-shipped behavior alongside the pure-ranking comparison. The claim language is precise everywhere: M3b answers "semantic ranking vs recency ranking," the §10.4 arm-2-vs-arm-3 slice — not the whole success bar (§2).

**The change:** `RecencyOnlyTarget.decide` populates `candidates = [SelectedChunk(key=c.key, score=None, tokens=estimate_tokens(c.content)) for c in store.all_live_chunks()]` (full live set, newest-first); `selected` is unchanged (its `store.query` onload slice).

**Determinism-test impact (C3 — my earlier "additive, tests assert selected" justification was WRONG):** the replay determinism tests (`test_byte_identical_across_runs`, `test_byte_identical_with_budget_and_multitool`) compare `log.model_dump()` of the **full** Decision, which DOES include `candidates`. They stay green not because candidates is unread, but because the new candidates pool is **deterministic across two runs** (the store is rebuilt per run; `_seq` is assigned identically) → byte-identical. **Before implementing, grep for any committed `DecisionLog`/Decision JSON snapshot or `model_dump()`-equality test that encodes arm-2's old empty `candidates`** and update it deliberately; do not assume none exists.

### 3.5 Weight sweep (`eval/sweep.py`)

```python
# Sweep ONLY w_similarity at M3b (round-2 M-4/C-2): with ~8 train fixtures you cannot
# resolve 3 free params (27 cells / 8 points = selection-bias noise). w_similarity is the
# one parameter the keystone question turns on; sim_floor/decay_lambda stay at defaults
# until the corpus grows. Small space => small selection bias.
DEFAULT_GRID = [
    {"w_similarity": s, "w_recency": round(1 - s, 2)} for s in (0.4, 0.5, 0.65, 0.8, 1.0)
]

@dataclass
class SweepCell:
    weights: PolicyWeights
    loo_ndcg: float            # leave-one-out CV mean nDCG on train
    fold_std: float            # std-dev across LOO folds (flat-optimum signal)

@dataclass
class SweepResult:
    best: PolicyWeights
    top_cells: list[SweepCell]  # ranked; reveals a flat/degenerate optimum (M5)

def grid_sweep(train_fixtures, embedder, grid=DEFAULT_GRID, k=10,
               base=PolicyWeights()) -> SweepResult:
    # for each combo: w = replace(base, **combo); evaluate via LOO-CV (below); track best mean nDCG.
    # Deterministic: grid iterated in fixed order; ties keep the first-seen combo.
```
`w_tag` stays 0 (tags are tool-provenance, §M3a). The grid is **5 cells** (`w_similarity` only) and deterministic.

**The sweep is a coarse directional scan, honestly labeled (C-2).** Each cell's score is its leave-one-out CV mean nDCG over the train fixtures (lower per-cell variance), and `SweepResult` carries the **top cells with their scores + fold variance** so a flat optimum is visible (M5). But the spec does NOT claim LOO-CV "controls overfitting": **selecting the argmax over even a 5-cell grid scored on ~8 fixtures is still selection-bias noise; the chosen cell is not statistically distinguishable from its neighbors.** The sweep output is **directional guidance only, gated on a larger corpus** (§2) — it is not authoritative tuning and does not by itself change `PolicyWeights`' defaults.

### 3.6 Keystone command (`eval/keystone.py`)

```python
@dataclass
class KeystoneReport:
    best_weights: PolicyWeights
    arm3: ArmMetrics                 # tuned semantic, on HELD-OUT
    arm2: ArmMetrics                 # recency-ranking, on HELD-OUT
    n_test: int                      # held-out fixture count (small — directional)
    per_fixture_ndcg_delta: list[float]   # arm3 - arm2, per held-out fixture
    delta_ci90: tuple[float, float]  # paired bootstrap 90% CI on the mean nDCG delta
    verdict: str                     # see below

def run_keystone(corpus_dir, embedder, k=10, seed=0) -> KeystoneReport:
    # load corpus; train/test by `split`; sweep = grid_sweep(train, embedder); best_w = sweep.best;
    # arm3 = evaluate(test, PolicyTarget(RelevancePolicy(embedder, best_w)), embedder, k);
    # arm2 = evaluate(test, RecencyOnlyTarget(), embedder, k);
    # per-fixture nDCG deltas -> PAIRED BOOTSTRAP (resample fixtures with the fixed `seed`) -> 90% CI.
```
**Verdict is honestly underpowered at this n (C-2).** The bootstrap CI is computed and reported as a **width-of-ignorance display**, but at n_test≈4 it is expected to straddle 0, so the verdict is **explicitly directional, not inferential**: `KeystoneReport.verdict` = `"directional: arm-3 ahead by Δ (UNDERPOWERED, n=<n_test>, 90% CI [lo,hi] includes 0 — inconclusive)"` (or arm-2 ahead). It does NOT claim a win. A real "beats/loses" verdict (CI excludes 0) only becomes meaningful once the corpus grows to n ≳ 30, stated in the output. `KeystoneReport` carries `n_test` and the per-fixture deltas so the reader sees the sample.
**`seed` fixes resampling ONLY, not the result (I-2):** the fixed bootstrap `seed` makes the *resampling indices* reproducible, but the underlying per-fixture nDCGs are functions of machine-sensitive bge floats — so the verdict/`best_weights` are **NOT reproducible across machines/onnxruntime versions**. The spec does not claim otherwise.

`__main__` builds a `FastEmbedEmbedder`, runs `run_keystone("fixtures/realistic")`, prints the table (arm × nDCG@k / precision@k / recall@3, plus the production-faithful `selected` row §3.4) + the CI (as ignorance-width) + the directional reading + the explicit **power caveat** ("not powered to detect |Δ|<…; grow corpus to n≳30") + the top sweep cells, and writes the report to a **gitignored** `results/keystone-<k>.md` (ephemeral; **regenerate, do not diff** — bge floats are machine-sensitive). Run: `uv run python -m context_curator.eval.keystone` (needs `uv sync --extra embed`; the `embed` extra **pins** `fastembed` + `onnxruntime` for as-much-reproducibility-as-possible).

## 4. Testing (CI, deterministic — no bge)

A `KeywordEmbedder` test helper (the **graded bag-of-keywords** scheme from §3.2, NOT orthogonal-per-keyword) embeds each text as the unit-normalized sum of its keywords' basis vectors over a small fixed vocabulary, so shared-but-not-identical keyword sets give **intermediate** cosines — exact and reproducible. **Canonical adversarial fixture (the strict arm-2 win, verified against the real scoring math):** vocab `{A,B,C,D,E,F}`, task keywords `{A,B,C,D}`; **gold** = `{A,B,C}` (cos 0.866), placed **recency-new** (i=0); **distractor** = `{A,B,C,D}` (cos 1.0), **recency-old** (i=1). At default weights (w_sim 0.65, w_rec 0.35, sim_floor 0.5, λ 0.1): `s_gold ≈ 0.826`, `s_dist ≈ 0.967` → **arm-3 ranks the distractor first** (nDCG@3 ≈ 0.63) while **arm-2 ranks the recency-new gold first** (nDCG@3 = 1.0) → **strict arm-2 > arm-3**. (A binary/orthogonal embedder would make both cos=1.0 → equal sim → the recency tie-break ranks the newer gold first for *both* arms → only a tie, never a strict win; hence the graded scheme is required.)
- **`test_metrics.py` (the authority on metric math, C1)** — precision/recall/nDCG against **hand-computed golden VALUES**: perfect ranking → 1.0; gold absent → 0.0; a fixed ranking with gold at ranks 0 and 2 → the literal `(1 + 1/log2(4)) / (1 + 1/log2(3))`; `precision` with `len(ranked)<k` → `/min(k,n)`; empty gold → 0.0; `|gold|>k`.
- **`test_fixtures.py`** — load a `controlled` fixture, round-trip the model, assert chronological→recency order is preserved when stored; assert the schema rejects a `pin` field (M3).
- **`test_runner.py`** — `evaluate` over one controlled fixture (`KeywordEmbedder`): `PolicyTarget` ranks planted gold first (nDCG = 1.0 exactly); `RecencyOnlyTarget` ranks it low; **the embedder-binding assert fires** when a mismatched-embedder `PolicyTarget` is passed (I1).
- **`test_keystone_proxy.py` (the CI proxy — asserts EXACT values, not a sign, C1)** — over `controlled/`: assert the **exact** per-arm mean nDCG/precision values (so a metric bug shifting both arms equally still fails), AND that on the **adversarial control fixture** (gold recency-new) **arm-2 ≥ arm-3** (the machinery can produce a negative). The aggregate "arm-3 > arm-2 over the pro-semantic fixtures" is a *consequence*, not the only assertion.
- **`test_bootstrap.py`** — the paired-bootstrap CI helper on hand-made delta arrays: an all-positive delta → CI lower bound > 0; a straddling delta → CI contains 0; fixed seed → identical CI across runs.
- **`test_sweep.py`** — `grid_sweep` over controlled train fixtures is deterministic across runs and returns a `SweepResult` whose top cells + LOO scores are populated; the winner's train nDCG ≥ the base default's.
- **`RecencyOnlyTarget` candidates** (`tests/replay/test_recency_candidates.py`) — `decide` now populates `candidates` (recency-ranked, full pool) AND the full existing replay suite (incl. the two `model_dump()` determinism tests) stays green after the grep-and-update check (§3.4).
- **bge keystone** — a skip-if-model-unavailable smoke test that `run_keystone` executes end-to-end on a tiny realistic subset and returns a populated `KeystoneReport` (the real numbers come from the manual command, not CI).

`bootstrap_ci(deltas: list[float], *, seed: int, alpha: float = 0.1) -> tuple[float, float]` (a small helper in `eval/stats.py`) resamples the per-fixture deltas with replacement (fixed seed → reproducible) and returns the `[alpha/2, 1-alpha/2]` percentile interval of the resampled means. Pure stdlib (`random.Random(seed)`), no numpy.

## 5. File structure

```
src/context_curator/eval/
  __init__.py
  metrics.py
  stats.py        # bootstrap_ci (paired bootstrap, fixed seed, stdlib)
  fixtures.py
  runner.py       # evaluate() + the embedder-binding assert
  sweep.py        # grid_sweep (LOO-CV) + SweepResult/SweepCell + DEFAULT_GRID
  keystone.py
  fixtures/controlled/*.json   # incl. >=1 adversarial fixture where arm-2 should win
  fixtures/realistic/*.json    # hard negatives, mixed gold-recency, blind labels
src/context_curator/policy/relevance.py # MODIFY: public `embedder` accessor (I-4)
src/context_curator/replay/target.py   # MODIFY: RecencyOnlyTarget.candidates + PolicyTarget.embedder
pyproject.toml                          # MODIFY: pin fastembed+onnxruntime in [extras].embed
.gitignore                              # MODIFY: results/
results/                                 # keystone output — GITIGNORED, ephemeral (regenerate, don't diff)
tests/eval/
  test_metrics.py        # golden metric values (the authority)
  test_stats.py          # bootstrap_ci CI behavior
  test_fixtures.py
  test_runner.py         # incl. embedder-binding assert
  test_keystone_proxy.py # exact values + the strict arm-2-win adversarial fixture
  test_sweep.py
tests/replay/test_recency_candidates.py  # the RecencyOnlyTarget change
```

## 6. How this connects / what it concludes

- M3b produces the **keystone number** and **tuned default weights**. If arm-3 beats arm-2 on held-out nDCG, the differentiated build is validated; the tuned weights become `PolicyWeights`' new defaults (a follow-up). If not, that is a documented finding and the recency baseline is the honest recommendation.
- The fixture corpus is the **growable research artifact** (§10.2 bootstrap: hand-curated → planted-synthetic → retrospective mining). M3b ships the starter; it expands over time.
- M4 (live onload) reuses the tuned weights; eviction-regret + the live Layer-4 suite extend the eval later.

---

## Design Critique Log

Three independent adversarial review rounds (fresh reviewer each round, each seeing the prior revision) before presentation.

### Critique Round 1
**Findings (Critical):** the CI "keystone proxy" was **circular** — asserting "arm-3 > arm-2" on fixtures built so arm-3 wins, with a planted embedder, validating nothing (passes under compensating metric bugs) (C1); **arm-2's candidate pool was a strawman** (full recency over all-live) not the production baseline, flattering arm-3 (C2); the "additive, tests assert selected" determinism justification was **factually wrong** — the determinism tests `model_dump` the full Decision (C3). **Important:** embedder divergence between store and policy silently measured the fallback (I1); the keystone was a point estimate over ~4 fixtures with no CI and an undefined "within noise" (I2); recall@k saturates over all-gold pools (I3); dropping arm-1 silently shrank the §10.4 success bar (I4); single-author construct-validity bias, no hard negatives (I5).
**Resolved:** metric correctness moved to golden-value `test_metrics.py`; proxy asserts exact values + an adversarial fixture arm-2 must win; arm-2-vs-arm-3 reframed as "ranking signal over identical pool"; embedder-binding assert; bootstrap CI + LOO-CV + per-fixture deltas; recall demoted to recall@3; arm-1 caveat + claim down-scoped; corpus rules (hard negatives, mixed gold-recency, blind labels); `pin` forbidden; precision `/min(k,n)`; the determinism justification corrected + a grep-for-snapshots step.

### Critique Round 2
**Findings (Critical):** the round-1 statistical apparatus was **false rigor at n≈4/8** — a bootstrap CI over ~4 points is structurally pre-committed to "inconclusive" (a null-result generator), and LOO-CV argmax over a 27-cell grid on ~8 points still overfits via selection bias (C-2); the arm-2 reframe still compared production arm-3 against a **non-production** recency baseline (C-1). **Important:** the orthogonal `KeywordEmbedder` is trivially separable so "exact value" asserts test only plumbing, and the adversarial fixture yielded a *tie* not a strict win (I-1); committing bge-float keystone output churns and `seed` fixes only resampling (I-2); `recent_tools`→`ToolRef` mapping + required `call_id` underspecified (I-3); the binding assert reached through two private layers (I-4).
**Resolved:** the **reframe** — M3b delivers the validated harness + an explicitly-underpowered directional first-look, inferential stats demoted to "meaningful once n≳30," verdict honestly directional; clarified arm-2 = recency-only with **no** tag pre-filter (faithful, since recency-only uses no semantic filter) + a production-faithful `selected` row + precise "semantic-vs-recency ranking" claim; **graded** bag-of-keywords `KeywordEmbedder` making a strict arm-2 win constructible; sweep reduced to `w_similarity` only; results gitignored + version-pinned + "seed fixes resampling only"; `ToolRef`/`TaskSignal` synthesis specified; public `embedder` accessor.

### Critique Round 3
**Verdict: implementation-ready after a small must-fix list.** The reviewer **verified the strict-arm-2-win fixture is constructible** with the real scoring math (a worked 2-chunk example). **Critical:** §3.2 (graded) and §4 (still orthogonal) gave two contradictory `KeywordEmbedder` definitions — and the orthogonal one makes the adversarial assert *unsatisfiable* (C1). **Important:** `recall@3` was not wired into `ArmMetrics`/`evaluate` (everything at one `k`) (I1); the production-faithful `selected` sanity row had no code path (I2). **Minor:** stale "27 combos" vs the 5-cell grid; `fold_std` naming.
**Resolved:** unified `KeywordEmbedder` to the graded scheme + embedded the verified worked adversarial example as canonical; `evaluate(... , recall_k=3)` with `ArmMetrics.{recall_at_rk, selected_precision}`; corrected the grid size and `fold_std` doc. No design decision changed.
