# M7 — Package as a Claude Code Plugin

## 1. Purpose
Package the **real, built** components of ContextCurator into an installable Claude Code **plugin**
that works when installed into *any* repo (not just the dev checkout): the 5 hooks + the
`context-curator-mcp` server, with a **per-project store**. The success test (DESIGN.md M7): install
into a throwaway scratch repo and have the hooks + MCP work end-to-end **through the real
(marketplace/cache) install path**, not just `--plugin-dir`.

**Invocation architecture (set by round 1):** the plugin carries **no Python venv**. Claude Code
copies a marketplace plugin to `~/.claude/plugins/cache` and runs it from there; a `.venv` is
gitignored and never copied, so `uv run --project "${CLAUDE_PLUGIN_ROOT}"` would cold-sync or fail on
every real install. Instead, the curator is installed **machine-globally** via `uv tool install
<checkout>` (no PyPI), which exposes **console-script entry points on PATH**; the plugin's hooks/MCP
call those bare console-script names directly — no `${CLAUDE_PLUGIN_ROOT}`, no `uv run`, no venv in the
plugin cache, and no per-hook sync latency.

**In scope:** console-script entry points for the hooks + MCP; the plugin manifest + hooks + MCP +
local marketplace (pure config); the one-line `$CLAUDE_PROJECT_DIR` per-project store tweak; the
dev↔plugin double-registration fix; an install/verify procedure (doc + script) exercising the **cache
path**; the deferred remote-control caveat note.

**Out of scope (recorded as future work):** the `cc-explorer`/`cc-guard` subagents and orchestration
skill — *never built* (M5 deferred the offload loop); this is a deliberate divergence from DESIGN.md
M7's wording (line 358), recorded so the milestone isn't later judged "incomplete." PyPI publishing.
A plugin-provided `statusLine` (Claude Code does not allow it — stays a documented manual config).

## 2. Console-script entry points (the mechanism)
Each hook module + the MCP already expose a `main()`. Add to `pyproject.toml [project.scripts]`
(alongside the existing `cc-statusline`):
```toml
cc-mcp                   = "context_curator.mcp_server:main"
cc-inspect               = "context_curator.observe.decision_log:main"   # CLI (argv), NOT a hook
cc-hook-session-start    = "context_curator.hooks.session_start:main"
cc-hook-user-prompt      = "context_curator.hooks.user_prompt_submit:main"
cc-hook-pre-tool-use     = "context_curator.hooks.pre_tool_use:main"
cc-hook-post-tool-use    = "context_curator.hooks.post_tool_use:main"
cc-hook-subagent-stop    = "context_curator.hooks.subagent_stop:main"
```
`uv tool install --editable <ABS_CHECKOUT>` creates an isolated tool venv and drops these shims in
`uv tool dir`'s bin (Windows: `cc-*.exe` under e.g. `%USERPROFILE%\.local\bin`). `--editable` keeps
`.py` edits live; **adding/renaming entry points or deps requires re-running `uv tool install`** (M7
adds 7 scripts — call this out in the install doc, round-2 M1). `mcp` is a **core** dependency
(verified — not an extra), so the MCP server runs from a plain install; `embed` (bge) stays an
optional extra (`uv tool install "context-curator[embed]"`) — without it the dark-default recency path
is used, exactly as today.

**PATH is the load-bearing risk (round-2 C1/C2 — this is the M6 statusline lesson, do NOT repeat it).**
`uv tool install` does NOT itself put the bin dir on PATH; it requires a one-time `uv tool
update-shell` and then a **fresh shell / Claude Code restart** so spawned hook + MCP subprocesses
inherit the updated PATH. Claude Code runs hook/MCP commands **through a shell**, so a bare `cc-*` name
resolves via PATH (and Windows PATHEXT → `.exe`) — but ONLY if that one-time setup ran. The committed
plugin config CANNOT hardcode an absolute shim path (it'd be machine-specific, unlike M6's
user-written statusLine), so bare names are the only portable committed form. M7 makes the dependence
**loud, not silent**: (1) the install doc requires `uv tool update-shell` + restart; (2) the §7
install-verify is a **hard gate** that runs `cc-mcp --help` / `cc-hook-user-prompt < probe.json` from a
**freshly-spawned shell** (NOT the dev's already-configured terminal — that's the false-green trap) and
aborts on non-resolution; (3) the doc gives an **escape hatch** for locked-down environments: the user
may override in their own project `.claude/settings.json` with absolute shim paths (the statusline.md
pattern). The design does not pretend PATH "just works."

## 3. Plugin layout (repo-as-plugin, pure config)
Added at the **repo root** (the repo *is* the plugin; verified against the plugin reference — plugin
subdirs live at the root, never inside `.claude-plugin/`):
- `.claude-plugin/plugin.json` — manifest (`name: "context-curator"`, `version`, `description`,
  `author`; only `name` is required).
- `hooks/hooks.json` — the 5 hook events (same JSON schema as settings.json hooks), each command the
  bare console-script name (exec form, no shell needed):
  ```json
  { "hooks": { "UserPromptSubmit": [ { "hooks": [
    { "type": "command", "command": "cc-hook-user-prompt" } ] } ] , … } }
  ```
- `.mcp.json` — registers the `context-curator` stdio MCP (§5), `command: "cc-mcp"`.
- `.claude-plugin/marketplace.json` — one-entry local marketplace so `/plugin marketplace add <root>`
  → `/plugin install` works.

The plugin files are tiny config + reference PATH console scripts, so the cache copy needs no venv —
C1 cannot bite.

## 4. Per-project store (the one production touch)
`src/context_curator/store/paths.py::resolve_db_path` gains ONE branch, **after** the `$CC_DB_PATH`
check and **before** the `__file__`-walk:
```python
    proj = os.environ.get("CLAUDE_PROJECT_DIR")   # plugin runs across repos -> per-project store
    if proj:
        return str((Path(proj) / ".context-curator" / "store.db").resolve())
```
- `$CC_DB_PATH` still wins (existing tests/overrides intact).
- **Dev-in-repo unchanged:** Claude sets `CLAUDE_PROJECT_DIR` to *this* repo → same path the
  `__file__`-walk produced.
- **pytest:** a **bare-terminal** `uv run pytest` doesn't set `CLAUDE_PROJECT_DIR` → falls through to
  the `__file__`-walk as today. But pytest launched **from inside a Claude session** DOES inherit
  `CLAUDE_PROJECT_DIR` (the in-repo dev workflow after §6), so the env-default tests MUST
  `monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)` to deterministically reach the walk/home
  branches (round-3 I2).
- Only the **plugin-across-repos** case changes → store under the *user's* repo. `observe.decisions_dir()`
  (db-parent / `decisions`) follows automatically.
- **Load-bearing (round-2 I3):** without this branch the uv-tool install is *actively broken*, not
  merely "unchanged" — the installed package's `__file__` lives in the isolated tool venv (e.g.
  `~/AppData/Roaming/uv/tools/context-curator/…`), whose ancestors have their OWN `pyproject.toml`, so
  the `__file__`-walk would resolve the store **inside the tool dir**, never the user's repo. The
  branch must land before the walk (it does).
- **Hook↔MCP consistency:** hooks reach `<proj>/.context-curator/store.db` via the `CLAUDE_PROJECT_DIR`
  branch; the MCP reaches the **same absolute path** via its `.mcp.json` `CC_DB_PATH` (both `.resolve()`d).
  The unit test asserts byte-equality of the two for the same `CLAUDE_PROJECT_DIR` (round-1 M1).

## 5. MCP server wiring
`context_curator.mcp_server.main()` (→ `build_mcp().run()`, stdio) is the `cc-mcp` console script.
`.mcp.json`:
```json
{ "mcpServers": { "context-curator": { "command": "cc-mcp" } } }
```
No `uv run`, no `${CLAUDE_PLUGIN_ROOT}` — `cc-mcp` resolves on PATH (the uv-tool install).

**Store path — NO `CC_DB_PATH` override (round-2 I2).** An earlier draft set
`env: {CC_DB_PATH: "${CLAUDE_PROJECT_DIR}/…"}`, but `${CLAUDE_PROJECT_DIR}` expansion inside `.mcp.json`
env is NOT a documented guarantee (the plugin docs list `${CLAUDE_PLUGIN_ROOT}` + shell env vars); if
unexpanded, the literal string would *poison* `CC_DB_PATH` (which wins in `resolve_db_path`) → a broken
path. So the MCP relies on the **same `resolve_db_path` `$CLAUDE_PROJECT_DIR` branch (§4) as the
hooks** — a single code path → guaranteed-identical store path *iff* `CLAUDE_PROJECT_DIR` is in the
process env. **Open verification (§7 manual item):** confirm Claude Code exports `CLAUDE_PROJECT_DIR`
to MCP-server subprocesses (it does for hooks). If it does NOT, the MCP would fall to the `__file__`
-walk inside the uv-tool venv (wrong dir) — documented fallback: the user pins `CC_DB_PATH` in their
own project `.mcp.json`/env, or accepts a global MCP store. Exposes the existing `cc_*` store tools.

## 6. Dev ↔ plugin coexistence (round-1 I3)
The dev `.claude/settings.json` wires the SAME hook events via `uv run python -m …`. Claude Code
**merges** plugin hooks with project/user hooks rather than replacing them — so in the context-curator
repo with the plugin enabled, every event would fire **twice** (double injection + double
decision-log records, corrupting M6's log). **Resolution:** the plugin is the single source of hook
wiring. M7 **removes the hook block from `.claude/settings.json`** — the file is hooks-only today, so
it becomes `{}` (kept, not deleted, as the seam for future non-hook settings). The in-repo developer
enables the plugin like everyone else (after `uv tool install --editable .`, so code edits are live).
The install doc states: never run both wirings at once. **Dev-ergonomics note (round-3 M4):**
`uv run pytest` is unaffected by the removal (tests import modules directly; only the repointed
manifest test reads config), so day-to-day dev/test in-repo is unchanged — only *live Claude-session*
hooks now require the plugin enabled.

## 7. Install & end-to-end verification (the success test — the REAL path)
Documented procedure + a non-interactive script, run against a throwaway scratch repo:
1. `uv tool install --editable <ABS_CHECKOUT>` → `uv tool update-shell` → **restart the shell / Claude
   Code**. **Hard gate (round-2 C1/C2, round-3 C1):** from a **freshly-spawned** shell (NOT the dev
   terminal that already has PATH), check **resolution** with `Get-Command cc-mcp,cc-hook-user-prompt`
   (PowerShell) / `command -v cc-mcp cc-hook-user-prompt` (POSIX) — NOT `cc-mcp --help` (the FastMCP
   `run()` has no argparse; it would block/error even on a correct install — a false red). Then check
   **the hook runs**: `echo '{"prompt":"hi","session_id":"s"}' | cc-hook-user-prompt` exits 0. If
   resolution fails, the plugin will silently no-op — abort and fix PATH first.
2. **Marketplace/cache path (the one users hit, round-1 C2):** `/plugin marketplace add <ABS_CHECKOUT>`
   → `/plugin install context-curator@context-curator` → restart. (`--plugin-dir` is mentioned only as
   a dev convenience, explicitly NOT the success criterion.)
3. **Smoke each surface in the scratch repo:** SessionStart fires; a prompt injects context (or `CC ·`);
   a decision record lands under `<scratch>/.context-curator/decisions/`; `cc_query` via the MCP
   returns; `cc-statusline` (manual statusLine config) renders.
4. **Recorded exit criteria (round-3 I1 — "done" only when these are OBSERVED, not assumed).** During
   implementation, empirically resolve and **record the outcome in `docs/plugin-install.md`**: (a) does
   `CLAUDE_PROJECT_DIR` appear in the running `cc-mcp` server's `os.environ`? (log it to stderr on
   startup; observe under a real session) — if NOT, the MCP store diverges from the hooks' and the §5
   fallback (user pins `CC_DB_PATH`) becomes mandatory; (b) does the **marketplace/cache** install
   (§7.2) resolve bare `cc-*` on PATH? Both are Claude-runtime behaviors the spec cannot prove on its
   own; M7's completion is contingent on observing them and writing the result down.
5. **`scripts/verify-plugin.ps1`** (Windows-primary; a `.sh` sibling only if cheap) automates the
   deterministic parts WITHOUT the Claude binary: with `CLAUDE_PROJECT_DIR` set to a temp dir, pipe a
   minimal `UserPromptSubmit` event JSON into `cc-hook-user-prompt` and assert **exit 0 AND a record
   file at the resolved `$CLAUDE_PROJECT_DIR/.context-curator/decisions/`** (assert the *resolved
   path* deterministically, not "a file appeared" — round-1 I4); start `cc-mcp` and assert it **stays
   up briefly without exiting nonzero** (process liveness — the full `initialize` JSON-RPC handshake is
   brittle in PowerShell for marginal value, round-2 M2; reserve it for the §7.3 manual smoke). This
   script also doubles as the §7.1 fresh-shell resolution gate.

## 8. Testing
- **`resolve_db_path` branch test** — extend the existing `tests/test_resolve_db_path.py` (round-3 I2:
  the design must name it; its `test_default_is_absolute_and_cwd_independent` currently omits the
  `CLAUDE_PROJECT_DIR` delenv and would now hit the new branch). Cover: CC_DB_PATH wins → (delenv
  CC_DB_PATH) CLAUDE_PROJECT_DIR → (delenv both) __file__-walk hits the repo pyproject. **The `home`
  branch is unreachable in-repo** (the walk always finds this repo's `pyproject.toml`); rather than
  hedge, **descope the home-branch assertion** (it's pre-existing, unchanged behavior) OR monkeypatch
  `paths.Path(__file__)` to a markerless tmp — pick descope (YAGNI). **Assert the
  CLAUDE_PROJECT_DIR-branch path == the CC_DB_PATH-branch path** for the same project dir (locks
  hook↔MCP consistency, round-1 M1).
- **`tests/test_plugin_manifests.py`:** `json.load` each of `plugin.json` / `hooks/hooks.json` /
  `.mcp.json` / `.claude-plugin/marketplace.json`; assert required keys (`plugin.json.name`; the 5
  hook events present; `.mcp.json` `mcpServers.context-curator.command == "cc-mcp"`; marketplace lists
  the plugin); and assert **every hook/MCP command is one of the declared `cc-*` console scripts**
  (regression guard: no `uv run`/cwd-dependent command, no missing entry point — cross-check the set
  against `[project.scripts]` in `pyproject.toml`).
- **Entry-point smoke:** each `cc-hook-*` module has a `main()` (assert importable + callable).
- **Retire/rewrite the existing settings.json test (round-2 I1):** `tests/test_hooks_onload_smoke.py`
  has `test_settings_registers_both_onload_hooks`, which reads `.claude/settings.json` and asserts the
  hook block §6 removes — it would KeyError after the removal. M7 **repoints it at `hooks/hooks.json`**
  (assert the plugin registers `UserPromptSubmit` + `SessionStart` via the `cc-*` commands). The
  `test_plugin_manifests.py` cross-check (commands ⊆ declared `[project.scripts]`) catches NAME typos
  only — NOT the C1/C2 PATH/exec runtime resolution, which no static test can; a green manifest test
  must not be read as "the plugin launches" (round-2 M3).
- The full marketplace-path install (§7.2–3) + the `CLAUDE_PROJECT_DIR`-reaches-MCP check (§5) are
  **manual/scripted**, not CI unit tests (need the Claude binary) — stated honestly.
- Full suite stays green (the `resolve_db_path` branch + the retired test must not leave a red).

## 9. Privacy (§9 boundary)
Each repo's `.context-curator/` (store + `decisions/`) is gitignored locally; the committed plugin
files contain only commands + metadata, no content. The MCP + store are local-only, no outbound
calls; Context7 boundary unchanged.

## 10. File structure
- **New (repo root):** `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  `hooks/hooks.json`, `.mcp.json`.
- **New:** `scripts/verify-plugin.ps1` (+ `.sh` if cheap); `docs/plugin-install.md`;
  `tests/test_plugin_manifests.py`; the `resolve_db_path` branch test.
- **Modify:** `pyproject.toml` (`[project.scripts]` entry points — §2); `src/context_curator/store/paths.py`
  (the one branch + docstring); `.claude/settings.json` (remove the hook block — §6);
  `tests/test_hooks_onload_smoke.py` (repoint `test_settings_registers_both_onload_hooks` at
  `hooks/hooks.json` — round-2 I1); `README` (install pointer). `.gitignore` already covers
  `.context-curator/`.
- **No change** to hook/MCP/policy/store logic beyond the path branch.

## 11. Out of scope / future
- `cc-explorer`/`cc-guard` subagents + an orchestration skill (the home for the M5-deferred offload
  loop) — would live under the plugin's auto-discovered `agents/` and `skills/` dirs later.
- PyPI / public `uvx` distribution (removes the `uv tool install <checkout>` prerequisite).
- A plugin-contributed statusLine if Claude Code ever supports it.

## Design Critique Log

### Critique Round 1
The critic verified against the Claude Code docs + reproduced the failures, and found my original
`uv run --project "${CLAUDE_PLUGIN_ROOT}"` mechanism fundamentally broken:
- **C1 (Critical):** Claude Code copies marketplace plugins to `~/.claude/plugins/cache`; the gitignored
  `.venv` isn't copied, so `uv run --project ${CLAUDE_PLUGIN_ROOT}` cold-syncs (~3 s reproduced) or
  fails on every real install. **Resolved by re-architecting (§1/§2/§3):** the plugin carries no venv;
  the curator is installed via `uv tool install <checkout>` (console scripts on PATH) and the plugin
  calls bare `cc-*` commands — the cache copy needs no venv.
- **C2 (Critical):** the original success test used `--plugin-dir` (dev venv present) and never the
  cache path → false green. **Resolved (§7):** the procedure exercises the marketplace/cache path as
  the criterion; `--plugin-dir` is demoted to a dev convenience.
- **I1 (Important):** per-hook `uv run` (110–780 ms) in the UserPromptSubmit latency path. **Resolved:**
  direct console-script invocation (no uv run, no sync).
- **I3 (Important):** dev `settings.json` + plugin hooks double-fire (double injection + double
  decision records). **Resolved (§6):** M7 removes the settings.json hook block; the plugin is the
  single wiring; documented.
- **I4/M1/M2/M3/M4:** verify-script asserts the *resolved* decision path (not "a file appeared");
  unit test asserts hook↔MCP path byte-equality; `mcp` confirmed core (no extra needed); over-build
  trimmed (one primary `.ps1` verifier); the DESIGN.md subagents/skill divergence recorded in §1/§11.

### Critique Round 2
Critic verified the hook/entry-point surface is solid (all 5 hooks + `cc-mcp` + `cc-inspect` have
stdin-driven `main()`s; `mcp` is core) but the new console-script mechanism still had real holes:
- **C1/C2 (Critical):** bare `cc-*` names repeat M6's PATH lesson (Claude runs commands via a shell
  with a non-login PATH; the repo's own `docs/statusline.md` mandates an absolute shim). "Works on my
  machine" is a false green. **Resolved (§2/§7):** bare names are kept (the only portable *committed*
  form — a plugin can't hardcode a machine-specific abs path), but the dependence is made LOUD —
  required `uv tool update-shell` + restart, a **fresh-shell hard verify gate** before the milestone is
  "done," explicit Windows `.exe`/shell facts, and a documented absolute-shim escape hatch for
  locked-down envs.
- **I2 (Important):** the `.mcp.json` `CC_DB_PATH: "${CLAUDE_PROJECT_DIR}/…"` override would *poison*
  `CC_DB_PATH` with a literal if Claude doesn't expand it there. **Resolved (§5):** override dropped;
  the MCP uses the same `resolve_db_path` `$CLAUDE_PROJECT_DIR` branch as hooks (one code path), with
  an explicit open verification that `CLAUDE_PROJECT_DIR` reaches the MCP subprocess env + a fallback.
- **I1 (Important):** removing the settings.json hook block breaks
  `test_hooks_onload_smoke.py::test_settings_registers_both_onload_hooks`, so "suite stays green" was
  false. **Resolved (§8/§10):** that test is repointed at `hooks/hooks.json`.
- **I3 (Important):** the `resolve_db_path` branch is load-bearing — without it the uv-tool install
  resolves the store inside the tool venv. **Recorded (§4)** as actively-broken-without-it.
- **M1/M2/M3:** re-run `uv tool install` when entry points change (§2); verify script = process
  liveness, not a full handshake (§7); a green manifest test ≠ "launches" (§8).

### Critique Round 3
Critic verified every code surface (5 stdin-driven hook `main()`s; `cc-mcp`/`cc-inspect` mains; `mcp`
core; hatchling exposes the 8 scripts fine) and found the mechanism sound, with three required inline
fixes (no re-architecture):
- **C1 (Critical):** the §7.1 gate probe `cc-mcp --help` misfires — FastMCP `run()` has no argparse, so
  it blocks/errors even on a correct install (false RED). **Fixed (§7.1):** resolution via
  `Get-Command`/`command -v` (doesn't execute the server) + the hook stdin probe; liveness via the
  verify script.
- **I1 (Important):** the two open Claude-runtime facts (`CLAUDE_PROJECT_DIR` in the MCP env; cache-path
  `cc-*` PATH resolution) were parked as caveats. **Fixed (§7.4):** promoted to **recorded exit
  criteria** — M7 is "done" only when both are observed and the outcome is written into the install doc.
- **I2 (Important):** the existing `tests/test_resolve_db_path.py` was unnamed and its default test omits
  the `CLAUDE_PROJECT_DIR` delenv (the new branch would fire under a Claude-session pytest); the `home`
  branch is unreachable in-repo. **Fixed (§4/§8):** named the test, required the delenv, descoped the
  home-branch assertion (YAGNI), corrected §4's "pytest unchanged" wording.
- **M1–M4:** 8 total entry points (7 new); `cc-inspect` flagged as CLI-not-hook (§2);
  `.claude/settings.json` → `{}` not deleted (§6); dev-ergonomics note added (§6). No scope sprawl.
