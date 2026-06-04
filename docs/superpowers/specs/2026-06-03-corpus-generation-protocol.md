# Powered Corpus Generation Protocol (M4c)

> The committed, reproducible recipe for the M4c **fair, powered** synthetic eval corpus.
> Referenced by `docs/superpowers/plans/2026-06-03-m4c-powered-corpus.md` Task 7. Encodes the
> fairness constraints from `2026-06-03-powered-corpus-tuning-design.md` §3–§4 verbatim so the
> corpus cannot be rigged to make bge win. A generator (LLM subagent) emits `Fixture`-schema JSON
> honoring every constraint below; an independent gold-judge (Task 8) and the corpus audit
> (`eval/corpus_audit.py`) then certify it.

## 0. Why this exists (the circularity we are avoiding)
A corpus is **rigged** for bge if (a) gold chunks sit at the newest position (a trivial recency
baseline wins for free), (b) there are no hard negatives (any method looks good), or (c) gold
overlaps the prompt's exact tokens so heavily that BM25 already wins (then bge has nothing to
prove) **or** so little that only an embedding could ever find it (then bge wins by construction).
A FAIR corpus plants gold with *realistic, paraphrased* overlap, includes lexical decoys that
tempt BM25, and spreads gold across recency positions. The verdict is only meaningful on such a
corpus.

## 1. Fixture schema (matches `src/context_curator/eval/fixtures.py`)
Each fixture is one JSON object:
```json
{
  "name": "kebab-case-unique-name",
  "prompt": "a natural first-person developer question for this task",
  "chunks": [
    {"key": "k_snake_case_id", "content": "one chunk of prior session context", "tags": []}
  ],
  "gold_keys": ["k_...", "k_...", "k_..."],
  "split": "train",
  "recent_tools": []
}
```
Rules enforced by the schema / audit / tests:
- `chunks` are **CHRONOLOGICAL, oldest first** (index 0 = oldest). Recency = position.
- `tags` is optional (defaults `[]`). A **hard negative** carries the tag `"hard_neg"`.
- `recent_tools` optional (defaults `[]`); usually `[]` for this corpus.
- `split` is `"train"` or `"test"` — leave every generated fixture as `"train"`; Task 9 assigns
  the train/test split centrally so it can balance recency thirds across splits.
- `name` unique across the whole corpus; `key` unique within a fixture.

## 2. Fairness constraints (HARD — the audit/tests enforce them)
For EVERY fixture:
1. **Size:** 12–20 chunks.
2. **Gold:** **≥3** gold keys. Each gold chunk genuinely answers the prompt but is **paraphrased** —
   it shares *some* but not *all* salient terms with the prompt (realistic overlap, neither
   verbatim nor disjoint). Spread the gold's own positions; do not cluster all gold at the end.
3. **Hard negatives:** **≥2** chunks tagged `"hard_neg"`. A hard negative is **off-topic for the
   prompt** yet shares **as many or more** prompt tokens as the gold chunks do (a lexical decoy
   that tempts BM25 into ranking it above gold). It must be genuinely NON-relevant (a blind judge
   would say "no, this does not answer the prompt").
4. **Filler:** the remaining chunks are plausible unrelated session context (other tasks, tool
   output, file edits) with low prompt overlap.
5. **Realistic chunk types:** mix these forms across the corpus — code/file-edit summaries
   (`"refactored auth middleware to read the token from the Authorization header"`), tool-call
   outputs (`"pytest: 3 failed, 41 passed — test_login_expired_token AssertionError"`), decisions
   (`"decided to store sessions in Redis with a 30-minute sliding TTL"`), and short notes. Do NOT
   make every chunk a bare keyword bag.

## 3. Recency-third targeting (drives corpus-level fairness)
The audit (`eval/corpus_audit.py`) requires each recency third to hold ≥20% of fixtures' gold,
where the third is decided by the FIRST gold key's chronological position:
`frac = gpos / (n_chunks − 1)` → `oldest` if `frac < 1/3`, `middle` if `< 2/3`, else `newest`.

When generating in batches, the controller tells each batch which third to fill (round-robin over
`oldest / middle / newest`) based on the running histogram, so the assembled corpus has gold
genuinely mixed across recency — NOT all newest. To place a fixture's first gold in the:
- **oldest** third: put a gold key in the first ⌊n/3⌋ chunks;
- **middle** third: first gold key in the middle third;
- **newest** third: first gold key in the last third.
(Other gold keys may fall anywhere; only the first gold's position sets the third.)

## 4. Generation procedure (Task 7)
1. Controller dispatches generation subagents in batches of ~8–12 fixtures. Each batch prompt names
   the target recency third and restates §1–§3.
2. Each subagent WRITES one JSON file per fixture to
   `fixtures/powered/_raw_pregenerated/<name>.json` and returns only a terse manifest
   (names + per-fixture chunk/gold/hard_neg counts + intended third). It does NOT return full JSON
   (keeps the controller's context lean).
3. Generate a **pilot of ~15** fixtures first (Task 9 uses it for the variance/power estimate),
   then the remainder up to the power target.
4. **Bounded loop:** hard cap `MAX_GEN_BATCHES = 12`. If the target n is not reached by then, the
   fallback ladder applies: (a) relax the pilot variance assumption and report a wider CI;
   (b) ship the largest fair corpus achieved and record it as INCONCLUSIVE-underpowered rather than
   fabricate fixtures. Never pad with low-quality fixtures to hit a number — `log` what was dropped.
5. Commit the raw pre-judge corpus + this protocol (the reproducibility record) BEFORE judging.

## 5. Independent gold-judge (Task 8) — what it may and may not do
- A **blind** judge (sees prompt + a chunk's content, NOT the "gold" label) answers yes/no:
  "does this chunk correctly answer the prompt?" per gold key. `eval/gold_judge.judge_corpus`
  DROPS a fixture iff any planted gold is judged non-relevant. The judge **never** ranks or
  adjudicates which chunk is "best" — that would relocate the circularity.
- Circularity guard: compute each fixture's BM25 recall, then
  `eval/gold_judge.drop_rate_by_bm25_tercile(kept_recalls, dropped_recalls)`. If the judge dropped
  disproportionately many HIGH-BM25-recall fixtures, the survivor set is secretly bge-aligned →
  record **RIGGED** risk and tighten generation (less extreme paraphrase) before proceeding.

## 6. Worked example A — first gold in the OLDEST third (n=12, thirds 0-3 / 4-7 / 8-11)
```json
{
  "name": "redis-session-store",
  "prompt": "how should I persist user sessions so they survive a server restart",
  "chunks": [
    {"key": "k_redis_sessions", "content": "decided to keep sessions in Redis with a 30-minute sliding expiry so they outlive process restarts", "tags": []},
    {"key": "k_session_schema", "content": "session record holds user_id, issued_at, and a rotating csrf token; serialized as JSON under a sess: key", "tags": []},
    {"key": "k_redis_conn", "content": "added a redis connection pool with a 50-connection cap and health-check ping on checkout", "tags": []},
    {"key": "k_signup_form", "content": "the signup form validates email format and rejects passwords under 12 characters", "tags": ["hard_neg"]},
    {"key": "k_invoice_pdf", "content": "generate the monthly invoice as a PDF and email it to the billing contact", "tags": []},
    {"key": "k_session_restore", "content": "on boot the app reloads live sessions from the persistent session store instead of starting empty", "tags": []},
    {"key": "k_user_persist_table", "content": "users are saved in the postgres users table; a restart must not lose their saved profile data", "tags": ["hard_neg"]},
    {"key": "k_css_theme", "content": "switched the dashboard to a dark theme using CSS custom properties", "tags": []},
    {"key": "k_log_rotation", "content": "rotate application logs daily and keep 14 compressed archives", "tags": []},
    {"key": "k_metrics", "content": "export request latency histograms to Prometheus on /metrics", "tags": []},
    {"key": "k_feature_flag", "content": "gate the new checkout flow behind a feature flag defaulting to off", "tags": []},
    {"key": "k_healthcheck", "content": "the /healthz endpoint returns 200 once the database pool is ready", "tags": []}
  ],
  "gold_keys": ["k_redis_sessions", "k_session_restore", "k_redis_conn"],
  "split": "train",
  "recent_tools": []
}
```
Notes: first gold `k_redis_sessions` is at index 0 (oldest third). Gold paraphrases the prompt
("persist … survive a restart" → "outlive process restarts", "reloads live sessions"). The two
`hard_neg` chunks (`k_signup_form`, `k_user_persist_table`) share tempting tokens — "session"/
"form", "persist"/"restart"/"saved" — yet do NOT answer *how to persist sessions across restarts*.

## 7. Worked example B — first gold in the NEWEST third (n=13, thirds 0-4 / 5-8 / 9-12)
```json
{
  "name": "exponential-backoff-retry",
  "prompt": "what is the right way to retry a flaky downstream HTTP call",
  "chunks": [
    {"key": "k_css_grid", "content": "rebuilt the report layout with a responsive CSS grid", "tags": []},
    {"key": "k_retry_attempts_decoy", "content": "the signup retry counter locks the account after 5 failed password attempts", "tags": ["hard_neg"]},
    {"key": "k_db_index", "content": "added a composite index on (tenant_id, created_at) to speed up the activity feed", "tags": []},
    {"key": "k_email_queue", "content": "outbound email is pushed onto a background queue and sent by a worker", "tags": []},
    {"key": "k_http_call_decoy", "content": "the webhook sender makes an HTTP POST to the customer URL and logs the status code", "tags": ["hard_neg"]},
    {"key": "k_timezone", "content": "store all timestamps in UTC and convert to the user's timezone in the UI", "tags": []},
    {"key": "k_pagination", "content": "switch the list endpoint to keyset pagination using the last seen id", "tags": []},
    {"key": "k_cache_ttl", "content": "cache the product catalog for 10 minutes and bust it on any write", "tags": []},
    {"key": "k_config_reload", "content": "reload config on SIGHUP without dropping in-flight requests", "tags": []},
    {"key": "k_backoff", "content": "wrap the downstream call in exponential backoff with full jitter, capped at 3 attempts", "tags": []},
    {"key": "k_retry_idempotency", "content": "only retry the request when it is safe to repeat, keying on an idempotency token so a retry is not double-applied", "tags": []},
    {"key": "k_circuit_breaker", "content": "trip a circuit breaker after repeated downstream failures so retries stop hammering a dead service", "tags": []},
    {"key": "k_audit_log", "content": "record every admin action in an append-only audit log", "tags": []}
  ],
  "gold_keys": ["k_backoff", "k_retry_idempotency", "k_circuit_breaker"],
  "split": "train",
  "recent_tools": []
}
```
Notes: first gold `k_backoff` is at index 9 (newest third of 13). The `hard_neg` chunks reuse
"retry"/"attempts" and "HTTP POST"/"call" — high lexical overlap with the prompt — but are about
account lockout and webhooks, NOT how to retry a flaky call. The gold uses "downstream"/"backoff"/
"idempotency"/"circuit breaker": correct, paraphrased, not a verbatim echo of the prompt.

## 8. Self-check before committing a batch
For each generated fixture confirm: parses under `Fixture`; 12–20 chunks; ≥3 gold; ≥2 `hard_neg`;
first gold in the intended third; gold paraphrased (not verbatim); hard negatives genuinely
non-relevant but lexically tempting; chunk types varied. Reject and regenerate any fixture failing
these — do not commit it.
