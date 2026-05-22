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
        )
    except Exception as exc:
        return ToolExecutionEnvelope(
            execution_id=execution_id,
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            execution_status="failed",
            failure_message=str(exc),
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


if __name__ == "__main__":
    sys.exit(main())
