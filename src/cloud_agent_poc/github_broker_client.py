from __future__ import annotations

from typing import Any

import httpx

from .sandbox_protocol import ToolExecutionEnvelope


class GitHubBrokerError(RuntimeError):
    pass


class GitHubBrokerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def execute_tool(
        self,
        run_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        tool_call_id: str,
        workspace_path: str | None = None,
    ) -> ToolExecutionEnvelope:
        payload = await self._request(
            "POST",
            "/internal/tool-executions",
            json={
                "run_id": run_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "args": args,
                "workspace_path": workspace_path,
            },
        )
        return ToolExecutionEnvelope.model_validate(payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=90) as client:
            response = await client.request(method, path, json=json)
        if response.is_success:
            return response.json()
        detail = response.text
        try:
            detail = str(response.json().get("detail", detail))
        except ValueError:
            pass
        raise GitHubBrokerError(
            f"GitHub broker request {method} {path} failed with HTTP "
            f"{response.status_code}: {detail}"
        )
