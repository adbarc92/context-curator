"""Normalized replay trace schema (replay harness design §3.1). Frozen pydantic v2."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class UserPrompt(BaseModel):
    kind: Literal["user_prompt"] = "user_prompt"
    turn_index: int
    text: str
    subtask_id: str | None = None


class ToolCall(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    args: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    content: str
    error: bool = False


class AssistantMessage(BaseModel):
    kind: Literal["assistant_message"] = "assistant_message"
    text: str


TraceEvent = Annotated[
    UserPrompt | ToolCall | ToolResult | AssistantMessage,
    Field(discriminator="kind"),
]


class Trace(BaseModel):
    session_id: str
    source: str
    events: list[TraceEvent]


class ToolRef(BaseModel):
    name: str
    call_id: str


class TaskSignal(BaseModel):
    turn_index: int
    prompt: str
    subtask_id: str | None
    recent_tool_calls: list[ToolRef]


class SelectedChunk(BaseModel):
    key: str
    score: float | None
    tokens: int


class Decision(BaseModel):
    turn_index: int
    subtask_id: str | None
    prompt_preview: str
    selected: list[SelectedChunk]
    total_tokens: int
    candidates: list[SelectedChunk] = Field(default_factory=list)
    offloaded: list[str] = Field(default_factory=list)


class DecisionLog(BaseModel):
    trace_session_id: str
    target_name: str
    decisions: list[Decision]
