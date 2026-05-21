from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..domain import AgentTaskResult, SessionEvent, TaskRecord, Workspace
from .claude_agent import ClaudeCodingAgent
from .github_workflow import GitHubWorkflowService
from .planner import AgentTaskPlanner

if TYPE_CHECKING:
    from ..session_store import PostgresSessionStore


class RunOrchestrator:
    def __init__(
        self,
        *,
        store: PostgresSessionStore,
        planner: AgentTaskPlanner,
        agent: ClaudeCodingAgent,
        github: GitHubWorkflowService,
    ) -> None:
        self.store = store
        self.planner = planner
        self.agent = agent
        self.github = github

    async def execute(self, *, session_id: str, run_id: str, prompt: str) -> None:
        try:
            await self.store.update_run(run_id, "running", started=True)
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="run.started",
                payload={"run_id": run_id},
            )
            workspace = await self.github.create_workspace(run_id)
            await self.store.update_run(
                run_id,
                "running",
                metadata={
                    "workspace_path": workspace.path,
                },
            )
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="plan.started",
                payload={"planner": "agent_task_planner"},
            )
            tasks = await self.store.create_tasks(
                run_id,
                await self.planner.plan(prompt, workspace),
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
            claude_session_id: str | None = None
            for task in tasks:
                result = await self._execute_model_task(
                    session_id=session_id,
                    run_id=run_id,
                    prompt=prompt,
                    task=task,
                    workspace=workspace,
                    resume_session_id=claude_session_id,
                )
                claude_session_id = result.claude_session_id or claude_session_id
                if claude_session_id:
                    await self.store.update_run(
                        run_id,
                        "running",
                        metadata={"claude_session_id": claude_session_id},
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
            await self.store.update_run(run_id, "completed", ended=True)
            await self._emit(
                session_id=session_id,
                run_id=run_id,
                event_type="run.completed",
                payload={"run_id": run_id},
            )
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
        workspace: Workspace,
        resume_session_id: str | None,
    ) -> AgentTaskResult:
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

        result = await self.agent.implement(
            prompt=prompt,
            task=task,
            workspace=workspace,
            resume_session_id=resume_session_id,
            emit=emit,
        )

        await self.store.update_task(
            task.id,
            result.status,
            ended=True,
            result_summary=result.summary,
        )
        await self._emit_task(
            session_id,
            run_id,
            task,
            f"task.{result.status}",
            {
                "summary": result.summary,
                "claude_session_id": result.claude_session_id,
            },
        )
        await self._emit_task(
            session_id,
            run_id,
            task,
            "task.handoff",
            {
                "status": result.status,
                "summary": result.summary,
                "claude_session_id": result.claude_session_id,
                "next_resume_session_id": result.claude_session_id,
            },
        )
        if result.status == "failed":
            raise RuntimeError(f"Task {task.seq} failed: {result.summary}")
        return result

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
