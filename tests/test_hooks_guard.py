from context_curator.hooks.pre_tool_use import handle


def _ev(tool, tool_input):
    return {"tool_name": tool, "tool_input": tool_input}


def test_write_to_env_blocked():
    assert handle(_ev("Write", {"file_path": ".env", "content": "X=1"})).exit_code == 2


def test_planted_aws_key_blocked():
    assert handle(_ev("Bash", {"command": "echo AKIAIOSFODNN7EXAMPLE"})).exit_code == 2


def test_multiedit_secret_in_second_edit_blocked():
    ev = _ev("MultiEdit", {"file_path": "x.py", "edits": [
        {"old_string": "a", "new_string": "b"},
        {"old_string": "c", "new_string": 'api_key = "abcdef0123456789ABCDEF"'}]})
    assert handle(ev).exit_code == 2


def test_bash_redirect_to_sensitive_blocked_space_and_nospace():
    assert handle(_ev("Bash", {"command": "cat foo > .env"})).exit_code == 2
    assert handle(_ev("Bash", {"command": "cat foo >.env"})).exit_code == 2
    assert handle(_ev("Bash", {"command": "echo x >> deploy/id_rsa"})).exit_code == 2


def test_benign_bash_read_allowed():
    assert handle(_ev("Bash", {"command": "grep secrets config.txt"})).exit_code == 0
    assert handle(_ev("Bash", {"command": "ls prod/"})).exit_code == 0


def test_benign_write_allowed():
    assert handle(_ev("Write", {"file_path": "src/app.py", "content": "print(1)"})).exit_code == 0


def test_unknown_tool_allowed_with_marker(capsys):
    r = handle(_ev("FancyNewTool", {"whatever": 1}))
    assert r.exit_code == 0
