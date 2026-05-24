from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .brain.github_workflow import GitHubWorkflowService
from .config import Settings
from .ownership import DEFAULT_USER_ID, normalize_user_id
from .sandbox_manager import SandboxManagerError, create_tool_execution_runner
from .sandbox_protocol import ToolExecutionEnvelope, ToolExecutionRequest


settings = Settings.from_env()
workspaces = GitHubWorkflowService(settings)
runner = create_tool_execution_runner(settings)
app = FastAPI(title="Cloud Agent PoC Sandbox Manager")


class WorkspaceCreateRequest(BaseModel):
    user_id: str | None = None


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "sandbox-manager"}


@app.post("/internal/workspaces/{run_id}")
async def create_workspace(
    run_id: str,
    body: WorkspaceCreateRequest | None = None,
) -> dict[str, str]:
    _validate_run_id(run_id)
    user_id = normalize_user_id((body.user_id if body else None) or DEFAULT_USER_ID)
    try:
        workspace = await workspaces.create_workspace(
            f"users/{user_id}/{run_id}"
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Workspace already exists.") from exc
    return {"path": workspace.path}


@app.delete("/internal/workspaces/{run_id}")
async def delete_workspace(
    run_id: str,
    workspace_path: str | None = None,
) -> dict[str, str]:
    _validate_run_id(run_id)
    if workspace_path:
        workspace_root = _workspace_path_from_string(workspace_path)
    else:
        workspace_root = _workspace_path(run_id)
    if not workspace_root.exists():
        return {"status": "missing"}
    if not workspace_root.is_dir():
        raise HTTPException(status_code=409, detail="Workspace path is not a directory.")
    shutil.rmtree(workspace_root)
    return {"status": "deleted"}


@app.post("/internal/tool-executions")
async def execute_tool(request: ToolExecutionRequest) -> ToolExecutionEnvelope:
    workspace_path = _workspace_path_from_request(request)
    try:
        return await runner.execute(request, workspace_path=workspace_path)
    except SandboxManagerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _workspace_root(run_id: str) -> Path:
    workspace_root = _workspace_path(run_id)
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise HTTPException(status_code=404, detail="Sandbox workspace was not found.")
    return workspace_root


def _workspace_path(run_id: str) -> Path:
    _validate_run_id(run_id)
    workspace_root = (settings.workspace_root / run_id).resolve()
    base_root = settings.workspace_root.resolve()
    if base_root not in workspace_root.parents:
        raise HTTPException(status_code=400, detail="Workspace escaped sandbox root.")
    return workspace_root


def _workspace_path_from_request(request: ToolExecutionRequest) -> Path:
    if request.workspace_path:
        return _workspace_path_from_string(request.workspace_path)
    return _workspace_root(request.run_id)


def _workspace_path_from_string(workspace_path: str) -> Path:
    workspace_root = Path(workspace_path).resolve()
    base_root = settings.workspace_root.resolve()
    if base_root not in workspace_root.parents:
        raise HTTPException(status_code=400, detail="Workspace escaped sandbox root.")
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise HTTPException(status_code=404, detail="Sandbox workspace was not found.")
    return workspace_root


def _validate_run_id(run_id: str) -> None:
    if not re.match(r"^run_[a-f0-9]{32}$", run_id):
        raise HTTPException(status_code=400, detail="Run id is not valid.")
