from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolExecutionRequest(BaseModel):
    run_id: str
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    execution_id: str | None = None


class SandboxToolResult(BaseModel):
    ok: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class SandboxRuntimeMetadata(BaseModel):
    pod_name: str | None = None
    pod_phase: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None


class ToolExecutionEnvelope(BaseModel):
    execution_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    execution_status: Literal["succeeded", "failed"]
    tool_result: SandboxToolResult | None = None
    failure_message: str | None = None
    runtime: SandboxRuntimeMetadata = Field(default_factory=SandboxRuntimeMetadata)

