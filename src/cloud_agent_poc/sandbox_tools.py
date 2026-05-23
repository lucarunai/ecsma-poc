from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .brain.github_workflow import GitHubWorkflowError, GitHubWorkflowService
from .brain.processes import CommandResult
from .config import Settings
from .domain import Workspace
from .sandbox_protocol import SandboxToolResult, ToolExecutionRequest


TOOL_NAMES = {
    "read_workspace_file",
    "write_workspace_file",
    "edit_workspace_file",
    "glob_workspace_files",
    "grep_workspace_files",
    "create_git_branch",
    "git_status",
    "git_diff_stat",
    "run_python_unittest",
    "commit_git_changes",
}


async def execute_tool(
    request: ToolExecutionRequest,
    *,
    settings: Settings,
    workspace_path: Path,
) -> SandboxToolResult:
    if request.tool_name not in TOOL_NAMES:
        return _error(f"Sandbox tool is not supported: {request.tool_name}")

    try:
        return await _dispatch(request, settings=settings, workspace_path=workspace_path)
    except (GitHubWorkflowError, FileNotFoundError, ValueError) as exc:
        return _error(str(exc))


async def _dispatch(
    request: ToolExecutionRequest,
    *,
    settings: Settings,
    workspace_path: Path,
) -> SandboxToolResult:
    args = request.args
    github = GitHubWorkflowService(settings)
    workspace = Workspace(path=str(workspace_path))

    if request.tool_name == "read_workspace_file":
        relative_path = _required_string(args, "path")
        path = _workspace_file(workspace_path, relative_path)
        if not path.is_file():
            raise FileNotFoundError("Workspace file was not found.")
        content = path.read_text(encoding="utf-8")
        return _ok(
            f"Read {relative_path}.",
            {
                "path": relative_path,
                "content": content,
                "size_bytes": len(content.encode()),
            },
        )

    if request.tool_name == "write_workspace_file":
        relative_path = _required_string(args, "path")
        content = _string(args, "content")
        path = _workspace_file(workspace_path, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return _ok(
            "Workspace file written.",
            {
                "path": relative_path,
                "size_bytes": len(content.encode()),
                "message": "Workspace file written.",
            },
        )

    if request.tool_name == "edit_workspace_file":
        relative_path = _required_string(args, "path")
        old_text = _required_string(args, "old_text")
        new_text = _string(args, "new_text")
        path = _workspace_file(workspace_path, relative_path)
        if not path.is_file():
            raise FileNotFoundError("Workspace file was not found.")
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ValueError(f"Edit expected exactly one match, found {occurrences}.")
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return _ok(
            "Workspace file edited.",
            {"path": relative_path, "message": "Workspace file edited."},
        )

    if request.tool_name == "glob_workspace_files":
        pattern = _required_string(args, "pattern")
        _validate_relative_pattern(pattern)
        matches = [
            str(path.relative_to(workspace_path))
            for path in sorted(workspace_path.glob(pattern))
            if path.is_file() and _inside_workspace(workspace_path, path)
        ]
        return _ok("Workspace glob completed.", {"matches": matches[:200]})

    if request.tool_name == "grep_workspace_files":
        pattern = _required_string(args, "pattern")
        glob = _optional_string(args, "glob", "**/*")
        _validate_relative_pattern(glob)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid grep regex: {exc}") from exc
        matches: list[dict[str, Any]] = []
        for path in sorted(workspace_path.glob(glob)):
            if not path.is_file() or not _inside_workspace(workspace_path, path):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "path": str(path.relative_to(workspace_path)),
                            "line": line_number,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= 200:
                        return _ok("Workspace grep completed.", {"matches": matches})
        return _ok("Workspace grep completed.", {"matches": matches})

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

    if request.tool_name == "create_git_branch":
        branch_name = _required_string(args, "branch_name")
        result = await github.create_branch(
            workspace=workspace,
            branch_name=branch_name,
        )
        return _command_ok("Git branch created.", result, branch_name=branch_name)

    if request.tool_name == "checkout_git_branch":
        branch_name = _required_string(args, "branch_name")
        result = await github.checkout_branch(
            workspace=workspace,
            branch_name=branch_name,
        )
        return _command_ok("Git branch checked out.", result, branch_name=branch_name)

    if request.tool_name == "git_status":
        return _command_ok("Git status read.", await github.status(workspace))

    if request.tool_name == "git_diff_stat":
        return _command_ok("Git diff stat read.", await github.diff(workspace))

    if request.tool_name == "run_python_unittest":
        result = await github.run_unittest(
            workspace=workspace,
            start_directory=_required_string(args, "start_directory"),
        )
        summary = (
            "Python unittest passed."
            if result.returncode == 0
            else "Python unittest failed."
        )
        return SandboxToolResult(
            ok=result.returncode == 0,
            summary=summary,
            data=_command_data(result),
        )

    if request.tool_name == "commit_git_changes":
        result = await github.commit_changes(
            workspace=workspace,
            commit_message=_required_string(args, "commit_message"),
        )
        return _command_ok("Git commit created.", result)

    if request.tool_name == "push_current_git_branch":
        result = await github.push_current_branch(workspace)
        return _command_ok("Git branch pushed.", result)

    pull_request = await github.create_pull_request(
        workspace=workspace,
        target_branch=_required_string(args, "target_branch"),
        title=_required_string(args, "title"),
        body=_string(args, "body"),
    )
    return _ok(f"Pull request created: {pull_request['url']}", dict(pull_request))


def _command_ok(summary: str, result: CommandResult, **data: Any) -> SandboxToolResult:
    return _ok(summary, {**data, **_command_data(result)})


def _command_data(result: CommandResult) -> dict[str, Any]:
    return {
        "returncode": result.returncode,
        "summary": result.summary,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "command": _redact_command(result.command),
    }


def _redact_command(command: list[str]) -> list[str]:
    return [
        "[redacted-github-auth-header]"
        if "extraheader=AUTHORIZATION:" in part
        else part
        for part in command
    ]


def _ok(summary: str, data: dict[str, Any]) -> SandboxToolResult:
    return SandboxToolResult(ok=True, summary=summary, data=data)


def _error(summary: str) -> SandboxToolResult:
    return SandboxToolResult(ok=False, summary=summary, data={"error": summary})


def _required_string(args: dict[str, Any], key: str) -> str:
    value = _string(args, key)
    if not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _string(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value


def _optional_string(args: dict[str, Any], key: str, default: str) -> str:
    value = args.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _workspace_file(workspace_path: Path, relative_path: str) -> Path:
    candidate = (workspace_path / relative_path).resolve()
    if not _inside_workspace(workspace_path, candidate):
        raise ValueError("File path escaped workspace.")
    return candidate


def _inside_workspace(workspace_path: Path, candidate: Path) -> bool:
    workspace_root = workspace_path.resolve()
    resolved = candidate.resolve()
    return resolved == workspace_root or workspace_root in resolved.parents


def _validate_relative_pattern(pattern: str) -> None:
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Pattern must stay in workspace.")
