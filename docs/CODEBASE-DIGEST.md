# ContextCurator — Codebase Digest (for agents)

> Audience: an agent extending / owning this code.
> Source: branch `chore/post-m7-followups` @ `1ff3ea2` (4 ahead / 1 behind `origin/main` @ `63afb5d`, PR #13 merged) · 2026-06-23 · digested by reading ~14 files + manifests + eval results (rest inferred from DESIGN.md §1–13). Re-verified: `uv run --no-sync pytest` green (exit 0).
> Purpose: ownership / extension + next-steps planning.

## TL;DR
ContextCurator is a **relevance-driven working-set policy + durable context store for Claude Code**, shipped as an installable plugin. Stack: **Python 3.11+ / UV**, embedded **SQLite** store (no daemon), **MCP** stdio server (`cc-mcp`) exposing `cc_*` tools, and five Claude Code **hooks** (`cc-hook-*`) that capture state and inject a re-onload slice each turn. It does **not** evict from the live window (the CLI has no such lever, DESIGN §11) — it *persists* state so compaction can safely drop it, then *re-injects* the relevant slice. The single most important thing to know: **the keystone eval came back NEGATIVE** — the semantic policy beats the strongest baseline by only +0.056 nDCG vs. a pre-registered +0.1 bar ([results/keystone-10.md](../results/keystone-10.md)). The differentiated centerpiece is, as of now, *not* differentiated by its own success criterion. M0–M7 are built and v0.0.2 is released/merged.

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
| Install the plugin | [docs/plugin-install.md](plugin-install.md); out-of-session runbook [docs/M7-runbook.md](M7-runbook.md) |

## Architecture
Five components (DESIGN §4); first two are the build, the rest is wiring/adoption. Single Python package `src/context_curator/`, ~55 modules, 70 test files.

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
`UserPromptSubmit` on stdin → `hooks/user_prompt_submit.py` → policy scores live candidates (`Store.all_live_chunks()`) → `onload/select.py` picks top-k under token budget → `onload/format.py` emits `hookSpecificOutput.additionalContext` on **exit 0**. Hard latency budget p50<300ms/p95<600ms; degrades to tag+recency if blown. Default injection is `[recency]` (dark); `[curator]` semantic path needs the curator warmed + flag/`[embed]`.

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
| `CLAUDE_PROJECT_DIR` | `resolve_db_path` uses it for per-project store location (exit-criterion (a), unverified — #14) |
| `CC_DB_PATH` | explicit DB path override (documented fallback if `CLAUDE_PROJECT_DIR` doesn't reach `cc-mcp`) |
| `CC_ALLOWED_PREFIX` | server-side keyspace/tenant isolation prefix |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `sonnet` for workers, Opus orchestrator (DESIGN §4.4) |
| optional extra `[embed]` | fastembed + onnxruntime for the bge semantic path |

## Build · run · test
Package manager: **UV** (`uv.lock` present). **`cc-mcp` is installed via `uv tool` (currently `v0.0.1`) → the exe is locked; always test with `uv run --no-sync`** to avoid the rebuild-vs-locked-exe collision (`os error 32`).
- Install: `uv sync --all-groups`
- Test: `uv run --no-sync pytest -p no:cacheprovider` — green at HEAD (exit 0; ~346 passed / 6 skipped). Former flake `test_curator_lifecycle_and_handshake` (idle-timeout under full-suite load) was fixed in `11c01c6`.
- Lint: `uv run ruff check .` (line-length 100, py311, rules E/F/I/UP/B) — **clean (0 errors)** since the temp #14 diagnostic was reverted.
- Install as plugin: `uv tool install --editable . ; uv tool update-shell` + restart Claude.
- Verify installed plugin (Windows): `pwsh -NoProfile -File scripts/verify-plugin.ps1` → expect 4 `OK:` lines + `VERIFY PASS`.
- While the plugin's `cc-mcp` is alive, use `uv run --no-sync` to avoid rebuild-vs-locked-exe collision.

## Status (milestones)
M0–M7 **all implemented**. v0.0.2 released (tag `v0.0.2`), **PR #13 merged to main** (`63afb5d`). Work branch **`chore/post-m7-followups`** (off the merged tree) is landing the post-M7 close-out: **#14's two exit criteria are now verified YES** (2026-06-25) — `CLAUDE_PROJECT_DIR` reaches `cc-mcp` (official Claude Code MCP docs, v2.1.139+ parity; this machine v2.1.190; corroborated by per-project `store.db` placement) and the plugin resolves bare `cc-*` on PATH (18 successful stdio connections across ~8 projects). The temp #14 stderr diagnostic (`990c9fb`) has been **reverted**, and the now-spent `M7-runbook.md` + `Session-Pickup-2026-06-04.md` **deleted**. (Earlier `feat/m7-plugin-package` branch is stale.)

Open issues: **#14** (out-of-session M7 verification — both exit criteria (a)+(b) recorded YES; **stays open** because the gui-script mitigation only *partially* stopped the Windows console flash — user reports it still flashes sometimes); **#12** (upstream Claude Code spawns hook/MCP exes without `windowsHide` — our gui-script/pythonw mitigation reduced but did **not** eliminate the flash; live investigation surface for the residual flicker).

The one remaining product lever (real-data M4d keystone verdict) and the close-out steps are decomposed into parallel lanes in **[`docs/handoff/handoff-2026-06-25-swarm.md`](handoff/handoff-2026-06-25-swarm.md)**.

## Gotchas & invariants
- **NEGATIVE keystone result — and the synthetic question is SETTLED, not weak-gold.** Synthetic (M4c, [keystone-powered.md](superpowers/keystone-powered.md)): semantic nDCG@10=0.880 vs BM25 0.811, effect **+0.056, 90% CI [0.009, 0.102]**, n=26 — CI *excludes 0* (effect is real) but below pre-registered MEI +0.10 ⇒ powered NEGATIVE. The corpus is rigorously fair (blind gold-judge dropped 5/40, 0/80 hard-neg FP, recency-mixed 12/12/11, circularity-guarded) and built *favorable* to semantic (paraphrased gold + lexically-tempting negatives). The pilot (n=8) showed +0.129 (looked GREEN) but the powered run **regressed it to +0.056** — so growing the synthetic corpus will only tighten the CI around a sub-MEI effect, not flip it. Recency arm 0.428 is badly beaten, so store+hooks clearly help — only *semantic vs BM25* is the null. bge floats are machine-sensitive: **regenerate, don't diff.**
- **Real-data keystone (M4d) is the only live lever — and it's blocked on data, not code.** [keystone-real.md](superpowers/keystone-real.md): harness ran end-to-end on real transcripts but returned **HARNESS-ONLY (no verdict)** because `n_sessions=1 < 3` floor. To get a real verdict: drop ≥2 more `.jsonl` work-sessions into `src/context_curator/eval/fixtures/_real_local/` (gitignored) and re-run harvest → keystone → cluster_bootstrap → precision_gate. Encouraging diagnostic: on real data, gold is **not** a BM25 proxy (lexical-bias guard non-degenerate) — the central methodological worry doesn't materialize. Production stays dark (`CC_CURATOR_ONLOAD="0"`) until a powered real verdict exists.
- **Never run both wirings.** Dev `.claude/settings.json` hooks + installed plugin + running `cc-mcp` → dev `uv run` can't replace the locked `cc-mcp.exe` (`os error 32`), and on `PreToolUse` that **blocks every Write/Edit/Bash**. M7's `settings.json` is `{}` for this reason. Recover from a shell *outside* Claude. Branch curator-runtime fixes off an M7 tree, not `main`.
- **Hooks must stay `[project.gui-scripts]`** (pythonw) — console scripts reintroduce Windows console-window flashing (#12). Piped stdin/stdout still works under pythonw because Claude pipes the handles.
- **No per-chunk eviction exists in the CLI** (DESIGN §11). `cc_evict` removes from the *store*, never the live window. Don't design features that assume window eviction.
- **Default injection is `[recency]`/`NullEmbedder` (dark).** Semantic `[curator]` requires the curator warmed-to-ready + flag/`[embed]` extra.
- **Privacy boundary (DESIGN §9):** project context stays in local SQLite. Context7 receives only library names + topics, never project code.
- **Tenant isolation enforced server-side** (`CC_ALLOWED_PREFIX`), not by convention — `cc_query` must never cross a prefix.

## Open questions / unverified
- Exit-criterion (a): does `CLAUDE_PROJECT_DIR` actually reach `cc-mcp`'s `os.environ`? Unconfirmed (#14). If NO, pin `CC_DB_PATH` as the documented default.
- gui-script hooks empirically stopping the flash requires a Claude reinstall+restart to confirm (#14).
- The keystone negative: synthetic is settled (powered NEGATIVE on a fair corpus). The open decision is whether to (b) ship BM25 as the ranker and demote/drop the semantic path, or (c) keep semantic optional/dark — **pending the M4d real-data verdict**, which needs ≥2 more captured sessions. Growing the *synthetic* corpus is NOT a productive path (the effect shrank from pilot→powered). Product call, not yet made in-repo.
