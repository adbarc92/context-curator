# ContextCurator — Design

**Status:** Draft v1.2
**Target runtime:** Claude Code (CLI), Max plan
**Implementation model:** subagent-driven development (Superpowers methodology)
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
context-management primitives, with offloaded context living in a Redis-backed curated
store exposed to Claude Code (and its subagents) as an MCP server.
Use subagents as the primary offload-by-delegation mechanism.

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
- Networked/multi-machine store. Single machine now; the Redis connection is a config
  value so a Tailscale-networked store is a later config swap, not a rewrite.
- Replacing native compaction or context editing. We sit on top of them, not beside.
- Cross-session *learning*/personalization. v1 is working-set management, not a
  preference model. (Adjacent to the portable fine-tuning idea; explicitly deferred.)

---

## 3. What we adopt vs. what we build

| Layer | Mechanism | Source | Build or adopt |
|---|---|---|---|
| Offload — whole history | Compaction (server-side summarization) | Native | Adopt |
| Offload — tool/thinking bloat | Context editing (`clear_tool_uses_20250919`, `clear_thinking_20251015`) | Native (Agent SDK / API beta) | Adopt where reachable; see §11 |
| Offload — delegation | Subagent context isolation (returns summary only) | Native (CLI) | Adopt + orchestrate |
| Offload — orchestration | Dynamic Workflows (intermediate state stays in the script) | Native (Max) | Adopt for migrations/audits |
| Onload — external docs | Context7 MCP and similar | Third-party | Adopt as one source |
| Persistence | Working-memory store (Redis) | **Build** | Build |
| **Relevance policy** | What to page in/out per subtask | **Build** | **Build — the centerpiece** |
| Wiring | Hooks (seed / inject / capture / guard) | Native + **Build** | Build |

The bottom three rows are the project. The rest is configuration and orchestration.

---

## 4. Architecture (CLI-native)

Five components. The first two are the build; the rest is wiring and adoption.

### 4.1 Curated Store — `context-curator-mcp` (Redis-backed MCP server)
A standalone MCP server attached to Claude Code. It is the durable home for offloaded
context and the read surface for onload. Exposed as tools so that **both the main
session and subagents** can use it (tool-based access works inside subagents; a
swapped memory-tool backend would not).

Tools:
- `cc_store(key, content, tags[], ttl?, pin?)` — write/offload a chunk.
- `cc_retrieve(key)` — exact fetch.
- `cc_query(task_context, tags?, k)` — relevance retrieval (returns ranked chunk refs).
- `cc_list(prefix)` — enumerate (debug/inspection).
- `cc_evict(key)` / `cc_pin(key)` — explicit lifecycle control.

Backed by Redis on the local machine. Namespacing in §5. The MCP-server approach is
preferred over subclassing the Agent-SDK memory tool because it works uniformly in the
CLI and inside subagents today; the SDK memory-tool backend is noted as a future path
(§11) for standalone agents built outside the CLI.

### 4.2 Relevance Policy Engine — `cc-policy`
The centerpiece. Decides, at turn and subtask boundaries:
- **Offload set:** chunks safe to evict from the active window (a finished exploration
  thread, superseded plan, resolved tool output) — written to `context-curator-mcp`, then dropped.
- **Onload set:** chunks worth paging back in for the current subtask, retrieved by
  `cc_query`.

Scoring per chunk: `score = w_r·recency + w_s·task_similarity + w_t·tag_match + pin_bias`.
- `task_similarity`: embedding similarity between the chunk and the current task
  signal (current prompt + active subtask + last N tool calls). Start with a local
  embedding model; the policy interface (§6) hides the implementation.
- Pinned chunks (architectural decisions, API contracts) never auto-evict.

This goes beyond context editing's generic "clear the oldest tool result": it is
*semantic and task-scoped*, and it operates over conversation/project state, not just
tool results.

### 4.3 Hook integration layer
Deterministic Claude Code wiring (`.claude/settings.json` + scripts):
- **SessionStart** → seed working set from `proj:*` and relevant `shared:*` keys.
- **UserPromptSubmit** → run `cc-policy`; inject the selected onload slice via the
  exit-2 context-injection path. (This is the page-in moment.)
- **PostToolUse** → write state deltas to `context-curator-mcp` (decisions, file-touch ledger,
  contract changes); tag for later retrieval. (This is the capture moment.)
- **SubagentStop / Stop** → persist subagent summaries into `shared:*` so the
  orchestrator and future subagents can retrieve them by key.
- **PreToolUse** → guardrails: block writes to prod/sensitive paths; secret scan.

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
`cc-policy` can pull from multiple sources, not just Redis:
- **Context7** (and similar doc-retrieval MCPs) — external, public library/framework
  docs (AWS SDKs, Kubernetes, mobile frameworks). Keeps the model current on API
  surfaces; complementary, not a substitute for any of the above.
- **`context-curator-mcp`** — internal/project/session state (private; never leaves the machine).
- **Project docs / CLAUDE.md** — static project conventions.

The registry abstracts "where context comes from" so adding a source is config.

### 4.6 Native substrate config
Configured once, then left alone: compaction enabled; context editing thresholds set
where the runtime exposes them (§11); subagent model selection; Dynamic Workflows
reserved for large migrations and codebase-wide audits (the orchestration-level
offload — the workflow script holds intermediate state, the main window sees only the
converged result).

---

## 5. Data model

Redis keyspace (single machine, v1):

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
store(key, content, tags, ttl?, pin?) -> {key}
retrieve(key) -> chunk | null
query(task_context, tags?, k) -> [chunk_ref]   # ranked
evict(key) -> {evicted: bool}
pin(key) -> {pinned: bool}
```

**Policy interface** (`cc-policy`):
```
select_onload(task_context, candidates) -> [chunk_ref]
select_offload(active_window_summary, task_context) -> [key]
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
`description` as the auto-invocation trigger, `model` pinned). These serve double duty
— they execute the build *and* ship as part of the delivered system.

- `cc-explorer` — read-only codebase/context gathering; returns summary only.
- `cc-builder` — implements a single milestone task against a frozen interface.
- `cc-reviewer` — two-stage review against the plan and the contract tests.
- `cc-policy-tuner` — runs retrieval-precision evals and proposes weight changes.
- `cc-guard` — audits hooks and Redis namespaces for the security checklist (§9).

Orchestration stays in the main session; subagents never spawn subagents.

---

## 8. Implementation plan — milestones as delegatable tasks

TDD throughout (red/green; watch the test fail first). Each task is scoped small
enough to hand to a fresh `cc-builder` subagent with only the named files and the
relevant interface, then passed to `cc-reviewer`. Build the plumbing before the
orchestration: the policy is only as good as the store beneath it.

**M0 — Scaffold & substrate**
- Stand up Redis locally; `context-curator-mcp` skeleton with the §6 store interface and contract
  tests (no logic yet, just the wire format).
- Enable compaction + subagent model selection; register an empty hook set.
- Adopt Context7 MCP; add a CLAUDE.md rule to invoke it for library docs.
- **Build the replay harness (§10.0)** — session-trace capture + offline replay. The
  rest of the eval depends on it, so it lands here, not later.

**M1 — Working-memory store**
- Implement `cc_store / cc_retrieve / cc_list / cc_evict / cc_pin` against Redis with
  the §5 schema. Contract tests green.
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
  exit-2. Verify the page-in slice is what the eval predicts.

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
- **Privacy boundary:** internal/project context lives only in local Redis. Context7
  receives only library names + topics, never your code — keep that boundary intact.

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
- **Token delta:** active-window tokens with vs. without Curator on a fixed suite.
- **Injection fidelity:** what the policy *selected* equals what actually landed in the
  window (catch silent drops).
- **Behavioral probe:** after an onload, ask a question whose answer lives only in the
  onloaded chunk; confirm the model can answer it.

### 10.4 Layer 3 — Ablation (not on/off)
The methodological core. On/off conflates store + hooks + policy. Run three arms:
1. **Native substrate only** — compaction + context editing (what the CLI already does).
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

**v1 success target:** arm 3 beats both baselines on retrieval (Layer 1) *and* delivers
a meaningful active-token reduction on long sessions with no drop in Layer 4 task success
— a working set that is smaller *and* more relevant, with the centerpiece's value
demonstrated by ablation rather than asserted.

---

## 11. Environment & a known open question

- **Plan:** Max — Dynamic Workflows and comfortable Agent Teams headroom available.
- **CLI vs Agent SDK (open):** context editing (`clear_tool_uses` / `clear_thinking`,
  beta header `context-management-2025-06-27`) and the swappable memory-tool backend
  are first-party in the Agent SDK / API. The Claude Code *CLI* natively uses
  compaction + its memory systems + subagents. **Verify current CLI exposure of
  fine-grained tool-result clearing before M0.** If the CLI doesn't surface it, the
  MCP-server design (§4.1) already gives us the offload/onload surface without it; the
  SDK path becomes relevant only when building standalone agents outside the CLI.
- **remote-control:** Max-eligible; one remote session per machine; interactive-picker
  commands (`/mcp`, `/plugin`, `/resume`) are local-only. SSH-over-Tailscale + Termius
  remains the fallback for raw shell and flaky links.

---

## 12. Deferred decisions

- Embedding model choice for `task_similarity` (local vs. hosted) — settle in M3
  against the eval harness, not by guess.
- Networked/multi-machine store (Tailscale) — config swap post-v1.
- Cross-session learning / portable personalization — separate track.
- Whether `cc-policy` ever runs *as* a Dynamic Workflow for very large offload sweeps.

---

## 13. Reference features (basis, for accuracy)

- Hooks: events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
  SubagentStop, Stop); exit codes 0/1/2; JSON stdin.
- Context editing: `clear_tool_uses_20250919`, `clear_thinking_20251015`; beta header
  `context-management-2025-06-27`.
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
