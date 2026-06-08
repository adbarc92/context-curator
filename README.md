# ContextCurator

Relevance-driven working-set policy and a curated, durable context store for Claude Code.
See `DESIGN.md` for the full design and `docs/superpowers/plans/` for implementation plans.

**Install as a Claude Code plugin:** see [docs/plugin-install.md](docs/plugin-install.md).

## Verify the plugin install

`scripts/verify-plugin.ps1` is an end-to-end smoke of the **installed** plugin (Windows/PowerShell).
It drives the real `cc-*` console scripts with stdin JSON events through the full
capture → store → inject → working-set loop, then checks `cc-mcp` liveness — against a throwaway temp
store, so nothing is persisted and it's safe to re-run regularly. No Claude binary required. Run it
from a fresh shell after `uv tool install --editable . ; uv tool update-shell` + restart:

```powershell
pwsh -NoProfile -File scripts/verify-plugin.ps1   # also works under Windows PowerShell 5.1
```

Expect four `OK:` lines (PATH, capture, inject, MCP) then `VERIFY PASS`; it exits non-zero on any
failure. To schedule a daily health check:

```powershell
schtasks /create /tn "cc-plugin-smoke" /sc daily /st 09:00 `
  /tr "pwsh -NoProfile -File D:\MajorProjects\INFRASTRUCTURE\context-curator\scripts\verify-plugin.ps1"
```

See [docs/plugin-install.md](docs/plugin-install.md) for the full install/verify procedure.

## Develop
```bash
uv sync --all-groups
uv run pytest
```
