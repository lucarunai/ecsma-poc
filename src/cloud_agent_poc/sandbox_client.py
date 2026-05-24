from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from .domain import Workspace
from .sandbox_protocol import ToolExecutionEnvelope


class SandboxLayerError(RuntimeError):
    pass


class SandboxLayerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def create_workspace(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> Workspace:
        payload = await self._request(
            "POST",
            f"/internal/workspaces/{run_id}",
            json={"user_id": user_id} if user_id else None,
        )
        return Workspace(path=str(payload["path"]))

    async def delete_workspace(
        self,
        run_id: str,
        *,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        params = f"?workspace_path={workspace_path}" if workspace_path else ""
        return await self._request("DELETE", f"/internal/workspaces/{run_id}{params}")

    async def read_file(self, run_id: str, path: str) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "read_workspace_file",
            {"path": path},
        )

    async def write_file(
        self,
        run_id: str,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "write_workspace_file",
            {"path": path, "content": content},
        )

    async def edit_file(
        self,
        run_id: str,
        path: str,
        old_text: str,
        new_text: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "edit_workspace_file",
            {"path": path, "old_text": old_text, "new_text": new_text},
        )

    async def glob_files(self, run_id: str, pattern: str) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "glob_workspace_files",
            {"pattern": pattern},
        )

    async def grep_files(
        self,
        run_id: str,
        pattern: str,
        glob: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "grep_workspace_files",
            {"pattern": pattern, "glob": glob},
        )

    async def clone_repository(
        self,
        run_id: str,
        repository_url: str,
        source_branch: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "clone_github_repository",
            {
                "repository_url": repository_url,
                "source_branch": source_branch,
            },
        )

    async def create_branch(self, run_id: str, branch_name: str) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "create_git_branch",
            {"branch_name": branch_name},
        )

    async def checkout_branch(self, run_id: str, branch_name: str) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "checkout_git_branch",
            {"branch_name": branch_name},
        )

    async def git_status(self, run_id: str) -> dict[str, Any]:
        return await self._execute_tool_data(run_id, "git_status", {})

    async def git_diff_stat(self, run_id: str) -> dict[str, Any]:
        return await self._execute_tool_data(run_id, "git_diff_stat", {})

    async def run_python_unittest(
        self,
        run_id: str,
        start_directory: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "run_python_unittest",
            {"start_directory": start_directory},
            allow_tool_error=True,
        )

    async def commit_changes(
        self,
        run_id: str,
        commit_message: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "commit_git_changes",
            {"commit_message": commit_message},
        )

    async def push_current_branch(self, run_id: str) -> dict[str, Any]:
        return await self._execute_tool_data(run_id, "push_current_git_branch", {})

    async def create_pull_request(
        self,
        run_id: str,
        *,
        target_branch: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        return await self._execute_tool_data(
            run_id,
            "create_github_pull_request",
            {
                "target_branch": target_branch,
                "title": title,
                "body": body,
            },
        )

    async def execute_tool(
        self,
        run_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        tool_call_id: str | None = None,
        workspace_path: str | None = None,
    ) -> ToolExecutionEnvelope:
        payload = await self._request(
            "POST",
            "/internal/tool-executions",
            json={
                "run_id": run_id,
                "tool_call_id": tool_call_id or f"toolcall_{uuid4().hex}",
                "tool_name": tool_name,
                "args": args,
                "workspace_path": workspace_path,
            },
        )
        envelope = ToolExecutionEnvelope.model_validate(payload)
        return envelope

    async def _execute_tool_data(
        self,
        run_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        allow_tool_error: bool = False,
    ) -> dict[str, Any]:
        envelope = await self.execute_tool(run_id, tool_name, args)
        tool_result = envelope.tool_result
        if envelope.execution_status != "succeeded":
            raise SandboxLayerError(
                envelope.failure_message or f"Sandbox execution failed for {tool_name}."
            )
        if not tool_result:
            raise SandboxLayerError(f"Sandbox tool returned no result for {tool_name}.")
        if not tool_result.ok and not allow_tool_error:
            raise SandboxLayerError(tool_result.summary)
        return tool_result.data

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=60) as client:
            response = await client.request(method, path, json=json)
        if response.is_success:
            return response.json()
        detail = response.text
        try:
            detail = str(response.json().get("detail", detail))
        except ValueError:
            pass
        raise SandboxLayerError(
            f"Sandbox request {method} {path} failed with HTTP "
            f"{response.status_code}: {detail}"
        )
