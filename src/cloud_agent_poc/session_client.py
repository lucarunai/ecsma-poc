from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .domain import PlannedTask, RunRecord, SessionEvent, TaskAttemptRecord, TaskRecord
from .ownership import DEFAULT_USER_ID, normalize_user_id


class SessionLayerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _user_headers(user_id: str | None) -> dict[str, str]:
        return {"X-User-Id": normalize_user_id(user_id or DEFAULT_USER_ID)}

    async def create_session(self, *, user_id: str | None = None) -> str:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/api/sessions",
                headers=self._user_headers(user_id),
            )
            response.raise_for_status()
            return str(response.json()["session_id"])

    async def create_run(
        self,
        session_id: str,
        prompt: str,
        *,
        idempotency_key: str | None = None,
        user_id: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {"prompt": prompt}
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/api/sessions/{session_id}/runs",
                json=payload,
                headers=self._user_headers(user_id),
            )
            response.raise_for_status()
            return str(response.json()["run_id"])

    async def get_run(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get(
                f"/api/runs/{run_id}",
                headers=self._user_headers(user_id),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def wake_run(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/api/runs/{run_id}/wake",
                headers=self._user_headers(user_id),
            )
            response.raise_for_status()
            return response.json()

    async def claim_next_queued_run(
        self,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 60,
    ) -> RunRecord | None:
        payload: dict[str, Any] = {"lease_seconds": lease_seconds}
        if worker_id:
            payload["worker_id"] = worker_id
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post("/internal/runs/claim", json=payload)
            if response.status_code == 204:
                return None
            response.raise_for_status()
            return RunRecord(**response.json())

    async def requeue_expired_run_leases(self) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post("/internal/runs/requeue-expired-leases")
            response.raise_for_status()
            return response.json()

    async def fail_runs_over_attempt_limit(
        self,
        *,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/runs/fail-exhausted-attempts",
                json={"max_attempts": max_attempts},
            )
            response.raise_for_status()
            return response.json()

    async def heartbeat_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> bool:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/internal/runs/{run_id}/heartbeat",
                json={
                    "worker_id": worker_id,
                    "lease_seconds": lease_seconds,
                },
            )
            if response.status_code == 409:
                return False
            response.raise_for_status()
            return True

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
        lease_seconds: int = 60,
    ) -> TaskAttemptRecord:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/internal/tasks/{task_id}/attempts",
                json={
                    "run_id": run_id,
                    "lease_seconds": lease_seconds,
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
        failure_kind: str | None = None,
        failure_reason: str | None = None,
        ended: bool = False,
    ) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.patch(
                f"/internal/task-attempts/{attempt_id}",
                json={
                    "status": status,
                    "claude_session_id": claude_session_id,
                    "failure_kind": failure_kind,
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

    async def create_approval_request(
        self,
        *,
        run_id: str,
        task_id: str,
        task_attempt_id: str | None,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        reason: str,
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                "/internal/approval-requests",
                json={
                    "run_id": run_id,
                    "task_id": task_id,
                    "task_attempt_id": task_attempt_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "reason": reason,
                    "requested_by": requested_by,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_approval_request(self, approval_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get(f"/internal/approval-requests/{approval_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def expire_approval_request(self, approval_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/internal/approval-requests/{approval_id}/expire"
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def decide_approval_request(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str | None = "web-ui",
        decision_reason: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(
                f"/api/approvals/{approval_id}/decision",
                json={
                    "decision": decision,
                    "decided_by": decided_by,
                    "decision_reason": decision_reason,
                },
                headers=self._user_headers(user_id),
            )
            response.raise_for_status()
            return response.json()

    async def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
        schema_version: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
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
                    "schema_version": schema_version,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
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

    async def get_run_replay_report(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get(
                f"/internal/runs/{run_id}/replay",
                headers=self._user_headers(user_id),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
