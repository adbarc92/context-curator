"""Every cc-* entry point declared in pyproject must resolve to an importable, callable main()
(the actual PATH resolution is a runtime concern verified by scripts/verify-plugin.ps1, not here).

The five hooks live under [project.gui-scripts] (pythonw / GUI subsystem) so Claude Code spawning
them per event does NOT pop a console window on Windows; cc-inspect/cc-statusline/cc-mcp stay
[project.scripts] (console). test_hooks_are_gui_scripts guards against a regression that would
reintroduce the window-flash."""
import importlib
import tomllib
from pathlib import Path

EXPECTED_CONSOLE = {
    "cc-statusline": "context_curator.observe.statusline:main",
    "cc-inspect": "context_curator.observe.decision_log:main",
    "cc-mcp": "context_curator.mcp_server:main",
}
EXPECTED_GUI = {
    "cc-hook-session-start": "context_curator.hooks.session_start:main",
    "cc-hook-user-prompt": "context_curator.hooks.user_prompt_submit:main",
    "cc-hook-pre-tool-use": "context_curator.hooks.pre_tool_use:main",
    "cc-hook-post-tool-use": "context_curator.hooks.post_tool_use:main",
    "cc-hook-subagent-stop": "context_curator.hooks.subagent_stop:main",
}


def _project() -> dict:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]


def _all_scripts() -> dict[str, str]:
    proj = _project()
    return {**proj.get("scripts", {}), **proj.get("gui-scripts", {})}


def test_pyproject_declares_all_expected_scripts():
    assert _all_scripts() == {**EXPECTED_CONSOLE, **EXPECTED_GUI}


def test_console_and_gui_split():
    assert _project().get("scripts", {}) == EXPECTED_CONSOLE
    assert _project().get("gui-scripts", {}) == EXPECTED_GUI


def test_hooks_are_gui_scripts():
    # Regression guard: every cc-hook-* MUST be a gui-script (pythonw, no console window). If a hook
    # slips back into [project.scripts] it will flash a console window on every event on Windows.
    console = _project().get("scripts", {})
    gui = _project().get("gui-scripts", {})
    hooks = [n for n in _all_scripts() if n.startswith("cc-hook-")]
    assert hooks, "no cc-hook-* entry points found"
    for h in hooks:
        assert h in gui and h not in console, f"{h} must be a gui-script, not a console script"


def test_every_script_target_is_importable_and_callable():
    for name, target in _all_scripts().items():
        module_path, func = target.split(":")
        module = importlib.import_module(module_path)
        assert callable(getattr(module, func)), f"{name} -> {target} is not callable"
