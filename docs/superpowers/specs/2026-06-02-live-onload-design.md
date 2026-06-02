# M4a — Live Onload (the read half) — Design

**Status:** Hardened through 3 adversarial critique rounds (log below)
**Parent design:** `DESIGN.md` v1.3 §4.3 (hooks), §8 M4 (onload path), §4.2 (latency budget), §10.3 (injection fidelity)
**Milestone:** M4a (first half of M4), after M0/M1 + replay + M2 + M3a. Uses M3a's `RelevancePolicy`/`scored`/`pick`, M2's hook I/O (`HookResult`/`run_hook`/`open_store`), the store (`all_live_chunks`), and `HashingEmbedder`. (M3b's eval is independent and not required.)
**Stack:** Python + UV. Live path is uniformly `HashingEmbedder` (256-dim, no model load); bge stays offline-only until the M4b resident process.

---

## 1. Purpose

Close the loop: make the captured store actually feed the model. `UserPromptSubmit` runs the relevance policy over the live store and injects the relevant slice via `additionalContext`; `SessionStart` seeds the durable pinned/convention set. This is the **read half** — capture (M2) writes; onload (M4a) reads back the relevant working set, the page-in moment of the whole thesis.

## 2. Scope & decisions

**In scope (M4a):**
- **The onload mechanism** on a **fast, in-budget embedder** (HashingEmbedder): the two hooks, the `additionalContext` injection path, the relevance gate, observability, and injection fidelity. The embedder is the single swap point so bge drops in later (M4b).
- **Cosine-gated onload selection** (product) + an `additionalContext` formatter.
- Two thin hooks: **`UserPromptSubmit`** (per-prompt relevance onload, pins excluded) and **`SessionStart`** (seed pins + conventions once).
- A small **hook-I/O extension** so a hook can emit `additionalContext` (the verified inject path) on exit 0, with a **stdout-only** contract.
- `settings.json` registration of both hooks.

**Out of scope (deferred):**
- **bge-semantic live onload (deferred to M4b/resident-process).** A per-prompt hook is a fresh OS process; loading the ~130MB bge ONNX model on every `UserPromptSubmit` costs ~0.5–2s, blowing the §4.2 p95<600ms budget every turn. bge-quality live onload needs a *resident warm-model process* (a curator daemon / server-side onload), which is its own design. M4a therefore uses HashingEmbedder live (no model load → in budget) and the embedder is a one-line swap once the resident process lands.
- **Re-onload dedup / cooldown (round-2 M1+I1 — deferred to M4b).** Round-1 added a `last_onloaded_at` wall-clock cooldown to avoid "re-flooding the window every turn." Round-2 showed this is both unnecessary and *wrong* here: `additionalContext` is injected into the current turn's context and is **transient per turn** (it does not permanently accumulate across turns), so re-injecting the relevant slice each turn is not flooding — it is exactly the behavior that keeps relevant context present **across a compaction boundary** (DESIGN §1.3, the headline feature). A wall-clock cooldown cannot tell "already in the window, skip" from "compaction dropped it, must restore," and a 600s cooldown would suppress re-onload *precisely when compaction drops a chunk that is still relevant*. M4a therefore injects the relevant slice every turn (no cooldown, no `touch_onloaded`, no `Store` ABC change). Dedup, if it proves needed, returns in M4b on a **turn/window-membership** model (not wall-clock), re-tuned alongside bge. This cut also dissolves round-2 C2 (touch_onloaded recency-corruption risk), C3 (clock-threading determinism), and I2 (frozen-`Store`-ABC amendment).
- Active-window `select_offload` wiring + eviction-regret; an explicit store re-embed migration (`cc-reembed`) — M4b.
- Content neutralization for the re-onload poisoning vector (§3.3 note) — the §10.6 adversarial milestone.
- The decision log + statusline (M6).

**Locked decisions (from brainstorming + round-1/2 critique):**
1. **Uniform HashingEmbedder live path (round-1 C2/I1/I4 fix).** `open_store()`, `build_default_store()`, AND the onload hook all use `HashingEmbedder()` (256-dim), so captured chunks and the onload task-embedding share one space with **no per-prompt model load, no dim-mismatch re-embed, no cross-process inconsistency**. The onload hook builds `RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)`. bge stays for the offline replay/eval only until the resident-process upgrade. (The original "bge-with-fallback resolver" is dropped from M4a — it doesn't fit the per-process latency budget.)
2. **Raw-cosine gate, reconciled with the score's `sim_floor` (round-1 C3 + round-2 C1).** A pure-recency score floor fails (the newest off-topic chunk always scores ~`w_recency·1.0`); and gating on the affine-*rescaled* `sim` at the default 0.5 floor would require cosine ≥ 0.75 (double-flooring). So eligibility gates on the **raw cosine** (`cos ≥ ONLOAD_COSINE_THRESHOLD`). **Round-2 C1 fix:** the onload `RelevancePolicy` uses a dedicated `ONLOAD_WEIGHTS` whose **`sim_floor` equals the gate threshold** — otherwise the admitted band `cos ∈ [threshold, 0.5)` would rescale to `sim = 0` and rank by recency *only* (the gate would admit chunks the score then ignores). With `sim_floor = threshold`, an admitted chunk's similarity grows from 0 at the gate, so ranking among the eligible is a genuine recency+similarity blend, not recency alone. Off-topic prompt → no eligible chunks → **empty injection** (a valid, common outcome).
3. **HashingEmbedder onload is lexical-overlap + recency, NOT semantic — stated honestly (round-2 C1).** HashingEmbedder cosine counts (hashed) **exact shared tokens**, so a chunk that is semantically on-topic but worded differently (≈zero shared tokens) scores ≈0 and is gate-excluded. M4a's live onload therefore ranks by **lexical overlap + recency**; the bge semantic-recall that demonstrates the DESIGN §10.8 differentiator waits for the resident process (M4b). M4a ships the **mechanism** (hooks, gate, inject path, fidelity) — not a semantic-quality claim — and a gate-discrimination sanity test (§5) checks the gate separates lexically-relevant from off-topic for *this* embedder. The `ONLOAD_COSINE_THRESHOLD` default is an **untuned placeholder** (no eval artifact in M4a; M3b is out of scope), re-derived on the bge swap.
4. **SessionStart/UserPromptSubmit pin split:** SessionStart seeds the durable set (pins + `proj:*:conventions`) once; UserPromptSubmit onloads task-relevant **non-pinned** chunks every turn. No cross-turn dedup (deferred — see out-of-scope).

## 3. Architecture

```
src/context_curator/
  policy/relevance.py  # + scored_with_similarity() (exposes per-chunk raw cosine for the gate)
  policy/weights.py    # ONLOAD_WEIGHTS (sim_floor == gate threshold — round-2 C1)
  onload/
    __init__.py
    select.py          # onload_select() (raw-cosine gate) + seed_select()
    format.py          # format_block() -> additionalContext text
  hooks/
    _io.py             # MODIFY: HookResult.additional_context + run_hook emits the inject JSON (stdout-only)
    user_prompt_submit.py
    session_start.py
.claude/settings.json  # MODIFY: register UserPromptSubmit + SessionStart
```
(No `embeddings.py`/`mcp_server.py`/`open_store` embedder change, and no `Store` change — M4a keeps the uniform HashingEmbedder live path (§3.1) and defers dedup/`touch_onloaded` to M4b (§2).)

### 3.1 Embedder — uniform HashingEmbedder live (round-1 C2)

M4a makes **no embedder change**: `open_store()` (hooks) and `build_default_store()` (MCP server) keep `HashingEmbedder()`, and the onload hook builds `RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)` (the gate-pinned weights — §3.2). So the live path is uniformly 256-dim:
- **No per-prompt model load** — HashingEmbedder is pure-Python hashing, microseconds; the cosine over the live set is the only per-prompt cost. Comfortably within §4.2.
- **No dim-mismatch storm** — captured chunks and the task embedding are both HashingEmbedder(256), so `RelevancePolicy.scored`'s re-embed branch fires at most for an individual chunk persisted with `embedding=None` (a cheap in-space re-embed), never a legacy bulk re-embed storm (§3.2).
- **No cross-process inconsistency** — every process uses the same fixed embedder; no `find_spec`-at-call-time divergence.

**Quality tradeoff (stated honestly):** HashingEmbedder similarity is hashed bag-of-words overlap, not semantic — so live onload ranks by recency + lexical overlap, a real but weaker signal than bge. The embedder is the single swap point (`RelevancePolicy(<embedder>)`); when a resident warm-model process lands (M4b), onload swaps to bge with no other change. The relevance-gate threshold (§3.2) is therefore an **untuned placeholder set for HashingEmbedder**, re-derived on the swap.

### 3.2 Onload selection (`onload/select.py`) — the similarity gate

`RelevancePolicy` gains `scored_with_similarity(task_text, candidates, query_tags=None) -> list[tuple[Chunk, float, float]]` (chunk, total score, **raw cosine** between the task and the chunk — the value *before* the affine rescale), sorted by score DESC. **It MUST keep `scored()`'s `query_tags` parameter (round-3 C2)** — `scored()` uses it for the tag term; a method that dropped it would silently zero the tag contribution and break the M3a `query_tags` tests. To keep one scoring implementation (round-1 M4), `scored()` **delegates**: `return [(c, s) for c, s, _cos in self.scored_with_similarity(task_text, candidates, query_tags)]`.

**Branch restructure (round-3 C3 — not a trivial "expose"):** today `cos` is a local *inside the `else`* of `if emb is None`, and the over-cap / no-embedding paths set `emb=None` and never assign `cos`. To thread the raw cosine out, hoist a `cos = 0.0` before the branch so all three None-producing paths (emb None, dim-mismatch over the re-embed cap, no content match) report `0.0` (round-2 I4: no comparison → cosine 0 → gate-excluded), and only the comparable path overwrites it:
```python
cos = 0.0
if emb is None:
    sim = 0.0
else:
    cos = _cosine(task_emb, emb)
    denom = max(1e-9, 1.0 - w.sim_floor)
    sim = min(1.0, max(0.0, (cos - w.sim_floor) / denom))
# ... score computed as before; append (c, score, cos)
```
On the uniform-256-dim live path every chunk embeds in-space, so the re-embed branch is at most a cheap HashingEmbedder re-embed of a `None`-embedding chunk — **no dim-mismatch storm** (the earlier "never hits the re-embed branch" was too strong: a chunk persisted with `embedding=None` would re-embed, harmlessly).

```python
import re
from context_curator.policy.weights import ONLOAD_WEIGHTS, ONLOAD_COSINE_THRESHOLD   # round-3 C1
from context_curator.tokens import estimate_tokens   # round-1 M5

# proj:{project}:conventions are SessionStart's job — exclude from per-prompt onload (round-3 I1)
_CONV_RE = re.compile(r"proj:[^:]+:conventions")

def onload_select(policy: RelevancePolicy, task_text: str, candidates: list[Chunk], *,
                  cos_threshold: float, k: int, token_budget: int | None) -> list[Chunk]:
    """Per-prompt onload: candidates whose RAW COSINE >= cos_threshold (round-1 C3),
    ranked by full score, first-fit under k+budget. EXCLUDES pins AND proj:*:conventions —
    both are seeded at SessionStart, so onloading them here would double-inject the durable
    set on a post-compaction turn (round-3 I1). `policy` carries ONLOAD_WEIGHTS (sim_floor ==
    cos_threshold, round-2 C1) so the admitted band ranks by a real recency+similarity blend,
    not recency alone. No cross-turn dedup — the relevant slice is (re)injected every turn
    (round-2 M1)."""
    eligible = [(c, score)
                for c, score, cos in policy.scored_with_similarity(task_text, candidates)
                if not c.pin and not _CONV_RE.fullmatch(c.key) and cos >= cos_threshold]
    return policy.pick(eligible, k, token_budget)


def seed_select(store: Store, *, token_budget: int | None) -> list[Chunk]:
    """SessionStart durable set (no task signal, NO embedding): ALL pinned chunks (never
    budget-truncated — round-1 M2) + proj:{project}:conventions under the remaining budget."""
    chunks = store.all_live_chunks()                   # newest-first
    pins = [c for c in chunks if c.pin]                # always included
    conv = [c for c in chunks
            if not c.pin and _CONV_RE.fullmatch(c.key)]   # round-1 I2 (same regex as onload exclusion)
    out, used = list(pins), sum(estimate_tokens(c.content) for c in pins)
    for c in conv:
        t = estimate_tokens(c.content)
        if token_budget is not None and used + t > token_budget:
            break
        out.append(c); used += t
    return out
```
**Constant placement to avoid a circular import (round-3 C1):** `ONLOAD_COSINE_THRESHOLD` and `ONLOAD_WEIGHTS = PolicyWeights(sim_floor=ONLOAD_COSINE_THRESHOLD)` **both live in `policy/weights.py`**, and `select.py` imports both from there. (If the threshold lived in `select.py` while `weights.py` referenced it to build `ONLOAD_WEIGHTS`, and `select.py` imported `ONLOAD_WEIGHTS` back — `weights → select → relevance → weights` — that eager cycle raises `ImportError` at first import. Co-locating them keeps the "gate floor == score floor by construction" guarantee with no cycle.) The remaining onload constants stay in `select.py` (no cross-dependency): `ONLOAD_K=10`, `ONLOAD_TOKEN_BUDGET=1500`, `SEED_TOKEN_BUDGET=1500`. All env-overridable; **untuned placeholders for HashingEmbedder — no M4a eval artifact (round-2 C1); re-derived on the bge swap.**

**Onload ranks at a different operating point than the eval (round-3 I5):** `ONLOAD_WEIGHTS` forces `sim_floor=0.15` (vs the default policy's 0.5), so for a given cosine the rescaled similarity is larger and `w_similarity` weighs more heavily than in the default `RelevancePolicy` the M3a replay validated. This is deliberate (it's the round-2 C1 reconciliation), but it means **the live read-half ranking is NOT the exact operating point the eval scored** — the eval covers the default policy; M4a's live onload uses the gate-pinned variant. M4b re-tunes both together (so §7's "improves the onload ranking here and the eval" applies *after* that unification, not before).

### 3.3 Formatter (`onload/format.py`)

```python
def format_block(chunks: list[Chunk], *, title: str, per_chunk_chars: int = 1200) -> str:
    """Render selected chunks as an additionalContext block, or "" if empty. Each line names
    the source key/provenance so the model knows this is auto-onloaded curated context."""
    if not chunks:
        return ""
    lines = [f"## {title}", "_(auto-onloaded by ContextCurator)_"]
    for c in chunks:
        body = c.content if len(c.content) <= per_chunk_chars else c.content[:per_chunk_chars] + "…"
        lines.append(f"- **[{c.key}]** ({c.source}): {body}")
    return "\n".join(lines)
```
**Budget is approximate, not exact (round-2 M2):** `onload_select`/`seed_select` budget on `estimate_tokens(c.content)` (full content), while `format_block` truncates each chunk to `per_chunk_chars` and adds per-line boilerplate (`- **[key]** (source):`) + the title/marker. So the *rendered* token count differs from the budgeted count — long chunks render smaller (wasted budget), boilerplate renders larger (unbudgeted). `ONLOAD_TOKEN_BUDGET` is therefore a **soft target on selection**, not a hard cap on rendered size. Acceptable at M4a's k≤10 / 1500-token scale (boilerplate ≈ a few dozen tokens); revisit if either grows.

**Poisoning-vector debt (round-1 M3):** `format_block` renders captured tool-result `content` verbatim into `additionalContext`. Per DESIGN §9, re-onloaded tool content is untrusted (prompt-injection vector). M4a **builds** the re-onload path but does **not** neutralize content — the `_(auto-onloaded)_` marker is a label, not a defense. Neutralization is explicitly tracked debt for the §10.6 adversarial milestone; flagged here so it's intentional, not an oversight.

### 3.4 Hook-I/O extension (`hooks/_io.py`)

`HookResult` gains `additional_context: str | None = None`. `run_hook` emits the **verified inject path** (M0 spike): on exit 0 with `additional_context`, write to **stdout** via `json.dump(obj, sys.stdout)`:
```python
{"hookSpecificOutput": {"hookEventName": <event name>, "additionalContext": <text>}}
```
(`hookEventName` read from the event — `hook_event_name`/`hookEventName`.)
**Stdout-only contract (round-1 C1):** Claude Code requires the hook's **stdout to contain ONLY this JSON** — any stray byte breaks parsing and the injection silently fails. Therefore: the inject JSON is the *sole* stdout writer; `message`, `log()`, the open_store DB-path line, and the §3.5 onload breadcrumb all go to **stderr**; capture/guard hooks write **nothing** to stdout. (With the uniform-HashingEmbedder decision there is no fastembed in the hook path, so the fastembed-progress-on-stdout hazard is gone — but the contract is enforced regardless.) `json.dump(obj, sys.stdout)` is used deliberately (it emits **no trailing newline**, unlike `print(json.dumps(...))`); the emit site is commented to that effect so a future "tidy-up" can't reintroduce a stray `\n`.

**Enforcement (round-2 I3):** a unit test asserting stdout `== json.dumps(obj)` (exact bytes, not just "parseable") catches both stray prefixes and the trailing-newline regression. Because a `print()` buried in a transitively-imported module might only fire on certain inputs, the §5 **smoke test runs each hook end-to-end via subprocess** and asserts stdout is empty (capture/guard) or exactly the JSON (inject) — the structural guard that a single in-process capture can't give. Fail-open still applies: any handler error → exit 0, no injection.

### 3.5 The two onload hooks

**`hooks/user_prompt_submit.py`** — `handle(event, store)`:
- `prompt = (event.get("prompt") or "").strip()`; if empty/whitespace → `HookResult(0)` (no injection) and a `"...onloaded 0 (empty prompt)"` breadcrumb (round-2 M3 — a whitespace prompt embeds to the zero vector, so this short-circuit is also a small latency win).
- `policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)` (same embedder `open_store` used → same 256-dim space as the stored chunks; `ONLOAD_WEIGHTS` reconciles the gate and the score floor — §3.2, round-2 C1).
- `chunks = onload_select(policy, prompt, store.all_live_chunks(), cos_threshold=ONLOAD_COSINE_THRESHOLD, k=ONLOAD_K, token_budget=ONLOAD_TOKEN_BUDGET)`.
- **Observability breadcrumb (round-1 I3):** `log(f"context-curator: onloaded {len(chunks)} chunk(s)")` to **stderr** (or `"...onloaded 0 (off-topic)"`); on a handler error the `run_hook` fail-open path already logs `capture failed:` — strengthen it to a distinct `{ALERT} onload failed` so a silently-degraded read half is greppable, not invisible.
- `block = format_block(chunks, title="Relevant context from earlier in this project")`.
- `return HookResult(0, additional_context=block or None)`. `needs_store=True`, fail-open.

**`hooks/session_start.py`** — `handle(event, store)`:
- `chunks = seed_select(store, token_budget=SEED_TOKEN_BUDGET)` (no embedding — round-1 M1).
- **`source` is intentionally ignored (round-3 I1).** SessionStart fires with `source ∈ {startup, resume, compact, clear}`; we re-seed the durable set on **all** of them. This is correct by design: `compact` is the moment the window was just trimmed, so re-seeding pins/conventions is exactly the restore we want. Because `onload_select` now excludes `proj:*:conventions` (round-3 I1), the post-compaction turn does NOT double-inject the durable set (SessionStart seeds it; UserPromptSubmit onloads only the task-relevant non-durable slice).
- `log(f"context-curator: seeded {len(chunks)} pinned/convention chunk(s)")` to stderr.
- `block = format_block(chunks, title="Project context: pinned decisions, contracts, conventions")`.
- `return HookResult(0, additional_context=block or None)`. `needs_store=True`, fail-open. (SessionStart never embeds; the store is opened but no model loads — HashingEmbedder anyway.)

### 3.6 Settings (`.claude/settings.json`)

Populate the M0 empty arrays:
```json
"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "uv run python -m context_curator.hooks.user_prompt_submit"}]}],
"SessionStart":    [{"hooks": [{"type": "command", "command": "uv run python -m context_curator.hooks.session_start"}]}]
```
(`uv run python -m …` per the M2 fix — resolves the venv.)

## 4. Latency (§4.2) — honest (round-1 C2)

`UserPromptSubmit` blocks the turn. Cost = **one HashingEmbedder embed** (pure-Python hashing, microseconds — NO model load) + pure-Python cosine over `all_live_chunks` (uniform 256-dim → **no** per-prompt re-embed) + formatting. The only real cost is the full-store load + JSON-deserialize of stored 256-float embeddings + Pydantic `Chunk` validation per row.

**Store-size ceiling, stated not hand-waved (round-3 I3):** this scales linearly with live-chunk count. The store is **TTL-bounded** (`CAPTURE_TTL_S`, default 86400s, sweeps on open), which caps unbounded growth, but a busy project can still reach hundreds–low-thousands of live chunks. M4a's acceptance includes a **latency test at a declared ceiling**: at **1000 live chunks**, `UserPromptSubmit` p50 must stay `< 300ms` / p95 `< 600ms` (§4.2) on the dev reference machine; the test records the measured number. If a real store exceeds that and trips the budget, the mitigation is the **packed-BLOB embedding** path (deferred to M4b) — documented here as the known ceiling, not discovered in production. `SessionStart` is off the per-turn path and never embeds. **The earlier draft's "bge embed per prompt within budget" was wrong** — a per-process hook has no warm model; that is exactly why M4a uses HashingEmbedder and defers bge to a resident process (§2).

## 5. Testing

Deterministic via a fake/`KeywordEmbedder`-style embedder (no bge in M4a).
- **`scored_with_similarity`** — returns `(chunk, score, raw_cosine)`; `raw_cosine` equals `_cosine(task_emb, chunk_emb)` for the same input; a candidate with `embedding=None` (or beyond the re-embed cap) → third element is **`0.0`** (round-2 I4); `scored()` delegates to it (one scoring impl — round-1 M4); full existing policy suite stays green.
- **Gate↔floor reconciliation (round-2 C1)** — `ONLOAD_WEIGHTS.sim_floor == ONLOAD_COSINE_THRESHOLD`; a chunk admitted at the gate boundary (`cos == threshold`) contributes `sim == 0`, and a chunk above the threshold contributes `sim > 0`, so the admitted band ranks by a real recency+similarity blend (not recency-only).
- **Gate discrimination — non-circular (round-2 C1 / round-3 I4)** — with the actual `HashingEmbedder` (which does **NOT** strip stopwords — `text.lower().split()`, so shared `the/to/a` inflate cosine), the off-topic control chunk must **share common English stopwords with the prompt yet no topical tokens**, and still fall below `cos >= 0.15`; a topically-overlapping chunk clears it. This tests the *threshold against a realistic adversarial control*, not hand-picked zero-overlap text. The test asserts (and the spec documents) that **stopword-driven overlap is the dominant false-positive mode** and that `0.15` is an untuned placeholder — so the gate is "lexically relevant," and a zero-shared-token semantic match would NOT be found (the M4b/bge gap).
- **`onload_select`** — off-topic prompt (all cosines below `cos_threshold`) → `[]`; a relevant chunk (cosine above) → included; **pinned chunks excluded**; **`proj:*:conventions` chunks excluded even when topically relevant** (round-3 I1 — they're SessionStart's job); `k`/`token_budget` respected; ranking among eligible follows the full score; `ONLOAD_WEIGHTS` is passed (not the default) so the admitted band ranks by similarity, not recency-only.
- **`seed_select`** — **ALL pins included even past budget** (round-1 M2); `proj:myapp:conventions` (the real §5 key shape) selected; a non-pin non-convention excluded; a `shared:decisions:naming-conventions` key (ends `-conventions`, not `:conventions`) correctly NOT matched.
- **`format_block`** — empty → `""`; non-empty contains every selected key + the "auto-onloaded" marker; over-long content truncated.
- **`run_hook` inject path (round-1 C1 / round-2 I3)** — capture stdout: a handler returning `additional_context` → stdout `== json.dumps({"hookSpecificOutput": {...}})` **exact bytes** (no leading/trailing bytes, no trailing newline); `log`/`message`/breadcrumb went to stderr only; no `additional_context` → stdout empty.
- **`UserPromptSubmit` golden — against the REAL sqlite backend (round-3 I2)** — run through `open_store()` (the sqlite store that actually ships, not only InMemoryStore) so seq-DESC ordering + deserialize are exercised on the real path: relevant stored chunk → block names it; off-topic → empty; whitespace-only prompt → empty + "empty prompt" breadcrumb (round-2 M3); pinned-only store → empty (pins excluded); a topically-relevant `proj:*:conventions` chunk → NOT in the onload block (round-3 I1).
- **`SessionStart` golden** — pins/conventions → block names them (sqlite backend).
- **Injection fidelity (§10.3)** — keys in the emitted block == keys `onload_select`/`seed_select` returned (no silent drops/extras).
- **Observability (round-1 I3)** — onload emits a stderr breadcrumb with the count; an onload failure emits the distinct `ALERT` marker on stderr.
- **Latency ceiling (round-3 I3)** — seed the sqlite store with **1000 live chunks**, time `UserPromptSubmit handle`; assert p50 `< 300ms` and record the measured value (skippable/`xfail` on slow CI but run on the dev reference machine).
- **No regression** — M2 capture + M3a policy suites stay green (no embedder change, no `Store` change; `scored` still behaves identically via the delegate — **including a `query_tags`-passing case** to prove the delegation preserves the tag term, round-3 C2).
- **Subprocess smoke / stdout structural guard (round-2 I3 / round-3 C4)** — invoke the hook module end-to-end and assert stdout is **exactly** the JSON (inject) or empty (no-injection); stderr has the breadcrumb. **Pin the invocation so `uv`'s own resolver output can't pollute stdout (round-3 C4):** run via `uv run --no-sync python -m context_curator.hooks.user_prompt_submit` (env pre-synced in a fixture) — or invoke the venv interpreter directly — rather than a cold `uv run` that may emit sync progress. Running end-to-end is what catches a stray `print()` anywhere in the import chain.

## 6. File structure

```
src/context_curator/
  policy/relevance.py    # MODIFY: + scored_with_similarity(task_text, candidates, query_tags=None) (raw cosine, 0.0 when emb None); scored() delegates (keeps query_tags — round-3 C2)
  policy/weights.py      # MODIFY: + ONLOAD_COSINE_THRESHOLD + ONLOAD_WEIGHTS=PolicyWeights(sim_floor=ONLOAD_COSINE_THRESHOLD) (co-located, round-3 C1)
  onload/__init__.py     # NEW (empty package marker — no re-exports)
  onload/select.py       # NEW: onload_select/seed_select + ONLOAD_K/ONLOAD_TOKEN_BUDGET/SEED_TOKEN_BUDGET + _CONV_RE
  onload/format.py       # NEW: format_block()
  hooks/_io.py           # MODIFY: HookResult.additional_context + run_hook stdout-only inject JSON
  hooks/user_prompt_submit.py          # NEW
  hooks/session_start.py               # NEW
.claude/settings.json    # MODIFY: register UserPromptSubmit + SessionStart
tests/
  test_onload_select.py  # gate, gate↔floor reconciliation, pins-excluded, seed_select, k/budget
  test_onload_format.py
  test_hooks_onload.py   # run_hook stdout-only inject (exact bytes) + the two handlers + fidelity + observability
  test_hooks_onload_smoke.py            # subprocess end-to-end stdout structural guard (round-2 I3)
tests/test_policy_relevance.py         # MODIFY: scored_with_similarity (+ scored delegates, None-emb → 0.0)
```
(No `embeddings.py`/`mcp_server.py`/`open_store` embedder change — uniform HashingEmbedder live path, §3.1. No `store/` change — dedup/`touch_onloaded` deferred to M4b, §2.)

## 7. How this connects forward

- **M4b** wires `select_offload` once an active-window representation is fed to the policy, adds eviction-regret to the eval, and a one-time store re-embed migration (`cc-reembed`) so a store written before bge can be brought into the bge space in bulk (vs the per-read capped fallback).
- **M3b's tuned weights** (once the corpus grows and a conclusive sweep promotes them) replace `PolicyWeights` defaults. Note (round-3 I5): M4a's onload uses `ONLOAD_WEIGHTS` (gate-pinned `sim_floor`), a *different* operating point than the default policy the eval scores — so M4b's re-tune must **unify** them (re-derive the gate threshold and weights together against the bge corpus) before the eval's verdict transfers to the live ranking.
- **M6** adds the decision log + statusline observing exactly what this hook injected (the §10.3 fidelity check becomes a runtime display).

---

## Design Critique Log

Three independent adversarial rounds (fresh opus subagent each, each seeing the prior round's revision). Findings ranked Critical / Important / Minor; every Critical and Important was resolved in-spec or explicitly deferred with rationale.

### Critique Round 1

**Architecture-breaking finding (C2):** the draft assumed bge embedding "~10–40ms warm" inside the `UserPromptSubmit` hook. But hooks are **fresh OS processes per event** — there is no warm model, so every prompt would pay a ~0.5–2s bge ONNX load, blowing the §4.2 p95<600ms budget *every turn*. Escalated to the user, who chose **ship the onload mechanism on the fast embedder now, defer bge to a resident process.**

- **C2 → Uniform HashingEmbedder live path.** `open_store`, `build_default_store`, and the onload hook all use `HashingEmbedder()` (256-dim). This simultaneously killed **I1** (dim-mismatch re-embed storm: captured + task embeddings now share one space) and **I4** (cross-process embedder inconsistency from `find_spec`-at-call-time). bge stays offline-only (replay/eval) until the M4b resident process; the embedder is the single swap point.
- **C3 → Raw-cosine gate.** Gating on the affine-*rescaled* `sim` at the 0.5 floor actually requires cosine ≥ 0.75 (double-flooring → over-selective). Switched the eligibility gate to the **raw cosine** via a new `scored_with_similarity` that exposes it; provisional `ONLOAD_COSINE_THRESHOLD`.
- **C1 → stdout-only inject contract.** Any stray stdout byte silently breaks Claude Code's `additionalContext` parse. Made the inject JSON the sole stdout writer (`json.dump`); everything else (message, `log`, breadcrumb, DB-path line) → stderr; capture/guard hooks emit nothing on stdout; test asserts stdout is exactly one JSON object.
- **I5 (at the time) → dedup** via `last_onloaded_at` cooldown + a new `Store.touch_onloaded` (later *reversed* in round 2 — see below). **M1–M5:** SessionStart does not embed; pins never budget-truncated; precise `proj:[^:]+:conventions` regex; `scored()` delegates to the new method (one scoring impl); `estimate_tokens` reused.

### Critique Round 2

Fresh reviewer probed the next layer and produced one decisive simplification plus a coherence fix.

- **M1 + I1 → Dedup DEFERRED to M4b (the round's biggest result).** The round-1 wall-clock cooldown was shown to be both unnecessary and *wrong*: `additionalContext` is injected per-turn and **transient** (not cumulative), so re-injecting the relevant slice each turn isn't flooding — it is precisely what restores context **across a compaction boundary** (the product thesis). A 600s cooldown would suppress re-onload *exactly when compaction drops a still-relevant chunk*. Deferring dedup also dissolved round-2 **C2** (`touch_onloaded` could corrupt seq/recency or reset TTL if mis-implemented as a re-store), **C3** (no consistent injectable `now` → non-deterministic tests), and **I2** (amending the frozen `Store` ABC). Net: dropped `touch_onloaded`, the cooldown, the `last_onloaded_at` path, the ABC change, and 2 contract tests. Dedup returns in M4b on a turn/window-membership model if needed.
- **C1 → Gate↔floor reconciliation.** With a raw-cosine gate at 0.15 but the score's `sim_floor=0.5`, the admitted band `cos ∈ [0.15, 0.5)` rescales to `sim=0` and ranks by **recency only** (the gate admits chunks the score then ignores). Fixed with a dedicated `ONLOAD_WEIGHTS = PolicyWeights(sim_floor=ONLOAD_COSINE_THRESHOLD)` so gate threshold == score floor by construction; admitted chunks rank by a real recency+similarity blend. Also added the **honest scope statement**: HashingEmbedder cosine is exact-token overlap, so M4a onload is **lexical-overlap + recency, not semantic** — the DESIGN §10.8 differentiator is not demonstrable until bge/M4b; the threshold default is an untuned placeholder.
- **I3 / I4 / M2 / M3:** structural stdout guard (exact-bytes test + `json.dump` no-trailing-newline note); `scored_with_similarity` third element is `0.0` on the no-embedding branch; `format_block` budget disclosed as a soft target (boilerplate/truncation make rendered ≠ budgeted); empty/whitespace prompt short-circuits via `prompt.strip()`.

### Critique Round 3

Final implementation-readiness pass against the actual code; caught two build-breaking bugs in the seams.

- **C1 (build-breaking) → circular import.** `ONLOAD_WEIGHTS` was placed in `weights.py` (a leaf module) but built from `ONLOAD_COSINE_THRESHOLD` declared in `select.py`, which imports `ONLOAD_WEIGHTS` back → `weights → select → relevance → weights`, an **eager cycle that raises `ImportError` at first import**. Fixed by co-locating `ONLOAD_COSINE_THRESHOLD` *and* `ONLOAD_WEIGHTS` in `weights.py`; `select.py` imports both.
- **C2 (build-breaking) → dropped `query_tags`.** The proposed `scored_with_similarity(task_text, candidates)` omitted `scored()`'s `query_tags` parameter; the delegation would have silently zeroed the tag term and broken the M3a tag tests (not byte-identical). Restored `query_tags=None` on the new signature and threaded it through the delegate; added a `query_tags`-passing regression test.
- **C3 → branch restructure made explicit.** Exposing the raw cosine is not a trivial "return the value": `cos` is scoped inside the `else` of `if emb is None`, and the over-cap path never assigns it. Spec now shows the `cos = 0.0` hoist before the branch so all None-producing paths report 0.0.
- **C4 → subprocess smoke-test flake.** A cold `uv run` can emit resolver/sync text to stdout, which would fail the exact-bytes stdout assertion. Pinned the invocation to `uv run --no-sync` (pre-synced fixture) or the venv interpreter directly.
- **I1 → conventions double-inject.** `proj:*:conventions` chunks are non-pinned, so they were eligible for *both* `seed_select` (SessionStart) and `onload_select` (UserPromptSubmit) → double-injection on a post-compaction turn. Fixed by excluding `_CONV_RE` keys from `onload_select`; documented that SessionStart intentionally re-seeds on **all** `source` values (incl. `compact`).
- **I2 / I3 / I5:** golden + latency tests run against the **real sqlite backend** (not only InMemoryStore); declared a **1000-live-chunk latency ceiling** test with the packed-BLOB path as the documented mitigation; acknowledged that `ONLOAD_WEIGHTS` ranks at a **different operating point than the eval-validated default policy** (M4b must unify them) and corrected §7's implication that they share weights.
- **I4 → non-circular gate test.** HashingEmbedder does **not** strip stopwords, so shared `the/to/a` inflate cosine; the gate-discrimination test now requires the off-topic control to share stopwords yet still fall below the gate, and the spec names stopword overlap as the dominant false-positive mode. **Minor:** `onload/__init__.py` specified as an empty marker; `Chunk.source`/`.key`/`.pin`/`.content` confirmed to exist.
