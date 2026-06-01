from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRunRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=20000)
    app_id: str = Field(default="demo_app", pattern=r"^[a-zA-Z0-9_-]+$")


class SessionCreateRequest(BaseModel):
    first_prompt: str | None = Field(default=None, max_length=20000)


class ToolCallPayload(BaseModel):
    tool: str
    input: dict[str, Any]


class StreamEvent(BaseModel):
    type: Literal[
        "message",
        "message_chunk",
        "tool_started",
        "tool_finished",
        "agent_started",
        "agent_finished",
        "status",
        "workspace",
        "final",
        "error",
    ]
    payload: dict[str, Any]


class WorkspaceFile(BaseModel):
    path: str
    content: str


class WorkspaceEntry(BaseModel):
    path: str
    name: str
    is_dir: bool


class WorkspaceSnapshot(BaseModel):
    app_id: str
    entries: list[WorkspaceEntry] = Field(default_factory=list)
    files: list[WorkspaceFile]


class ChatMessage(BaseModel):
    id: int
    role: Literal["assistant", "system", "user"]
    agent: str
    content: str
    created_at: str


class SessionSummary(BaseModel):
    id: str
    title: str
    app_id: str
    created_at: str
    updated_at: str
    last_prompt: str = ""
    last_message_preview: str = ""
    message_count: int = 0
    generation_duration_ms: int | None = None


class SessionDetail(BaseModel):
    session: SessionSummary
    messages: list[ChatMessage] = Field(default_factory=list)
    trace_events: list[StreamEvent] = Field(default_factory=list)
    workspace: WorkspaceSnapshot


class SkillInfo(BaseModel):
    name: str
    body: str
