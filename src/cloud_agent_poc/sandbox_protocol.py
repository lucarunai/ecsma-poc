from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .session_contracts import TOOL_EXECUTION_ENVELOPE_SCHEMA


class ToolExecutionRequest(BaseModel):
    run_id: str
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    task_attempt_id: str | None = None
    workspace_path: str | None = None
    execution_id: str | None = None
    sandbox_session_id: str | None = None
    sandbox_scope: str | None = None
    runtime_policy: str | None = None
    policy_reason: str | None = None


class SandboxSessionCreateRequest(BaseModel):
    run_id: str
    task_id: str | None = None
    task_attempt_id: str
    workspace_path: str | None = None
    sandbox_session_id: str | None = None
    scope: str = "task_attempt"
    runtime_policy: str = "task_attempt_sandbox"


class SandboxToolResult(BaseModel):
    ok: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class SandboxRuntimeMetadata(BaseModel):
    type: str | None = None
    runtime_profile: str | None = None
    isolation: str | None = None
    runtime_class: str | None = None
    pod_name: str | None = None
    pod_phase: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    sandbox_session_id: str | None = None
    sandbox_scope: str | None = None
    runtime_policy: str | None = None
    policy_reason: str | None = None
    network_policy: str | None = None
    egress_policy: str | None = None
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionEnvelope(BaseModel):
    schema_version: str = TOOL_EXECUTION_ENVELOPE_SCHEMA
    execution_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    execution_status: Literal["succeeded", "failed"]
    tool_result: SandboxToolResult | None = None
    failure_message: str | None = None
    runtime: SandboxRuntimeMetadata = Field(default_factory=SandboxRuntimeMetadata)
