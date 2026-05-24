from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .sandbox_protocol import ToolExecutionEnvelope, ToolExecutionRequest
from .sandbox_tools import execute_tool


async def execute_runtime_request(
    request: ToolExecutionRequest,
    *,
    settings: Settings,
    workspace_path: Path,
) -> ToolExecutionEnvelope:
    execution_id = request.execution_id or f"sbxexec_{uuid4().hex}"
    try:
        tool_result = await execute_tool(
            request,
            settings=settings,
            workspace_path=workspace_path,
        )
        return ToolExecutionEnvelope(
            execution_id=execution_id,
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            execution_status="succeeded",
            tool_result=tool_result,
            runtime=_runtime_metadata(
                settings=settings,
                workspace_path=workspace_path,
                execution_mode=settings.sandbox_execution_mode,
                tool_result=tool_result,
            ),
        )
    except Exception as exc:
        return ToolExecutionEnvelope(
            execution_id=execution_id,
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            execution_status="failed",
            failure_message=str(exc),
            runtime=_runtime_metadata(
                settings=settings,
                workspace_path=workspace_path,
                execution_mode=settings.sandbox_execution_mode,
                tool_result=None,
            ),
        )


def main() -> int:
    request = _request_from_env()
    workspace_path = Path(os.getenv("SANDBOX_WORKSPACE_PATH", "/workspace"))
    envelope = asyncio.run(
        execute_runtime_request(
            request,
            settings=Settings.from_env(),
            workspace_path=workspace_path,
        )
    )
    print(envelope.model_dump_json())
    return 0 if envelope.execution_status == "succeeded" else 1


def _request_from_env() -> ToolExecutionRequest:
    encoded = os.environ["SANDBOX_TOOL_REQUEST_B64"]
    payload = base64.b64decode(encoded).decode("utf-8")
    return ToolExecutionRequest.model_validate_json(payload)


def _runtime_metadata(
    *,
    settings: Settings,
    workspace_path: Path,
    execution_mode: str,
    tool_result,
):
    from .sandbox_protocol import SandboxRuntimeMetadata

    return SandboxRuntimeMetadata(
        type=execution_mode,
        runtime_profile=_runtime_profile(execution_mode),
        isolation=_isolation_level(execution_mode),
        network_policy=(
            settings.sandbox_network_policy_name
            if execution_mode == "kubernetes"
            else None
        ),
        egress_policy=(
            settings.sandbox_egress_policy
            if execution_mode == "kubernetes"
            else None
        ),
        resource_limits={
            "cpu": "500m",
            "memory": "512Mi",
            "ephemeral_storage": settings.sandbox_ephemeral_storage_limit,
            "timeout_seconds": settings.sandbox_tool_timeout_seconds,
            "tool_output_bytes": settings.sandbox_tool_output_bytes_limit,
            "runtime_log_bytes": settings.sandbox_runtime_log_bytes_limit,
            "workspace_bytes": settings.sandbox_workspace_bytes_limit,
            "workspace_files": settings.sandbox_workspace_file_limit,
        },
        workspace=_workspace_evidence(workspace_path),
        output=_output_evidence(tool_result),
    )


def _runtime_profile(execution_mode: str) -> str:
    if execution_mode == "kubernetes":
        return "kubernetes_container"
    return execution_mode


def _isolation_level(execution_mode: str) -> str:
    if execution_mode == "kubernetes":
        return "container"
    return "process"


def _workspace_evidence(workspace_path: Path) -> dict[str, object]:
    try:
        path = workspace_path.resolve()
    except OSError:
        path = workspace_path
    evidence: dict[str, object] = {"path": str(path)}
    try:
        files = [item for item in path.rglob("*") if item.is_file()]
    except OSError:
        return evidence
    total_bytes = 0
    counted_files = 0
    for item in files:
        try:
            total_bytes += item.stat().st_size
            counted_files += 1
        except OSError:
            continue
    evidence["file_count"] = counted_files
    evidence["bytes"] = total_bytes
    return evidence


def _output_evidence(tool_result) -> dict[str, object]:
    output: dict[str, object] = {}
    if not tool_result:
        return output
    stdout = tool_result.data.get("stdout")
    stderr = tool_result.data.get("stderr")
    if isinstance(stdout, str):
        output["stdout_bytes"] = len(stdout.encode("utf-8"))
        output["stdout_original_bytes"] = tool_result.data.get(
            "stdout_original_bytes",
            output["stdout_bytes"],
        )
        output["stdout_truncated"] = bool(tool_result.data.get("stdout_truncated"))
    if isinstance(stderr, str):
        output["stderr_bytes"] = len(stderr.encode("utf-8"))
        output["stderr_original_bytes"] = tool_result.data.get(
            "stderr_original_bytes",
            output["stderr_bytes"],
        )
        output["stderr_truncated"] = bool(tool_result.data.get("stderr_truncated"))
    output_limit = tool_result.data.get("output_limit_bytes")
    if isinstance(output_limit, int):
        output["limit_bytes"] = output_limit
    return output


if __name__ == "__main__":
    sys.exit(main())
