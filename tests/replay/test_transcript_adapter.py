from pathlib import Path

from context_curator.replay.capture.transcript import parse_transcript
from context_curator.replay.schema import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    UserPrompt,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def test_parses_expected_normalized_sequence():
    trace = parse_transcript(FIXTURE)
    assert trace.source == "transcript"
    kinds = [type(e) for e in trace.events]
    assert kinds == [UserPrompt, AssistantMessage, ToolCall, ToolCall,
                     ToolResult, ToolResult, UserPrompt]


def test_tool_result_user_record_does_not_increment_turn_index():
    trace = parse_transcript(FIXTURE)
    prompts = [e for e in trace.events if isinstance(e, UserPrompt)]
    assert [p.turn_index for p in prompts] == [0, 1]  # tool_result user records are NOT turns


def test_sidechain_orphan_result_is_dropped():
    trace = parse_transcript(FIXTURE)
    # the sidechain tool_use_id never appears as a ToolResult in the normalized trace
    result_ids = {e.call_id for e in trace.events if isinstance(e, ToolResult)}
    assert "sidechain-orphan" not in result_ids


def test_two_tool_use_blocks_become_two_calls_in_order():
    trace = parse_transcript(FIXTURE)
    calls = [e for e in trace.events if isinstance(e, ToolCall)]
    assert [c.call_id for c in calls] == ["c0", "c1"]


def test_non_sidechain_orphan_tool_result_is_dropped():
    trace = parse_transcript(FIXTURE)
    result_ids = {e.call_id for e in trace.events if isinstance(e, ToolResult)}
    assert "never-seen-main" not in result_ids
