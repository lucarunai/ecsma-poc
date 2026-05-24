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
        _reject_unsafe_existing_file(path)
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
        _reject_unsafe_existing_file(path)
        _enforce_workspace_limits(
            settings,
            workspace_path,
            pending_path=path,
            pending_bytes=len(content.encode()),
        )
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
        _reject_unsafe_existing_file(path)
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ValueError(f"Edit expected exactly one match, found {occurrences}.")
        next_content = content.replace(old_text, new_text, 1)
        _enforce_workspace_limits(
            settings,
            workspace_path,
            pending_path=path,
            pending_bytes=len(next_content.encode()),
        )
        path.write_text(next_content, encoding="utf-8")
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
            if _safe_workspace_data_file(workspace_path, path)
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
            if not _safe_workspace_data_file(workspace_path, path):
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
        _enforce_workspace_limits(settings, workspace_path)
        return _command_ok(
            "Repository cloned.",
            result,
            settings=settings,
            repository_url=repository_url,
            source_branch=source_branch,
        )

    if request.tool_name == "create_git_branch":
        branch_name = _required_string(args, "branch_name")
        result = await github.create_branch(
            workspace=workspace,
            branch_name=branch_name,
        )
        return _command_ok(
            "Git branch created.",
            result,
            settings=settings,
            branch_name=branch_name,
        )

    if request.tool_name == "checkout_git_branch":
        branch_name = _required_string(args, "branch_name")
        result = await github.checkout_branch(
            workspace=workspace,
            branch_name=branch_name,
        )
        _enforce_workspace_limits(settings, workspace_path)
        return _command_ok(
            "Git branch checked out.",
            result,
            settings=settings,
            branch_name=branch_name,
        )

    if request.tool_name == "git_status":
        return _command_ok(
            "Git status read.",
            await github.status(workspace),
            settings=settings,
        )

    if request.tool_name == "git_diff_stat":
        return _command_ok(
            "Git diff stat read.",
            await github.diff(workspace),
            settings=settings,
        )

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
            data=_command_data(result, settings=settings),
        )

    if request.tool_name == "commit_git_changes":
        result = await github.commit_changes(
            workspace=workspace,
            commit_message=_required_string(args, "commit_message"),
        )
        return _command_ok("Git commit created.", result, settings=settings)

    if request.tool_name == "push_current_git_branch":
        result = await github.push_current_branch(workspace)
        return _command_ok("Git branch pushed.", result, settings=settings)

    pull_request = await github.create_pull_request(
        workspace=workspace,
        target_branch=_required_string(args, "target_branch"),
        title=_required_string(args, "title"),
        body=_string(args, "body"),
    )
    return _ok(f"Pull request created: {pull_request['url']}", dict(pull_request))


def _command_ok(
    summary: str,
    result: CommandResult,
    *,
    settings: Settings,
    **data: Any,
) -> SandboxToolResult:
    return _ok(summary, {**data, **_command_data(result, settings=settings)})


def _command_data(result: CommandResult, *, settings: Settings) -> dict[str, Any]:
    stdout, stdout_meta = _truncate_text(
        result.stdout,
        limit_bytes=settings.sandbox_tool_output_bytes_limit,
    )
    stderr, stderr_meta = _truncate_text(
        result.stderr,
        limit_bytes=settings.sandbox_tool_output_bytes_limit,
    )
    return {
        "returncode": result.returncode,
        "summary": result.summary,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": stdout_meta["bytes"],
        "stderr_bytes": stderr_meta["bytes"],
        "stdout_original_bytes": stdout_meta["original_bytes"],
        "stderr_original_bytes": stderr_meta["original_bytes"],
        "stdout_truncated": stdout_meta["truncated"],
        "stderr_truncated": stderr_meta["truncated"],
        "output_limit_bytes": settings.sandbox_tool_output_bytes_limit,
        "command": _redact_command(result.command),
    }


def _truncate_text(text: str, *, limit_bytes: int) -> tuple[str, dict[str, Any]]:
    encoded = text.encode("utf-8")
    original_bytes = len(encoded)
    if limit_bytes < 0 or original_bytes <= limit_bytes:
        return text, {
            "bytes": original_bytes,
            "original_bytes": original_bytes,
            "truncated": False,
        }
    truncated = encoded[-limit_bytes:].decode("utf-8", errors="replace")
    return truncated, {
        "bytes": len(truncated.encode("utf-8")),
        "original_bytes": original_bytes,
        "truncated": True,
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
    raw_candidate = workspace_path / relative_path
    if raw_candidate.is_symlink():
        raise ValueError("Symlink workspace files are not allowed.")
    candidate = raw_candidate.resolve()
    if not _inside_workspace(workspace_path, candidate):
        raise ValueError("File path escaped workspace.")
    return candidate


def _reject_unsafe_existing_file(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError("Symlink workspace files are not allowed.")
    try:
        if path.is_file() and path.stat().st_nlink > 1:
            raise ValueError("Hardlinked workspace files are not allowed.")
    except OSError as exc:
        raise ValueError("Workspace file metadata could not be inspected.") from exc


def _safe_workspace_data_file(workspace_path: Path, path: Path) -> bool:
    if path.is_symlink():
        return False
    if not path.is_file() or not _inside_workspace(workspace_path, path):
        return False
    try:
        return path.stat().st_nlink <= 1
    except OSError:
        return False


def _enforce_workspace_limits(
    settings: Settings,
    workspace_path: Path,
    *,
    pending_path: Path | None = None,
    pending_bytes: int | None = None,
) -> None:
    usage = _workspace_usage(
        workspace_path,
        pending_path=pending_path,
        pending_bytes=pending_bytes,
    )
    if usage["bytes"] > settings.sandbox_workspace_bytes_limit:
        raise ValueError(
            "Workspace size limit exceeded: "
            f"{usage['bytes']} > {settings.sandbox_workspace_bytes_limit} bytes."
        )
    if usage["file_count"] > settings.sandbox_workspace_file_limit:
        raise ValueError(
            "Workspace file limit exceeded: "
            f"{usage['file_count']} > {settings.sandbox_workspace_file_limit} files."
        )


def _workspace_usage(
    workspace_path: Path,
    *,
    pending_path: Path | None = None,
    pending_bytes: int | None = None,
) -> dict[str, int]:
    total_bytes = 0
    file_count = 0
    pending_resolved = pending_path.resolve() if pending_path else None
    for item in workspace_path.rglob("*"):
        if not item.is_file() or item.is_symlink():
            continue
        try:
            resolved = item.resolve()
            if pending_resolved and resolved == pending_resolved:
                continue
            total_bytes += item.stat().st_size
            file_count += 1
        except OSError:
            continue
    if pending_path is not None and pending_bytes is not None:
        total_bytes += pending_bytes
        if not pending_path.exists() or pending_path.is_file():
            file_count += 1
    return {"bytes": total_bytes, "file_count": file_count}


def _inside_workspace(workspace_path: Path, candidate: Path) -> bool:
    workspace_root = workspace_path.resolve()
    resolved = candidate.resolve()
    return resolved == workspace_root or workspace_root in resolved.parents


def _validate_relative_pattern(pattern: str) -> None:
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Pattern must stay in workspace.")
