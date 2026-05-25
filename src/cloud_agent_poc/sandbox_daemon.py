from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .config import Settings
from .sandbox_protocol import ToolExecutionEnvelope, ToolExecutionRequest
from .sandbox_runtime import execute_runtime_request


settings = Settings.from_env()
app = FastAPI(title="Cloud Agent Task Sandbox Runtime")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "task-sandbox-runtime"}


@app.post("/execute")
async def execute_tool(request: ToolExecutionRequest) -> ToolExecutionEnvelope:
    workspace_path = Path(os.getenv("SANDBOX_WORKSPACE_PATH", "/workspace"))
    return await execute_runtime_request(
        request,
        settings=settings,
        workspace_path=workspace_path,
    )
