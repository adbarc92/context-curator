# M4b-1 — Resident Curator Process (warm-bge semantic onload) — Design

**Status:** Hardened through 3 adversarial critique rounds (log below)
**Parent design:** `DESIGN.md` v1.3 §4.2 (latency budget + tag/recency degradation), §4.1 (embedded store), §9 (privacy boundary), §12 (embedder decision). Builds directly on the M4a live-onload spec (`docs/superpowers/specs/2026-06-02-live-onload-design.md`) and its merged code.
**Milestone:** M4b-1 (first sub-project of M4b). M4b's other pieces — re-onload dedup, packed-BLOB storage, and the offload write-back — are out of scope here (see §11). `cc-reembed` is **subsumed** by this design's background reconcile.
**Stack:** Python + UV. bge-small via the optional `embed` extra (`FastEmbedEmbedder`, dim 384); HashingEmbedder retained only as the deterministic test embedder.

---

## 1. Purpose

Lay the rails for semantic live onload — and turn the rails on later. M4a shipped onload on a uniform `HashingEmbedder` (lexical + recency) because a per-process hook can't afford the ~0.5–2s bge model load every turn. This sub-project introduces a **resident curator process** that holds a warm bge model, owns the store's embeddings, and answers the onload hook over a local socket — fail-open, degrading to recency when the curator is cold/down.

**Honest framing (round-3 finding #1 — what M4b-1 delivers on day one).** The bge gate-threshold is *unmeasured* until the M3b eval tunes it, so live semantic injection ships **behind a default-OFF flag** (`CC_CURATOR_ONLOAD`). With the flag off — the shipped default — **onload behavior is unchanged from today (recency)**: the user gets *no* behavioral difference yet. What M4b-1 actually delivers is the **substrate and the proven machinery**: (1) the store becomes **bge-native** (the curator backfills bge vectors in the background) — exactly the corpus M3b needs to tune against; (2) the daemon, handshake, lifecycle, and fail-open are exercised in production conditions. The **§10.8 differentiator turns on at M3b**, when the threshold is measured and the flag flips — a one-line change, no new architecture. The full semantic scoring path is *built and tested here* (behind the flag) so the flip is trivial; it just isn't the default.

## 2. Scope & decisions

**In scope (M4b-1):**
- A new **`curator` process** (`python -m context_curator.curator`): warm bge, owns the store, background **backfill reconcile**, serves onload over a loopback socket. Auto-spawned, single-instance per store, idle-exits. Ships with **live semantic injection behind a default-OFF flag** (`CC_CURATOR_ONLOAD`, round-2 I2): the daemon runs and backfills "dark" — proving all machinery — while the unvalidated bge gate-threshold awaits M3b tuning; flipping the flag (or the post-M3b default) turns on live semantic onload with no code change.
- An **onload client** in the `UserPromptSubmit` hook: discover → connect → request a bge selection; on any failure, fire-and-forget spawn the curator and serve this turn in-process via **recency fallback**.
- The store becomes **bge-native**: capture writes `embedding = NULL` (a new `NullEmbedder`); the curator backfills bge vectors. `store.store()` learns to persist a `None` embedding as SQL `NULL`.
- TCP-loopback transport + token + atomically-written runtime file; JSON-lines protocol.

**Out of scope (deferred):**
- **Re-onload dedup** (turn/window-membership) — now *tractable* because the curator is a stateful long-lived process that can track what it injected, but it is its own sub-project (§11).
- **packed-BLOB embedding storage** — the latency-at-scale optimization; embeddings stay JSON text here (§11).
- **Offload write-back** (`select_offload` wiring + eviction-regret) — that is **M5** (DESIGN roadmap: "M5 — subagent offload loop"), not M4b (§11).
- **bge gate-threshold tuning against the eval** — M4b-1 ships a *provisional* bge threshold; a principled value comes from the (currently unmerged) M3b eval harness run with bge (§5.4, §11).

**Locked decisions (from brainstorming):**
1. **Auto-spawn on demand.** The first onload that finds no curator running spawns one detached and serves *that* turn via the recency fallback; subsequent turns use the warm curator. Zero user action; cold start is one fallback turn.
2. **Recency fallback when the curator is unavailable** (DESIGN §4.2's sanctioned "tag+recency, no embedding" degradation). The store is therefore **bge-native with a single nullable embedding column**: capture writes `NULL`, the curator backfills bge. Similarity is fully curator-gated; with auto-spawn, "down" is rare (one turn / crash).
3. **The curator is the sole embedding authority.** Only the curator computes vectors (onload-time prompt embedding + background chunk backfill). This subsumes `cc-reembed`: old 256-dim / `NULL` rows are simply "needs embedding," reconciled in the background.
4. **TCP loopback + token-keyed mutual handshake.** `127.0.0.1:<ephemeral>`; identical on Windows/macOS/Linux (dev is Windows). The 256-bit token is **never sent in cleartext** — it keys an HMAC handshake in which the **server proves token-knowledge before the client sends the prompt** (round-1 C1), so a port-reused stranger can never receive prompt text or the token. Nothing is sent off-box (§9 intact).

## 3. Architecture

```
src/context_curator/
  embeddings.py          # MODIFY: + NullEmbedder (dim 384, embed -> None)
  policy/relevance.py    # MODIFY: scored_with_similarity guard when task_emb is None (round-1 I5)
  policy/weights.py      # MODIFY: + ONLOAD_BGE_COSINE_THRESHOLD + ONLOAD_BGE_WEIGHTS (provisional)
  onload/select.py       # MODIFY: + recency_select() + shared _eligible/_first_fit helpers
  store/sqlite_store.py  # MODIFY: store() persists a None embedding as SQL NULL
  hooks/_io.py           # MODIFY: open_store() uses NullEmbedder (capture no longer embeds)
  hooks/user_prompt_submit.py  # MODIFY: curator client in front of the recency fallback
  curator/
    __init__.py
    __main__.py          # `python -m context_curator.curator` (+ --prefetch)
    server.py            # non-blocking lock, bind, warming->ready, handshake, accept+idle loop, teardown
    handler.py           # pure, READ-ONLY: handle_onload(read_store, embedder, req) -> response
    reconcile.py         # backfill NULL/wrong-dim chunks in bounded batches (write store, sole writer)
    client.py            # discover + handshake + request; raises CuratorUnavailable
    runtime.py           # .curator.json (atomic, state), .curator.lock (non-blocking), token/HMAC, spawn_detached
.claude/settings.json    # (unchanged — hooks already registered in M4a)
.gitignore               # MODIFY: ignore .curator.json / .curator.lock
```

### 3.1 Data flow — curator warm (common case; handshake + two-store handler — round-3 C-4)
```
UserPromptSubmit.handle → client.request_onload(prompt, k, budget)
  runtime.discover() -> {state, port, token}; require state live (§5.1)
  connect 127.0.0.1:port (single per-request deadline, §6.1)
  send {op:"hello", nonce}                 # NO token, NO prompt
  recv {server, proof}; verify proof == HMAC(token, nonce)   # token never sent; stranger can't forge
  send {op:"onload", prompt, k, budget};  recv {ok, keys}
curator.server: handshake (prove token-knowledge) -> handler.handle_onload(read_store, bge, req)
  if CC_CURATOR_ONLOAD off (default): return {keys: []}        # dark — hook uses recency
  else: bge.embed(prompt) -> in-memory embed ≤12 fresh NULL candidates (§5.4) ->
        RelevancePolicy(bge).scored_with_similarity -> onload_select(cos>=bge_threshold,
        pins/conventions excluded) -> {keys}
hook: map keys -> chunks (from its own all_live_chunks) preserving rank -> format_block -> inject
```
The handler reads only (`read_store`); it performs **no DB write** (round-3 C-1: on-demand vectors live in memory for this turn; the reconcile persists later). The reconcile thread is the sole writer.

### 3.2 Data flow — curator cold / down (first turn, crash, never-started)
```
UserPromptSubmit.handle → client.request_onload(...)  raises CuratorUnavailable
  (.curator.json absent, or connect refused/timeout, or malformed reply)
hook: runtime.spawn_detached()          # fire-and-forget; warm by next turn
  -> recency_select(all_live_chunks, k, budget)   # NO embedding, pins/conv excluded
  -> format_block -> inject              # fast, non-semantic, fully fail-open
```

**Invariant:** the hook never blocks on the model. Warm curator → semantic, in budget. No curator → recency, instant. bge quality is best-effort on top of an always-fast floor.

**SessionStart is unchanged from M4a.** Seeding is pins + `proj:*:conventions` selected by *key*, not similarity — it needs no embedding and therefore no curator. Only `UserPromptSubmit` gains the curator client.

## 4. The store change (bge-native)

No DDL change — `chunks.embedding` is already nullable JSON text. The semantics change: it now holds **bge(384)** vectors, and `NULL` means "not yet bge-embedded." packed-BLOB stays deferred (still JSON text).

- **`NullEmbedder`** (`embeddings.py`): `dim == 384` (so the store's notion of "the live dim" matches bge), `embed(text) -> None`. Pure, dependency-free.
- **Capture stops embedding.** `open_store()` (`hooks/_io.py`) constructs `SqliteStore(..., embedder=NullEmbedder())` instead of `HashingEmbedder()`. So `store.store()` (capture, file ledger, subagent capture, MCP `cc_store`) writes `embedding = NULL`. Capture gets *faster* (no hashing) and trivially fail-open — capture never depends on the curator. The MCP server's `build_default_store()` likewise uses `NullEmbedder` so every non-curator write path is consistent.
- **`store.store()` persists `None` as SQL NULL.** Today it always does `json.dumps(embedding)`. Change: `json.dumps(embedding) if embedding is not None else None` in the INSERT bind. `_row_to_chunk` already maps a falsy column to `embedding=None`, so reads round-trip.

**Why safe:** the down-curator fallback is recency (no similarity), so nothing reads a capture-time vector anymore. The curator is the only producer of embeddings. (M4a's in-process `HashingEmbedder` onload is *replaced* by curator-bge + recency fallback — HashingEmbedder leaves the live path entirely, surviving only as a deterministic test embedder.)

## 5. The curator process (`context_curator/curator/`)

### 5.1 Startup & single-instance (ordered to kill the spawn storm — round-1 C2)
1. Acquire a **NON-BLOCKING exclusive OS lock** on `<store_dir>/.curator.lock` (POSIX `fcntl.flock(LOCK_EX|LOCK_NB)`; Windows `msvcrt.locking(LK_NBLCK)`) via a small cross-platform helper. If the lock is held, **the loser exits 0 immediately — before loading bge** (another curator owns/ is warming this store). Non-blocking is mandatory: a *blocking* lock would leak a process parked until the winner's idle-exit.
2. Bind `127.0.0.1:0` (ephemeral port). Generate a 256-bit token.
3. **Immediately** write `.curator.json` with `{state, pid, port, token, dim, started_at, embedder}` (atomic temp+`os.replace`, `0600`) — *before* the bge load — so warmup turns discover a curator and don't respawn (round-1 C2). `state` is `warming` (model cached) or `provisioning` (first-run download — C2). **`pid` + `started_at` are written now**, making the state liveness-checkable (round-2 C1).
4. Load bge (`FastEmbedEmbedder`, ~0.5–2s if cached). **Wrap the load in try/except: on ANY failure (OOM, import error, ONNX load, download failure) remove `.curator.json` and exit non-zero** — a crash during load must NOT leave a frozen `warming` file (round-2 C1).
5. Open the store (two connections — §5.2/§5.3); run the **one-time wrong-dim migration** (§5.2) if needed.
6. **Atomically rewrite** `.curator.json` with `"state":"ready"`. Start the reconcile thread, then enter the accept loop (idle handled *inside* it — §5.3).

**States & the warming-liveness bound (round-2 C1).** Discoverable states: `provisioning`/`warming` (bound, loading), `ready` (serving), or absent. `client.discover()` treats a `warming`/`provisioning` file as actionable (recency this turn, **no respawn**) *only if* `pid` is alive **and** `now − started_at < deadline` (`CURATOR_WARMING_DEADLINE_S`=15s for `warming`; `CURATOR_PROVISION_DEADLINE_S`=300s for `provisioning`). A **dead-pid or stale** warming file is treated as **absent** → `CuratorUnavailable(respawn=True)` → spawn. The non-blocking lock (step 1) backstops the double-spawn. This closes round-1's spawn-storm-fix permanent-wedge hole.

**C2 — first-run model provisioning is an explicit outbound fetch (round-2 C2).** `FastEmbedEmbedder` lazy-downloads bge-small (~130MB from HuggingFace) on first use, so first-ever `warming` can take seconds–minutes or **fail offline**. Hence the `provisioning` state + long deadline + step-4 crash-cleanup. **§9 privacy is sharpened:** *the prompt and store content never leave the box; model weights are fetched from HuggingFace on first run.* Provisioning is a **documented prerequisite** — `python -m context_curator.curator --prefetch` (download-and-exit) for setup/CI so production runs hit a warm cache and never surprise a privacy-conscious user mid-session.

### 5.2 Backfill reconcile (`reconcile.py`, background thread)

**Two connections inside the curator (round-2 C4a, finalized round-3 C-1).** `SqliteStore` holds a *single* `sqlite3` connection (`check_same_thread=False` only disables Python's affinity assertion — it does **not** make one connection safe for concurrent use; a single connection has one transaction state). WAL's reader/writer concurrency is **cross-connection**. So the curator constructs **two `SqliteStore` instances**: a **read store** used by the accept loop / `handle_onload` (`all_live_chunks` — **reads only**, round-3 C-1) and a **write store** used **exclusively by the reconcile thread** (the on-demand handler does NOT write — §5.4). Each connection is therefore touched by exactly one thread → no write↔write or read-mid-write hazard on a shared connection, and WAL MVCC lets a reconcile write transaction and a concurrent onload read coexist without `SQLITE_MISUSE` / "transaction within a transaction". (Contract-tested in §9.)

**The curator does not sweep (round-3 I-1).** `sweep_expired` runs inside `open_store()` — i.e. the *hooks* own expiry sweeping. The curator builds its two `SqliteStore`s directly (not via `open_store`), so it **never sweeps**; this avoids a double-sweep `cc_meta.last_sweep` TOCTOU across the two connections and keeps the write lock for reconcile alone. (Each `SqliteStore.__init__` idempotently runs `CREATE TABLE IF NOT EXISTS` — harmless with two connections.)

**Two cross-process writers** (capture hooks + the curator write store) on one WAL db make write-lock duration load-bearing (round-1 I2): a `PostToolUse` capture colliding with a held reconcile transaction waits on `BEGIN IMMEDIATE` up to `busy_timeout` (5s) — a visible per-tool hang. Mitigations:
- **Embed outside the transaction** (mirroring `store.store()`'s "embed before BEGIN IMMEDIATE", `sqlite_store.py:94`).
- **Small batches with a yield between them (round-2 C4b).** `RECONCILE_BATCH` is small (default **16**); each tick: (1) `SELECT key, content WHERE embedding IS NULL LIMIT RECONCILE_BATCH`; (2) embed the batch **with no lock held**; (3) `BEGIN IMMEDIATE` → per-row `UPDATE chunks SET embedding=? WHERE key=?` (touching **ONLY** `embedding` — never `seq`/`created_at`/`expires_at`; the round-2-C2-lesson invariant, §9) → `COMMIT`; (4) sleep `RECONCILE_INTERVAL_S` (default 2s) so capture always finds a write gap. A small batch holds the lock for ~16 fast UPDATEs, not a full backfill.
- **Back off, don't block capture:** the reconcile catches `SQLITE_BUSY` and retries next tick rather than letting *capture* eat the 5s wall. (`test_store_concurrency.py` covers capture-vs-capture; §9 adds a capture-vs-reconcile test.)

**Steady state is cheap (round-1 I2):** once no `embedding IS NULL` rows remain, the tick is a `WHERE embedding IS NULL LIMIT 1` that returns nothing and sleeps — **no full-table Python scan**.

**One-time wrong-dim migration (subsumes `cc-reembed`):** old M4a rows carry 256-dim vectors (not NULL), which `WHERE embedding IS NULL` won't catch. So on first startup against a given store, a **single** pass (gated by a `cc_meta` flag `curator_dim_migrated=<dim>`) scans non-null embeddings, and any whose length ≠ `dim` is set to `NULL` (so the normal reconcile then backfills it). The flag makes this run **once per store**, never re-scanning the full table on subsequent startups. Stragglers the reconcile hasn't reached score similarity 0 under bge (the existing `relevance.py` wrong-dim path) and are gate-excluded — never a crash.

### 5.3 Request loop & idle shutdown (`server.py`)
- **Idle detection lives INSIDE the accept loop (round-1 I3 — no separate watchdog thread).** The loop blocks on `accept()` with a socket timeout equal to the remaining idle budget; a timeout with no connection since the last request → clean exit. This makes "serving" and "idle-exit" mutually exclusive by construction — a separate watchdog could otherwise tear down mid-request.
- On a connection: perform the §7 handshake (server proves token-knowledge), then read one newline-delimited JSON request; reject unknown ops; dispatch `onload` → `handler.handle_onload`, `ping` → `{ok:true}`. Reset the idle deadline on each served request.
- **Teardown order (round-1 I3):** stop the reconcile thread (signal + brief join), close the listening socket, remove `.curator.json`, **release the lock LAST**. Releasing the lock last ensures a racing `spawn_detached` loser still fails the non-blocking lock and exits cleanly while the client has already done its recency fallback this turn. Same teardown on SIGTERM/SIGINT/atexit.
- **Accepted cost:** idle-exit at a turn boundary can cost up to **two** recency turns (one to notice the gone curator + respawn, one while it warms) — acceptable given auto-spawn.
- Single-threaded accept loop is adequate (one Claude Code session = serial prompts); a request is embed + score + select, all fast. The reconcile runs on its own thread but only ever holds the *write* lock briefly (§5.2); the accept loop does reads.

### 5.4 Onload handler (`handler.py`, pure)
`handle_onload(read_store, embedder, req) -> dict` — **READ-ONLY; performs no DB write** (round-3 C-1/C-2). Signature dropped `write_store`: the on-demand vectors are used in memory for this turn only; the reconcile thread is the sole writer.

**Flag gate first (round-2 I2 / round-3 #1).** If `CC_CURATOR_ONLOAD` is off (the M4b-1 default) → return `{ok:true, keys:[]}` immediately (the hook injects via recency; the curator's day-one value is the background backfill, not injection). The rest runs only when the flag is on (M3b flips it):

1. `candidates = read_store.all_live_chunks()`; `task = embedder.embed(req.prompt)` (the **only** prompt-side bge call).
2. **On-demand in-memory embed of fresh NULLs (round-2 C3, fixed round-3 C-2/C-3).** A chunk captured this turn has `embedding=NULL` until a reconcile tick, so it would score cosine 0 and be gate-excluded — invisible to semantic onload, defeating M4a's "page in just-captured-and-now-relevant context" purpose. Fix: take up to `ONDEMAND_EMBED_CAP` (default **12**) of the **most-recent** NULL candidates, embed their content with the warm bge, and **write the vectors onto the in-memory candidate objects** — `Chunk` is a frozen pydantic model, so rebuild via `c.model_copy(update={"embedding": vec})` (round-3 C-3: persisting to the DB would NOT mutate the already-read in-memory snapshot, so the freshly-computed vectors must be threaded into the objects that get scored, or the C3 fix silently does nothing). **No DB persist on the hot path** (round-3 C-1/C-2): the reconcile backfills these same rows within `RECONCILE_INTERVAL_S`; the ≤12 may be embedded twice (once in-memory here, once by reconcile) — harmless (same content→same vector) and far cheaper than a hot-path write that could block on the reconcile's `BEGIN IMMEDIATE` (up to `busy_timeout`). Older NULLs beyond the cap score 0 → gate-excluded this turn (acceptable; they're old, reconcile reaches them).
3. `RelevancePolicy(embedder, ONLOAD_BGE_WEIGHTS).scored_with_similarity(req.prompt, candidates)` → `onload_select(..., cos_threshold=ONLOAD_BGE_COSINE_THRESHOLD, k, token_budget)` (M4a's selector — pins + `proj:*:conventions` excluded, raw-cosine gate). Return `{ok:true, keys:[...]}` in rank order (hook maps keys→chunks and formats — it stays the only stdout writer).

**Shared embedder, no lock (round-3 thread-safety).** The accept thread (prompt + on-demand embeds) and the reconcile thread share **one** warm bge embedder; ONNX Runtime inference (`session.run`) is thread-safe, so concurrent `.embed()` needs no lock — and must NOT take one (a lock would make an onload embed wait behind a 16-vector reconcile batch, blowing the deadline). If a future embedder isn't inference-thread-safe, the fallback is a per-thread instance (2× model RAM) — noted for the implementer.

**Policy guard against a None task embedding (round-1 I5).** `scored_with_similarity` gains a top guard: `if task_emb is None: return [(c, recency_only_score, 0.0) ...]` (similarity 0 for all). This makes the policy **fail-safe under any non-embedding embedder** (e.g. `NullEmbedder`) instead of crashing in `_cosine(None, …)` — defensive, no behavior change for real embedders (which never return None).

**bge threshold — a reasoned provisional value, not 0.15, with observability + a calibration test (round-1 I1).** bge-small is **anisotropic**: *unrelated* English text routinely sits at cos 0.3–0.5, and related text at ~0.6–0.8 — so M4a's HashingEmbedder gate of 0.15 would pass *everything*. Provisional `ONLOAD_BGE_COSINE_THRESHOLD = 0.55` (mid-band, biased toward precision). `ONLOAD_BGE_WEIGHTS = PolicyWeights(sim_floor=ONLOAD_BGE_COSINE_THRESHOLD)` keeps the gate↔floor reconciliation (round-2 C1). Because the headline feature rides on this number and **both miscalibration modes are otherwise invisible** (too high → curator returns `[]` → indistinguishable from recency but slower; too low → window flooded):
- **Observability:** behind `CC_CURATOR_DEBUG`, the handler logs (to the curator's stderr/logfile) the top-K candidate cosines per request, so the operating regime is diagnosable.
- **Calibration test — separation-margin, not a single hand-picked pair (round-2 I2).** A single relevant/unrelated fixture would pass "by construction" for any threshold in a wide band — it validates the fixtures, not 0.55. Instead: with real bge, sample **N (≥8) relevant pairs and N unrelated pairs**, assert the two cosine **populations separate** (e.g. `mean(relevant) − mean(unrelated)` exceeds a margin) **and that 0.55 sits between the population means**. This grounds the placeholder in a measured distribution rather than folklore — while acknowledging it's still not precision/recall-tuned.
- **True tuning** is derived from an M3b-eval run with `FastEmbedEmbedder` (§11), which also flips `CC_CURATOR_ONLOAD` on by default — this is where the §10.8 differentiator gets its real verdict.

## 6. The onload client & recency fallback

### 6.1 Client (`client.py`)
`request_onload(prompt, *, k, token_budget) -> list[str]` (keys) or raises `CuratorUnavailable`:
- `runtime.discover()` → `{state, port, token, pid, started_at}` or raise (file absent). **If `state ∈ {warming, provisioning}` and the file is LIVE** (pid alive, within its deadline — §5.1) → raise `CuratorUnavailable(respawn=False)` (a curator is coming up; fall back, don't double-spawn). **A dead/stale warming file → treat as absent → `CuratorUnavailable(respawn=True)`** (round-2 C1).
- **Single total deadline for the whole request (round-2 I1).** The handshake is two round-trips (hello→proof, onload→keys); a per-`recv` timeout could sum to >budget. Instead set one `deadline = monotonic() + REQUEST_DEADLINE_S` (default 0.25s) and `settimeout(deadline − now)` before each socket op, so connect + both round-trips **share** one budget that stays inside §4.2. On loopback this is sub-ms; the deadline only bites a hung/warming curator (→ fallback).
- **Handshake first, prompt second (round-1 C1):** send `{op:"hello", nonce:<client random>}` — NO prompt, NO token. The server replies `{server:"context-curator", proof: HMAC_token(nonce)}`. The client recomputes `HMAC_token(nonce)` (it has the token from the runtime file) and **verifies it** — a port-reused stranger cannot produce it. Only on a valid proof does the client send `{op:"onload", prompt, k, token_budget}`. The token never crosses the wire; the prompt is never sent to an unverified peer.
- No banner / bad proof / `ok:false` / timeout / any socket error → raise `CuratorUnavailable` (respawn=True).

### 6.2 Hook integration (`user_prompt_submit.handle`)
The M4a body (which built `RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)` and called `onload_select`) is **replaced**. The hook now **constructs no embedder and no policy** (round-1 I4) — embedding is the curator's job, the fallback is pure recency. Imports drop `HashingEmbedder`/`RelevancePolicy`/`onload_select`/`ONLOAD_WEIGHTS`/`ONLOAD_COSINE_THRESHOLD`; add the curator `client`/`runtime` + `recency_select`.
```
prompt = (event.get("prompt") or "").strip()
if not prompt: log "...0 (empty prompt)"; return HookResult(0)
chunks_all = store.all_live_chunks()
try:
    keys = client.request_onload(prompt, k=ONLOAD_K, token_budget=ONLOAD_TOKEN_BUDGET)
    by_key = {c.key: c for c in chunks_all}
    chunks = [by_key[k] for k in keys if k in by_key]        # preserve curator RANK order (round-1 M1)
    log "...onloaded N chunk(s) [curator]"
except CuratorUnavailable as e:
    if e.respawn: runtime.spawn_detached()                   # fire-and-forget; skipped if 'warming'
    chunks = recency_select(chunks_all, k=ONLOAD_K, token_budget=ONLOAD_TOKEN_BUDGET)
    log "...onloaded N chunk(s) [recency-fallback]"
block = format_block(chunks, title=_TITLE)
return HookResult(0, additional_context=block or None)
```
Curator rank order is preserved by mapping `keys → chunks` — NOT by filtering `chunks_all` (which would revert to `seq` order, round-1 M1). `recency_select` is provably embedder-free (§6.3; asserted in §9).

### 6.3 `recency_select` (`onload/select.py`, pure, no embedding)
Newest-first (candidates already arrive `seq DESC`), **pins and `proj:*:conventions` excluded**, first-fit under `k` + `token_budget`. **DRY (round-2 M4):** the pin + `_CONV_RE.fullmatch` exclusion and the first-fit budget loop already exist in `onload_select`/`seed_select` (`select.py:31-34, 42-50`); factor them into a small shared helper (`_eligible(candidates)` + `_first_fit(pairs, k, budget)`) that all three reuse, rather than a third copy. `recency_select` is `_first_fit(_eligible(candidates), k, budget)` with no scoring. **Honest note:** DESIGN §4.2 says "tag+recency," but tags here are *tool-provenance, not topic* (`weights.py`: `w_tag` defaults 0), so a tag term wouldn't express relevance — the fallback is **recency-only** in practice. Stated plainly, not silently dropped.

**Constants (round-2 M2):** the hook drops its *imports* of `ONLOAD_WEIGHTS`/`ONLOAD_COSINE_THRESHOLD`, but the constants **remain in `weights.py`** — they are the HashingEmbedder operating point still referenced by `onload_select`'s unit tests (`test_onload_select.py`, `test_onload_weights.py`), now relabeled "legacy/test operating point." The **live curator** uses `ONLOAD_BGE_WEIGHTS`/`ONLOAD_BGE_COSINE_THRESHOLD`. So neither set is orphaned: BGE = live, hashing = tests.

### 6.4 Spawn (`runtime.spawn_detached`)
Launch `python -m context_curator.curator` fully detached so it outlives the hook: POSIX `start_new_session=True`; Windows `creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; `stdin/stdout/stderr → DEVNULL`, `close_fds=True`. Fire-and-forget — the hook does **not** wait (the curator is warm next turn). Only called when the runtime file is **absent or a stale/dead warming file** (a *live* `warming`/`ready` file suppresses spawns — §6.1); the non-blocking single-instance lock (§5.1) is the final backstop against a spawn race — a loser exits 0 before loading bge.

**Detach invariants (round-2 I3 — Windows is the dev platform, so these are CI-verified, not "probably"):** (a) the child survives the parent hook's immediate `sys.exit(0)` — `Popen` returns once the OS process exists, independent of parent lifetime, on both platforms; (b) the child's **fd 1 is `DEVNULL`** (`stdout=DEVNULL`, `close_fds=True`) so the detached curator can **never** write a byte to the hook's stdout — protecting the stdout-only `additionalContext` contract (`_io.py` `json.dump(obj, sys.stdout)`); (c) the spawn happens in the `except` block **before** the hook formats/writes its own stdout, so ordering is spawn-then-write. The §9 integration test verifies child-survival on Windows.

**Same-store guarantee (round-1 M4):** the child must open the *same* DB the capture hooks write to. `resolve_db_path()` already consults `CC_DB_PATH` (the env seam used throughout the hook/test suite), so `spawn_detached` passes the parent's resolved path as `CC_DB_PATH` and the curator's `__main__` calls the same `resolve_db_path()` — one seam, no divergence. (Verify `store/paths.py` reads `CC_DB_PATH`; it does — the M2 hook tests rely on it.)

## 7. Protocol, runtime file, security

- **Runtime file** `<store_dir>/.curator.json`, mode `0600`, atomic temp+`os.replace`: `{state, pid, port, token, dim, started_at, embedder}` where **`state ∈ {provisioning, warming, ready}`** (round-3 I-3). The hook treats it as a *hint*: `ready` → handshake+connect; `provisioning`/`warming` **and live** (pid alive + within deadline, §5.1) → recency this turn, no respawn; absent **or stale/dead warming** → recency + spawn. A stale `ready` file → handshake/connect fails → fallback + respawn.
- **Protocol & HMAC, implementer-precise (round-1 C1 / round-3 I-2):** newline-delimited JSON; mutual handshake then one request/response.
  1. client → `{op:"hello", nonce}` (no prompt, no token). `nonce = secrets.token_hex(16)`, **fresh per connection** (replay protection).
  2. server → `{server:"context-curator", proof}` where `proof = hmac.new(key, nonce.encode(), sha256).hexdigest()`.
  3. client recomputes the same and compares with `hmac.compare_digest`; mismatch/absent → disconnect + fall back (**never sends the prompt**).
  4. client → `{op:"onload"|"ping", ...}`; server → `{ok, keys}` / `{ok:true}`.

  **Key/token details:** `token = secrets.token_hex(32)` (64-char hex string), stored verbatim in `.curator.json`; the HMAC **key = the UTF-8 bytes of that hex string** (both sides use the string form — agree exactly or every proof mismatches). The token is **generated once at §5.1 step 2 and is invariant across the warming→ready rewrite** (round-3 I-2: regenerating it would break a client that read it during `warming`). This is **server→client identity only** — it proves the listener knows the token (⇒ it's our curator, not a port-reuse stranger); the client's *authorization* is implicit in being able to read the `0600` file (same-user), so no separate client-auth step is needed. A warming→ready rewrite between the client's file-read and its connect is benign: same port, same token.
- **Security (§9):** bind `127.0.0.1` only (never off-box); identity is proven by the HMAC handshake (a port-reused stranger can't forge `proof`, and learns neither the token nor the prompt); the runtime file is user-only (`0600`). The curator reads the local store and returns local keys — it transmits nothing outward, so the privacy boundary stays intact. `.curator.json` / `.curator.lock` are git-ignored.

## 8. Latency & fail-open (§4.2)

- **Dark default (flag off):** handshake + `{keys:[]}` — sub-ms; the hook then runs `recency_select`. This is the shipped M4b-1 cost.
- **Warm path, flag ON** cost: loopback connect (<1ms) + HMAC handshake (sub-ms) + **exactly one** bge embed of the short prompt (~10–40ms warm) + ≤ `ONDEMAND_EMBED_CAP`=12 **in-memory** bge embeds of fresh NULLs (§5.4; bounded for exactly this reason; **no DB write on the hot path** — round-3 C-1/C-2, so the deadline never waits on the reconcile lock) + pure-Python cosine over the live set + return. Comfortably inside p50<300ms / p95<600ms.
- **Cold / down path:** `recency_select` (no embedding) — *faster* than M4a's HashingEmbedder path.
- The **single per-request deadline** (§6.1, round-2 I1) bounds the worst case across the 2-round-trip handshake: a hung/slow/warming curator can't blow the budget — the hook hits the deadline and falls back. Candidates are **never** re-embedded wholesale (only ≤12 fresh NULLs); the reconcile owns bulk embedding off the request path.
- **Fail-open everywhere:** capture never touches the curator; onload degrades to recency; the curator crashing or never starting only costs similarity, never a stalled or broken turn. `run_hook` remains fail-open (exit 0, no injection) on any unhandled error.

## 9. Testing

The socket/process machinery is a thin shell around pure logic, so most tests need no daemon:
- **`handle_onload` (pure)** — store + deterministic embedder, no socket: relevant chunk's key returned; off-topic → empty; pins/conventions excluded; gate honored.
- **Policy None-embedding guard (round-1 I5)** — `RelevancePolicy(NullEmbedder()).scored_with_similarity(...)` returns recency-only (sim 0, cos 0) and does **not** raise; existing real-embedder scoring stays byte-identical.
- **Client handshake + fail-open (most important)** — fake transport: a server returning a valid `HMAC(token,nonce)` proof → keys returned + injected in **curator rank order**; a server with a **bad/absent proof → `CuratorUnavailable` and the prompt is NEVER sent** (round-1 C1, asserted on the fake socket's received bytes); `state:"warming"` → `CuratorUnavailable(respawn=False)` (no spawn); connection-refused / timeout / malformed reply → `CuratorUnavailable(respawn=True)` → recency fallback + `spawn_detached` (mocked).
- **`recency_select` (pure)** — newest-first, pins/conventions excluded, k/budget respected; **no embedder constructed** (assert via a monkeypatch that fails if any Embedder is instantiated).
- **`reconcile` (pure)** — NULL rows fill to 384-dim; batch bound respected; the **one-time wrong-dim migration** NULL-then-backfills old 256-dim rows and sets `curator_dim_migrated` so a second run is a no-op (`cc-reembed`-subsumption); **invariant test:** reconcile leaves `seq`/`created_at`/`expires_at` unchanged (round-2-C2 lesson, round-1 I2).
- **On-demand in-memory embed (round-2 C3 / round-3 C-3, flag ON)** — `handle_onload` over a store with a just-captured NULL chunk relevant to the prompt: the chunk is embedded on-demand **in memory** and **selected** using that fresh vector (not an inline reembed-cap artifact, not the stale NULL snapshot) → visible to semantic onload. **The handler writes nothing** — assert the store row is still NULL immediately after the call (round-3 C-1: reconcile, not the handler, persists). Cap respected: ≤ `ONDEMAND_EMBED_CAP` NULLs embedded per request.
- **Handler is read-only (round-3 C-1)** — `handle_onload` is given only a read store; assert no write occurs on the hot path (the write-store connection sees no transaction from the accept thread).
- **Two-connection concurrency (round-2 C4a)** — a reconcile write transaction on the curator's *write* store and a concurrent `all_live_chunks()` read on the *read* store do not raise `SQLITE_MISUSE` / "transaction within a transaction".
- **Capture-vs-reconcile (round-2 C4b)** — a `PostToolUse` capture interleaved with reconcile ticks completes without hitting the 5s `busy_timeout` wall (small batch + yield + back-off).
- **Shared-embedder concurrency (round-3)** — the accept thread and reconcile thread call one embedder's `.embed()` concurrently without error or corruption (deterministic with the hashing test-embedder).
- **Warming liveness (round-2 C1)** — a `warming` file with a **dead pid** OR `started_at` older than the deadline is treated as **absent** by `discover` → `respawn=True`; a live, recent `warming` → `respawn=False`. A simulated bge-load crash leaves **no** `.curator.json` (cleanup), not a frozen `warming`.
- **Dark-mode flag (round-2 I2)** — with `CC_CURATOR_ONLOAD` off, `onload` returns `{keys:[]}` (hook uses recency) while the reconcile still backfills; with it on, keys are returned.
- **`NullEmbedder` + `store.store()`** — writing through a `NullEmbedder` store persists SQL NULL; round-trips as `embedding is None`. **Note (round-2 M1):** the existing store-contract suite stays green only because it injects its *own* (HashingEmbedder) fixture embedder — it is NOT transparent to the NullEmbedder change; the NULL path is covered by this dedicated test, not the contract suite.
- **Request deadline (round-2 I1)** — a fake server that delays past `REQUEST_DEADLINE_S` across the two round-trips → client raises `CuratorUnavailable` within budget (one shared deadline, not per-recv).
- **Runtime file & handshake helpers** — atomic write/read; `0600`; `HMAC(token,nonce)` round-trips; a stale `ready` file → connect fails → fallback (via the client).
- **Lock helper (isolated, round-1 M2)** — two **non-detached** processes/threads contend the non-blocking lock; exactly one acquires, the other returns "already held" immediately. Tested directly (not through detached spawn, whose exit code isn't observable from the spawner).
- **Real-subprocess integration test (slow-marked, `CC_CURATOR_EMBEDDER=hashing`, round-1 M2)** — spawn the curator detached, **poll-with-deadline** for `.curator.json` `state:"ready"`; do a real handshake+`onload` round-trip over loopback (correct keys; a forged proof rejected); then trigger idle-exit (short `CURATOR_IDLE_TIMEOUT_S` via env) and **poll-with-deadline** for file removal. No fixed sleeps; no reliance on reading a detached child's exit code (that's the separate lock-helper test).
- **Latency micro-check** — warm `handle_onload` over a 1000-chunk store (hashing test-embedder) stays well under budget.
- **bge behavior + calibration (optional, `embed`-extra-gated, round-1 I1)** — with real bge: `handle_onload` returns sane keys, AND the **calibration test** asserts the `0.55` gate **separates** a clearly-relevant chunk (clears it) from a clearly-unrelated one (below it) for a sample prompt. Hashing tests prove *plumbing*; this is the only test that exercises bge *behavior* (round-1 M3 — boundary kept sharp). CI stays fast/deterministic (gated off by default).
- **No regression** — M2 capture (now NULL-embedding), M3a policy, M4a onload suites stay green; the M4a HashingEmbedder-onload hook assertions are **rewritten** to the curator-client/recency model (the hook no longer constructs a policy).

## 10. File structure

```
src/context_curator/
  embeddings.py                 # MODIFY: NullEmbedder (dim 384, embed->None; comment: routing convenience)
  policy/relevance.py           # MODIFY: scored_with_similarity guard if task_emb is None (round-1 I5)
  policy/weights.py             # MODIFY: ONLOAD_BGE_COSINE_THRESHOLD=0.55 + ONLOAD_BGE_WEIGHTS
  onload/select.py              # MODIFY: recency_select
  store/sqlite_store.py         # MODIFY: store() None-embedding -> SQL NULL
  hooks/_io.py                  # MODIFY: open_store() uses NullEmbedder
  hooks/user_prompt_submit.py   # MODIFY: curator client + recency fallback (no policy/embedder)
  mcp_server.py                 # MODIFY: build_default_store() uses NullEmbedder
  curator/
    __init__,__main__.py        # NEW: `python -m context_curator.curator`
    server.py                   # NEW: lock, bind, warming->ready, handshake, accept+idle loop, teardown
    handler.py                  # NEW: pure handle_onload
    reconcile.py                # NEW: backfill + one-time wrong-dim migration
    client.py                   # NEW: discover + handshake + request; CuratorUnavailable
    runtime.py                  # NEW: .curator.json (atomic), .curator.lock (non-blocking helper),
                                #      token, HMAC proof, spawn_detached, discover(state)
.gitignore                      # MODIFY: .curator.json, .curator.lock
tests/
  test_curator_handler.py       # NEW: read-only handle_onload + gate + in-memory on-demand embed + dark-flag {keys:[]}
  test_curator_client.py        # NEW: handshake-then-prompt, bad-proof never sends prompt, warming-liveness, single-deadline, refused fail-open
  test_curator_reconcile.py     # NEW: backfill + one-time migration + seq/created_at/expires_at invariant
  test_curator_runtime.py       # NEW: atomic file, 0600, HMAC round-trip, discover(state)
  test_curator_lock.py          # NEW: non-blocking single-instance (non-detached, observable)
  test_curator_integration.py   # NEW: real detached subprocess, poll-for-ready/removed, slow-marked
  test_onload_recency.py        # NEW: recency_select (no embedder constructed)
  test_null_embedder.py         # NEW: NullEmbedder + store NULL round-trip + policy None-guard
  test_hooks_onload.py          # MODIFY: hook now curator-client + recency fallback (rewritten M4a onload tests)
  test_curator_bge.py           # NEW: optional, embed-extra-gated: bge behavior + separation-margin calibration
```

## 11. How this connects forward

- **Re-onload dedup** is now tractable: the curator is a stateful long-lived process, so it can remember what it injected per session/turn and suppress immediate re-injection on a **turn/membership** model (not the wall-clock cooldown M4a rejected). Its own sub-project.
- **packed-BLOB embedding storage** — once bge(384) vectors dominate the store, the JSON-text deserialize cost at scale is the lever to pull; a binary column + migration is a self-contained store optimization.
- **bge gate tuning + the flag flip** — run the (to-be-merged) M3b eval harness with `FastEmbedEmbedder` to derive `ONLOAD_BGE_COSINE_THRESHOLD`/weights against precision/recall/nDCG, replacing the provisional `0.55`. M3b then **flips `CC_CURATOR_ONLOAD` on by default** — turning the curator's already-built, already-tested scoring path live. This is where the **§10.8 differentiator** finally gets a real verdict and live semantic onload actually ships.
- **Offload write-back** (`select_offload` + eviction-regret) — **M5**, where heavy reads route through `cc-explorer` and an active-window representation feeds the policy.

---

## Design Critique Log

Three independent adversarial rounds (fresh opus subagent each, each seeing the prior round's revision). The machinery survived, but each round found genuinely load-bearing flaws — several created by the *previous* round's fix. Findings ranked Critical / Important / Minor; every Critical and Important was resolved in-spec or deferred with rationale.

### Critique Round 1

Daemon/IPC correctness — concurrency, lifecycle, fail-open.

- **C1 (§9 privacy leak) → token-keyed HMAC handshake.** The original protocol sent `{token, prompt}` in the first write. Under a realistic crash + ephemeral-port-reuse sequence, a client would connect to an *unrelated* local process and transmit the prompt + token (the token only authenticates client→server, not server identity). Fixed with a mutual handshake: the server proves token-knowledge via `HMAC(token, client_nonce)` **before** the client sends the prompt; the token is never transmitted.
- **C2 (spawn storm) → publish `warming` before the bge load + non-blocking lock.** The runtime file was written *after* the ~2s model load, so every warmup-window turn re-spawned a bge-loading interpreter (thundering herd). Fixed by writing a `warming` runtime file immediately after bind (pre-load) so warmup turns discover-and-don't-respawn, and mandating a **non-blocking** single-instance lock (losers exit before loading bge).
- **I1–I5:** untuned bge gate → provisional `0.55` (anisotropy-reasoned) + observability + a calibration test; reconcile write-lock duration → embed-outside-transaction + one-time wrong-dim migration + cheap steady-state; idle-exit races → idle handled *inside* the accept loop + lock-released-last teardown; `NullEmbedder.dim` lie → a `task_emb is None` policy guard; curator rank-order preserved by `keys→chunks` mapping; `CC_DB_PATH` same-store seam.

### Critique Round 2

The next layer — seams between the round-1 fixes.

- **C1 (permanent silent wedge) → warming-liveness bound.** Round-1's "discover `warming` → don't respawn" created a worse failure: if the curator crashes *during* the bge load, the file is frozen at `warming` forever and nothing ever respawns — semantic onload silently off permanently. Fixed: `warming` carries `pid`+`started_at`; `discover()` treats it as live only if the pid is alive and within a deadline, else **absent → respawn**; a bge-load exception removes the file.
- **C2 (first-run model download) → `provisioning` state + prefetch + §9 carve-out.** `FastEmbedEmbedder` downloads ~130MB from HuggingFace on first use — "~0.5–2s warm" assumed a warm cache; first run could hang/fail. Added a `provisioning` state with a long deadline, a `--prefetch` prerequisite command, and a precise §9 boundary (*prompt/store content never leave the box; model weights are fetched on first run*).
- **C3 (NULL-until-reconciled defeats the thesis) → on-demand embed of fresh NULLs.** A just-captured chunk relevant to *this* prompt had `embedding=NULL` until a background tick, so the warm semantic path scored it 0 and excluded it — losing exactly the just-captured-context M4a exists to surface. Fixed by embedding the ≤12 most-recent NULL candidates on the request.
- **C4 (two-writer WAL on one connection) → two connections + bounded batches.** `SqliteStore`'s single connection isn't safe for the reconcile thread + accept loop concurrently. Fixed: the curator opens a read store and a write store; reconcile uses small batches + a yield + `SQLITE_BUSY` backoff so capture never hits the 5s wall.
- **I1–I3 / M1–M4:** single per-request deadline across the 2-round-trip handshake; **ship live injection behind a default-OFF `CC_CURATOR_ONLOAD` flag** + a separation-margin calibration test (a single-pair test was theater); detached-survival + child-fd-1=DEVNULL invariants; store-contract "stays green only because it injects its own embedder"; constants retained (BGE=live, hashing=tests); `recency_select` DRY via shared `_eligible`/`_first_fit`.

### Critique Round 3

Implementation-readiness + the collisions between two rounds of layered fixes.

- **The strategic finding (#1/#8) → honest reframe + scope shrink.** With the flag OFF by default, the *entire* semantic scoring path (on-demand embed, `0.55`, calibration, the C-1/C-2/C-3 seam below) is **dead code in the shipped default** — M4b-1 would build+test its hardest engineering for a path that doesn't run. Reframed §1 honestly: **M4b-1 delivers no day-one onload change (recency, as today); it lays the bge-native substrate + proves the daemon; the §10.8 differentiator flips on at M3b.** The scoring path is built and tested behind the flag so the flip is trivial.
- **C-1/C-2/C-3 (the C3↔C4 seam) → collapsed by making on-demand embed in-memory-only.** The round-2 C3 fix (on-demand *persist*) ran on the accept thread but used the reconcile-owned write connection — re-creating the C4 single-connection hazard (C-1), racing the reconcile on the same rows and risking a `busy_timeout` block inside the 250ms deadline (C-2), and — worst — scoring the *stale pre-embed in-memory snapshot* so the persist did nothing this turn (C-3). All three dissolve by: embed the ≤12 NULLs **in memory**, thread the vectors into the candidate objects via `Chunk.model_copy(update=…)` before scoring, and **never write on the hot path** (the reconcile persists later). Handler signature simplifies to `(read_store, embedder, req)`, read-only.
- **C-4 (stale §3.1 data-flow) → rewritten.** The §3.1 diagram still showed the pre-handshake cleartext `{token, prompt}` single-send and the old handler arity — an engineer building from it would implement the insecure path. Rewritten to the handshake + two-store-aware signature.
- **I-1 / I-2 / I-3:** the curator does **not** sweep (hooks own `sweep_expired`) — avoids a double-sweep `cc_meta` TOCTOU; HMAC pinned implementer-precise (`token_hex(32)`, key = UTF-8 of the hex, per-connection `nonce`, `compare_digest`, server→client identity only, **token invariant across warming→ready**); §7's `state` set corrected to include `provisioning`. Plus a shared-embedder thread-safety note (ONNX `Run` is thread-safe; no lock, which would blow the deadline).
