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
        return {
            "run": {"metadata": {}},
            "tasks": [],
            "task_attempts": [],
            "tool_calls": [],
            "task_handoffs": [],
        }

    async def create_task_attempt(self, *, run_id, task_id):
        self.attempt_no += 1
        return TaskAttemptRecord(
            id=f"attempt_{self.attempt_no}",
            run_id=run_id,
            task_id=task_id,
            attempt_no=self.attempt_no,
            status="running",
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
        self.handoffs: list[list[dict]] = []

    async def implement(
        self,
        *,
        task: TaskRecord,
        handoffs: list[dict],
        **_,
    ) -> AgentTaskResult:
        self.tasks.append(task.id)
        self.handoffs.append(list(handoffs))
        return AgentTaskResult(
            status="blocked",
            summary="Clone needs repository access.",
            claude_session_id="claude-session-1",
            criteria_results=[
                {
                    "criterion": criterion,
                    "status": "blocked",
                    "evidence": "Repository credentials were unavailable.",
                }
                for criterion in task.acceptance_criteria
            ],
            verification={
                "notes": "No workspace changes were made.",
            },
        )


class CompletingAgent:
    def __init__(self) -> None:
        self.tasks: list[str] = []
        self.handoffs: list[list[dict]] = []
        self.recovery_contexts: list[str | None] = []

    async def implement(
        self,
        *,
        task: TaskRecord,
        handoffs: list[dict],
        recovery_context: str | None = None,
        **_,
    ) -> AgentTaskResult:
        self.tasks.append(task.id)
        self.handoffs.append(list(handoffs))
        self.recovery_contexts.append(recovery_context)
        return AgentTaskResult(
            status="completed",
            summary=f"{task.id} completed.",
            claude_session_id="claude-session-1",
            criteria_results=[
                {
                    "criterion": criterion,
                    "status": "passing",
                    "evidence": f"{criterion} was verified in the test fixture.",
                }
                for criterion in task.acceptance_criteria
            ],
            verification={
                "commands_run": [
                    {
                        "tool": "fixture_tool",
                        "result": "passed",
                        "summary": f"{task.id} fixture completed.",
                    }
                ],
                "notes": "Fixture verification only.",
            },
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
            "task_handoffs": [
                {
                    "from_task_id": "task_done",
                    "status": "completed",
                    "summary": "Repository is ready.",
                    "payload": {
                        "schema_version": "task_handoff.v1",
                        "planned_task_results": [
                            {
                                "task_id": "task_done",
                                "task_seq": 1,
                                "title": "Clone repo",
                                "status": "passing",
                                "summary": "Repository is ready.",
                            },
                            {
                                "task_id": "task_resume",
                                "task_seq": 2,
                                "title": "Edit repo",
                                "status": "pending",
                                "summary": None,
                            },
                        ],
                        "from_task": {
                            "id": "task_done",
                            "seq": 1,
                            "kind": "model_task",
                            "title": "Clone repo",
                        },
                        "latest_completed_task": {
                            "task_id": "task_done",
                            "task_seq": 1,
                            "kind": "model_task",
                            "title": "Clone repo",
                            "status": "completed",
                            "criteria_results": [
                                {
                                    "criterion": "Repository cloned.",
                                    "status": "passing",
                                    "evidence": "Fixture repository is ready.",
                                }
                            ],
                            "verification": {
                                "commands_run": [
                                    {
                                        "tool": "clone_github_repository",
                                        "result": "passed",
                                        "summary": "Fixture clone completed.",
                                    }
                                ],
                                "notes": "Fixture recovery handoff.",
                            },
                        },
                        "status": "completed",
                        "summary": "Repository is ready.",
                    },
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
        self.assertEqual(agent.handoffs, [[]])
        self.assertIn(("task_1", "blocked"), [(task_id, status) for task_id, status, _ in store.task_updates])
        self.assertIn(("run", "blocked"), [(run_id, status) for run_id, status, _ in store.run_updates])
        self.assertIn("task.blocked", [event_type for event_type, _ in store.events])
        self.assertIn("task.handoff", [event_type for event_type, _ in store.events])
        self.assertIn("run.blocked", [event_type for event_type, _ in store.events])
        self.assertNotIn("run.completed", [event_type for event_type, _ in store.events])
        self.assertEqual(sandbox.deleted_run_ids, [])

    async def test_next_task_receives_previous_task_handoff_json(self) -> None:
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
        self.assertEqual(agent.handoffs[0], [])
        self.assertEqual(agent.handoffs[1][0]["from_task"]["id"], "task_1")
        self.assertEqual(agent.handoffs[1][0]["summary"], "task_1 completed.")
        self.assertEqual(agent.handoffs[1][0]["schema_version"], "task_handoff.v1")
        self.assertEqual(
            agent.handoffs[1][0]["planned_task_results"][0]["title"],
            "Clone repo",
        )
        self.assertEqual(
            agent.handoffs[1][0]["planned_task_results"][0]["status"],
            "passing",
        )
        self.assertEqual(
            agent.handoffs[1][0]["planned_task_results"][1]["status"],
            "pending",
        )
        self.assertEqual(
            agent.handoffs[1][0]["latest_completed_task"]["criteria_results"][0][
                "status"
            ],
            "passing",
        )
        self.assertEqual(
            agent.handoffs[1][0]["latest_completed_task"]["verification"][
                "commands_run"
            ][0]["tool"],
            "fixture_tool",
        )
        self.assertIn("task.handoff", [event_type for event_type, _ in store.events])
        handoff_events = [
            payload for event_type, payload in store.events
            if event_type == "task.handoff"
        ]
        self.assertEqual(handoff_events[0], store.handoffs[0]["payload"])
        self.assertNotIn("handoff_id", handoff_events[0])
        self.assertNotIn("transcript_artifact_id", handoff_events[0])
        self.assertNotIn("transcript_path", handoff_events[0])
        self.assertNotIn("task_id", handoff_events[0])
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
        self.assertEqual(agent.handoffs[0][0]["from_task"]["id"], "task_done")
        self.assertIn("toolcall_failed", agent.recovery_contexts[0])
        self.assertIn("sandbox_runtime_error", agent.recovery_contexts[0])
        self.assertIn("run.resumed", [event_type for event_type, _ in store.events])
        self.assertIn("run.completed", [event_type for event_type, _ in store.events])


if __name__ == "__main__":
    unittest.main()
