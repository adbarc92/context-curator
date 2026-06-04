# M5 — Cross-Turn Eviction-Regret Metric

## 1. Purpose
Add the **cross-turn eviction-regret** metric (DESIGN §10.2, reframed — see §3.1) to the eval
harness: how often the selection policy fails to **re-surface an older chunk that is needed again a
few turns later**. It is a new **evaluation lens** — old-gold re-selection recall over a multi-turn
accumulated pool — that isolates the *temporal re-query* failure mode, which the per-fixture
nDCG/recall of M4 (each fixture an independent single-turn decision) does not isolate.

**Honesty scope (round-2):** this milestone validates the metric's **computation and arm-ordering on
a constructed adversarial case**. Whether the metric **discriminates policies on real workloads** is
NOT established here — that is the deferred real-session question (§8), exactly the synthetic-validates-
mechanics / real-validates-conclusions split established in M4c/M4d.

**Deliverable (chosen scope):** the metric + a session-level replay over the three existing arms
(recency / BM25 / semantic) + small **hand-built multi-turn fixtures** with planted need-later labels
+ deterministic correctness tests. No production code changes. Applying the metric to *real* sessions
(deriving `needed_keys` from M4d's entity/re-fetch labeling) is a deferred follow-on, gated on M4d
(#8) merging.

## 2. Scope
**In scope:** `eval/eviction_regret.py` (a `Session`/`SessionTurn` schema, a `RegretReport`, and the
`eviction_regret(...)` evaluator that replays sessions turn-by-turn through the existing
`RecencyOnlyTarget` / `Bm25Target` / `PolicyTarget`); hand-built fixtures; deterministic tests.

**Out of scope (deferred):** real-session regret numbers; an LLM-generated multi-turn corpus; any
production/curator change; wiring regret into `run_keystone` (kept standalone — the keystone is
deliberately per-fixture).

## 3. The regret definition (pinned)
- A **`Session`** = ordered turns `t = 0..N`. Each turn introduces 0+ new chunks (keyed) and carries
  a `prompt` + planted **`needed_keys`** (the available chunks genuinely required for that turn).
- **Available at `T`** = all chunks introduced at turns `≤ T`. A chunk is introduced exactly once, at
  the first turn it appears (enforced — §4 validation).
- **`Surfaced(T)`** = `{c.key for c in target.decide(prompt_T, store_of_available).candidates[:k]}` —
  the arm's full ranking, truncated to the top-`k` window (`k`-uniform across all arms).
- **`age(C, T)`** = `T − introduced_turn(C)`.
- **Old need-event** = `(T, C)` with `C ∈ needed(T)`, `C` available at `T`, and `age(C, T) ≥ lag`
  ("re-queried a few turns later").
- **Regret event** = an old need-event where `C ∉ Surfaced(T)` — the policy let an older-but-needed
  chunk slip out of the window.
- **`eviction_regret = |regret events| / |old need-events|`** ∈ `[0, 1]`, **micro-averaged** across
  all sessions: `rate = Σ regret_events / Σ old_need_events` over every session (a zero-need session
  adds `(0, 0)` — contributes nothing). An all-zero corpus → `None` (undefined, not 0). `lag` is a
  `≥` bound, so `lag = 0` counts every need-event as old.
- **Pinned parameters:** `lag = 2`; `k = 5` (the surfaced-window size — a regret fixture MUST make
  `≥ k + 1` chunks available at the regret turn so eviction is actually forced). Both are arguments
  with these frozen defaults for the committed tests.
- **Pinned contract (round-1 I1):** the metric reads `decision.candidates` as the arm's **FULL ranked
  pool**. Verified for all three arms: `RecencyOnlyTarget.candidates` = all live chunks newest-first;
  `Bm25Target`/`PolicyTarget.candidates` = the full scored pool (only `selected` is truncated to the
  arm's own internal k, which the metric ignores). A test asserts `len(candidates) == n_available`
  per arm so a future truncation of `candidates` cannot silently corrupt the metric.

### 3.1 Why this is faithful — and distinct from per-fixture recall@k (round-1 I3)
The evaluator rebuilds the store from the full available set each turn and the policy is **memoryless**
— and that is **not a modeling shortcut, it is the production architecture**: the SQLite store
*persists everything* and the onload gate *re-selects* a slice each turn (DESIGN §4.2/§11 — per-chunk
eviction does **not** exist in the CLI; "offload" = persist-so-compaction-can-drop-it + re-select). So
eviction-regret here is **not** a persistent-window-with-TTL phenomenon; it is **re-selection
failure**: at the turn an old chunk is needed again, did the gate's top-`k` re-surface it from the
**accumulated** pool of all prior context?

**Supersedes the §10.2 wording (round-2 I-A).** DESIGN §10.2 *defines* regret via "an **evicted key**
re-queried a few turns later," but §11 established that **per-chunk eviction does not exist in the
CLI**. This metric therefore redefines regret as **re-selection failure** on a persist-all store;
the §10.2 "evicted key" phrasing is counterfactual and is superseded here.

**What is actually new vs M4's recall@k (honest, round-2 I-B).** Mechanically, at a regret turn this
*is* recall-failure@k for the old gold over the candidate pool. The **only metric-level novelty is the
`age ≥ lag` filter** — it scores re-selection *only* for chunks that have been around a while (the
temporal re-query case), which per-fixture recall (every fixture an independent fresh decision) never
isolates. The multi-turn `Session` is an **authoring convenience** that accumulates a pool larger than
`k` and supplies the `introduced_turn` bookkeeping needed to compute `age`; it is *not* a new
retrieval mechanism, and the metric only ever reads top-`k` membership, so "pool growth" beyond
`n_available > k` carries no extra weight. The value is the **lens** (old-gold re-selection recall),
not a new algorithm.

## 4. The evaluator
`eviction_regret(sessions, target, embedder, *, k=5, lag=2) -> RegretReport`:
- **Validate first (raise `ValueError`):** every `needed_key` across all turns must be introduced by
  some turn's `new_chunks`; no key may be introduced by more than one turn. (A chunk MAY be needed at
  multiple turns — its age differs per turn.) If `target` is a `PolicyTarget`, assert
  `target.embedder is embedder` (replicates `runner.evaluate`'s store/policy embedder-identity
  invariant, which the M5 evaluator otherwise bypasses).
- Per session: maintain `available: list[FixtureChunk]` (chronological) and
  `introduced_turn: dict[key,int]`. At each turn `T`, append the turn's `new_chunks` (recording intro
  turn), build a fresh `InMemoryStore(embedder=embedder)`, `store.store(c.key, c.content, tags=c.tags,
  ttl_s=None)` for every available chunk (insertion order = chronological → recency well-defined),
  then `decision = target.decide(TaskSignal(turn_index=T, prompt=turn.prompt, subtask_id=None,
  recent_tool_calls=[]), store)`. (`embedder` is used only to build the store; `RecencyOnlyTarget`
  and `Bm25Target` ignore embeddings, so it matters solely for the semantic arm — round-2 M-C.)
- `surfaced = {c.key for c in decision.candidates[:k]}`. For each `key` in `turn.needed_keys` with
  `key` available and `age(key, T) ≥ lag`: `old_need += 1`; if `key not in surfaced`: `regret += 1`.
- Returns `RegretReport` (dataclass, fields: `rate: float | None`, `regret_events: int`,
  `old_need_events: int`, `arm: str`), with `rate = regret/old_need if old_need else None`,
  `arm = target.name`. Determinism rests on the arms' pinned tiebreakers
  (recency: strict `_seq`; BM25: `(-score, key)`; policy: stable `incoming_index`).

## 5. Hand-built fixtures (a plumbing / sign test — NOT a validated discrimination claim)
**Round-2 I-C disclaimer:** these fixtures are *constructed-adversarial* — `stale-auth` places the
gold as the single oldest chunk with ≥5 fresher fillers, so a recency arm buries it **by
construction**, regardless of whether recency is a good policy for any real workload. So this test
proves the metric **computes regret correctly and orders the two arms on this constructed case** — it
does NOT establish that the metric discriminates policies on real sessions (that is the deferred §8
work). It is a unit test of the metric's plumbing + sign, nothing more.

Authored inline in the test, **all content in the `KeywordEmbedder` vocab `A B C D E F`** with
**disjoint gold/filler partitions** so the arithmetic is guaranteed (round-1 C1/C2/C3):
- **`stale-auth`** (lag 2, k 5):
  - turn 0: introduces `gold`, content `"A B C"`.
  - turn 1: introduces fillers `f1,f2,f3`, content `"D E F"`.
  - turn 2: introduces fillers `f4,f5,f6`, content `"D E F"`.
  - turn 3: prompt `"A B C"`, `needed_keys=["gold"]`. `age(gold,3)=3 ≥ lag`. Available = 7 chunks.
  - **Recency arm:** candidates newest-first `[f6,f5,f4,f3,f2,f1,gold]`; `gold` at position 7, outside
    top-5 → **regret = 1/1 = 1.0**.
  - **Semantic arm** built as `PolicyTarget(RelevancePolicy(emb))` where `emb = KeywordEmbedder()` is
    the **same instance** passed as the evaluator's `embedder` arg (so the §4 `target.embedder is
    embedder` assert passes). With default PolicyWeights `w_sim 0.65 / w_rec 0.35
    / decay 0.1 / sim_floor 0.5`): `gold` cos(`A B C`,`A B C`)=1.0 → affine-rescaled sim 1.0 → sim
    term 0.65; recency term at oldest ≈ `0.35·exp(-0.1·6) ≈ 0.19`; total ≈ **0.84**. Each filler:
    cos(`D E F`,`A B C`)=0 → sim 0; recency term ≤ `0.35·exp(0)=0.35`; total ≤ **0.35**. So `gold`
    ranks #1, inside top-5 → **regret = 0/1 = 0.0**. The disjoint vocab guarantees fillers contribute
    zero similarity, so `gold` cannot be displaced regardless of filler count.
- **`no-old-needs`**: a session whose every `needed_keys` entry is a chunk introduced *that* turn
  (age 0 < lag) → zero old need-events → contributes nothing; a corpus of only this session → `None`.

## 6. Testing
Fully deterministic, **no bge** — the semantic arm uses the existing `KeywordEmbedder` test double
(`tests/eval/conftest.py`), CI-stable and float-free. Assert:
- on `stale-auth`: `RecencyOnlyTarget` → `rate == 1.0` and `arm == "recency-only"`; semantic →
  `rate == 0.0` and `arm == "semantic-policy"` (pins the `arm` field too); hence recency `>` semantic
  on this constructed case.
- **contract test:** for each arm on `stale-auth`, `len(decision.candidates) == n_available` at the
  regret turn (guards the §3 full-pool invariant).
- `no-old-needs` corpus → `rate is None`, `old_need_events == 0`.
- **parameters:** `lag = 4` makes the age-3 need no longer old → `old_need_events == 0` and
  `rate is None` (excluded);
  `k = 7` (≥ available) admits the buried chunk → recency `rate == 0.0` (confirms `k` is the window).
- **validation:** a `needed_key` never introduced → `ValueError`; a key in two turns' `new_chunks` →
  `ValueError`.
- **smoke:** `Bm25Target` returns a valid `RegretReport` without error (rate depends on lexical
  overlap; not pinned to a value); a session with no chunks / no needs does not throw (empty
  `InMemoryStore` + `decide` → `rate is None`). Round-2 M-B.

## 7. File structure
- **New:** `src/context_curator/eval/eviction_regret.py` —
  `SessionTurn(prompt: str, new_chunks: list[FixtureChunk] = [], needed_keys: list[str] = [])`,
  `Session(name: str, turns: list[SessionTurn])` (pydantic v2; keep to exactly these fields — no
  `session_id`/`metadata` until the deferred real-session app needs them, round-3 minor),
  `RegretReport` (dataclass: `rate: float | None`, `regret_events: int`, `old_need_events: int`,
  `arm: str`), `eviction_regret(...)`.
- **New:** `tests/eval/test_eviction_regret.py` — hand-built sessions + the §6 assertions.
- **No changes** to production code, the curator, `run_keystone`, or existing fixtures.

## 8. Dependency / branching
Bases on **`main`**. Uses only `InMemoryStore`, the replay targets, `TaskSignal`, and `FixtureChunk`
— all on `main` (pre-M4d). No stacking on the unmerged M4d branch (#8). Real-session application
(reusing M4d's `real_corpus` entity/re-fetch labeling to derive `needed_keys`, and the `Session`
schema authored here) is a deferred follow-on once #8 lands — which is why the schema is formalized
now rather than inlined.

## Design Critique Log

### Critique Round 1
Independent critic found the discrimination demo would FAIL as written, plus validity/edge gaps:
- **C1/C2/C3 (Critical):** the `KeywordEmbedder` test double only embeds vocab tokens `A–F`, so the
  prose fixture content (`"JWT refresh-token rotation"`) → zero vector → the semantic arm collapses to
  recency → `regret=1.0`, not 0.0; and the recency `1.0` needs ≥5 fillers strictly after the gold
  chunk. **Resolved:** §5 rewritten in disjoint `A B C` (gold/prompt) vs `D E F` (fillers) vocab with
  6 pinned fillers and the explicit score arithmetic (gold ≈0.84 vs fillers ≤0.35 → gold #1).
- **I1 (Important):** `candidates` being the full pool is incidental across three classes.
  **Resolved:** pinned as a §3 contract + a test asserting `len(candidates)==n_available`.
- **I2 (Important):** the store/policy embedder-identity invariant lives in `runner.evaluate`, which
  the M5 evaluator bypasses. **Resolved:** §4 replicates `assert target.embedder is embedder` for
  `PolicyTarget`.
- **I3 (Important):** rebuild-each-turn + memoryless policy means no persistent-window mechanism, so
  the metric is mechanically "old-gold recall@k on the accumulated pool." **Resolved by honest
  reframe (§3.1):** memoryless re-selection from a persist-all store *is* the CLI architecture
  (§4.2/§11); the distinct ingredients vs per-fixture recall are the age filter + cross-turn
  accumulation (growing haystack), not a window-with-TTL claim.
- **I4 (Important):** edge cases. **Resolved:** §4 validates every `needed_key` is introduced and
  rejects duplicate-key re-introduction; documents same-chunk-needed-at-multiple-turns; states the
  per-arm tiebreakers for determinism.
- **M2 (Minor):** the pydantic `Session` schema is mildly heavy for a metric — kept, justified by the
  deferred real-session application (§8) that reuses it.

### Critique Round 2
Critic re-executed the real formula and **confirmed the round-1 arithmetic holds** (gold ≈0.842 via
sim term 0.65 + recency 0.192; each filler ≤0.35; recency index is newest-first so the oldest gold
gets the smallest recency term; sort highest-first; no k-boundary off-by-one). No Critical. Three
Important, all honesty/framing (no code/arithmetic change):
- **I-A:** §3.1 silently redefined DESIGN §10.2's "evicted key." **Resolved:** §3.1 now states it
  supersedes that wording given §11 (no CLI eviction).
- **I-B:** the "growing haystack / cross-turn accumulation" distinction was over-claimed (with `k`
  fixed and only top-`k` membership read, accumulation = `n_available > k`). **Resolved:** §1 + §3.1
  reframed — the metric is mechanically old-gold recall-failure@k; the **only** novelty is the
  `age ≥ lag` filter; the multi-turn `Session` is an authoring/bookkeeping convenience, not a new
  mechanism. The value is the *lens*, not an algorithm.
- **I-C:** the `stale-auth` fixture is constructed-adversarial (recency buries the oldest gold by
  construction), so it proves computation + ordering on one case, not general discriminative power.
  **Resolved:** §1 honesty-scope + §5 retitled "plumbing / sign test" with an explicit disclaimer
  that real-workload discrimination is the deferred §8 question.
- **Minors:** §3 micro-average formula made explicit (`Σregret/Σold_need`; zero-need adds `(0,0)`;
  `lag=0` counts all); §4 notes the embedder is store-construction-only for non-semantic arms; §6
  adds an empty-session smoke (must not throw).

### Critique Round 3
Verdict: **PROCEED**. Critic re-verified against actual code — the arithmetic (gold 0.842 vs filler
≤0.35), the three-arm full-pool `candidates` contract, and the `k=7`/`lag=4` edges all hold; the
replay plumbing matches the established `keystone._ndcg_per_fixture` pattern; `arm=target.name`
resolves. No Critical or Important. On "is the honest reframe now a trivial wrapper?" the critic
affirmed it is not: per-fixture recall@k structurally *cannot* express "alive ≥lag turns and needed
again" (no `introduced_turn` axis), so the age filter is a genuinely new *measurement*, not a
reparameterized knob — worth a standalone metric + sign test. Five Minor spec-completeness items
folded in: §4/§7 list the `RegretReport` dataclass field types; §5 states the
`PolicyTarget(RelevancePolicy(emb))` construction + shared-instance requirement; §6 adds `rate is
None` to the `lag=4` case and pins the `arm` field; §7 trims `Session`/`SessionTurn` to exactly the
needed fields (no speculative `session_id`/`metadata`).
