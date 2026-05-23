from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..domain import AgentTaskResult, SessionEvent, TaskRecord, Workspace
from .claude_agent import ClaudeCodingAgent
from .planner import AgentTaskPlanner

if TYPE_CHECKING:
    from ..sandbox_client import SandboxLayerClient
    from ..session_store import PostgresSessionStore


class RunOrchestrator:
    def __init__(
        self,
        *,
        store: PostgresSessionStore,
        planner: AgentTaskPlanner,
        agent: ClaudeCodingAgent,
        sandbox: SandboxLayerClient,
    ) -> None:
        self.store = store
        self.planner = planner
        self.agent = agent
        self.sandbox = sandbox

    async def execute(self, *, session_id: str, run_id: str, prompt: str) -> None:
        try:
            await self.store.update_run(run_id, "running", started=True)
            recovery_bundle = await self.store.get_run_recovery_bundle(run_id)
            existing_tasks = self._tasks_from_bundle(recovery_bundle)
            is_resume = bool(existing_tasks)
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="run.resumed" if is_resume else "run.started",
                payload={"run_id": run_id},
            )
            workspace = await self._workspace_for_run(run_id, recovery_bundle)
            if existing_tasks:
                tasks = existing_tasks
            else:
                await self._emit(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="plan.started",
                    payload={"planner": "agent_task_planner"},
                )
                tasks = await self.store.create_tasks(
                    run_id,
                    await self.planner.plan(
                        prompt,
                        workspace,
                        emit=lambda event_type, payload: self._emit(
                            session_id=session_id,
                            run_id=run_id,
                            event_type=event_type,
                            payload=payload,
                        ),
                    ),
                )
                await self._emit(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="tasks.created",
                    payload={
                        "tasks": [
                            {
                                "id": task.id,
                                "seq": task.seq,
                                "kind": task.kind,
                                "title": task.title,
                            }
                            for task in tasks
                        ]
                    },
                )
            handoffs = self._handoffs_from_bundle(recovery_bundle)
            for task in tasks:
                if task.status == "completed":
                    continue
                result = await self._execute_model_task(
                    session_id=session_id,
                    run_id=run_id,
                    prompt=prompt,
                    task=task,
                    tasks=tasks,
                    workspace=workspace,
                    handoffs=handoffs,
                    recovery_context=self._recovery_context(recovery_bundle, task),
                )
                if result.status == "blocked":
                    await self.store.update_run(
                        run_id,
                        "blocked",
                        error_message=result.summary,
                        ended=True,
                    )
                    await self._emit(
                        session_id=session_id,
                        run_id=run_id,
                        event_type="run.blocked",
                        payload={"run_id": run_id, "summary": result.summary},
                    )
                    return
                handoffs.append(self._handoff_payload(task, result, tasks, handoffs))
            await self.store.update_run(run_id, "completed", ended=True)
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="run.completed",
                payload={"run_id": run_id},
            )
            await self._cleanup_workspace(session_id=session_id, run_id=run_id)
        except Exception as exc:
            await self.store.update_run(
                run_id,
                "failed",
                error_message=str(exc),
                ended=True,
            )
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="run.failed",
                payload={"run_id": run_id, "error": str(exc)},
            )

    async def _execute_model_task(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        task: TaskRecord,
        tasks: list[TaskRecord],
        workspace: Workspace,
        handoffs: list[dict[str, Any]],
        recovery_context: str | None,
    ) -> AgentTaskResult:
        attempt = await self.store.create_task_attempt(
            run_id=run_id,
            task_id=task.id,
        )
        await self.store.update_task(task.id, "running", started=True)
        await self._emit_task(session_id, run_id, task, "task.started")

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                task_id=task.id,
                event_type=event_type,
                payload=payload,
            )

        async def record_claude_session(claude_session_id: str) -> None:
            await self.store.update_task_attempt(
                attempt.id,
                "running",
                claude_session_id=claude_session_id,
            )

        try:
            result = await self.agent.implement(
                prompt=prompt,
                task=task,
                task_attempt_id=attempt.id,
                handoffs=handoffs,
                recovery_context=recovery_context,
                emit=emit,
                record_claude_session=record_claude_session,
            )
        except Exception as exc:
            await self.store.update_task_attempt(
                attempt.id,
                "failed",
                failure_reason=str(exc),
                ended=True,
            )
            raise

        await self.store.update_task(
            task.id,
            result.status,
            ended=True,
            result_summary=result.summary,
        )
        await self.store.update_task_attempt(
            attempt.id,
            result.status,
            claude_session_id=result.claude_session_id,
            failure_reason=result.summary if result.status != "completed" else None,
            ended=True,
        )
        transcript_id, transcript_path = await self._record_agent_transcript(
            run_id=run_id,
            task_id=task.id,
            workspace=workspace,
            claude_session_id=result.claude_session_id,
        )
        await self._emit_task(
            session_id,
            run_id,
            task,
            f"task.{result.status}",
            {
                "summary": result.summary,
                "claude_session_id": result.claude_session_id,
                "transcript_artifact_id": transcript_id,
                "transcript_path": transcript_path,
            },
        )
        handoff_payload = self._handoff_payload(task, result, tasks, handoffs)
        await self.store.create_task_handoff(
            session_id=session_id,
            run_id=run_id,
            from_task_id=task.id,
            status=result.status,
            summary=result.summary,
            payload=handoff_payload,
            claude_session_id=result.claude_session_id,
            transcript_id=transcript_id,
        )
        await self._emit(
            session_id=session_id,
            run_id=run_id,
            task_id=task.id,
            event_type="task.handoff",
            payload=handoff_payload,
        )
        if result.status == "failed":
            raise RuntimeError(f"Task {task.seq} failed: {result.summary}")
        return result

    async def _workspace_for_run(
        self,
        run_id: str,
        recovery_bundle: dict[str, Any] | None,
    ) -> Workspace:
        metadata = (recovery_bundle or {}).get("run", {}).get("metadata") or {}
        workspace_path = metadata.get("workspace_path")
        if workspace_path:
            return Workspace(path=str(workspace_path))
        workspace = await self.sandbox.create_workspace(run_id)
        await self.store.update_run(
            run_id,
            "running",
            metadata={"workspace_path": workspace.path},
        )
        return workspace

    async def _cleanup_workspace(self, *, session_id: str, run_id: str) -> None:
        try:
            result = await self.sandbox.delete_workspace(run_id)
        except Exception as exc:
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="workspace.cleanup_failed",
                payload={"run_id": run_id, "error": str(exc)},
            )
            return
        await self.store.update_run(
            run_id,
            "completed",
            metadata={"workspace_cleaned": True},
        )
        await self._emit(
            session_id=session_id,
            run_id=run_id,
            event_type="workspace.cleaned",
            payload={"run_id": run_id, "status": result.get("status", "deleted")},
        )

    @staticmethod
    def _tasks_from_bundle(
        recovery_bundle: dict[str, Any] | None,
    ) -> list[TaskRecord]:
        return [
            TaskRecord(**task)
            for task in (recovery_bundle or {}).get("tasks", [])
        ]

    @staticmethod
    def _handoffs_from_bundle(
        recovery_bundle: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        handoffs: list[dict[str, Any]] = []
        for handoff in (recovery_bundle or {}).get("task_handoffs", []):
            payload = handoff.get("payload")
            if isinstance(payload, dict) and payload:
                handoffs.append(payload)
                continue
            handoffs.append(
                {
                    "from_task": {"id": handoff.get("from_task_id")},
                    "status": handoff.get("status"),
                    "summary": handoff.get("summary"),
                }
            )
        return handoffs

    @staticmethod
    def _handoff_payload(
        task: TaskRecord,
        result: AgentTaskResult,
        tasks: list[TaskRecord],
        prior_handoffs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "task_handoff.v1",
            "run_id": task.run_id,
            "from_task": {
                "id": task.id,
                "seq": task.seq,
                "kind": task.kind,
                "title": task.title,
            },
            "planned_task_results": RunOrchestrator._planned_task_results(
                tasks,
                current_task=task,
                current_result=result,
                prior_handoffs=prior_handoffs,
            ),
            "latest_completed_task": {
                "task_id": task.id,
                "task_seq": task.seq,
                "kind": task.kind,
                "title": task.title,
                "status": result.status,
                "criteria_results": result.criteria_results,
                **(
                    {"verification": result.verification}
                    if result.verification
                    else {}
                ),
            },
            "status": result.status,
            "summary": result.summary,
        }

    @staticmethod
    def _planned_task_results(
        tasks: list[TaskRecord],
        *,
        current_task: TaskRecord,
        current_result: AgentTaskResult,
        prior_handoffs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        latest_prior_results = RunOrchestrator._latest_planned_task_results(
            prior_handoffs
        )
        results: list[dict[str, Any]] = []
        for planned_task in tasks:
            prior_item = latest_prior_results.get(planned_task.id) or {}
            if planned_task.id == current_task.id:
                status = RunOrchestrator._progress_status(current_result.status)
                summary = current_result.summary
            elif prior_item:
                status = str(prior_item.get("status") or "pending")
                summary = prior_item.get("summary")
            else:
                status = RunOrchestrator._progress_status(planned_task.status)
                summary = None
            item: dict[str, Any] = {
                "task_id": planned_task.id,
                "task_seq": planned_task.seq,
                "title": planned_task.title,
                "status": status,
                "summary": summary,
            }
            results.append(item)
        return results

    @staticmethod
    def _latest_planned_task_results(
        handoffs: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        for handoff in reversed(handoffs):
            planned_results = handoff.get("planned_task_results")
            if not isinstance(planned_results, list):
                continue
            return {
                str(item.get("task_id")): item
                for item in planned_results
                if isinstance(item, dict) and item.get("task_id")
            }
        return {}

    @staticmethod
    def _progress_status(status: str) -> str:
        if status == "completed":
            return "passing"
        if status in {"blocked", "failed"}:
            return status
        return "pending"

    @staticmethod
    def _recovery_context(
        recovery_bundle: dict[str, Any] | None,
        task: TaskRecord,
    ) -> str | None:
        if task.status != "resume_queued":
            return None
        failed_calls = [
            {
                "tool_call_id": call["id"],
                "tool_name": call["tool_name"],
                "input": call["input"],
                "status": call["status"],
                "failure_kind": call.get("failure_kind"),
                "latest_execution_id": call.get("latest_execution_id"),
            }
            for call in (recovery_bundle or {}).get("tool_calls", [])
            if call["task_id"] == task.id and call["status"] == "failed"
        ]
        if not failed_calls:
            return "The previous task attempt stopped before a failed tool context was recorded."
        return (
            "The previous task attempt stopped after these tool failures:\n"
            f"{json.dumps(failed_calls[:3], ensure_ascii=True, indent=2)}"
        )

    async def _emit_task(
        self,
        session_id: str,
        run_id: str,
        task: TaskRecord,
        event_type: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> SessionEvent:
        payload = {
            "task_id": task.id,
            "seq": task.seq,
            "kind": task.kind,
            "title": task.title,
        }
        payload.update(extra_payload or {})
        return await self._emit(
            session_id=session_id,
            run_id=run_id,
            task_id=task.id,
            event_type=event_type,
            payload=payload,
        )

    async def _emit(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> SessionEvent:
        event = await self.store.append_event(
            session_id=session_id,
            run_id=run_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )
        return event

    async def _record_agent_transcript(
        self,
        *,
        run_id: str,
        task_id: str,
        workspace: Workspace,
        claude_session_id: str | None,
    ) -> tuple[str | None, str | None]:
        if not claude_session_id:
            return None, None
        finder = getattr(self.agent, "find_transcript_path", None)
        if finder is None:
            return None, None
        transcript_path = finder(
            workspace=workspace,
            claude_session_id=claude_session_id,
        )
        if transcript_path is None:
            return None, None
        path = Path(transcript_path)
        if not path.exists():
            return None, None
        content = path.read_bytes()
        transcript_id = await self.store.record_agent_transcript(
            run_id=run_id,
            task_id=task_id,
            provider="claude",
            provider_session_id=claude_session_id,
            artifact_path=str(path),
            checksum=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        return transcript_id, str(path)
