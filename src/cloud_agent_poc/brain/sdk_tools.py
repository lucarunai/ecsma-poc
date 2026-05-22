from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..domain import TaskRecord
from ..sandbox_client import SandboxLayerClient, SandboxLayerError
from ..sandbox_protocol import ToolExecutionEnvelope
from ..session_client import SessionLayerClient

EmitToolEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


class CodingToolServerFactory:
    def __init__(
        self,
        sandbox: SandboxLayerClient,
        store: SessionLayerClient,
    ) -> None:
        self.sandbox = sandbox
        self.store = store

    def create(
        self,
        task: TaskRecord,
        emit: EmitToolEvent,
    ) -> Any:
        try:
            from claude_agent_sdk import create_sdk_mcp_server, tool
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Run `uv sync` first."
            ) from exc

        run_id = task.run_id

        @tool(
            "read_workspace_file",
            "Read a UTF-8 text file inside the sandbox workspace.",
            {"path": str},
        )
        async def read_workspace_file(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "read_workspace_file",
                        {"path": args["path"]},
                    )
                )
                return self._result(f"Read {args['path']}.", payload)
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "write_workspace_file",
            "Write a UTF-8 text file inside the sandbox workspace. "
            "Creates parent directories when needed.",
            {"path": str, "content": str},
        )
        async def write_workspace_file(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "write_workspace_file",
                        {
                            "path": args["path"],
                            "content": args["content"],
                        },
                    )
                )
                return self._result(payload["message"], payload)
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "edit_workspace_file",
            "Replace exactly one matching text span in a UTF-8 sandbox file.",
            {"path": str, "old_text": str, "new_text": str},
        )
        async def edit_workspace_file(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "edit_workspace_file",
                        {
                            "path": args["path"],
                            "old_text": args["old_text"],
                            "new_text": args["new_text"],
                        },
                    )
                )
                return self._result(payload["message"], payload)
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "glob_workspace_files",
            "List sandbox workspace files matching a relative glob.",
            {"pattern": str},
        )
        async def glob_workspace_files(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "glob_workspace_files",
                        {"pattern": args["pattern"]},
                    )
                )
                return self._result("Workspace glob completed.", payload)
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "grep_workspace_files",
            "Search UTF-8 sandbox workspace files with a regex and relative glob.",
            {"pattern": str, "glob": str},
        )
        async def grep_workspace_files(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "grep_workspace_files",
                        {
                            "pattern": args["pattern"],
                            "glob": args["glob"],
                        },
                    )
                )
                return self._result("Workspace grep completed.", payload)
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "clone_github_repository",
            "Clone a GitHub repository branch into the empty run workspace. "
            "Use before repository file edits.",
            {"repository_url": str, "source_branch": str},
        )
        async def clone_github_repository(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "clone_github_repository",
                        {
                            "repository_url": args["repository_url"],
                            "source_branch": args["source_branch"],
                        },
                    )
                )
                return self._result(
                    "Repository cloned.",
                    {
                        "repository_url": args["repository_url"],
                        "source_branch": args["source_branch"],
                        "output": payload["summary"],
                    },
                )
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "create_git_branch",
            "Create and check out a Git branch for repository changes.",
            {"branch_name": str},
        )
        async def create_git_branch(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "create_git_branch",
                        {"branch_name": args["branch_name"]},
                    )
                )
                return self._result(
                    "Git branch created.",
                    {"branch_name": args["branch_name"], "output": payload["summary"]},
                )
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool("git_status", "Read the current Git branch and working tree status.", {})
        async def git_status(_: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(run_id, task, emit, "git_status", {})
                )
                return self._result(payload["summary"], {"output": payload["summary"]})
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool("git_diff_stat", "Read a summary of unstaged Git changes.", {})
        async def git_diff_stat(_: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(run_id, task, emit, "git_diff_stat", {})
                )
                return self._result(payload["summary"], {"output": payload["summary"]})
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "run_python_unittest",
            "Run Python standard-library unittest discovery from a workspace "
            "directory such as `tests` or `.`.",
            {"start_directory": str},
        )
        async def run_python_unittest(args: dict[str, Any]) -> dict[str, Any]:
            try:
                envelope = await self._execute_tool(
                    run_id,
                    task,
                    emit,
                    "run_python_unittest",
                    {"start_directory": args["start_directory"]},
                    allow_tool_error=True,
                )
                payload = self._data(envelope)
                tool_payload = {
                    "command": payload["command"],
                    "returncode": payload["returncode"],
                    "output": payload["summary"],
                }
                if not envelope.tool_result or not envelope.tool_result.ok:
                    return self._error("Python unittest failed.", tool_payload)
                return self._result("Python unittest passed.", tool_payload)
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "commit_git_changes",
            "Stage all workspace changes and create a Git commit.",
            {"commit_message": str},
        )
        async def commit_git_changes(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "commit_git_changes",
                        {"commit_message": args["commit_message"]},
                    )
                )
                return self._result("Git commit created.", {"output": payload["summary"]})
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "push_current_git_branch",
            "Push the current Git branch to its GitHub origin using sandbox credentials.",
            {},
        )
        async def push_current_git_branch(_: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "push_current_git_branch",
                        {},
                    )
                )
                return self._result("Git branch pushed.", {"output": payload["summary"]})
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "create_github_pull_request",
            "Create a GitHub pull request from the current pushed branch.",
            {"target_branch": str, "title": str, "body": str},
        )
        async def create_github_pull_request(args: dict[str, Any]) -> dict[str, Any]:
            try:
                pull_request = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "create_github_pull_request",
                        {
                            "target_branch": args["target_branch"],
                            "title": args["title"],
                            "body": args["body"],
                        },
                    )
                )
                return self._result(
                    f"Pull request created: {pull_request['url']}",
                    pull_request,
                )
            except SandboxLayerError as exc:
                return self._error(str(exc))

        return create_sdk_mcp_server(
            name="coding",
            version="0.1.0",
            tools=[
                read_workspace_file,
                write_workspace_file,
                edit_workspace_file,
                glob_workspace_files,
                grep_workspace_files,
                clone_github_repository,
                create_git_branch,
                git_status,
                git_diff_stat,
                run_python_unittest,
                commit_git_changes,
                push_current_git_branch,
                create_github_pull_request,
            ],
        )

    async def _execute_tool(
        self,
        run_id: str,
        task: TaskRecord,
        emit: EmitToolEvent,
        tool_name: str,
        args: dict[str, Any],
        *,
        allow_tool_error: bool = False,
    ) -> ToolExecutionEnvelope:
        envelope = await self.sandbox.execute_tool(run_id, tool_name, args)
        serialized = envelope.model_dump(mode="json")
        await self.store.record_tool_execution(
            run_id=run_id,
            task_id=task.id,
            envelope=serialized,
        )
        await emit(
            "tool.execution",
            {
                "task_id": task.id,
                "execution": serialized,
            },
        )
        if envelope.execution_status != "succeeded":
            raise SandboxLayerError(
                envelope.failure_message or f"Sandbox execution failed for {tool_name}."
            )
        if not envelope.tool_result:
            raise SandboxLayerError(f"Sandbox tool returned no result for {tool_name}.")
        if not envelope.tool_result.ok and not allow_tool_error:
            raise SandboxLayerError(envelope.tool_result.summary)
        return envelope

    @staticmethod
    def _data(envelope: ToolExecutionEnvelope) -> dict[str, Any]:
        if not envelope.tool_result:
            raise SandboxLayerError("Sandbox tool returned no tool result.")
        return envelope.tool_result.data

    @staticmethod
    def _result(text: str, structured_content: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured_content,
        }

    @classmethod
    def _error(
        cls,
        text: str,
        structured_content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = cls._result(text, structured_content or {"error": text})
        result["isError"] = True
        return result
