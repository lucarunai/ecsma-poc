from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .brain.github_workflow import GitHubWorkflowService
from .config import Settings
from .sandbox_manager import SandboxManagerError, create_tool_execution_runner
from .sandbox_protocol import ToolExecutionEnvelope, ToolExecutionRequest


settings = Settings.from_env()
workspaces = GitHubWorkflowService(settings)
runner = create_tool_execution_runner(settings)
app = FastAPI(title="Cloud Agent PoC Sandbox Manager")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "sandbox-manager"}


@app.post("/internal/workspaces/{run_id}")
async def create_workspace(run_id: str) -> dict[str, str]:
    _validate_run_id(run_id)
    try:
        workspace = await workspaces.create_workspace(run_id)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Workspace already exists.") from exc
    return {"path": workspace.path}


@app.post("/internal/tool-executions")
async def execute_tool(request: ToolExecutionRequest) -> ToolExecutionEnvelope:
    workspace_path = _workspace_root(request.run_id)
    try:
        return await runner.execute(request, workspace_path=workspace_path)
    except SandboxManagerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _workspace_root(run_id: str) -> Path:
    _validate_run_id(run_id)
    workspace_root = (settings.workspace_root / run_id).resolve()
    base_root = settings.workspace_root.resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise HTTPException(status_code=404, detail="Sandbox workspace was not found.")
    if base_root not in workspace_root.parents:
        raise HTTPException(status_code=400, detail="Workspace escaped sandbox root.")
    return workspace_root


def _validate_run_id(run_id: str) -> None:
    if not re.match(r"^run_[a-f0-9]{32}$", run_id):
        raise HTTPException(status_code=400, detail="Run id is not valid.")
