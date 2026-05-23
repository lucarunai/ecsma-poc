from __future__ import annotations

import time
import re
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from .brain.github_workflow import GitHubWorkflowError, GitHubWorkflowService
from .brain.processes import CommandResult
from .config import Settings
from .domain import Workspace
from .sandbox_protocol import (
    SandboxRuntimeMetadata,
    SandboxToolResult,
    ToolExecutionEnvelope,
    ToolExecutionRequest,
)
from .tool_policy import TRUSTED_GITHUB_TOOLS

settings = Settings.from_env()
github = GitHubWorkflowService(settings)
app = FastAPI(title="Cloud Agent PoC GitHub Broker")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "github-broker"}


@app.post("/internal/tool-executions")
async def execute_tool(request: ToolExecutionRequest) -> ToolExecutionEnvelope:
    if request.tool_name not in TRUSTED_GITHUB_TOOLS:
        raise HTTPException(status_code=404, detail="Trusted GitHub tool is not supported.")
    workspace_path = _workspace_root(request.run_id)
    started = time.monotonic()
    try:
        tool_result = await _dispatch(request, workspace_path=workspace_path)
        status = "succeeded"
        failure_message = None
    except (GitHubWorkflowError, FileNotFoundError, ValueError) as exc:
        tool_result = SandboxToolResult(ok=False, summary=str(exc), data={"error": str(exc)})
        status = "succeeded"
        failure_message = None
    except Exception as exc:
        tool_result = None
        status = "failed"
        failure_message = str(exc)
    return ToolExecutionEnvelope(
        execution_id=request.execution_id or f"sbxexec_{uuid4().hex}",
        run_id=request.run_id,
        tool_call_id=request.tool_call_id,
        tool_name=request.tool_name,
        execution_status=status,
        tool_result=tool_result,
        failure_message=failure_message,
        runtime=SandboxRuntimeMetadata(
            type="trusted_github_executor",
            duration_ms=int((time.monotonic() - started) * 1000),
        ),
    )


async def _dispatch(
    request: ToolExecutionRequest,
    *,
    workspace_path: Path,
) -> SandboxToolResult:
    args = request.args
    workspace = Workspace(path=str(workspace_path))
    if request.tool_name == "clone_github_repository":
        repository_url = _required_string(args, "repository_url")
        source_branch = _required_string(args, "source_branch")
        result = await github.clone_repository(
            workspace=workspace,
            repository_url=repository_url,
            source_branch=source_branch,
        )
        return _command_ok(
            "Repository cloned.",
            result,
            repository_url=repository_url,
            source_branch=source_branch,
        )
    if request.tool_name == "checkout_git_branch":
        branch_name = _required_string(args, "branch_name")
        result = await github.checkout_branch(workspace=workspace, branch_name=branch_name)
        return _command_ok("Git branch checked out.", result, branch_name=branch_name)
    if request.tool_name == "push_current_git_branch":
        return _command_ok("Git branch pushed.", await github.push_current_branch(workspace))
    pull_request = await github.create_pull_request(
        workspace=workspace,
        target_branch=_required_string(args, "target_branch"),
        title=_required_string(args, "title"),
        body=_string(args, "body"),
    )
    return SandboxToolResult(
        ok=True,
        summary=f"Pull request created: {pull_request['url']}",
        data=dict(pull_request),
    )


def _command_ok(summary: str, result: CommandResult, **data: object) -> SandboxToolResult:
    return SandboxToolResult(
        ok=True,
        summary=summary,
        data={
            **data,
            "returncode": result.returncode,
            "summary": result.summary,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "command": _redact_command(result.command),
        },
    )


def _redact_command(command: list[str]) -> list[str]:
    return [
        "[redacted-github-auth-header]"
        if "extraheader=AUTHORIZATION:" in part
        else part
        for part in command
    ]


def _required_string(args: dict[str, object], key: str) -> str:
    value = _string(args, key)
    if not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _string(args: dict[str, object], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value


def _workspace_root(run_id: str) -> Path:
    _validate_run_id(run_id)
    workspace_root = (settings.workspace_root / run_id).resolve()
    base_root = settings.workspace_root.resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise HTTPException(status_code=404, detail="GitHub broker workspace was not found.")
    if base_root not in workspace_root.parents:
        raise HTTPException(status_code=400, detail="Workspace escaped broker root.")
    return workspace_root


def _validate_run_id(run_id: str) -> None:
    if not re.match(r"^run_[a-f0-9]{32}$", run_id):
        raise HTTPException(status_code=400, detail="Run id is not valid.")
