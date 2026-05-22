import unittest

from cloud_agent_poc.brain.orchestrator import RunOrchestrator
from cloud_agent_poc.domain import AgentTaskResult, PlannedTask, SessionEvent, TaskRecord, Workspace


class RecordingStore:
    def __init__(self) -> None:
        self.run_updates: list[tuple[str, str, dict]] = []
        self.task_updates: list[tuple[str, str, dict]] = []
        self.events: list[tuple[str, dict]] = []
        self.handoffs: list[dict] = []

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

    async def append_event(self, **kwargs) -> SessionEvent:
        self.events.append((kwargs["event_type"], kwargs["payload"]))
        return None

    async def create_task_handoff(self, **kwargs) -> int:
        self.handoffs.append(kwargs)
        return len(self.handoffs)


class StaticPlanner:
    async def plan(self, _: str, __: Workspace) -> list[PlannedTask]:
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
            status="completed",
            summary=f"{task.id} completed.",
            claude_session_id="claude-session-1",
        )


class StaticWorkspaceService:
    async def create_workspace(self, _: str) -> Workspace:
        return Workspace(path="/tmp/run")


class RunOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_task_stops_following_tasks(self) -> None:
        store = RecordingStore()
        agent = BlockingAgent()
        orchestrator = RunOrchestrator(
            store=store,
            planner=StaticPlanner(),
            agent=agent,
            sandbox=StaticWorkspaceService(),
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

    async def test_next_task_resumes_previous_claude_session(self) -> None:
        store = RecordingStore()
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
            prompt="Clone a repo and edit it.",
        )

        self.assertEqual(agent.tasks, ["task_1", "task_2"])
        self.assertEqual(agent.resume_session_ids, [None, "claude-session-1"])
        self.assertIn("task.handoff", [event_type for event_type, _ in store.events])
        self.assertIn("run.completed", [event_type for event_type, _ in store.events])


if __name__ == "__main__":
    unittest.main()
