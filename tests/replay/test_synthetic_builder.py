from context_curator.replay.capture.synthetic import TraceBuilder
from context_curator.replay.schema import AssistantMessage, ToolCall, ToolResult, UserPrompt


def test_builder_assigns_turn_indices_and_call_ids():
    trace = (
        TraceBuilder("s1")
        .user("first")
        .tool("Read", {"path": "a.py"}).result("aaa")
        .user("second", subtask_id="task-2")
        .assistant("ok")
        .build()
    )
    assert trace.session_id == "s1"
    assert trace.source == "synthetic"
    kinds = [type(e) for e in trace.events]
    assert kinds == [UserPrompt, ToolCall, ToolResult, UserPrompt, AssistantMessage]
    assert trace.events[0].turn_index == 0
    assert trace.events[3].turn_index == 1
    assert trace.events[3].subtask_id == "task-2"
    # call_id of the tool matches its result
    assert trace.events[1].call_id == trace.events[2].call_id


def test_result_without_preceding_tool_raises():
    import pytest
    with pytest.raises(ValueError):
        TraceBuilder("s1").user("x").result("orphan")


def test_error_result_flag():
    trace = TraceBuilder("s1").user("x").tool("Bash", {}).result("boom", error=True).build()
    assert trace.events[-1].error is True
