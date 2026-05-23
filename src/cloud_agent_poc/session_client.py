from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .domain import PlannedTask, RunRecord, SessionEvent, TaskAttemptRecord, TaskRecord


class SessionLayerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def create_session(self) -> str:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post("/api/sessions")
            response.raise_for_status()
            return str(response.json()["session_id"])

    async def create_run(self, session_id: str, prompt: str) -> str:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/api/sessions/{session_id}/runs",
                json={"prompt": prompt},
            )
            response.raise_for_status()
            return str(response.json()["run_id"])

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get(f"/api/runs/{run_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def wake_run(self, run_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(f"/api/runs/{run_id}/wake")
            response.raise_for_status()
            return response.json()

    async def claim_next_queued_run(self) -> RunRecord | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post("/internal/runs/claim")
            if response.status_code == 204:
                return None
            response.raise_for_status()
            return RunRecord(**response.json())

    async def update_run(
        self,
        run_id: str,
        status: str,
        *,
        error_message: str | None = None,
        started: bool = False,
        ended: bool = False,
        metadata: dict[str, Any] | None = None,
        acceptance_criteria: list[dict[str, Any]] | None = None,
    ) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.patch(
                f"/internal/runs/{run_id}",
                json={
                    "status": status,
                    "error_message": error_message,
                    "started": started,
                    "ended": ended,
                    "metadata": metadata,
                    "acceptance_criteria": acceptance_criteria,
                },
            )
            response.raise_for_status()

    async def create_tasks(
        self,
        run_id: str,
        planned_tasks: list[PlannedTask],
    ) -> list[TaskRecord]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/internal/runs/{run_id}/tasks",
                json={
                    "tasks": [
                        {
                            "title": task.title,
                            "description": task.description,
                            "acceptance_criteria": task.acceptance_criteria,
                        }
                        for task in planned_tasks
                    ]
                },
            )
            response.raise_for_status()
            return [TaskRecord(**task) for task in response.json()["tasks"]]

    async def update_task(
        self,
        task_id: str,
        status: str,
        *,
        started: bool = False,
        ended: bool = False,
        result_summary: str | None = None,
    ) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.patch(
                f"/internal/tasks/{task_id}",
                json={
                    "status": status,
                    "started": started,
                    "ended": ended,
                    "result_summary": result_summary,
                },
            )
            response.raise_for_status()

    async def create_task_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
    ) -> TaskAttemptRecord:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/internal/tasks/{task_id}/attempts",
                json={
                    "run_id": run_id,
                },
            )
            response.raise_for_status()
            return TaskAttemptRecord(**response.json())

    async def update_task_attempt(
        self,
        attempt_id: str,
        status: str,
        *,
        claude_session_id: str | None = None,
        failure_reason: str | None = None,
        ended: bool = False,
    ) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.patch(
                f"/internal/task-attempts/{attempt_id}",
                json={
                    "status": status,
                    "claude_session_id": claude_session_id,
                    "failure_reason": failure_reason,
                    "ended": ended,
                },
            )
            response.raise_for_status()

    async def create_tool_call(
        self,
        *,
        run_id: str,
        task_id: str,
        task_attempt_id: str | None,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/tool-calls",
                json={
                    "run_id": run_id,
                    "task_id": task_id,
                    "task_attempt_id": task_attempt_id,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                },
            )
            response.raise_for_status()
            return str(response.json()["tool_call_id"])

    async def update_tool_call(
        self,
        tool_call_id: str,
        status: str,
        *,
        latest_execution_id: str | None = None,
        failure_kind: str | None = None,
        ended: bool = False,
    ) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.patch(
                f"/internal/tool-calls/{tool_call_id}",
                json={
                    "status": status,
                    "latest_execution_id": latest_execution_id,
                    "failure_kind": failure_kind,
                    "ended": ended,
                },
            )
            response.raise_for_status()

    async def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> SessionEvent:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/events",
                json={
                    "session_id": session_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "event_type": event_type,
                    "payload": payload,
                },
            )
            response.raise_for_status()
            data = response.json()
            data["created_at"] = datetime.fromisoformat(data["created_at"])
            return SessionEvent(**data)

    async def record_agent_transcript(
        self,
        *,
        run_id: str,
        task_id: str | None,
        provider: str,
        provider_session_id: str,
        artifact_path: str,
        checksum: str,
        size_bytes: int,
    ) -> str:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/agent-transcripts",
                json={
                    "run_id": run_id,
                    "task_id": task_id,
                    "provider": provider,
                    "provider_session_id": provider_session_id,
                    "artifact_path": artifact_path,
                    "checksum": checksum,
                    "size_bytes": size_bytes,
                },
            )
            response.raise_for_status()
            return str(response.json()["transcript_id"])

    async def create_task_handoff(
        self,
        *,
        session_id: str,
        run_id: str,
        from_task_id: str,
        to_task_id: str | None = None,
        status: str,
        summary: str,
        payload: dict[str, Any],
        claude_session_id: str | None = None,
        transcript_id: str | None = None,
    ) -> int:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/task-handoffs",
                json={
                    "session_id": session_id,
                    "run_id": run_id,
                    "from_task_id": from_task_id,
                    "to_task_id": to_task_id,
                    "status": status,
                    "summary": summary,
                    "payload": payload,
                    "claude_session_id": claude_session_id,
                    "transcript_id": transcript_id,
                },
            )
            response.raise_for_status()
            return int(response.json()["handoff_id"])

    async def record_tool_execution(
        self,
        *,
        run_id: str,
        task_id: str | None,
        task_attempt_id: str | None,
        failure_kind: str | None,
        envelope: dict[str, Any],
    ) -> str:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/tool-executions",
                json={
                    "run_id": run_id,
                    "task_id": task_id,
                    "task_attempt_id": task_attempt_id,
                    "failure_kind": failure_kind,
                    "envelope": envelope,
                },
            )
            response.raise_for_status()
            return str(response.json()["execution_id"])

    async def get_run_recovery_bundle(self, run_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get(f"/internal/runs/{run_id}/recovery-bundle")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
