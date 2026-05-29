# ContextCurator — Design

**Status:** Draft v1.3
**Target runtime:** Claude Code (CLI), Max plan
**Implementation model:** subagent-driven development (Superpowers methodology)
**Stack:** Python + UV (MCP server, hook scripts, eval harness — one language)
**Store backend (v1):** embedded SQLite (no daemon); networked backend is a v2 swap behind the frozen store interface
**Owner:** Alex / OpenBarclay
**License/distribution:** public (open source)

> **Naming convention.** Proper name **ContextCurator** (README, plugin manifest, this
> doc). Package/repo slug and CLI: **`context-curator`**. Conversational shorthand:
> **Curator**. Module namespace prefix: **`cc`** — MCP tools `cc_*`, MCP server
> `context-curator-mcp`, subagents `cc-*`. The name describes the *act* (curating the
> working set by relevance), not a mechanism, so the store backend can change without
> the name going stale.
> *Pre-publish check:* confirm `context-curator` / `contextcurator` is free on npm,
> PyPI, GitHub, and the Claude Code plugin ecosystem before first release.

---

## 1. Problem & thesis

Agentic coding sessions bloat their own context window. Exploration threads, file
reads, tool output, and stale decisions accumulate, and as the window fills,
accuracy and recall degrade ("context rot") — independent of how large the window
is. The goal is not to fit under the token cap; it is to keep the *working set*
small and relevant so quality stays high across long, multi-stage work.

Anthropic already ships the **mechanisms** for moving context in and out of the
window. What no off-the-shelf tool provides is the **policy**: a per-task decision
about *what* to page in and *what* to evict, by relevance to the subtask currently
in flight. That policy layer is the entire differentiated build. Everything else in
this design is substrate we adopt, configure, and orchestrate.

**Core thesis:** Build a relevance-driven working-set policy on top of the native
context-management primitives, with offloaded context living in a curated, durable
store (embedded SQLite, v1) exposed to Claude Code (and its subagents) as an MCP server.

**Mechanism reality (verified against the CLI, §11).** The Claude Code CLI lets a hook
*inject* context (`SessionStart` / `UserPromptSubmit` → `additionalContext` JSON on exit
0) but provides **no mechanism to surgically evict a specific chunk from the live main
window** mid-session. The only context-shrinking forces are automatic compaction,
user-invoked `/compact`, and subagent isolation. ContextCurator therefore does not
"page out" individual chunks. It is a **context-survival + smart-re-onload layer** with
three honest offload mechanisms:

1. **Delegation** — heavy reads route through subagents; raw output never enters the main
   window (the only *active* offload, and it is real).
2. **Compaction-survival** — decisions, contracts, the file-ledger, and exploration
   summaries are written to the store *verbatim* as they happen. Compaction is lossy and
   generic; the store is the durable, structured, queryable record that survives it.
3. **Smart re-onload** — at each prompt, the policy re-injects exactly the relevant slice
   after a compaction or delegation boundary.

So `select_offload` means **"what to persist so it is safe to let compaction drop it,"**
not "evict it now." The differentiated claim narrows accordingly (see §10): not "smaller
window by fiat" but **a working set that stays relevant across compaction/delegation
boundaries, with measurably better onload precision than a recency-only baseline** — a
claim the eval can actually prove or disprove.

---

## 2. Goals / non-goals

**Goals**
- Keep the active context window focused on what the current subtask needs.
- Offload completed/irrelevant context to durable storage; re-onload on demand.
- Make the store shared across multiple sessions and subagents on one machine.
- Be transparent and inspectable — every page-in/page-out decision is logged.
- Ship as a reusable Claude Code plugin that installs identically into both upcoming
  projects (the multi-tenant full-stack app and the mobile/web ecosystem).

**Non-goals (v1)**
- Networked/multi-machine store. Single machine now (embedded SQLite); the store sits
  behind a frozen interface (§6) so a Tailscale-networked backend is a later swap, not a
  rewrite.
- Replacing native compaction or context editing. We sit on top of them, not beside.
- Cross-session *learning*/personalization. v1 is working-set management, not a
  preference model. (Adjacent to the portable fine-tuning idea; explicitly deferred.)

---

## 3. What we adopt vs. what we build

| Layer | Mechanism | Source | Build or adopt |
|---|---|---|---|
| Offload — whole history | Compaction (server-side summarization) | Native | Adopt (we make it non-lossy via the store) |
| Offload — tool/thinking bloat | Context editing (`clear_tool_uses_20250919`, `clear_thinking_20251015`) | Agent SDK / API beta — **NOT exposed in the CLI** (§11) | Out of scope for v1 |
| Offload — delegation | Subagent context isolation (returns summary only) | Native (CLI) | Adopt + orchestrate — the primary active offload |
| Offload — orchestration | Dynamic Workflows (intermediate state stays in the script) | Native (Max) | Adopt for migrations/audits |
| Onload — external docs | Context7 MCP and similar | Third-party | Adopt as one source |
| Persistence | Working-memory store (embedded SQLite, v1) | **Build** | Build |
| **Relevance policy** | What to page in/out per subtask | **Build** | **Build — the centerpiece** |
| Wiring | Hooks (seed / inject / capture / guard) | Native + **Build** | Build |

The bottom three rows are the project. The rest is configuration and orchestration.

---

## 4. Architecture (CLI-native)

Five components. The first two are the build; the rest is wiring and adoption.

### 4.1 Curated Store — `context-curator-mcp` (SQLite-backed MCP server)
A standalone MCP server attached to Claude Code. It is the durable home for offloaded
context and the read surface for onload. Exposed as tools so that **both the main
session and subagents** can use it (tool-based access works inside subagents; a
swapped memory-tool backend would not).

Tools:
- `cc_store(key, content, tags[], ttl?, pin?)` — write/offload a chunk. Computes and
  stores the chunk embedding at write time (see §4.2 / latency budget).
- `cc_retrieve(key)` — exact fetch.
- `cc_query(task_context, tags?, k, token_budget?)` — relevance retrieval. Returns ranked
  chunks **with content** up to `token_budget` (the injection path needs content, not a
  ref it must re-fetch), plus the scores for the decision log.
- `cc_list(prefix)` — enumerate (debug/inspection).
- `cc_evict(key)` / `cc_pin(key)` — explicit *store* lifecycle control (delete-from-store
  and pin). Note: `cc_evict` removes a chunk from the **store**; it does not and cannot
  remove anything from the live context window (§11).

Backed by **embedded SQLite** on the local machine — no daemon, ships inside the plugin,
trivially portable. Vector similarity is brute-force cosine in-process over stored
embeddings, which is ample at single-machine chunk counts; a vector index is a later
optimization, not a v1 need. Namespacing/keyspace in §5. A **networked backend** (e.g.
Redis/Tailscale, the deferred §12 item) drops in behind this same frozen interface with
no policy or hook changes. The MCP-server approach is preferred over subclassing the
Agent-SDK memory tool because it works uniformly in the CLI and inside subagents today;
the SDK memory-tool backend is noted as a future path (§11) for standalone agents built
outside the CLI.

### 4.2 Relevance Policy Engine — `cc-policy`
The centerpiece. Decides, at turn and subtask boundaries:
- **Persist set (`select_offload`):** chunks worth writing to the store so they survive
  compaction *verbatim* — a finished exploration thread, a decision, a contract, resolved
  tool output. The CLI cannot evict these from the live window (§11); persisting them is
  what makes it *safe to let compaction drop them* and re-onload precisely later.
- **Onload set (`select_onload`):** chunks worth re-injecting for the current subtask,
  retrieved by `cc_query` and injected via `additionalContext` (§4.3). This is where the
  centerpiece earns its keep.

Scoring per chunk: `score = w_r·recency + w_s·task_similarity + w_t·tag_match + pin_bias`.
- `task_similarity`: embedding similarity between the chunk and the current task
  signal (current prompt + active subtask + last N tool calls). Start with a local
  embedding model; the policy interface (§6) hides the implementation.
- Pinned chunks (architectural decisions, API contracts) are never dropped from the store
  and are always re-onload candidates.

**Latency budget (`UserPromptSubmit` blocks every turn).** Onload selection runs on the
critical path of each prompt, so it has a hard budget: **p50 < 300 ms, p95 < 600 ms**.
This forces two design constraints: (1) chunk embeddings are computed and stored at
`cc_store` time, never at query time; (2) only the task-signal embedding (one short text)
is computed per prompt, then brute-force cosine against stored vectors. If the budget is
ever blown, onload degrades gracefully to tag+recency (no embedding) rather than stalling
the turn.

This goes beyond a generic "clear the oldest tool result": onload is *semantic and
task-scoped*, and it operates over curated conversation/project state, not just raw tool
results.

### 4.3 Hook integration layer
Deterministic Claude Code wiring (`.claude/settings.json` + Python scripts). The
injection mechanism is **`hookSpecificOutput.additionalContext` JSON on exit 0** —
verified against the CLI (§11). (Exit 2 *blocks* on `UserPromptSubmit`/`PreToolUse`; it
is not an injection path. Earlier drafts said "exit-2 inject" — that was wrong.)
- **SessionStart** → seed working set from `proj:*` and relevant `shared:*` keys; inject
  via `additionalContext`.
- **UserPromptSubmit** → run `cc-policy`; inject the selected onload slice via
  `additionalContext` on exit 0, within the §4.2 latency budget. (This is the page-in
  moment — and, post-compaction, the re-onload moment.)
- **PostToolUse** → write state deltas to `context-curator-mcp` (decisions, file-touch
  ledger, contract changes); tag for later retrieval. (This is the capture moment.)
- **SubagentStop / Stop** → persist subagent summaries into `shared:*` so the
  orchestrator and future subagents can retrieve them by key.
- **PreToolUse** → guardrails: block writes to prod/sensitive paths (exit 2); secret scan.

Hooks run with your shell credentials — every hook is reviewed before registration
(§9).

### 4.4 Subagent offload pattern (architectural, not just implementation)
Subagents are a core part of the *running system*, not only how we build it:
- Heavy reads / codebase exploration → `Explore` subagent → returns a summary → the
  summary is captured to `context-curator-mcp`; the raw output never enters the main window.
- The main session stays the orchestrator (subagents are one level deep and cannot
  spawn subagents — all coordination lives in the main session).
- Working-set entries reference subagent summaries by key, so a later subtask can
  re-onload a prior exploration without re-running it.
- `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` for workers; Opus for the orchestrator.

### 4.5 Onload-source registry
`cc-policy` can pull from multiple sources, not just the local store:
- **Context7** (and similar doc-retrieval MCPs) — external, public library/framework
  docs (AWS SDKs, Kubernetes, mobile frameworks). Keeps the model current on API
  surfaces; complementary, not a substitute for any of the above.
- **`context-curator-mcp`** — internal/project/session state (private; never leaves the machine).
- **Project docs / CLAUDE.md** — static project conventions.

The registry abstracts "where context comes from" so adding a source is config.

### 4.6 Native substrate config
Configured once, then left alone: compaction enabled (the store makes it non-lossy for
curated state); subagent model selection; Dynamic Workflows reserved for large migrations
and codebase-wide audits (the orchestration-level offload — the workflow script holds
intermediate state, the main window sees only the converged result). Context editing
(`clear_tool_uses` / `clear_thinking`) is **not exposed in the CLI** (§11), so there is
nothing to configure there for v1; it is an Agent-SDK concern only.

---

## 5. Data model

Logical keyspace (single machine, v1). Stored in SQLite as a `chunks` table keyed by the
string below, plus a `tags` index and an `embedding` BLOB column; the key grammar is
backend-independent and survives a later networked-store swap:

```
session:{session_id}:turn_log        # rolling per-session activity
session:{session_id}:offloaded:{id}  # chunks evicted from this session's window
shared:contracts:{name}              # API/interface contracts (pinned by default)
shared:decisions:{id}                # architectural decisions (pinned)
shared:exploration:{id}              # subagent exploration summaries
shared:file_ledger                   # who-touched-what across agents
proj:{project}:conventions           # seeded at SessionStart
```

Chunk entry schema (value):
```json
{
  "id": "string",
  "content": "string",
  "tags": ["backend", "auth", "contract"],
  "source": "subagent:explore | tool:read | decision | contract",
  "created_at": "iso8601",
  "last_onloaded_at": "iso8601 | null",
  "pin": false,
  "ttl_s": 86400,
  "provenance": "session_id | subagent_id"     // for poisoning audit (§9)
}
```

Tenant isolation: in the multi-tenant project, prefix tenant-scoped chunks with
`proj:{project}:tenant:{tenant_id}:` and never allow `cc_query` to cross a tenant
boundary (enforced in the server, not by convention).

---

## 6. Interfaces / contracts (define first, so subagents build independently)

Each interface is frozen and contract-tested before any implementation, so a fresh
subagent can build against it with no broader context.

**Store interface** (`context-curator-mcp`):
```
store(key, content, tags, ttl?, pin?) -> {key}          # embeds content at write time
retrieve(key) -> chunk | null
query(task_context, tags?, k, token_budget?) -> [chunk] # ranked, WITH content+score, ≤budget
evict(key) -> {evicted: bool}                           # removes from STORE only, not the window
pin(key) -> {pinned: bool}
```

**Policy interface** (`cc-policy`):
```
select_onload(task_context, candidates) -> [chunk]       # ranked slice to inject, ≤token_budget
select_offload(active_window_summary, task_context) -> [key]  # what to PERSIST so compaction can drop it
score(chunk, task_context) -> float
```

**Hook contract:** each hook is a script reading event JSON on stdin, returning the
documented exit code (0 allow / 1 block+stderr / 2 event-specific inject-or-block) and
JSON for `systemMessage` where used.

**Subagent summary schema:** every specialist subagent returns
`{ summary, artifacts[], contracts_touched[], followups[] }`, which the
`SubagentStop` hook maps directly onto chunk entries.

---

## 7. Subagent topology

Project subagents in `.claude/agents/` (each: tight `tools` allowlist, precise
`description` as the auto-invocation trigger, `model` pinned). Two distinct classes —
kept separate so build scaffolding does not ship as product:

**Runtime (ship as part of the delivered plugin):**
- `cc-explorer` — read-only codebase/context gathering; returns summary only. This is the
  active-offload mechanism (§4.4).
- `cc-guard` — audits hooks and store namespaces for the security checklist (§9).
- `cc-policy-tuner` — runs retrieval-precision evals and proposes weight changes (also
  used at build time, but ships because tuning is an ongoing runtime concern).

**Build-time only (do NOT ship in the plugin):**
- `cc-builder` — implements a single milestone task against a frozen interface.
- `cc-reviewer` — two-stage review against the plan and the contract tests.

Orchestration stays in the main session; subagents never spawn subagents. The M7 package
step bundles only the runtime class.

---

## 8. Implementation plan — milestones as delegatable tasks

TDD throughout (red/green; watch the test fail first). Each task is scoped small
enough to hand to a fresh `cc-builder` subagent with only the named files and the
relevant interface, then passed to `cc-reviewer`. Build the plumbing before the
orchestration: the policy is only as good as the store beneath it.

**M0 — Scaffold & substrate**
- Initialize the Python/UV project; `context-curator-mcp` skeleton (SQLite) with the §6
  store interface and contract tests (no logic yet, just the wire format).
- Confirm compaction + subagent model selection; register an empty hook set.
- Adopt Context7 MCP; add a CLAUDE.md rule to invoke it for library docs.
- **Build the replay harness (§10.0)** — session-trace capture + offline replay. The rest
  of the eval depends on it. *Sequencing note:* M0 is heavy. If it bloats, the replay
  harness may slip to immediately after M1 (store first, then the rig that exercises it) —
  it must land before M3's policy work either way.

**M1 — Working-memory store**
- Implement `cc_store / cc_retrieve / cc_list / cc_evict / cc_pin` against SQLite with the
  §5 schema; store embeddings at write time. Contract tests green.
- Tenant-isolation enforcement in `query`/`retrieve` (server-side).

**M2 — Capture path (hooks: write)**
- `PostToolUse` hook: extract decisions/contracts/file-touches → `cc_store` with tags.
- `SubagentStop` hook: map the subagent summary schema → chunk entries.
- Guardrail hooks (`PreToolUse`): prod-path block + secret scan.

**M3 — Relevance policy engine**
- `score()` + `select_offload()` + `select_onload()` against the frozen policy
  interface. Local embeddings for `task_similarity`. Pin handling.
- Retrieval-precision eval harness (`cc-policy-tuner`): labeled task→chunk fixtures,
  measure precision@k and token delta.

**M4 — Onload path (hooks: inject)**
- `SessionStart` seed; `UserPromptSubmit` runs policy and injects the onload slice via
  `additionalContext` (exit 0), within the §4.2 latency budget. Verify the page-in slice
  is what the eval predicts and that it actually lands in the window (injection fidelity,
  §10.3).

**M5 — Subagent offload loop**
- Wire `cc-explorer` so heavy reads route through it; confirm raw output stays out of
  the main window and the summary lands in `shared:exploration:*`.

**M6 — Inspection & ergonomics**
- Decision log + statusline indicator (working-set size, last page-in/out).
- remote-control sanity pass; document the ultraplan/ultracode ↔ remote-control
  channel conflict (both occupy claude.ai/code) so it isn't hit mid-session.

**M7 — Package**
- Bundle hooks + `context-curator-mcp` + subagents + the orchestration skill into a plugin with
  this DESIGN.md. Install into a scratch repo end-to-end. Portfolio-ready.

---

## 9. Security & multi-tenant considerations

- **Memory poisoning:** chunks are read back into context, so the store is a
  prompt-injection vector. Validate keys/paths, record `provenance` on every chunk,
  and treat tool-sourced content as untrusted on re-onload. `cc-guard` audits this.
- **Hook credentials:** hooks run with your environment's credentials; review every
  hook before registering. No hook makes outbound network calls except the explicit,
  reviewed ones.
- **Tenant isolation:** enforced in `context-curator-mcp` (queries cannot cross a tenant prefix),
  not left to prompt discipline.
- **Privacy boundary:** internal/project context lives only in the local SQLite store on
  disk. Context7 receives only library names + topics, never your code — keep that
  boundary intact.

---

## 10. Testing & evaluation

The hard problem here is **attribution**, not test-writing. Outcomes in an agentic
session are multi-causal and the model is stochastic, so a single end-to-end "Curator
on vs. off" comparison mostly measures noise and never tells you *which* component earned
the result. The plan is therefore layered: deterministic at the bottom where signal is
clean, noisy and ablated at the top where it isn't.

### 10.0 Keystone: the replay harness
Everything depends on this, so it is built first (folded into M0). Capture real session
traces — prompts, tool calls, tool results, subtask boundaries — and replay them through
the policy and hooks **offline**. This converts most evaluation from flaky, expensive
live runs into deterministic replay, which is what makes regression tests, weight
tuning, and CI possible at all. Only Layer 4 then needs live sessions.

### 10.1 Layer 0 — Component correctness (deterministic, no LLM)
Cheap, deterministic, and where most *correctness* lives. Get this airtight first.
- **Store:** contract tests — CRUD, TTL expiry, pins surviving eviction.
- **Tenant isolation (security-critical, 100% pass):** fuzz `cc_query` across tenant
  prefixes; zero crossings tolerated.
- **Hooks:** golden-file tests — known event JSON on stdin → assert exit code + output.
  Prod-path write blocks; planted secret blocks; `UserPromptSubmit` against a seeded
  store injects the expected slice.
- **`score()`:** property tests — a pinned chunk always clears the eviction threshold;
  a tag-matching chunk outranks an unrelated one; recency breaks ties.

### 10.2 Layer 1 — Retrieval quality (offline, IR metrics)
The real evaluation of the centerpiece, treated as the information-retrieval problem it
is: given a task signal, did the policy return the right chunks?
- **Metrics:** precision@k, recall@k, nDCG (ranking matters — we inject top-k), and
  **eviction regret** (an evicted key re-queried a few turns later = a false offload).
- Runs entirely offline against the labeled fixture set, so `w_r/w_s/w_t` and the
  embedding choice can be swept without a live session. Owned by `cc-policy-tuner`.

> **The fixture set is the hard research artifact**, exactly as the GTSDB labels were —
> bad gold labels make precision@k meaningless. Bootstrap it: (1) hand-curate a small
> high-quality set; (2) generate synthetic tasks where the needed context is *planted*,
> so gold is known by construction; (3) grow by retrospective mining (chunks Claude
> actually referenced downstream = weak positives). Keep a **held-out split** — tuning
> weights on the same fixtures you score on Goodharts the metric.

### 10.3 Layer 2 — Mechanism / counterfactual
Verify it *does what it claims* before asking whether it *helps*.
- **Token delta:** active-window tokens with vs. without Curator on a fixed suite. Note
  the reduction comes from delegation routing + letting compaction run without losing
  curated state (§1) — *not* from per-chunk eviction, which the CLI cannot do (§11). This
  is a secondary metric; retrieval precision (Layer 1) is the primary claim.
- **Injection fidelity:** what the policy *selected via `additionalContext`* equals what
  actually landed in the window (catch silent drops). This is the load-bearing mechanism
  check now that injection — not eviction — is the controllable lever.
- **Behavioral probe:** after an onload, ask a question whose answer lives only in the
  onloaded chunk; confirm the model can answer it.

### 10.4 Layer 3 — Ablation (not on/off)
The methodological core. On/off conflates store + hooks + policy. Run three arms:
1. **Native substrate only** — compaction + subagent isolation (what the CLI already does;
   context editing is not a CLI lever, §11).
2. **Store + hooks, recency-only onload** — the dumb baseline.
3. **Full semantic policy.**

Arm 3 must beat *both* 1 and 2 to justify its existence. If recency-only is within noise
of the semantic policy, that is a real, valuable finding — the expensive part isn't
earning its keep, and it's better to learn that from our own eval than to ship it. The
eval is deliberately built to be capable of disproving the value of its own centerpiece.

> **Keystone experiment — arm 3 vs. arm 2** (semantic vs. recency-only) on a properly
> held-out labeled set. This single comparison decides whether the differentiated build
> is differentiated.

### 10.5 Layer 4 — End-to-end outcome (live, noisy)
A fixed suite of long, multi-stage tasks (50+ turns, multiple subsystems — where context
rot actually bites), scored by rubric: tests pass, correct files touched, no
cross-contamination of conventions, no re-deriving already-settled decisions.
- Stochastic, so: n≥5–10 runs per condition, paired design, report distributions with
  bootstrap CIs — never single numbers.
- LLM-as-judge is acceptable for the fuzzy criteria but **calibrated against a small
  human-labeled subset** and treated as a noisy signal.

### 10.6 Adversarial / safety (pass-bar, not better-than-baseline)
A red-team fixture set, not a metric: plant a prompt-injection inside tool output that
gets stored, then confirm it is either not re-onloaded into a sensitive context or is
neutralized, with provenance flagging it. Plus the tenant-leakage fuzzing from Layer 0.
100% pass required.

### 10.7 Observability (runtime, continuous)
- Decision log + statusline (working-set size, last page-in/out).
- `/context` breakdown, statusline, and the decision log must agree.

### 10.8 CI strategy
Layers 0, 1 (frozen fixtures), and 10.6 gate every change. Layer 4 is too expensive to
gate on — it runs periodically as a tracked benchmark. The deterministic layers map onto
the `verification-workflow` skill; the offline eval (10.1–10.4) runs as a delegated
`cc-policy-tuner` job.

**v1 success target (primary):** arm 3 beats both baselines on retrieval quality (Layer 1
/ the arm-3-vs-arm-2 keystone) — the working set is *more relevant* than recency-only,
demonstrated by ablation rather than asserted. **Secondary:** a meaningful active-token
reduction on long sessions (from delegation + non-lossy compaction, §1) with no drop in
Layer 4 task success. The primary target is the honest differentiator and is fully
controllable; the secondary is a welcome-but-not-load-bearing consequence, since the CLI
gives us no per-chunk eviction lever to force it directly (§11).

---

## 11. Environment & resolved mechanism questions

- **Plan:** Max — Dynamic Workflows and comfortable Agent Teams headroom available.
- **CLI context mechanisms (RESOLVED — spike, 2026-05-29):** verified against current
  Claude Code CLI docs/behavior:
  - **Injection works.** `SessionStart` and `UserPromptSubmit` inject context via
    `hookSpecificOutput.additionalContext` on **exit 0**. `PostToolUse` can also inject.
    Exit 2 *blocks* (it is not an injection path) — correcting earlier drafts.
  - **Per-chunk eviction does NOT exist in the CLI.** No hook, command, or MCP tool can
    surgically remove a specific message/tool-result from the live main window. The only
    context-shrinking forces are automatic compaction, user `/compact`, and subagent
    isolation. This is why §1/§4.2 reframe offload as *persist-so-compaction-can-drop-it*
    plus *delegation*, not as eviction.
  - **Context editing** (`clear_tool_uses` / `clear_thinking`, beta header
    `context-management-2025-06-27`) is **Agent-SDK / API-only — not exposed in the CLI**.
    Out of scope for v1; relevant only if we later build standalone agents outside the CLI.
  - **Subagent isolation confirmed:** raw subagent tool output stays out of the main
    window; only the final summary (plus a small metadata trailer) returns. This is our
    primary active-offload mechanism (§4.4).
- **remote-control:** Max-eligible; one remote session per machine; interactive-picker
  commands (`/mcp`, `/plugin`, `/resume`) are local-only. SSH-over-Tailscale + Termius
  remains the fallback for raw shell and flaky links.

---

## 12. Deferred decisions

- Embedding model choice for `task_similarity` (local vs. hosted) — settle in M3
  against the eval harness, not by guess.
- Networked/multi-machine store (e.g. Redis over Tailscale) — a backend swap behind the
  frozen §6 store interface, post-v1.
- Cross-session learning / portable personalization — separate track.
- Whether `cc-policy` ever runs *as* a Dynamic Workflow for very large offload sweeps.

---

## 13. Reference features (basis, for accuracy)

- Hooks: events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
  SubagentStop, Stop); exit codes 0/1/2; JSON stdin.
- Context editing: `clear_tool_uses_20250919`, `clear_thinking_20251015`; beta header
  `context-management-2025-06-27`. **Agent-SDK / API only — not exposed in the CLI (§11).**
- Hook context injection: `hookSpecificOutput.additionalContext` on exit 0
  (`SessionStart`, `UserPromptSubmit`, `PostToolUse`); exit 2 blocks, it does not inject.
- Memory tool: Agent SDK abstract classes for custom storage backends.
- Subagents: `.claude/agents/*.md`; one level deep; `CLAUDE_CODE_SUBAGENT_MODEL`.
- Dynamic Workflows: Max/Team/Enterprise; activated by "workflow" / `/effort ultracode`.
- Superpowers: brainstorm → plan → subagent-driven dev → TDD → review; Iron-Law /
  red-flag skill structure.
- Context7: `resolve-library-id`, `get-library-docs`; public docs only.

---

### Changelog
- **v1** — initial design: CWM as relevance policy over native substrate, Redis MCP
  working-memory store, hook wiring, subagent offload, subagent-driven build plan.
- **v1.1** — expanded §10 into a full layered testing & evaluation plan (replay-harness
  keystone, deterministic component tests, offline IR retrieval metrics, mechanism
  counterfactuals, three-arm ablation, live end-to-end suite, adversarial pass-bar, CI
  strategy); added the replay harness as an explicit M0 deliverable.
- **v1.2** — named the project **ContextCurator**; established the `cc` namespace
  convention; renamed MCP tools (`cc_*`), server (`context-curator-mcp`), and subagents
  (`cc-*`) throughout; retired the `wm`/CWM working-set-manager placeholder.
- **v1.3** — resolved the §11 open question via a CLI mechanism spike. **No per-chunk
  eviction exists in the CLI**, so reframed offload as *delegation + compaction-survival +
  smart re-onload* (§1, §4.2) and narrowed the differentiated claim to *onload precision*
  (§10). Corrected the injection mechanism throughout: `additionalContext` on exit 0, not
  exit-2 (§4.3, M4). Switched the store backend from Redis to **embedded SQLite** for a
  zero-infra, portable v1 (§4.1, §5), with a networked backend deferred behind the frozen
  store interface. Fixed the stack to **Python + UV**. Added a `UserPromptSubmit` latency
  budget and write-time embeddings (§4.2). Split subagents into runtime vs build-time
  classes (§7). Noted context editing is Agent-SDK-only (§4.6, §11).
