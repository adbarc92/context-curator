# M3a — Relevance Policy Engine — Design

**Status:** Draft (pre-critique)
**Parent design:** `DESIGN.md` v1.3 §4.2 (scoring), §6 (policy interface), §10.1 (Layer-0 property tests), §12 (embedder choice deferred to M3)
**Milestone:** M3a (first half of M3), after M0/M1 + replay harness + M2. The frozen `Store`/`Chunk`/`Embedder` interfaces, the replay harness (`ReplayTarget` Protocol, `RecencyOnlyTarget`, `TaskSignal`, `Decision`), and `estimate_tokens` all exist.
**Stack:** Python + UV. Adds `fastembed` (ONNX, no torch).

---

## 1. Purpose

Build the **centerpiece**: a relevance policy that scores chunks by *semantic* task-similarity (not just recency) and selects the onload set. This is the "arm 3" the eval (M3b) compares against the "arm 2" recency-only baseline — the single comparison (§10.4) that decides whether the differentiated build is differentiated. M3a builds the engine; **M3b** (separate cycle) builds the labeled-fixture retrieval eval that proves it.

## 2. Scope & decisions

**In scope (M3a):**
- A real local embedder: **`FastEmbedEmbedder`** (bge-small-en-v1.5, 384-dim, ONNX via `fastembed`, no torch) implementing the existing `Embedder` interface. `HashingEmbedder` stays as the fast deterministic *test* embedder.
- **`policy/`** package (product, replay-agnostic): `RelevancePolicy.score / select_onload / select_offload` + `PolicyWeights`.
- **`PolicyTarget`** (in `replay/`, the eval adapter): a `ReplayTarget` that drives the policy through the replay harness (the arm-3 target).
- §10.1 Layer-0 property tests (deterministic, via a controlled embedder).

**Out of scope (deferred):**
- The labeled fixture set, precision@k/nDCG, weight tuning, the arm-2-vs-arm-3 measurement → **M3b**.
- Live onload wiring (`UserPromptSubmit` hook injecting the selected slice) → **M4**.
- A real active-window representation for offload → M4. M3a's `select_offload` is the minimal threshold form (§4.4).

**Locked decisions (from brainstorming):**
1. **Embedder:** fastembed/bge-small-en-v1.5; the engine is embedder-agnostic via the `Embedder` interface.
2. **Embedding consistency:** the policy uses a candidate's stored embedding **only if its dim matches the active embedder**; otherwise it **re-embeds the content, capped at `reembed_cap` per call** (beyond the cap → `similarity=0` + degraded log, so the per-prompt latency stays bounded — C2). Replay shares one embedder → no mismatch. The correct *live* fix (one-time store re-embed on embedder change) is deferred to M4.
3. **Offload:** minimal — `select_offload(task_text, candidates)` returns non-pinned candidates scoring below `eviction_threshold` (the §10.1 pin-eviction property). It is **unit-tested but not wired into `PolicyTarget`** (eviction-regret needs an active window → M4).
4. **Scoring is scale-coherent:** every term is absolute on `[0,1]` — recency via size-stable decay, similarity via affine-floor rescale, tag (off by default) — so the M3b weight sweep operates on a well-conditioned objective, not a recency-dominated artifact.
5. **Candidate source:** the policy scores the **full live set** via the new `Store.all_live_chunks()`, never the recency-truncating `query`. §6 policy interface amended accordingly (DESIGN §6 bumped).

## 3. Architecture & boundary

```
policy/                    (PRODUCT — knows only Chunk/Embedder/tokens; NOT replay)
  weights.py     PolicyWeights
  relevance.py   RelevancePolicy.{score, select_onload, select_offload, scored}
embeddings.py    + FastEmbedEmbedder
replay/                    (EVAL TOOLING — depends on product, not vice versa)
  target.py      + PolicyTarget(ReplayTarget) — adapts TaskSignal -> policy calls
```

**Dependency direction (load-bearing):** `policy/` imports `store.interface.Chunk`, `embeddings.Embedder`, `tokens.estimate_tokens` — nothing from `replay/`. `PolicyTarget` lives in `replay/` and imports `RelevancePolicy`. So the product never depends on the eval tooling.

### 3.1 `FastEmbedEmbedder` (`embeddings.py`)

```python
class FastEmbedEmbedder(Embedder):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model = None          # lazy: the ONNX model loads on first embed
    @property
    def dim(self) -> int:
        return 384                  # bge-small-en-v1.5
    def embed(self, text: str) -> list[float]:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(self._model_name)
        vec = list(next(iter(self._model.embed([text]))))   # numpy row -> list[float]
        return _unit_normalize([float(x) for x in vec])     # unit norm so cosine == dot
```
**`fastembed` is an OPTIONAL dependency** (I5) — `[project.optional-dependencies] embed = ["fastembed>=0.3"]`, NOT a base dep. The base install stays `pydantic`+`mcp` only; CI does **not** install the `embed` extra, so onnxruntime/tokenizers/huggingface-hub never enter the gating path. `FastEmbedEmbedder` imports `fastembed` lazily (inside `embed`), so importing `embeddings.py` without the extra is fine. The model (~130MB) downloads on first real use. The skip predicate **probes the model, not the package** (a fixture that tries to construct `TextEmbedding(...)` and `pytest.skip`s on any exception) — `find_spec("fastembed")` is insufficient (package present, model absent → would trigger a 130MB CI download/hang). `_unit_normalize` guards a zero vector (returns it unchanged); fastembed already L2-normalizes bge outputs, so this is a **defensive** re-normalization (idempotent), not a correctness dependency — the probe test confirms `‖embed(x)‖ ≈ 1`.

**Performance note (I1):** `all_live_chunks` + scoring deserializes every chunk's embedding (`json.loads` of a 384-float array) once per prompt over the whole live store. With the single-pass fix (C-2) this is 1× per chunk per prompt, bounded by the TTL'd store size — acceptable at v1 single-machine scale. Storing embeddings as a packed binary BLOB (vs JSON text — the DDL comment already flags JSON as a placeholder) is a deferred perf optimization, not built in M3a.

### 3.2 `PolicyWeights` (`policy/weights.py`)

```python
@dataclass(frozen=True)
class PolicyWeights:
    w_recency: float = 0.35
    w_similarity: float = 0.65
    w_tag: float = 0.0              # tags are tool-provenance not topic today (C3) -> OFF by default
    pin_bias: float = 1000.0        # large additive: pins always win onload / clear eviction
    eviction_threshold: float = 0.15
    decay_lambda: float = 0.1       # recency = exp(-decay_lambda * rank); size-stable (I2)
    sim_floor: float = 0.5          # affine-rescale cosine above this floor (I1/I3 — see below)
    reembed_cap: int = 128          # max candidates re-embedded per scoring pass on dim mismatch (C2)
```
Defaults are **provisional starting points**; M3b's sweep finalizes them, and whether arm-3 beats arm-2 is M3b's empirical question (M3a ships the mechanism + a grounded default, not a proven setting). **`sim_floor=0.5` (corrected, I3):** bge-small-en-v1.5 related short-text pairs typically cosine ~0.6–0.85 and unrelated ~0.25–0.5, so a floor of 0.5 places most *unrelated* pairs at/near 0 after rescale while preserving the related band — a floor of 0.3 (an earlier draft) wrongly left unrelated text scoring up to ~0.57. The default is validated by a 10-pair cos-distribution probe in `test_fastembed.py` (related vs unrelated) and refined in M3b; it is not asserted from prose. **`w_tag=0` by default:** captured chunk tags are a single tool name (`capture_tool_result`), i.e. provenance, not topic — rewarding tag overlap would inject a tool-type-recency signal that confounds the arm-2-vs-arm-3 comparison (§10.4). The `tag_match` term stays implemented (for when M2/M3b supplies semantic tags) but contributes nothing at the default weight.

### 3.3 `RelevancePolicy` (`policy/relevance.py`)

```python
class RelevancePolicy:
    def __init__(self, embedder: Embedder, weights: PolicyWeights = PolicyWeights()) -> None: ...

    # --- single scoring pass (THE entry point; embeds task ONCE, one reembed budget) ---
    def scored(self, task_text: str, candidates: list[Chunk],
               query_tags: list[str] | None = None) -> list[tuple[Chunk, float]]:
        """Embed task_text ONCE; score every candidate; return (chunk, score) sorted by
        (-score, incoming_index). `candidates` MUST be recency newest-first (the contract).
        The `reembed_cap` budget is consumed across THIS call only (one pass per prompt)."""

    # --- selection over an ALREADY-scored list (no re-scoring, no re-embedding) ---
    def pick(self, scored_pairs: list[tuple[Chunk, float]], k: int = 10,
             token_budget: int | None = None) -> list[Chunk]:
        """Top chunks from a (score-sorted) list under k and token_budget. First-fit
        semantics IDENTICAL to RecencyOnlyTarget/store.query: walk in order, **break** at
        the first chunk that would exceed token_budget (not skip-and-continue), stop at k —
        so arm-2 vs arm-3 differ ONLY in ranking, not budget-fill behavior (round-3 #4)."""

    def offload_keys(self, scored_pairs: list[tuple[Chunk, float]]) -> list[str]:
        """Keys of non-pinned chunks scoring below eviction_threshold."""

    # --- convenience wrappers (one scoring pass each) for non-replay callers ---
    def select_onload(self, task_text, candidates, query_tags=None, k=10, token_budget=None):
        return self.pick(self.scored(task_text, candidates, query_tags), k, token_budget)
    def select_offload(self, task_text, candidates, query_tags=None) -> list[str]:
        return self.offload_keys(self.scored(task_text, candidates, query_tags))
```
**Single-pass rule (C-2/C-3):** the only method that embeds + scores is `scored`; `pick`/`offload_keys` operate on its output. `PolicyTarget.decide` calls `scored` **once** and feeds the result to both `pick` (for `selected`) and the logged `candidates` pool — so per prompt: one task embed, one scoring pass, one `reembed_cap` budget. The `select_onload`/`select_offload` wrappers exist for non-replay callers and each do one pass; **calling both on the same input would score twice** — the combined onload+offload path is an M4 concern that should call `scored` once and feed the pool to both `pick` and `offload_keys` (round-3 #6). M3a has no such caller (`PolicyTarget` uses `scored`+`pick`).

**Task ordering (round-3 #7, "plumbing before policy"):** implement in three steps so the store work lands/reviews independently — **(1)** store changes (`Store.all_live_chunks` on the ABC + both backends, the shared `store/expiry.py` helper, `InMemoryStore` TTL parity, contract tests on both backends); **(2)** `FastEmbedEmbedder` + `policy/` (`PolicyWeights`, `RelevancePolicy`); **(3)** `PolicyTarget` + its replay test.

**Candidate-source contract (C1):** `candidates` MUST be the **full set of live (non-expired) chunks in recency order, newest first** — the policy derives recency from position, so it must see the whole population, not a `query`-truncated slice. M3a adds **`Store.all_live_chunks() -> list[Chunk]`** with these exact semantics (interface amendment — §6 / DESIGN §6):
- `seq DESC`, **no** `k`/`token_budget`/tag filtering.
- **Per-row expiry** via the same `_is_expired` check `query` uses (parse `expires_at`, not SQL string compare; the M2 sweep is rate-limited so expired-but-unswept rows are present and MUST be filtered here — do NOT rely on the sweep). Does not lazily evict (like `query`, unlike `retrieve`).
- **Tenant scope = `query`'s full defence-in-depth** (security-critical, I-2): the SQL prefix match **and** the Python-side `is_within_scope` re-check, so a tenant prefix containing a `_`/`%` LIKE-wildcard cannot over-match. The contract test includes the wildcard case (DESIGN §9, 100% pass).
- **`InMemoryStore` gets TTL parity (C1):** today `InMemoryStore` has *no* expiry logic, so "non-expired" would be untestable on it. **Shared helper (round-3 #2):** `InMemoryStore` holds `Chunk` objects, which have **no `expires_at` field** (it is a sqlite-only DDL column) — so it cannot call sqlite's row-shaped `_is_expired`. M3a extracts a shared `store/expiry.py::is_expired(created_at, ttl_s, pin, now=None) -> bool` (computes `created_at + ttl_s`, returns False when `pin` or `ttl_s is None`); **`SqliteStore._is_expired` is refactored to call it**, and `InMemoryStore` calls it on its `Chunk`s in `query`/`retrieve`/`all_live_chunks`. One implementation, both backends. (`_now()` wall-clock is fine for liveness; it does not enter the replay-determinism path, which uses `ttl_s=None` → `expires_at` never computed → never reached. Verified: replay ingest → `ttl_s=None`, so byte-identical logs hold.)

**Scoring** (`score` per chunk; every term is absolute on a comparable `[0,1]` scale so static weights are meaningful — I1/I2):
```
score = w_recency·recency_decay + w_similarity·similarity + w_tag·tag_match + (pin_bias if chunk.pin else 0)
```
- **recency_decay** — `exp(-decay_lambda * rank)`, `rank` = 0-based position in the recency-ordered list (0 = newest). **Size-stable**: newest is always 1.0 and the decay rate is fixed regardless of `N` (rank-10 ~ 0.37 whether `N` is 20 or 2000), unlike a linear rank that flattens as `N` grows and silently re-weights recency vs similarity.
- **similarity** — affine-rescaled cosine: `clamp01((cosine(task_emb, chunk_emb) - sim_floor) / (1 - sim_floor))`. bge cosines for unrelated English cluster ~0.3–0.7, so a raw `max(0,cos)` leaves the discriminating band compressed and recency dominates; the floor (default `sim_floor=0.5`, §3.2) re-expands `[sim_floor,1] -> [0,1]` so similarity's spread matches recency's. Negatives fall below the floor → clamp 0 (deliberate). `cosine` = pure-Python `dot/(norm·norm)`, zero-norm → 0.0 (so `policy/` needs no numpy).
  - **`chunk_emb` source + bounded re-embed (C2/C-3):** use stored `chunk.embedding` **iff** `len == embedder.dim`. On dim mismatch, re-embed `chunk.content` (cached by `sha1(content)` within the call) — but the re-embed budget is **`reembed_cap` per scoring pass** (i.e. per prompt, since `decide` scores once). Because candidates are recency-ordered, the cap re-embeds the **newest** mismatched candidates first and zeros (`similarity=0` + a one-line *degraded* log) the older mismatched ones — a deliberate recency-priority bias, stated. This bounds the worst case: a live store of legacy `HashingEmbedder(256)` chunks would otherwise force N bge inferences on the per-prompt path, violating §4.2's one-embed budget. In replay (ingest + policy share one embedder) there is **no** mismatch, so the cap never triggers. The correct *live* fix is a one-time store re-embed migration on embedder change (M4/ops); the inline cap is the transition fallback + §4.2 graceful degradation.
- **tag_match** — `|set(query_tags) ∩ set(chunk.tags)| / |set(query_tags)|` if `query_tags`, else `0.0`. Inert at the `w_tag=0` default (C3).
- **pin_bias** — additive constant; with defaults a pin's score ≥ 1000, so it always outranks non-pins and always clears `eviction_threshold`. **Two pins** are ordered among themselves by their underlying (non-pin-bias) sub-score, then `incoming_index` — fully deterministic; `1000 + [0,1]` is exactly representable in float64 with no precision loss.

**Internal `_score` + how `scored` wires the rank (round-3 #1, load-bearing):** `scored` embeds `task_text` once, then **enumerates `candidates` in incoming order** (which the contract guarantees is recency newest-first). For the i-th candidate it computes `recency_decay_i = exp(-decay_lambda · i)` and calls the internal `_score(chunk, task_emb, recency_decay_i, query_tags) -> float` (which takes the **already-computed `task_emb`** so the task is embedded once per pass, not per chunk). **The same incoming index `i` is the `incoming_index` tie-break key, and the `reembed_cap` budget is consumed walking that same order** (so the newest mismatched candidates are the ones re-embedded). Rank is therefore the incoming position — never a re-sort. There is no public `score(chunk, task_text)`; `scored` is the public entry (DESIGN §6 amended to `scored(task_text, candidates) -> [(chunk, score)]`).

**Determinism & tie-break:** for a fixed embedder, scoring is a pure function of (task_text, chunk-set). `scored` sorts by `(-score, incoming_index)`. Note that with distinct `recency_decay` per rank, two candidates have **exactly equal** total score only when recency is neutralized (e.g. `w_recency=0`) or for equal-sub-score pins — so the `incoming_index` tie-break is exercised only there (the "recency breaks ties" property test must set `w_recency=0` or use a pinned pair to construct a true tie; at default weights, recency differentiates directly via `recency_decay`). Sorting uses **full-precision** scores; rounding (§3.4) is presentation-only.

### 3.4 `PolicyTarget` (`replay/target.py`, the arm-3 target)

```python
class PolicyTarget:
    name = "semantic-policy"
    def __init__(self, policy: RelevancePolicy, k: int = 10, token_budget: int | None = None,
                 score_ndigits: int = 6) -> None: ...
    def decide(self, signal: TaskSignal, store: Store) -> Decision: ...
```
`decide`:
1. `task_text = signal.prompt` + (if `signal.recent_tool_calls`) `"\n" + " ".join(r.name for r in signal.recent_tool_calls)`. **`subtask_id` is NOT embedded** — it is an opaque ID (slug/UUID), not natural language, so it only adds tokenizer noise to the bge input (M3 round-1 M3).
2. `candidates = store.all_live_chunks()` — the FULL live set, recency-ordered (C1; NOT `query`, which truncates).
3. **`pool = policy.scored(task_text, candidates)` — scored ONCE** (C-2). Then `chosen = policy.pick(pool, k=k, token_budget=token_budget)` — selection over the already-scored list, no re-embed, no re-score. **No `query_tags`** — tags are tool-provenance and would confound the eval (C3); arm-3 is purely recency+similarity at default weights. (One task embed, one scoring pass, one `reembed_cap` budget per prompt.)
4. Return a `Decision` with `selected` (chosen, scores **rounded to `score_ndigits`**), `total_tokens`, and the forward-stable `candidates` pool (also rounded). `Decision.offloaded` stays `[]` — see the offload/regret note below.

**Score quantization:** float scores are rounded to `score_ndigits` so `DecisionLog.model_dump()` is byte-stable **on a given machine** (same trace twice → identical log; tested). Sorting uses full-precision scores; only the *logged* values are rounded. Cross-machine byte-identity is *best-effort* (bge floats are machine-sensitive; a value can rarely straddle a rounding boundary). The `(-score, incoming_index)` tie-break and the single-machine M3b eval make this a non-issue in practice. (Pre-flagged in the replay spec §3.1.)

**Offload / eviction-regret deferred:** `PolicyTarget.decide` does NOT call `select_offload`, so `Decision.offloaded` is empty. Meaningful offload (and the §10.2 **eviction-regret** metric) needs a real active-window representation the replay engine does not feed (deferred to M4). M3b therefore measures onload quality (precision@k / nDCG / recall over `Decision.candidates`); eviction-regret moves to M4. `RelevancePolicy.select_offload` still exists and is unit-tested (the §10.1 pin-eviction property) — it just isn't wired into the arm-3 replay target yet.

## 4. Testing (§10.1 Layer-0, deterministic)

All property tests use a **deterministic embedder** — either `HashingEmbedder` or a tiny `FakeEmbedder` that returns fixed vectors for planted contents — so scores are exact and reproducible. `policy/` and `PolicyTarget` are tested without ever downloading bge.

- **`score` properties** (`tests/test_policy_relevance.py`):
  - a **pinned** chunk always clears `eviction_threshold` (never in `select_offload`) and outranks any non-pin in `select_onload`;
  - a **tag-matching** chunk outranks a non-matching one **when `w_tag>0`** (the term works; it's just off by default);
  - **recency_decay differentiates by rank** at default weights (newer outranks older when similarity is equal); and with `w_recency=0` (or two equal-sub-score pins) the **`incoming_index` tie-break** resolves a true score tie by recency deterministically (the only way to construct an exact tie);
  - **recency_decay is size-stable:** the rank-r chunk gets the same `recency_decay` for `N=5` and `N=500` (decay independent of pool size);
  - **similarity rescale:** a cosine at `sim_floor` maps to 0 and a cosine of 1.0 maps to 1.0; a pair of mid-band cosines (0.5 vs 0.7) is meaningfully separated after rescale;
  - a **semantically-near** chunk (planted close vector) outranks a far one;
  - **dim-mismatch re-embed:** a candidate whose stored embedding is 256-dim (HashingEmbedder) is re-embedded against a 384-dim active embedder without crashing and is scored;
  - **re-embed cap (C2/C-3):** with `reembed_cap=1` and two dim-mismatched candidates, exactly one is re-embedded and the other scores `similarity=0` — and it is the **newest** (recency-priority) that is re-embedded;
  - `select_onload` respects `k` and `token_budget` (first-fit);
  - `select_offload` returns exactly the sub-threshold non-pinned keys (and never a pinned key).
- **`Store.all_live_chunks`** (added to `tests/test_store_contract.py`, runs against memory + sqlite): returns ALL non-expired chunks in `seq DESC`, excludes expired, respects tenant scope, and is NOT limited by `k`/`token_budget`.
- **`PolicyTarget`** (`tests/replay/test_policy_target.py`): satisfies the `ReplayTarget` Protocol; `decide` returns a `Decision` with populated `score`s and the `candidates` pool, `offloaded == []`; **same trace twice → byte-identical `DecisionLog`** (single-machine determinism with the deterministic embedder); uses `all_live_chunks` (sees a chunk that a `k`-truncated `query` would have dropped); runs end-to-end through `ReplayEngine`.
- **`FastEmbedEmbedder`** (`tests/test_fastembed.py`, skip-on-model-construct-failure): returns a 384-dim unit vector (`‖v‖≈1`, confirming the normalize is idempotent); deterministic (same text → same vector); **cos-distribution probe (I3)** — a small set (≥4) of related and unrelated short-text pairs with a **concrete, slack-tolerant** assertion (round-3 #5): `max(unrelated_cos) < 0.5 < min(related_cos)` AND `min(related_cos) - max(unrelated_cos) > 0.1`. This validates that `sim_floor=0.5` sits between the bands rather than asserting it from prose; if it ever fails, the floor (not the test) is what changes.

## 5. File structure

```
src/context_curator/
  embeddings.py          # MODIFY: add FastEmbedEmbedder + _unit_normalize
  store/
    interface.py         # MODIFY: add all_live_chunks() to the Store ABC
    sqlite_store.py      # MODIFY: implement all_live_chunks (seq DESC, non-expired, scope)
    memory.py            # MODIFY: add TTL parity (expires_at + expiry filtering) + all_live_chunks
  policy/
    __init__.py
    weights.py           # PolicyWeights
    relevance.py         # RelevancePolicy
  replay/target.py       # MODIFY: add PolicyTarget (imports policy)
pyproject.toml           # MODIFY: add fastembed under [project.optional-dependencies].embed
DESIGN.md                # MODIFY: §6 policy-interface amendment (candidates source; signatures)
tests/
  test_policy_relevance.py
  test_fastembed.py
  test_store_contract.py # MODIFY: all_live_chunks contract test (both backends)
  replay/test_policy_target.py
```

## 6. How this connects forward

- **M3b** writes labeled task→chunk fixtures, runs `PolicyTarget` (arm 3) vs `RecencyOnlyTarget` (arm 2) through the replay harness over the fixtures, and computes **precision@k / nDCG / recall** from the decision logs (the `Decision.candidates` pool this design populates is exactly what nDCG needs) — the §10.4 keystone. **Eviction-regret is deferred to M4** (it needs real offload/active-window, which the replay engine does not feed). M3b then sweeps `PolicyWeights` (`w_*`, `decay_lambda`, `sim_floor`).
- **M4** wires `select_onload` into the `UserPromptSubmit` hook (inject the slice via `additionalContext`), feeds a real active-window summary to `select_offload`, and adds the **one-time store re-embed migration** that is the correct live fix for the dim-mismatch (the inline `reembed_cap` is only the transition fallback).

---

## Design Critique Log

Three independent adversarial review rounds (fresh reviewer each round, each seeing the prior revision) before presentation.

### Critique Round 1
**Findings (Critical):** the candidate-order contract was silently violated by `store.query`'s `k`/budget/expiry truncation, so the policy would score a recency-truncated slice and `recency_norm` over the wrong N (C1); per-prompt O(N) bge re-embedding of the whole legacy-`HashingEmbedder` candidate set blew the §4.2 latency budget on the live path while M3a tests stayed green (C2); `tag_match` rewarded tool-provenance (single tool-name tags) not topic, confounding the arm-2-vs-arm-3 eval (C3); a literal tie-break self-contradiction (`incoming_index` vs `key`) that flips a property test (C4). **Important:** rank-based recency was scale-mismatched with magnitude cosine and size-dependent (I1/I2); the §6 interface was edited without acknowledgement (I3); `Decision.offloaded` unpopulated so eviction-regret had no arm-3 data (I4); `fastembed` as a hard dep dragged onnxruntime into the lightweight install and the skip checked package not model (I5).
**Resolved:** added `Store.all_live_chunks()` as the full-live-set candidate source; replaced recency with size-stable `exp(-λ·rank)` and similarity with affine-floor rescale; bounded the re-embed (`reembed_cap`, then similarity=0); set `w_tag=0` default (tags kept but inert); single `incoming_index` tie-break; deferred eviction-regret to M4; `fastembed` → optional dependency group with a model-probe skip; §6 amended explicitly.

### Critique Round 2
**Findings (Critical):** `all_live_chunks`'s "non-expired" property was **untestable on `InMemoryStore`**, which has no TTL logic at all (C-1); the round-1 split made `decide` call `scored` AND `select_onload`, double-embedding + double-scoring + doubling the re-embed budget per prompt (C-2); `reembed_cap` "per call" × two calls = 2× the cap, with an unstated recency-priority bias (C-3). **Important:** full-store JSON-deserialize of every embedding per prompt (I-1); `all_live_chunks` needed `query`'s Python-side `is_within_scope` defence-in-depth + per-row expiry, not just SQL (security-critical, I-2); `sim_floor=0.3` contradicted the spec's own "unrelated cos ~0.3–0.7" (I-3); recency could still dominate via the ungrounded floor (I-4); `score(chunk, task_text)` was inconsistent across §6/spec/tests and implied per-chunk task embedding (I-5).
**Resolved:** restructured around a **single scoring pass** (`scored` → `pick`/`offload_keys`; `decide` scores once); gave `InMemoryStore` TTL parity; mandated the full defence-in-depth scope + per-row expiry in `all_live_chunks`; set `sim_floor=0.5` validated by a cos probe and framed defaults as provisional (M3b finalizes); internal `_score(chunk, task_emb, …)` with the task embedded once; noted the deserialize cost (BLOB optimization deferred).

### Critique Round 3
**Verdict: implementation-ready after a small must-fix list.** The reviewer verified the InMemory-TTL change does NOT break replay determinism (replay ingest uses `ttl_s=None` → wall-clock never reached) and the API surfaces now agree. **Important:** state explicitly that `scored` computes `recency_decay = exp(-λ·i)` from the incoming index, that the same index is the tie-break and the re-embed walk order (#1); and resolve that `InMemoryStore` can't call sqlite's row-shaped `_is_expired` — extract a shared `(created_at, ttl_s, pin)` helper (#2). **Minor:** a stale "(default 0.3)" (#3); pin down `pick`'s first-fit as **break** (matching arm-2) so arms differ only in ranking (#4); give the cos-probe a concrete numeric bar (#5); note combined onload+offload double-work is M4's (#6); make the store-first task ordering explicit (#7).
**Resolved:** all applied — explicit recency-rank wiring; shared `store/expiry.py::is_expired` helper (sqlite refactored to call it, InMemory uses it); `sim_floor` references unified to 0.5; `pick` break semantics pinned; concrete probe assertion (`max(unrelated)<0.5<min(related)`, gap>0.1); combined-use M4 note; three-step store→policy→target task ordering. No design decision changed.
