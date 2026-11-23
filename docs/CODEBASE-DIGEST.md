# ContextCurator — Codebase Digest (for agents)

> Audience: an agent extending / owning this code.
> Source: branch `feat/track-a-stickiness-ranker` (PR #17, 15 ahead / 0 behind `origin/main` @ `410c417`) · first digested 2026-06-23, refreshed 2026-11-23 · re-verified: `uv run --no-sync pytest` 408 passed / 8 skipped, `ruff check` clean.
> Purpose: ownership / extension + next-steps planning.

## TL;DR
ContextCurator is a **relevance-driven working-set policy + durable context store for Claude Code**, shipped as an installable plugin. Stack: **Python 3.11+ / UV**, embedded **SQLite** store (no daemon), **MCP** stdio server (`cc-mcp`) exposing `cc_*` tools, and five Claude Code **hooks** (`cc-hook-*`) that capture state and inject a re-onload slice each turn. It does **not** evict from the live window (the CLI has no such lever, DESIGN §11) — it *persists* state so compaction can safely drop it, then *re-injects* the relevant slice. The single most important thing to know: **the semantic centerpiece failed its keystone** — +0.056 nDCG on the synthetic corpus vs. a pre-registered +0.1 bar ([keystone-powered.md](superpowers/keystone-powered.md)), and it *loses* to BM25 on real sessions ([keystone-real.md](superpowers/keystone-real.md)), so semantic is dark. The live onload ranker is now **refetch stickiness with a BM25 tie-break (Track A, PR #17)**, whose own powered verdict is still pending on data. M0–M7 are built and v0.0.2 is released/merged.

## Where to look (navigation index)
| I need to… | Go to |
|------------|-------|
| Understand the whole design / claims / eval plan | [DESIGN.md](../DESIGN.md) (§1 thesis, §10 eval, §11 CLI mechanisms) |
| Change the MCP tool surface | [src/context_curator/mcp_server.py](../src/context_curator/mcp_server.py) |
| Change the store (CRUD/TTL/pin/query) | [src/context_curator/store/sqlite_store.py](../src/context_curator/store/sqlite_store.py), interface in [store/interface.py](../src/context_curator/store/interface.py) |
| Change where the DB lives (per-project) | [src/context_curator/store/paths.py](../src/context_curator/store/paths.py) (`resolve_db_path`, honours `$CLAUDE_PROJECT_DIR`) |
| Change capture (what gets stored) | [src/context_curator/hooks/post_tool_use.py](../src/context_curator/hooks/post_tool_use.py), [capture/](../src/context_curator/capture/) |
| Change onload (what gets injected) | [src/context_curator/hooks/user_prompt_submit.py](../src/context_curator/hooks/user_prompt_submit.py), [onload/select.py](../src/context_curator/onload/select.py), [onload/format.py](../src/context_curator/onload/format.py) |
| Change the relevance scoring | [src/context_curator/policy/relevance.py](../src/context_curator/policy/relevance.py), [policy/weights.py](../src/context_curator/policy/weights.py) |
| Change guardrails (prod-path/secret block) | [src/context_curator/hooks/pre_tool_use.py](../src/context_curator/hooks/pre_tool_use.py), [guard/](../src/context_curator/guard/) |
| Work on the semantic/bge curator process | [src/context_curator/curator/](../src/context_curator/curator/) (server/client/runtime/reconcile) |
| Run / tune the eval | [src/context_curator/eval/](../src/context_curator/eval/) (keystone.py, runner.py, sweep.py, precision_gate.py) |
| Replay real sessions offline | [src/context_curator/replay/](../src/context_curator/replay/) (engine.py, ingest.py) |
| Install the plugin | [docs/plugin-install.md](plugin-install.md) |
| Run the stickiness confirmatory eval | [src/context_curator/eval/stickiness.py](../src/context_curator/eval/stickiness.py), prereg [stickiness-powered-prereg.md](superpowers/stickiness-powered-prereg.md) |

## Architecture
Five components (DESIGN §4); first two are the build, the rest is wiring/adoption. Single Python package `src/context_curator/`, 73 modules, 68 test files.

| Unit | Path | Purpose |
|------|------|---------|
| MCP server (`cc-mcp`) | `mcp_server.py` | Long-lived stdio server; `_StoreFacade` registers `cc_store/retrieve/query/list/evict/pin` |
| Store | `store/` | SQLite-backed `chunks` table + tags + embedding BLOB; frozen `Store` interface |
| Policy | `policy/` | `scored()`/`pick()`/`select_onload()`/`select_offload()`; `score = w_r·recency + w_s·sim + w_t·tag + pin_bias` |
| Hooks | `hooks/` | 5 entry points: session_start, user_prompt_submit, pre/post_tool_use, subagent_stop (+ `_io.py`) |
| Capture | `capture/` | file_ledger, subagent summary, tool_result extraction |
| Onload | `onload/` | `select.py` (slice selection) + `format.py` (additionalContext rendering) |
| Guard | `guard/` | prod-path + secret-scan guardrails for PreToolUse |
| Curator | `curator/` | detached semantic/bge process (warm embeddings); client/server/runtime/reconcile/lock |
| Embeddings | `embeddings.py` | `Embedder` protocol; `NullEmbedder` default (dark), fastembed/bge optional `[embed]` extra |
| Eval | `eval/` | IR metrics, keystone 3-arm ablation, bm25 baseline, gold judge, sweeps, precision gate |
| Replay | `replay/` | session-trace capture + deterministic offline replay (the eval keystone, DESIGN §10.0) |
| Observe | `observe/` | `cc-inspect` decision log + `cc-statusline` |

## Key flows
### Capture (write) — PostToolUse
`PostToolUse` event JSON on stdin → `hooks/post_tool_use.py` → `capture/*` extract decisions/contracts/file-touches → `Store.store(key, content, tags, …)` (embeds at write time per DESIGN §4.2). `SubagentStop` maps the `{summary, artifacts, contracts_touched, followups}` schema → chunks under `shared:exploration:*`.

### Onload (inject) — UserPromptSubmit
`UserPromptSubmit` on stdin → `hooks/user_prompt_submit.py` → asks the (dark) curator; with no keys, `onload/select.py::stickiness_select` ranks the newest `ONLOAD_POOL_CHARS` (4 M chars) of `Store.all_live_chunks()` by `onload/stickiness.py::stickiness_rank` (refetch count of the chunk's `entity:` path tags, BM25 tie-break, newer first), drops byte-identical duplicates, and first-fits top-k under the token budget measured on the **rendered** body → `onload/format.py` emits `hookSpecificOutput.additionalContext` on **exit 0**. Hard latency budget p50<300ms/p95<600ms. Default injection is `[stickiness]` (Track A, 2026-11-22; was `[recency]`); `[curator]` semantic path needs the curator warmed + flag/`[embed]`. PostToolUse writes the `entity:<canonical path>` tags (`context_curator/entities.py`, shared with the eval harvest) and keys chunks on Claude Code's `tool_use_id`.

### Guard — PreToolUse
`PreToolUse` → `hooks/pre_tool_use.py` → `guard/paths.py` + `guard/secrets.py` → **exit 2 blocks** (prod-path write / planted secret), exit 0 allows.

## Contracts (integration surface)
### MCP tools (`mcp_server.py`)
| Tool | Purpose |
|------|---------|
| `cc_store(key, content, tags?, ttl_s?, pin?, source?, provenance?)` | write/offload; returns key |
| `cc_retrieve(key)` | exact fetch → chunk dict or null |
| `cc_query(task_context, tags?, k=10, token_budget?)` | ranked retrieval (recency v1) → chunk dicts |
| `cc_list(prefix)` / `cc_evict(key)` / `cc_pin(key)` | enumerate / delete-from-store / pin |

### Console & GUI entry points (`pyproject.toml`)
- Console scripts: `cc-mcp` (server), `cc-inspect` (decision log), `cc-statusline`.
- **GUI scripts (pythonw, no console window): `cc-hook-session-start|user-prompt|pre-tool-use|post-tool-use|subagent-stop`** — must stay gui-scripts; `test_entry_points.py` guards regression.

### Plugin manifests
`.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, `.claude-plugin/marketplace.json` — all invoke bare `cc-*` shims installed via `uv tool install --editable .`.

### Config & environment
| Var | Notes |
|-----|-------|
| `CLAUDE_PROJECT_DIR` | `resolve_db_path` uses it for per-project store location (verified to reach `cc-mcp`, #14 closed) |
| `CC_DB_PATH` | explicit DB path override (not needed for the plugin; kept for manual runs) |
| `CC_ALLOWED_PREFIX` | server-side keyspace/tenant isolation prefix |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `sonnet` for workers, Opus orchestrator (DESIGN §4.4) |
| optional extra `[embed]` | fastembed + onnxruntime for the bge semantic path |

## Build · run · test
Package manager: **UV** (`uv.lock` present). **Where the plugin is installed, `cc-mcp` comes from `uv tool` and its exe is locked while running; always test with `uv run --no-sync`** to avoid the rebuild-vs-locked-exe collision (`os error 32`, Windows).
- Install: `uv sync --all-groups`
- Test: `uv run --no-sync pytest -p no:cacheprovider` — green at HEAD (408 passed / 8 skipped, 2026-11-23). Former flake `test_curator_lifecycle_and_handshake` (idle-timeout under full-suite load) was fixed in `11c01c6`.
- Lint: `uv run ruff check .` (line-length 100, py311, rules E/F/I/UP/B) — **clean (0 errors)** since the temp #14 diagnostic was reverted.
- Install as plugin: `uv tool install --editable . ; uv tool update-shell` + restart Claude.
- Verify installed plugin (Windows): `pwsh -NoProfile -File scripts/verify-plugin.ps1` → expect 4 `OK:` lines + `VERIFY PASS`.
- While the plugin's `cc-mcp` is alive, use `uv run --no-sync` to avoid rebuild-vs-locked-exe collision.

## Status (milestones)
M0–M7 **all implemented**. v0.0.2 released (tag `v0.0.2`). `main` @ `410c417` contains:
- **#13** — M7 plugin packaging (v0.0.2).
- **#15** — post-M7 close-out: #14's exit criteria verified YES (`CLAUDE_PROJECT_DIR` reaches `cc-mcp`; plugin resolves bare `cc-*` on PATH) and the M4d real-data verdict (semantic loses to BM25 → semantic dark).
- **#16** — Track B, learned-ranker Cycle 1 feasibility ([keystone-learned.md](superpowers/keystone-learned.md)): learned 0.306 vs BM25 0.102 nDCG@10, +0.203, clustered 90% CI [+0.073, +0.372] → **GO to Cycle 2**. Its circularity audit found prior-refetch *alone* scores 0.445 — above the learned model — which is what Track A ships.

**In flight — Track A (PR #17, `feat/track-a-stickiness-ranker`):** onload ranks by refetch stickiness with a BM25 tie-break ([design](superpowers/specs/2026-11-22-track-a-stickiness-onload-design.md), [plan](superpowers/plans/2026-11-22-track-a-stickiness-onload.md)), plus the `tool_use_id` keying fix and `entity:` tags at capture. Independent review addressed (`1980ae7`). The final verdict is gated on the frozen powered run — see *Open questions*.

**Not started — Track B Cycle 2** (full learned-ranker build + wiring, gated sketch in the [learned-ranker spec](superpowers/specs/2026-06-25-learned-onload-ranker-design.md) §4; pickup in [handoff-2026-06-26-learned-ranker.md](handoff/handoff-2026-06-26-learned-ranker.md)). Its sequencing against the Track A verdict is undecided.

Open issues: **#12** only (upstream Claude Code spawns hook/MCP exes without `windowsHide` — the gui-script/pythonw mitigation reduced but did **not** eliminate the flash). #14 was closed 2026-07-07 with its residual-flash sub-item moved to #12.

## Gotchas & invariants
- **NEGATIVE keystone result — and the synthetic question is SETTLED, not weak-gold.** Synthetic (M4c, [keystone-powered.md](superpowers/keystone-powered.md)): semantic nDCG@10=0.880 vs BM25 0.811, effect **+0.056, 90% CI [0.009, 0.102]**, n=26 — CI *excludes 0* (effect is real) but below pre-registered MEI +0.10 ⇒ powered NEGATIVE. The corpus is rigorously fair (blind gold-judge dropped 5/40, 0/80 hard-neg FP, recency-mixed 12/12/11, circularity-guarded) and built *favorable* to semantic (paraphrased gold + lexically-tempting negatives). The pilot (n=8) showed +0.129 (looked GREEN) but the powered run **regressed it to +0.056** — so growing the synthetic corpus will only tighten the CI around a sub-MEI effect, not flip it. Recency arm 0.428 is badly beaten, so store+hooks clearly help — only *semantic vs BM25* is the null. bge floats are machine-sensitive: **regenerate, don't diff.**
- **Real-data keystone (M4d) is now SETTLED — powered, and semantic LOSES to BM25.** [keystone-real.md](superpowers/keystone-real.md) (2026-06-25): re-run on **5 real sessions / 5 projects** (154 test fixtures, 4 test sessions ≥ floor 3). Headline semantic − strongest cheap baseline = **−0.053 nDCG@10**, session-clustered 90% CI **[−0.085, −0.041]** (width 0.044 ≤ MEI ⇒ precision gate = *verdict*). Arms: BM25 **0.100** > recency 0.090 ≈ semantic 0.091. Both fairness diagnostics clean (lexical-bias non-degenerate; recency thirds ≈ even), so it's not an artifact. ⇒ **Decision made:** ship BM25 as the onload ranker (since superseded by Track A stickiness, BM25 as tie-break), demote semantic to dark ([decisions/semantic-ranker.md](decisions/semantic-ranker.md)). Store + hooks + guardrails keep (recency crushes the empty condition); only the *semantic differentiator* is dead. bge floats machine-sensitive: **regenerate, don't diff.**
- **Never run both wirings.** Dev `.claude/settings.json` hooks + installed plugin + running `cc-mcp` → dev `uv run` can't replace the locked `cc-mcp.exe` (`os error 32`), and on `PreToolUse` that **blocks every Write/Edit/Bash**. M7's `settings.json` is `{}` for this reason. Recover from a shell *outside* Claude. Branch curator-runtime fixes off an M7 tree, not `main`.
- **Hooks must stay `[project.gui-scripts]`** (pythonw) — console scripts reintroduce Windows console-window flashing (#12). Piped stdin/stdout still works under pythonw because Claude pipes the handles.
- **No per-chunk eviction exists in the CLI** (DESIGN §11). `cc_evict` removes from the *store*, never the live window. Don't design features that assume window eviction.
- **Default injection is `[stickiness]`/`NullEmbedder`** (Track A; `[recency]` before 2026-11-22). Semantic `[curator]` stays dark: it requires the curator warmed-to-ready + flag/`[embed]` extra.
- **Measured = shipped.** The eval scores the live `stickiness_rank` itself (`python -m context_curator.eval.stickiness`); `bm25_scores` lives in `context_curator/bm25.py` and must stay bit-identical to the published implementation (`tests/eval/test_bm25_equivalence.py`). Do not fork either for the hook.
- **Privacy boundary (DESIGN §9):** project context stays in local SQLite. Context7 receives only library names + topics, never project code.
- **Tenant isolation enforced server-side** (`CC_ALLOWED_PREFIX`), not by convention — `cc_query` must never cross a prefix.

## Open questions / unverified
- Exit-criterion (a) **RESOLVED YES** (2026-06-25): `CLAUDE_PROJECT_DIR` reaches `cc-mcp` (Claude Code MCP docs v2.1.139+ parity; corroborated by per-project `store.db` placement). No `CC_DB_PATH` fallback needed.
- gui-script flash mitigation **CONFIRMED PARTIAL** (#12): still flashes sometimes — residual flicker stays open under #12 (#14 closed 2026-07-07).
- The keystone decision is **MADE** (no longer open): both synthetic (powered NEGATIVE, +0.056 < MEI) and real-data (powered, semantic −0.053 *loses* to BM25) agree ⇒ demote semantic to dark ([decisions/semantic-ranker.md](decisions/semantic-ranker.md)). The live ranker is now stickiness with a BM25 tie-break (amendment in that doc).
- **Open (evidence gate):** the stickiness ranker's powered confirmatory run needs **≥ 45 harvestable main-session transcripts disjoint from the 5 discovery sessions** ([stickiness-powered-prereg.md](superpowers/stickiness-powered-prereg.md)). On 2026-11-22 the Linux machine had 2 harvestable sessions (13 main transcripts; subagent transcripts yield no fixtures). Collect transcripts from every machine into `src/context_curator/eval/fixtures/_real_local/` and run `uv run python -m context_curator.eval.stickiness`.
