from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..domain import TaskRecord, Workspace
from ..github_broker_client import GitHubBrokerClient, GitHubBrokerError
from ..sandbox_client import SandboxLayerClient, SandboxLayerError
from ..sandbox_protocol import ToolExecutionEnvelope
from ..session_client import SessionLayerClient
from ..tool_policy import (
    TRUSTED_GITHUB_TOOLS,
    approval_reason_for_tool,
    requires_human_approval,
    resolve_runtime_policy,
)

EmitToolEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


class CodingToolServerFactory:
    def __init__(
        self,
        sandbox: SandboxLayerClient,
        github_broker: GitHubBrokerClient,
        store: SessionLayerClient,
        *,
        approval_poll_seconds: float = 1.0,
        approval_timeout_seconds: float = 900.0,
    ) -> None:
        self.sandbox = sandbox
        self.github_broker = github_broker
        self.store = store
        self.approval_poll_seconds = approval_poll_seconds
        self.approval_timeout_seconds = approval_timeout_seconds

    def create(
        self,
        task: TaskRecord,
        task_attempt_id: str,
        emit: EmitToolEvent,
        workspace: Workspace | None = None,
        *,
        sandbox_session_id: str | None = None,
    ) -> Any:
        try:
            from claude_agent_sdk import create_sdk_mcp_server, tool
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Run `uv sync` first."
            ) from exc

        run_id = task.run_id
        workspace_path = workspace.path if workspace else None

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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
            "checkout_git_branch",
            "Fetch and check out an existing Git branch from the repository origin. "
            "Use this when the user asks to switch to an existing branch.",
            {"branch_name": str},
        )
        async def checkout_git_branch(args: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "checkout_git_branch",
                        {"branch_name": args["branch_name"]},
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
                    )
                )
                return self._result(
                    "Git branch checked out.",
                    {"branch_name": args["branch_name"], "output": payload["summary"]},
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "git_status",
                        {},
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
                    )
                )
                return self._result(payload["summary"], {"output": payload["summary"]})
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool("git_diff_stat", "Read a summary of unstaged Git changes.", {})
        async def git_diff_stat(_: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = self._data(
                    await self._execute_tool(
                        run_id,
                        task,
                        emit,
                        "git_diff_stat",
                        {},
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
                    )
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
                    )
                )
                return self._result("Git commit created.", {"output": payload["summary"]})
            except SandboxLayerError as exc:
                return self._error(str(exc))

        @tool(
            "push_current_git_branch",
            "Push the current Git branch to its GitHub origin using trusted broker credentials.",
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                        task_attempt_id=task_attempt_id,
                        workspace_path=workspace_path,
                        sandbox_session_id=sandbox_session_id,
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
                checkout_git_branch,
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
        task_attempt_id: str,
        workspace_path: str | None = None,
        sandbox_session_id: str | None = None,
        allow_tool_error: bool = False,
    ) -> ToolExecutionEnvelope:
        policy = resolve_runtime_policy(
            tool_name,
            args,
            task_sandbox_available=bool(sandbox_session_id),
        )
        tool_call_id = await self.store.create_tool_call(
            run_id=run_id,
            task_id=task.id,
            task_attempt_id=task_attempt_id,
            tool_name=tool_name,
            tool_input=args,
        )
        await emit(
            "tool.call.requested",
            {
                "task_id": task.id,
                "task_attempt_id": task_attempt_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "input": args,
                "runtime_policy": policy.policy,
                "policy_reason": policy.reason,
                "sandbox_session_id": (
                    sandbox_session_id
                    if policy.policy == "task_attempt_sandbox"
                    else None
                ),
            },
        )
        if requires_human_approval(tool_name):
            approval: dict[str, Any] | None = None
            try:
                approval = await self.store.create_approval_request(
                    run_id=run_id,
                    task_id=task.id,
                    task_attempt_id=task_attempt_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_input=args,
                    reason=approval_reason_for_tool(tool_name),
                    requested_by="agent",
                )
                await emit(
                    "approval.requested",
                    self._approval_event_payload(approval),
                )
                await self._await_approval(approval["id"], emit)
            except SandboxLayerError:
                approval_state = (
                    await self.store.get_approval_request(approval["id"])
                    if approval
                    else None
                )
                failure_kind = self._approval_failure_kind(approval_state)
                await self.store.update_tool_call(
                    tool_call_id,
                    "failed",
                    failure_kind=failure_kind,
                    ended=True,
                )
                await emit(
                    "tool.call.failed",
                    {
                        "task_id": task.id,
                        "task_attempt_id": task_attempt_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "failure_kind": failure_kind,
                    },
                )
                raise
        try:
            envelope = await self._execute_runtime_tool(
                run_id=run_id,
                tool_name=tool_name,
                args=args,
                tool_call_id=tool_call_id,
                task_attempt_id=task_attempt_id,
                workspace_path=workspace_path,
                sandbox_session_id=sandbox_session_id,
                runtime_policy=policy.policy,
                policy_reason=policy.reason,
            )
        except (SandboxLayerError, GitHubBrokerError):
            await self.store.update_tool_call(
                tool_call_id,
                "failed",
                failure_kind=self._request_failure_kind(tool_name),
                ended=True,
            )
            await emit(
                "tool.call.failed",
                {
                    "task_id": task.id,
                    "task_attempt_id": task_attempt_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "failure_kind": self._request_failure_kind(tool_name),
                },
            )
            raise
        serialized = envelope.model_dump(mode="json")
        failure_kind = (
            "sandbox_runtime_error"
            if envelope.execution_status != "succeeded"
            else "tool_error"
            if envelope.tool_result and not envelope.tool_result.ok
            else None
        )
        await self.store.record_tool_execution(
            run_id=run_id,
            task_id=task.id,
            task_attempt_id=task_attempt_id,
            failure_kind=failure_kind,
            envelope=serialized,
        )
        call_status = "succeeded" if failure_kind is None else "failed"
        await self.store.update_tool_call(
            tool_call_id,
            call_status,
            latest_execution_id=envelope.execution_id,
            failure_kind=failure_kind,
            ended=True,
        )
        await emit(
            "tool.execution",
            {
                "task_id": task.id,
                "task_attempt_id": task_attempt_id,
                "execution": serialized,
                "failure_kind": failure_kind,
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

    async def _await_approval(
        self,
        approval_id: str,
        emit: EmitToolEvent,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.approval_timeout_seconds
        while True:
            approval = await self.store.get_approval_request(approval_id)
            if approval is None:
                raise SandboxLayerError(
                    f"Human approval request {approval_id} was not found."
                )
            if approval["status"] == "approved":
                return approval
            if approval["status"] == "denied":
                raise SandboxLayerError(
                    f"Human approval denied for {approval['tool_name']}."
                )
            if approval["status"] == "expired":
                raise SandboxLayerError(
                    f"Human approval expired for {approval['tool_name']}."
                )
            if asyncio.get_running_loop().time() >= deadline:
                expired = await self.store.expire_approval_request(approval_id)
                if expired:
                    await emit(
                        "approval.expired",
                        self._approval_event_payload(expired),
                    )
                raise SandboxLayerError(
                    f"Human approval timed out for {approval['tool_name']}."
                )
            await asyncio.sleep(self.approval_poll_seconds)

    @staticmethod
    def _approval_event_payload(approval: dict[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": approval["id"],
            "run_id": approval["run_id"],
            "task_id": approval["task_id"],
            "task_attempt_id": approval["task_attempt_id"],
            "tool_call_id": approval["tool_call_id"],
            "tool_name": approval["tool_name"],
            "tool_input": approval["tool_input"],
            "reason": approval["reason"],
            "status": approval["status"],
            "requested_by": approval["requested_by"],
            "decided_by": approval["decided_by"],
            "decision_reason": approval["decision_reason"],
        }

    @staticmethod
    def _approval_failure_kind(approval: dict[str, Any] | None) -> str:
        if not approval:
            return "human_approval_required"
        if approval.get("status") == "denied":
            return "human_denied"
        if approval.get("status") == "expired":
            return "approval_timeout"
        return "human_approval_required"

    async def _execute_runtime_tool(
        self,
        *,
        run_id: str,
        tool_name: str,
        args: dict[str, Any],
        tool_call_id: str,
        task_attempt_id: str | None = None,
        workspace_path: str | None = None,
        sandbox_session_id: str | None = None,
        runtime_policy: str | None = None,
        policy_reason: str | None = None,
    ) -> ToolExecutionEnvelope:
        if runtime_policy == "broker_only" or tool_name in TRUSTED_GITHUB_TOOLS:
            return await self.github_broker.execute_tool(
                run_id,
                tool_name,
                args,
                tool_call_id=tool_call_id,
                task_attempt_id=task_attempt_id,
                workspace_path=workspace_path,
                runtime_policy=runtime_policy,
                policy_reason=policy_reason,
            )
        return await self.sandbox.execute_tool(
            run_id,
            tool_name,
            args,
            tool_call_id=tool_call_id,
            task_attempt_id=task_attempt_id,
            workspace_path=workspace_path,
            sandbox_session_id=(
                sandbox_session_id
                if runtime_policy == "task_attempt_sandbox"
                else None
            ),
            sandbox_scope="task_attempt",
            runtime_policy=runtime_policy,
            policy_reason=policy_reason,
        )

    @staticmethod
    def _request_failure_kind(tool_name: str) -> str:
        if tool_name in TRUSTED_GITHUB_TOOLS:
            return "github_broker_request_error"
        return "sandbox_request_error"

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
