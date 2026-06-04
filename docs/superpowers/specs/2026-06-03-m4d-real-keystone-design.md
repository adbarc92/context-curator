# M4d — Real-Data Keystone Harness + Contingent Flip Decision

## 1. Purpose
Build the **real-data evaluation harness** that can decide whether the bge onload gate beats cheap
baselines on real Claude Code transcripts, and run it. M4d is the real-data counterpart to the M4c
synthetic keystone (`docs/superpowers/keystone-powered.md`: NEGATIVE-powered, +0.056 < +0.10 MEI).

**Honest framing (set by 3 critique rounds): M4d's primary deliverable is the harness + an honest
verdict — which may well be "INCONCLUSIVE — insufficient independent sessions."** On a single
developer's local corpus the true sample size is **n_sessions** (not n_turns), likely a handful, so
a conclusive flip-or-decommission is *not guaranteed*. The production flip is **contingent**: it
fires only if a pre-registered minimum-sessions precondition (§5.4) is met **and** the verdict is
GREEN **and** the safety gates (§6.1) pass — otherwise M4d ships the harness + the honest finding,
and the flip/decommission decision is explicitly deferred.

## 2. Scope
**In scope (the real deliverables):**
1. **Entity + re-fetch extractor** (§3.3) — a first-class, separately-tested module; the captured
   store does NOT persist tool-call paths, so this works from the raw `Trace`.
2. **`eval/real_corpus.py`** — harvest per-turn fixtures with downstream-use gold + `session_id`.
3. **`Fixture.session_id`** (schema add) and a **keystone seam** that threads per-fixture session ids
   into the delta vector.
4. **`stats.cluster_bootstrap_ci`** — session-clustered CI (the true-N statistic) + the §5.4
   **self-correcting precision gate** (verdict-or-needed-N).
5. **Adapted/diagnostic `corpus_audit.py`** + a **lexical-bias diagnostic** (§5.2, symmetric).
6. **Flag-on regression tests** (deferred from M4b/M4c).
7. The real keystone run + the contingent flip / framework (§6).

**Out of scope (deferred):** the decommission *removal* (separate, sign-off-gated); threshold
re-tuning loops; a second (assistant-reference) gold signal unless §5.4 forces it; M5+.

## 3. Corpus harvest + downstream-use gold (centerpiece)
Approach A — offline, deterministic, a pure function of each `Trace`.

### 3.1 Parse
`parse_transcript(path) -> Trace` (exists; sidechains excluded, §4.4). Run over the chosen local
projects' `.jsonl`. **Each fixture records its source `session_id`** for the cluster bootstrap.

### 3.2 Per-turn fixtures
Decision point = a user-prompt turn `T` with ≥1 prior captured chunk.
- **candidates** = events strictly before `T` replayed into an `InMemoryStore` → `all_live_chunks()`
  (insertion order = transcript chronological order; recency arm well-defined; `_seq` = replay order).
- **prompt** = `T`'s user text. **The eval task text is the prompt ONLY** — `recent_tools` is NOT
  embedded, so all three arms (`PolicyTarget`, `Bm25Target`, `RecencyOnlyTarget`) score the *same*
  input. (Resolves the round-2 asymmetry where only the semantic arm saw tool names.)

### 3.3 Entity + re-fetch extractor and downstream-use gold (PRE-REGISTERED, frozen before the run)
**This is a first-class deliverable, not an assumption** — captured chunks store only content + tool
name (`capture_tool_result`), NOT the producing call's path. But the chunk **key** is
`session:{sid}:tool:{ordinal:06d}:{call_id}`, so the producing `ToolCall` is recovered by a **direct
`call_id` lookup** into the `Trace` (`ToolCall.args` retains the full tool `input`, incl.
`file_path`/`path`/`notebook_path`) — no fragile order-reconstructed index needed. Entities are
extracted from that call's args.

- **Entity extraction (pinned):** a chunk's entities = canonicalized **absolute file paths** from its
  producing call — `Read`/`Edit`/`Write`/`NotebookEdit` `file_path`/`notebook_path`, `Grep`/`Glob`
  `path`. Pattern-only `Glob` (no `path`) and path-less calls yield no entity. Bash: path-like tokens
  (secondary; flagged in the manifest as lower-confidence).
- **Path-equivalence (pinned):** entity A matches entity B iff their canonical absolute paths are
  equal, **or** one is a directory containing the other's file (a later `Grep path=/a` re-fetches an
  earlier `Read file_path=/a/b.py`). Pattern-only Glob never matches. Each tool-pair equivalence is
  unit-tested.
- **Gold rule:** candidate `C` is **gold for `T`** iff `C`'s entity is **re-fetched** by a
  *retrieval-type* call (`Read`/`Grep`/`Glob`/`NotebookRead`) within `[T, T+W]`, `W=5`, **excluding a
  re-`Read` that immediately follows an `Edit`/`Write` of the same entity** (that's verify-after-edit,
  not "needed-but-absent" — resolves the round-2 verify-Read confound).

**Honest proxy caveat (frozen into the verdict claim):** re-fetch gold is a **lower-bound, biased
proxy** for relevance — it *under-counts* context the agent used while it was still in working memory
(no re-fetch), and is *enriched* for recency-distant content that fell out and was re-fetched. M4d's
claim is therefore scoped to **"ranking context that left working memory and was re-fetched,"** not
the unqualified "best context selection." §1/§6 verdicts inherit this scope.

### 3.4 Keep / drop + split
Keep iff **≥1 gold** and **≥5 candidates**. **Split by whole session** (~1:3 train:test; no session
straddles splits — prevents leakage). The §5.5 selection-bias report is mandatory.

### 3.5 W-sensitivity (honest)
Report W ∈ {3,5,8} as a **sensitivity** check, each on **its own keep/drop membership** (gold is
W-dependent, so membership legitimately changes) — *not* claimed as a frozen-membership robustness
check. Headline uses W=5.

## 4. Privacy & artifact policy (§9)
- **Local-only, gitignored:** `src/context_curator/eval/fixtures/_real_local/` (`.gitignore` += it).
  Transcript content never committed/sent; Context7 gets only library names + topics.
- **Committed (no content):** verdict doc with aggregates only — per-arm nDCG; the **session-clustered**
  CI; **n_test AND n_sessions**; per-domain breakdown; the §5.2 lexical-bias number; the §5.5
  selection-bias report; pre-registered config (W, retrieval-tool list, entity/equivalence rules);
  reproducibility manifest of transcript **sha256 + local path**.
- **Shipped threshold (GREEN only):** the **numeric** swept `ONLOAD_BGE_COSINE_THRESHOLD` + recall-floor
  + per-cell CIs are committed (decision-reproducible). Cosine source (pinned): bge cosine of each
  **train-split** gold chunk to its prompt `T`. bge floats are machine-sensitive → the value ships with
  a machine/version stamp and is labeled a decision artifact, not a portable constant.

## 5. Methodological guards

### 5.1 Bias direction is MEASURED, not assumed (replaces the round-1 asymmetry doctrine)
The round-2 critique showed the "path-gold handicaps bge" claim is unproven and possibly false (the
re-fetch path need not appear in prompt `T`; the re-fetched file's *content* may favor bge). So M4d
**measures** the direction on the pilot and reports it: does gold's path-token overlap with prompts
exceed gold's content-term overlap? **The verdict table (§6) is SYMMETRIC** — no GREEN-trustworthy /
NEGATIVE-suspect asymmetry — unless this measurement establishes a clear direction, in which case the
direction (and its effect on interpretation) is recorded as a caveat, not baked into the rule.

### 5.2 Lexical-bias diagnostic (symmetric, a-priori threshold)
Report **BM25's own recall@k (k=3) on the gold set** beside every verdict. Pre-register the cutoff
**from a stated principle, before the pilot** (not pilot-tuned): gold is "lexically degenerate" iff
`BM25_R@3(gold) ≥ BM25_R@3(control) + 0.15` (absolute), where the **control** = a per-fixture random
sample of non-gold chunks of the **same count as that fixture's gold**, seeded for reproducibility.
The `+0.15` margin and the control's match dimension (count) are pinned in §5.7. If degenerate, the
verdict is downgraded to **INCONCLUSIVE** (can't separate method quality from labeling artifact).
Diagnostic, symmetric, recorded.

### 5.3 Session-cluster bootstrap (the true-N statistic) — fully contracted
**New `stats.cluster_bootstrap_ci(deltas, cluster_ids, *, seed, alpha=0.1, iters=2000) ->
(lo, hi)`**: resample whole sessions with replacement (then all of a resampled session's deltas).
Contract (pinned + tested):
- `len(deltas) != len(cluster_ids)` → `ValueError`.
- `n_sessions == 0` → `(0.0, 0.0)` (matches `bootstrap_ci`'s empty case).
- `n_sessions == 1` → **`(-inf, +inf)`** (width-of-ignorance): a single cluster carries no
  between-session information; it must NOT return a width-0 false-"powered" CI.
- single-fixture sessions are fine (a cluster of size 1).
The keystone is extended to emit per-fixture `session_id` alongside `per_fixture_ndcg_delta`; **every
CI in §6 is this clustered CI.** The verdict reports **n_sessions** as the true sample size.

### 5.4 Self-correcting precision gate (replaces a fixed minimum-sessions guess)
A fixed `MIN_SESSIONS` is an arbitrary guess — nobody can know up front whether N is "enough." Instead
the gate is **data-driven and self-correcting**: the session-clustered CI already widens exactly when
the corpus carries too little independent information, so let *its width* decide, and have the harness
report how much more data closes the gap.
- **Definitional floor:** `n_sessions ≥ 3`. Below this the clustered bootstrap cannot estimate
  between-session variance (`n_sessions==1`→`(-inf,+inf)`; `==2` is degenerate). This is a hard
  *definitional* minimum, not a power guess.
- **Precision gate (the real rule, pre-registered):** a substantive verdict (GREEN / any NEGATIVE
  branch / baseline-wins) is rendered ONLY if the clustered 90% CI is precise enough to *place* the
  effect against both decision boundaries (0 and MEI) — operationally **`(hi − lo) ≤ MEI`** (width
  ≤ 0.10). Wider than that → **INCONCLUSIVE-underpowered**, regardless of the point estimate. This
  ties "enough data" to the achieved estimate precision, not to a session count anyone had to guess.
- **Self-correcting feedback (the mechanism):** on INCONCLUSIVE-underpowered the harness reports the
  **additional sessions needed** to hit the precision target. Since clustered CI width scales
  ≈ ∝ 1/√n_sessions, `needed_n ≈ ceil(n_sessions × ((hi − lo) / MEI)²)` (first-order; assumes the
  between-session variance is roughly stable as sessions are added — stated as an estimate, not a
  promise). The verdict doc records "have X sessions; precision target needs ~Y; capture ~(Y−X) more
  and re-run."
- **M4d is re-runnable/incremental:** each run consumes whatever sessions exist and *either* renders a
  verdict *or* emits a concrete needed-N ask; adding sessions and re-running converges. On a small
  local corpus the expected first outcome is INCONCLUSIVE-underpowered with a needed-N — that is the
  designed behavior and the honest deliverable, not a failure.

### 5.5 Selection-bias report
The ≥1-gold filter keeps file-path-centric, revisit-heavy tasks and drops pure-reasoning / first-try
turns. **Mandatory:** report drop rate + dropped-vs-kept characterization (prompt length, tool mix,
path-density). The flip recommendation is **scoped to the represented task class**; large/systematic
drops narrow a GREEN to a documented opt-in for that class, not a blanket default-on.

### 5.6 Audit is diagnostic on real data
Real corpora can't be rebalanced. `audit_corpus` becomes **diagnostic**: reports the gold-recency-third
histogram + a **pinned** lexical-distractor fraction; **degenerate recency (gold clusters at the
front) downgrades the verdict toward INCONCLUSIVE with a recorded caveat — never silent data-dropping
to "pass."** `hard_neg` count becomes an optional flag (off for real corpora; on preserves M4c).

### 5.7 Pinned pre-registration constants (frozen before the run)
| Constant | Value | Where used |
|---|---|---|
| `MEI` | +0.10 nDCG | §6 verdict |
| CI | two-sided 90%, session-clustered | §5.3, §6 |
| `W` (forward window) | 5 turns | §3.3 gold |
| session definitional floor | `n_sessions ≥ 3` | §5.4 |
| precision gate (verdict requires) | clustered CI width `(hi−lo) ≤ MEI` (0.10) | §5.4 |
| needed-N feedback | `ceil(n_sessions × ((hi−lo)/MEI)²)` | §5.4 |
| lexical-bias margin | +0.15 absolute BM25 R@3 over control | §5.2 |
| lexical-bias control | per-fixture random non-gold, same count as gold, seeded | §5.2 |
| min candidates / min gold per kept turn | ≥5 / ≥1 | §3.4 |
| recency-third min fraction (diagnostic) | 0.20 (as M4c) | §5.6 |
| keep/drop & sweep split | by whole session, ~1:3 train:test | §3.4, §4 |

These are revisable only with justification documented *before* the effect is examined.

## 6. Decision & flip mechanics
Pre-registered (§5.7): **MEI = +0.10 nDCG**, two-sided **90% session-clustered CI** (§5.3). `m` = mean
per-fixture delta (semantic − max(recency,BM25)); `[lo,hi]` = clustered CI; `L` = §5.2 lexical-bias
("degenerate" trips). **Gating order (§5.4):** if `n_sessions < 3` → harness-only, no verdict. Else
if the clustered CI width `(hi − lo) > MEI` → **INCONCLUSIVE-underpowered** + the needed-N ask (no
flip/decommission). Only when `n_sessions ≥ 3` AND width `≤ MEI` is the table below applied.

| Outcome | Condition | Action |
|---|---|---|
| **INCONCLUSIVE (bias)** | `L` degenerate | Keep dark; corpus lexically degenerate — capture less path-centric tasks. No flip/decommission. |
| **GREEN** | `lo > 0`, `m ≥ 0.10`, `L` clear, **§6.1 gates pass** | Flip: change the default in `curator/config.py:23` `os.environ.get("CC_CURATOR_ONLOAD", "0")` → `"1"` (feeds the bool `CURATOR_ONLOAD_ENABLED`); set the numeric `ONLOAD_BGE_COSINE_THRESHOLD` in `policy/weights.py` to the swept value (§4); ship flag-on tests; staged/kill-switchable rollback. Scope per §5.5. |
| **NEGATIVE — retune** | `lo > 0`, `m < 0.10`, `hi ≥ 0.10`, `L` clear | Keep dark; recommend threshold/weight retune + revisit. |
| **NEGATIVE — decommission** | `lo > 0`, `hi < 0.10`, `m < 0.05`, `L` clear | Keep dark; **recommend decommission**; bring numbers to user to **confirm before removal**. |
| **NEGATIVE — middling** | `lo > 0`, `hi < 0.10`, `m ≥ 0.05`, `L` clear | Keep dark + retune; lean revisit. |
| **baseline wins** | `hi < 0`, `L` clear | Keep dark; recommend decommission (sign-off gated). |
| **INCONCLUSIVE** | `lo ≤ 0 ≤ hi` | Keep dark; gather more sessions (the likely small-corpus outcome). |

### 6.1 GREEN safety gates (a ranking win is necessary, not sufficient, for a global default flip)
Before flipping a default ON for all users, GREEN additionally requires:
- The eval ran the **production-capped path** (`ONDEMAND_EMBED_CAP=12`, `reembed_cap=0`), not just the
  fully-embedded path — closing the M4c eval/production gap; if only the uncapped path was measured,
  GREEN is downgraded to "tune threshold, keep dark."
- A minimal **performance/robustness check**: warm-daemon p95 within budget; recency-fallback quality
  delta bounded and recorded.
- The flip ships **reversible-by-default** (fast kill-switch / staged), not just a documented env var.

Decommission removal is **never automatic** — always sign-off gated (irreversible, subsystem-wide).
Flag-on regression tests ship **regardless of verdict**; per the M4c round-3 contract they assert a
**known-irrelevant chunk is EXCLUDED** by the gate (not the self-fulfilling "gold is selected"), plus
warm-daemon handshake and recency-fallback on empty, via monkeypatched config.

## 7. Testing
- **Deterministic (no bge/real data):** `tests/eval/test_real_corpus.py` on a committed hand-built
  `tests/eval/_traces/sample.jsonl` (no private content): candidate construction; **entity extraction
  + each tool-pair path-equivalence** (Read↔Grep-dir, pattern-Glob no-match); the retrieval-only W=5
  gold rule (**an edit/run must NOT create gold; a verify-Read-after-Edit must NOT; a true re-Read
  must**); keep/drop; `session_id` provenance. `cluster_bootstrap_ci` tests (clustered CI ≥ iid CI on
  the same deltas; **n_sessions=1 → (-inf,+inf)**; length-mismatch raises). **Precision-gate tests:**
  width > MEI → INCONCLUSIVE-underpowered with the needed-N formula; width ≤ MEI → table applied;
  n_sessions < 3 → harness-only. Adapted `corpus_audit`
  tests (optional `hard_neg`, pinned distractor fraction, degenerate-recency downgrade).
- **Flag-on regression:** `tests/curator/test_onload_flag_on.py` (known-irrelevant-excluded contract).
- **Real keystone run:** bge + real data, CI-skipped like M4c's bge tests; produces the verdict doc.

## 8. File structure
- **New:** `eval/real_corpus.py` (incl. the entity/re-fetch extractor, or split to `eval/entities.py`
  if it grows); `tests/eval/test_real_corpus.py` + `tests/eval/_traces/sample.jsonl`;
  `tests/curator/test_onload_flag_on.py`.
- **Modify:** `eval/fixtures.py` (+`session_id`); `eval/keystone.py` (thread per-fixture `session_id`
  into the delta output — a scoped change, overriding §-earlier "unchanged"); `eval/stats.py`
  (+`cluster_bootstrap_ci`); `eval/corpus_audit.py` (optional `hard_neg`, pinned distractor, degenerate
  downgrade); `.gitignore`.
- **GREEN-only:** `curator/config.py` (flip the `"0"` default in the `CC_CURATOR_ONLOAD` env read) +
  `policy/weights.py` (set the numeric `ONLOAD_BGE_COSINE_THRESHOLD` constant to the swept value).
- **Docs:** `docs/superpowers/keystone-real.md` (verdict of record, aggregate-only).
- **Local-only (gitignored):** `eval/fixtures/_real_local/`.

## 9. Privacy boundary (restated)
Real transcript content is local-only; Context7 receives only library names + topics; nothing under
`_real_local/` is committed/transmitted.

## Design Critique Log

### Critique Round 1
Three Criticals + four Importants, all tracing to the M4c root (any proxy in the gold loop relocates
circularity): **C1** path-entity gold is lexical → inflates the BM25 arm; **C2** "re-acted-on"
measures file churn, not relevance; **C3** iid bootstrap invalid on autocorrelated intra-session
turns. Plus recency degeneracy, selection bias, audit-can't-rebalance, threshold reproducibility.
Initial fixes: an asymmetry doctrine, a gold↔BM25 alignment guard, a session-cluster bootstrap,
retrieval-only gold, diagnostic audit, train-only sweep.

### Critique Round 2
Found the round-1 fixes were partly unbuilt or unsound: **C1-seam** the entity/re-fetch extraction
does not exist in the code (chunks store no paths) and was assumed, not specified; **C2-seam** the
`Fixture.session_id`→keystone→`cluster_bootstrap_ci` plumbing was unthreaded and the change-list
omitted it, with no contract for n_sessions∈{0,1}; **C3-power** n_sessions is the true N, so a small
local corpus almost certainly yields INCONCLUSIVE → the milestone risked being undeliverable as a
flip; **I4** the "counterfactual" justification for re-fetch gold is unsound (verify-after-edit
re-reads) and under-counts in-context-used content; **I5** the asymmetry doctrine's bias direction is
asserted, not established (and possibly favors bge), and its alignment threshold was pilot-tunable;
**I6** GREEN flips a global default on nDCG alone, inheriting the M4c capped-vs-uncapped gap and a
`recent_tools` input asymmetry.
Resolved by the present rewrite: entity/re-fetch extraction made a **first-class tested deliverable**
with pinned path-equivalence (§3.3) + verify-Read exclusion; **`Fixture.session_id` + keystone seam +
fully-contracted `cluster_bootstrap_ci`** (n_sessions=1→(-inf,+inf)) added to scope/§8; a
**pre-registered `MIN_SESSIONS` precondition** with M4d reframed as **harness-first, flip-contingent**
(§1,§5.4); the asymmetry doctrine **dropped** for a **measured bias direction + symmetric table**
(§5.1) and an **a-priori, principled lexical-bias diagnostic** (§5.2); gold re-scoped as a
**lower-bound biased proxy** with a narrowed verdict claim (§3.3); **GREEN safety gates** (capped path,
perf/robustness, reversible rollout) + the `recent_tools` asymmetry removed (§3.2, §6.1).

### Critique Round 3
Verdict: **PROCEED** — no Critical or blocking issues. The make-or-break feasibility check passed:
the entity extractor has its raw material (`ToolCall.args` retains the tool `input`; the producing
`call_id` is embedded in the chunk key, so chunk→ToolCall is a direct lookup — the design even
*under*-claimed this), and the infinite-CI (`n_sessions==1`) path maps cleanly to INCONCLUSIVE via the
table's last row. The round-2 reframe (harness-first, contracted cluster bootstrap, symmetric table,
measured bias, safety gates) holds up. Remaining items were "pin a named-but-unvalued number," all
fixed inline: **`MIN_SESSIONS = 8`** pinned (§5.4); the §5.2 lexical-bias **margin pinned at +0.15
BM25 R@3 over a same-count seeded random-chunk control**; all pre-registration constants consolidated
into a **pinned table (§5.7)**; and three doc-precision fixes — the `call_id`-in-key clarification
(§3.3), the exact flip target (`curator/config.py:23` `CC_CURATOR_ONLOAD` `"0"`→`"1"`, §6), and the
threshold constant's real home (`policy/weights.py`, not config.py — §6/§8). No further design round
needed.

### Post-review refinement (user feedback)
The user flagged that the pinned `MIN_SESSIONS = 8` was an unjustifiable guess and asked for a
**self-correcting** mechanism. Replaced the fixed floor with the §5.4 **precision gate**: a hard
*definitional* floor (`n_sessions ≥ 3`, so the clustered bootstrap is defined) plus a data-driven
rule — a substantive verdict is rendered only when the clustered CI is precise enough to place the
effect (`width ≤ MEI`); otherwise INCONCLUSIVE-underpowered with a computed **needed-N**
(`ceil(n_sessions × (width/MEI)²)`). M4d becomes re-runnable: it converges as sessions accumulate and
tells the user exactly how many more to capture, instead of relying on a guessed threshold.
