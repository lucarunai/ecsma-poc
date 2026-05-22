import unittest

from cloud_agent_poc.brain.orchestrator import RunOrchestrator
from cloud_agent_poc.domain import (
    AgentTaskResult,
    PlannedTask,
    SessionEvent,
    TaskAttemptRecord,
    TaskRecord,
    Workspace,
)


class RecordingStore:
    def __init__(self) -> None:
        self.run_updates: list[tuple[str, str, dict]] = []
        self.task_updates: list[tuple[str, str, dict]] = []
        self.task_attempt_updates: list[tuple[str, str, dict]] = []
        self.events: list[tuple[str, dict]] = []
        self.handoffs: list[dict] = []
        self.attempt_no = 0

    async def update_run(self, run_id: str, status: str, **kwargs) -> None:
        self.run_updates.append((run_id, status, kwargs))

    async def create_tasks(
        self,
        run_id: str,
        planned_tasks: list[PlannedTask],
    ) -> list[TaskRecord]:
        return [
            TaskRecord(
                id=f"task_{seq}",
                run_id=run_id,
                seq=seq,
                kind="model_task",
                title=task.title,
                description=task.description,
                acceptance_criteria=task.acceptance_criteria,
                status="pending",
            )
            for seq, task in enumerate(planned_tasks, start=1)
        ]

    async def update_task(self, task_id: str, status: str, **kwargs) -> None:
        self.task_updates.append((task_id, status, kwargs))

    async def get_run_recovery_bundle(self, _: str):
        return {"run": {"metadata": {}}, "tasks": [], "task_attempts": [], "tool_calls": []}

    async def create_task_attempt(self, *, run_id, task_id, resume_from_session_id):
        self.attempt_no += 1
        return TaskAttemptRecord(
            id=f"attempt_{self.attempt_no}",
            run_id=run_id,
            task_id=task_id,
            attempt_no=self.attempt_no,
            status="running",
            resume_from_session_id=resume_from_session_id,
        )

    async def update_task_attempt(self, attempt_id: str, status: str, **kwargs):
        self.task_attempt_updates.append((attempt_id, status, kwargs))

    async def append_event(self, **kwargs) -> SessionEvent:
        self.events.append((kwargs["event_type"], kwargs["payload"]))
        return None

    async def create_task_handoff(self, **kwargs) -> int:
        self.handoffs.append(kwargs)
        return len(self.handoffs)


class StaticPlanner:
    async def plan(self, _: str, __: Workspace, **___) -> list[PlannedTask]:
        return [
            PlannedTask("Clone repo", "Prepare checkout.", ["Repository cloned."]),
            PlannedTask("Edit repo", "Add code.", ["Code changed."]),
        ]


class BlockingAgent:
    def __init__(self) -> None:
        self.tasks: list[str] = []
        self.resume_session_ids: list[str | None] = []

    async def implement(
        self,
        *,
        task: TaskRecord,
        resume_session_id: str | None,
        **_,
    ) -> AgentTaskResult:
        self.tasks.append(task.id)
        self.resume_session_ids.append(resume_session_id)
        return AgentTaskResult(
            status="blocked",
            summary="Clone needs repository access.",
            claude_session_id="claude-session-1",
        )


class CompletingAgent:
    def __init__(self) -> None:
        self.tasks: list[str] = []
        self.resume_session_ids: list[str | None] = []
        self.recovery_contexts: list[str | None] = []

    async def implement(
        self,
        *,
        task: TaskRecord,
        resume_session_id: str | None,
        recovery_context: str | None = None,
        **_,
    ) -> AgentTaskResult:
        self.tasks.append(task.id)
        self.resume_session_ids.append(resume_session_id)
        self.recovery_contexts.append(recovery_context)
        return AgentTaskResult(
            status="completed",
            summary=f"{task.id} completed.",
            claude_session_id="claude-session-1",
        )


class StaticWorkspaceService:
    def __init__(self) -> None:
        self.deleted_run_ids: list[str] = []

    async def create_workspace(self, _: str) -> Workspace:
        return Workspace(path="/tmp/run")

    async def delete_workspace(self, run_id: str) -> dict[str, str]:
        self.deleted_run_ids.append(run_id)
        return {"status": "deleted"}


class FailingCleanupWorkspaceService(StaticWorkspaceService):
    async def delete_workspace(self, run_id: str) -> dict[str, str]:
        raise RuntimeError(f"cleanup failed for {run_id}")


class RecoveryStore(RecordingStore):
    async def get_run_recovery_bundle(self, _: str):
        return {
            "run": {
                "metadata": {
                    "workspace_path": "/tmp/resume-run",
                    "active_claude_session_id": "claude-session-active",
                }
            },
            "tasks": [
                TaskRecord(
                    id="task_done",
                    run_id="run",
                    seq=1,
                    kind="model_task",
                    title="Clone repo",
                    description="Prepare checkout.",
                    acceptance_criteria=["Repository cloned."],
                    status="completed",
                ).__dict__,
                TaskRecord(
                    id="task_resume",
                    run_id="run",
                    seq=2,
                    kind="model_task",
                    title="Edit repo",
                    description="Add code.",
                    acceptance_criteria=["Code changed."],
                    status="resume_queued",
                ).__dict__,
            ],
            "task_attempts": [],
            "tool_calls": [
                {
                    "id": "toolcall_failed",
                    "task_id": "task_resume",
                    "tool_name": "write_workspace_file",
                    "input": {"path": "hello.py"},
                    "status": "failed",
                    "failure_kind": "sandbox_runtime_error",
                    "latest_execution_id": "sbxexec_failed",
                }
            ],
        }


class RunOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_task_stops_following_tasks(self) -> None:
        store = RecordingStore()
        agent = BlockingAgent()
        sandbox = StaticWorkspaceService()
        orchestrator = RunOrchestrator(
            store=store,
            planner=StaticPlanner(),
            agent=agent,
            sandbox=sandbox,
        )

        await orchestrator.execute(
            session_id="session",
            run_id="run",
            prompt="Clone a repo and edit it.",
        )

        self.assertEqual(agent.tasks, ["task_1"])
        self.assertEqual(agent.resume_session_ids, [None])
        self.assertIn(("task_1", "blocked"), [(task_id, status) for task_id, status, _ in store.task_updates])
        self.assertIn(("run", "blocked"), [(run_id, status) for run_id, status, _ in store.run_updates])
        self.assertIn("task.blocked", [event_type for event_type, _ in store.events])
        self.assertIn("task.handoff", [event_type for event_type, _ in store.events])
        self.assertIn("run.blocked", [event_type for event_type, _ in store.events])
        self.assertNotIn("run.completed", [event_type for event_type, _ in store.events])
        self.assertEqual(sandbox.deleted_run_ids, [])

    async def test_next_task_resumes_previous_claude_session(self) -> None:
        store = RecordingStore()
        agent = CompletingAgent()
        sandbox = StaticWorkspaceService()
        orchestrator = RunOrchestrator(
            store=store,
            planner=StaticPlanner(),
            agent=agent,
            sandbox=sandbox,
        )

        await orchestrator.execute(
            session_id="session",
            run_id="run",
            prompt="Clone a repo and edit it.",
        )

        self.assertEqual(agent.tasks, ["task_1", "task_2"])
        self.assertEqual(agent.resume_session_ids, [None, "claude-session-1"])
        self.assertIn("task.handoff", [event_type for event_type, _ in store.events])
        self.assertIn("run.completed", [event_type for event_type, _ in store.events])
        self.assertIn("workspace.cleaned", [event_type for event_type, _ in store.events])
        self.assertEqual(sandbox.deleted_run_ids, ["run"])
        self.assertIn(
            ("run", "completed", {"metadata": {"workspace_cleaned": True}}),
            store.run_updates,
        )

    async def test_workspace_cleanup_failure_does_not_fail_completed_run(self) -> None:
        store = RecordingStore()
        agent = CompletingAgent()
        orchestrator = RunOrchestrator(
            store=store,
            planner=StaticPlanner(),
            agent=agent,
            sandbox=FailingCleanupWorkspaceService(),
        )

        await orchestrator.execute(
            session_id="session",
            run_id="run",
            prompt="Clone a repo and edit it.",
        )

        self.assertIn("run.completed", [event_type for event_type, _ in store.events])
        self.assertIn("workspace.cleanup_failed", [event_type for event_type, _ in store.events])
        self.assertNotIn("run.failed", [event_type for event_type, _ in store.events])

    async def test_resumed_task_receives_failed_tool_recovery_context(self) -> None:
        store = RecoveryStore()
        agent = CompletingAgent()
        orchestrator = RunOrchestrator(
            store=store,
            planner=StaticPlanner(),
            agent=agent,
            sandbox=StaticWorkspaceService(),
        )

        await orchestrator.execute(
            session_id="session",
            run_id="run",
            prompt="Resume the blocked edit.",
        )

        self.assertEqual(agent.tasks, ["task_resume"])
        self.assertEqual(agent.resume_session_ids, ["claude-session-active"])
        self.assertIn("toolcall_failed", agent.recovery_contexts[0])
        self.assertIn("sandbox_runtime_error", agent.recovery_contexts[0])
        self.assertIn("run.resumed", [event_type for event_type, _ in store.events])
        self.assertIn("run.completed", [event_type for event_type, _ in store.events])


if __name__ == "__main__":
    unittest.main()
