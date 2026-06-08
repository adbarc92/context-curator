"""Static validation of the repo-as-plugin manifests. NOTE (round-2 M3): a green test here proves
the JSON is well-formed and the command names match declared entry points -- it does NOT prove the
plugin launches (PATH/exec resolution is runtime-only, verified by scripts/verify-plugin.ps1)."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOOK_EVENTS = {"PreToolUse", "PostToolUse", "SubagentStop", "SessionStart", "UserPromptSubmit"}


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _declared_scripts() -> set[str]:
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    # hooks live under gui-scripts (pythonw, no console window); the MCP under scripts.
    return set(proj.get("scripts", {})) | set(proj.get("gui-scripts", {}))


def _hook_commands(hooks_json: dict) -> list[str]:
    cmds = []
    for event_groups in hooks_json["hooks"].values():
        for group in event_groups:
            for hook in group["hooks"]:
                cmds.append(hook["command"])
    return cmds


def test_plugin_manifest_has_required_keys():
    manifest = _load(".claude-plugin/plugin.json")
    assert manifest["name"] == "context-curator"
    for key in ("version", "description", "author"):
        assert key in manifest, f"plugin.json missing required key {key!r}"


def test_hooks_json_registers_exactly_the_five_events():
    hooks = _load("hooks/hooks.json")["hooks"]
    assert set(hooks.keys()) == HOOK_EVENTS


def test_mcp_json_registers_cc_mcp():
    mcp = _load(".mcp.json")
    assert mcp["mcpServers"]["context-curator"]["command"] == "cc-mcp"


def test_marketplace_lists_the_plugin():
    market = _load(".claude-plugin/marketplace.json")
    names = {p["name"] for p in market["plugins"]}
    assert "context-curator" in names


def test_every_hook_and_mcp_command_is_a_declared_cc_script():
    # Regression guard: no `uv run`/cwd-dependent command, no missing entry point.
    declared = _declared_scripts()
    commands = _hook_commands(_load("hooks/hooks.json"))
    commands.append(_load(".mcp.json")["mcpServers"]["context-curator"]["command"])
    for cmd in commands:
        assert cmd in declared, f"{cmd!r} is not a declared [project.scripts] entry"
