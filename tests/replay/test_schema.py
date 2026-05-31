from pydantic import TypeAdapter

from context_curator.replay.schema import (
    AssistantMessage,
    Decision,
    DecisionLog,
    SelectedChunk,
    TaskSignal,
    ToolCall,
    ToolRef,
    ToolResult,
    Trace,
    TraceEvent,
    UserPrompt,
)


def test_trace_roundtrips_with_discriminated_events():
    trace = Trace(
        session_id="s1",
        source="synthetic",
        events=[
            UserPrompt(turn_index=0, text="hi"),
            ToolCall(call_id="c0", name="Read", args={"path": "a.py"}),
            ToolResult(call_id="c0", content="data"),
            AssistantMessage(text="done"),
        ],
    )
    restored = Trace(**trace.model_dump())
    assert restored == trace
    # discriminated union picks the right type from a raw dict
    ev = TypeAdapter(TraceEvent).validate_python({"kind": "tool_result", "call_id": "c0", "content": "x"})
    assert isinstance(ev, ToolResult)


def test_decision_forward_stable_fields_default_empty():
    d = Decision(turn_index=1, subtask_id=None, prompt_preview="p",
                 selected=[SelectedChunk(key="k", score=None, tokens=3)], total_tokens=3)
    assert d.candidates == []
    assert d.offloaded == []


def test_task_signal_uses_slim_tool_refs():
    sig = TaskSignal(turn_index=0, prompt="p", subtask_id=None,
                     recent_tool_calls=[ToolRef(name="Read", call_id="c0")])
    assert sig.recent_tool_calls[0].name == "Read"


def test_decision_log_serializes_without_floats_in_v1():
    log = DecisionLog(trace_session_id="s1", target_name="recency-only", decisions=[])
    import json
    json.dumps(log.model_dump())  # must not raise
