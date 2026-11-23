# Pre-registration — stickiness ranker, powered confirmatory run

> **Committed 2026-11-22, before the analysis module has been run on any data and before the
> corpus exists.** It freezes the confirmatory test of the ranker Track A ships, so the verdict
> can't be reverse-engineered from the result. Feasibility evidence (n=5) and the hypothesis's
> origin: [`stickiness-probe-prereg.md`](stickiness-probe-prereg.md). Ranker as shipped:
> [`specs/2026-11-22-track-a-stickiness-onload-design.md`](specs/2026-11-22-track-a-stickiness-onload-design.md).

## Question
On a powered real corpus that is **disjoint from the 5 sessions that produced the hypothesis**, does
the shipped stickiness ranker rank re-fetched context better than plain BM25?

## Frozen analysis
- **Primary arm:** `stickiness_live` — `context_curator.onload.stickiness.stickiness_rank` applied
  to each fixture's candidates (oldest → newest, entities from the fixture). This is the function
  the `UserPromptSubmit` hook runs; no re-implementation.
- **Comparator:** `bm25` — `bm25_scores`, ranked by (score desc, key), exactly as
  `eval/learned/feasibility.bm25_ndcg` and every earlier keystone.
- **Fixtures:** `eval/real_corpus.harvest_trace` with `w=5`, `min_candidates=5`, **gap 0** (gold
  window = turns `[i, i+5]`, candidates = results of turns `< i`). Metric **nDCG@10**.
- **Headline:** mean over fixtures of `stickiness_live − bm25`, with a **90% session-clustered
  bootstrap CI** (`cluster_bootstrap_ci`, 2 000 iterations, seed 0). The ranker has no fitted
  parameters, so there is no train/test split and no LOSO.
- **Precision gate:** `precision_gate` with MEI = 0.10 (CI width ≤ MEI ⇒ a verdict; otherwise
  inconclusive-underpowered with a needed-N). Report `needed_n_range` as an order-of-magnitude range.

## Corpus rule
- **Unit:** one main-session transcript = one session (deduplicated by session id). Subagent
  transcripts (`…/subagents/*.jsonl`) are excluded: they are single-turn and yield no fixtures.
- **Source:** every main-session transcript available on any of the operator's machines at run time,
  copied into `src/context_curator/eval/fixtures/_real_local/` (gitignored, never committed,
  aggregate-only per DESIGN §9). The transcript of the session running the analysis is excluded.
- **Discovery exclusion:** the 5 sessions pinned in
  [`learned-feasibility-prereg.md`](learned-feasibility-prereg.md) (by sha256[:16]) are excluded
  from the primary analysis. They are the data the hypothesis came from.
- **Size floor:** at least **45 sessions with ≥ 1 fixture** after exclusions. Below that, the module
  reports *feasibility only* and no verdict is rendered from this document. The floor comes from
  the feasibility power read (needed-N order of magnitude ~45). The precision gate still decides
  whether 45+ is *enough*.
- The run records the sha256[:16] manifest of every included session in its report.

## Decision rule (anti-goalpost-moving)
Applies only when the corpus floor is met **and** the precision gate says `verdict`:
- **KEEP stickiness as the default floor** iff the CI lower bound is **> 0**. This holds even if the
  mean is below MEI. Rationale, fixed in advance: MEI was set to justify the *complexity and cost* of
  a semantic model. Stickiness costs about the same as BM25 (both run in the hook; counting is ~10 ms),
  so any reliable improvement is worth keeping.
- **REVERT the floor to plain BM25** (the ranker decided in
  [`decisions/semantic-ranker.md`](../decisions/semantic-ranker.md)) iff the CI includes 0 or lies
  below it. A reliable advantage was not shown, so the simpler ranker wins.
- **Underpowered** (gate = inconclusive): harvest to the reported needed-N and re-run unchanged.

## Secondary (reported; they do not change the decision unless stated)
- **Robustness — gap sweep** g ∈ {1, 2, 3, 5} (gold window `[i+g, i+g+5]`, candidates unchanged).
  If the gap-5 lift is less than half the gap-0 lift, flag *decaying*: the tautology concern
  reopens, and a KEEP becomes *keep pending review*.
- **Replication:** `refetch_solo` (refetch count only, ties by key), the analogue of the
  feasibility 0.445.
- **Sensitivity:** the headline recomputed with the 5 discovery sessions included.
- **Diagnostics:** `lexical_bias` (gold vs control BM25 R@3); fixtures and sessions per project.

## Known differences between this eval and the live hook (stated before the run)
1. The live candidate pool spans every session in the per-project store (24 h TTL). The eval pool
   is one session.
2. The live selector drops byte-identical duplicates and budgets on the rendered (≤ 1 200 char)
   body, after ranking. nDCG scores the ranking itself, so neither is measured here.
3. Live chunks are capped at 32 768 chars at capture; eval chunks are untruncated.

## Command
`uv run python -m context_curator.eval.stickiness` (defaults: the `_real_local` corpus, the 5
discovery shas excluded, floor 45, seed 0). The report is written to stdout; record the outcome
below and in the decision doc.

## Addendum — 2026-11-22, before any powered run (implementation of the rules above)
An independent review of the analysis module found gaps between this document's intent and the
first implementation. The analysis, arms, metric, MEI, floor and decision rule are unchanged; these
make the module do what the text above already says:
- **Disjointness:** discovery sessions are excluded by sha256[:16] **or** by session id (the
  pinned file-name prefix/suffix). A discovery transcript that grew after pinning, or a copy from
  another machine, has a new sha but the same id.
- **Current session:** excluded with `--exclude-session <id>`; the operator passes it.
- **Decaying downgrade:** a decaying gap sweep turns KEEP into "KEEP stickiness, pending review".
- **Sensitivity run** (`--include-discovery`) renders no decision.
- **Diagnostics:** fixtures and sessions per project; the project is the transcript's parent
  directory, so copy transcripts into `_real_local/<project>/`.
- **Further known eval-vs-live differences** (adds to the list above): (4) live chunks store
  `json.dumps(tool_response)` (escaped newlines and non-ASCII; Edit responses include the original
  file), while eval chunks are the transcript's plain-text results, so BM25 tie-breaks and dedupe can
  differ live. (5) Relative paths resolve against the hook process's CWD live and the harvest root
  in the eval.

## Outcome
*(not yet run)*
