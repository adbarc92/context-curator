"""Every cc-* console script declared in pyproject must resolve to an importable, callable
main(). Guards against a renamed/moved module silently breaking an entry point (the actual PATH
resolution is a runtime concern verified by scripts/verify-plugin.ps1, not here)."""
import importlib
import tomllib
from pathlib import Path

EXPECTED_SCRIPTS = {
    "cc-statusline": "context_curator.observe.statusline:main",
    "cc-mcp": "context_curator.mcp_server:main",
    "cc-inspect": "context_curator.observe.decision_log:main",
    "cc-hook-session-start": "context_curator.hooks.session_start:main",
    "cc-hook-user-prompt": "context_curator.hooks.user_prompt_submit:main",
    "cc-hook-pre-tool-use": "context_curator.hooks.pre_tool_use:main",
    "cc-hook-post-tool-use": "context_curator.hooks.post_tool_use:main",
    "cc-hook-subagent-stop": "context_curator.hooks.subagent_stop:main",
}


def _scripts() -> dict[str, str]:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]


def test_pyproject_declares_all_expected_scripts():
    assert _scripts() == EXPECTED_SCRIPTS


def test_every_script_target_is_importable_and_callable():
    for name, target in _scripts().items():
        module_path, func = target.split(":")
        module = importlib.import_module(module_path)
        assert callable(getattr(module, func)), f"{name} -> {target} is not callable"
