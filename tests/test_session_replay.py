import unittest

from cloud_agent_poc.session_contracts import (
    event_hash,
    event_payload_hash,
    schema_version_for_event,
)
from cloud_agent_poc.session_replay import replay_and_compare, replay_events


def event(seq, event_type, payload, previous_hash=None):
    payload_hash = event_payload_hash(payload)
    schema_version = schema_version_for_event(event_type)
    current_hash = event_hash(
        previous_event_hash=previous_hash,
        event_type=event_type,
        schema_version=schema_version,
        seq=seq,
        payload_hash=payload_hash,
    )
    return {
        "id": seq,
        "seq": seq,
        "event_type": event_type,
        "schema_version": schema_version,
        "payload": payload,
        "payload_hash": payload_hash,
        "previous_event_hash": previous_hash,
        "event_hash": current_hash,
    }, current_hash


class SessionReplayTests(unittest.TestCase):
    def test_replays_happy_path_and_matches_materialized_state(self) -> None:
        events = []
        previous = None
        for seq, event_type, payload in (
            (1, "user.prompt.accepted", {"run_id": "run_1", "prompt": "do it"}),
            (2, "run.started", {"run_id": "run_1"}),
            (
                3,
                "tasks.created",
                {
                    "run_id": "run_1",
                    "tasks": [{"id": "task_1", "seq": 1, "title": "Do it"}],
                },
            ),
            (4, "task.started", {"task_id": "task_1", "seq": 1, "title": "Do it"}),
            (
                5,
                "tool.call.requested",
                {
                    "task_id": "task_1",
                    "tool_call_id": "tool_1",
                    "tool_name": "git_status",
                    "input": {},
                },
            ),
            (
                6,
                "tool.execution",
                {
                    "task_id": "task_1",
                    "execution": {
                        "tool_call_id": "tool_1",
                        "tool_name": "git_status",
                        "execution_status": "succeeded",
                    },
                    "failure_kind": None,
                },
            ),
            (7, "task.completed", {"task_id": "task_1", "summary": "done"}),
            (8, "run.completed", {"run_id": "run_1"}),
        ):
            record, previous = event(seq, event_type, payload, previous)
            events.append(record)

        report = replay_and_compare(
            run_id="run_1",
            events=events,
            materialized={
                "run": {"id": "run_1", "status": "completed"},
                "tasks": [{"id": "task_1", "status": "completed"}],
                "tool_calls": [{"id": "tool_1", "status": "succeeded"}],
            },
        )

        self.assertTrue(report["consistent"])
        self.assertTrue(report["hash_chain_valid"])
        self.assertEqual(report["differences"], [])

    def test_reports_projection_drift(self) -> None:
        first, previous = event(
            1,
            "user.prompt.accepted",
            {"run_id": "run_1", "prompt": "do it"},
        )
        second, _ = event(2, "run.started", {"run_id": "run_1"}, previous)

        report = replay_and_compare(
            run_id="run_1",
            events=[first, second],
            materialized={
                "run": {"id": "run_1", "status": "queued"},
                "tasks": [],
                "tool_calls": [],
            },
        )

        self.assertFalse(report["consistent"])
        self.assertEqual(report["differences"][0]["kind"], "run")
        self.assertEqual(report["differences"][0]["event_state"], "running")
        self.assertEqual(report["differences"][0]["table_state"], "queued")

    def test_replays_human_approval_tool_statuses(self) -> None:
        events = []
        previous = None
        for seq, event_type, payload in (
            (1, "user.prompt.accepted", {"run_id": "run_1", "prompt": "push"}),
            (2, "run.started", {"run_id": "run_1"}),
            (
                3,
                "tool.call.requested",
                {
                    "task_id": "task_1",
                    "tool_call_id": "tool_1",
                    "tool_name": "push_current_git_branch",
                    "input": {},
                },
            ),
            (
                4,
                "approval.requested",
                {
                    "approval_id": "approval_1",
                    "task_id": "task_1",
                    "tool_call_id": "tool_1",
                    "tool_name": "push_current_git_branch",
                },
            ),
            (
                5,
                "approval.approved",
                {
                    "approval_id": "approval_1",
                    "tool_call_id": "tool_1",
                    "tool_name": "push_current_git_branch",
                },
            ),
            (
                6,
                "tool.execution",
                {
                    "task_id": "task_1",
                    "execution": {
                        "tool_call_id": "tool_1",
                        "tool_name": "push_current_git_branch",
                        "execution_status": "succeeded",
                    },
                    "failure_kind": None,
                },
            ),
        ):
            record, previous = event(seq, event_type, payload, previous)
            events.append(record)

        replay = replay_events(run_id="run_1", events=events)

        self.assertEqual(replay["state"]["tool_calls"]["tool_1"]["status"], "succeeded")
        self.assertEqual(replay["errors"], [])

    def test_rejects_invalid_transition(self) -> None:
        first, previous = event(
            1,
            "user.prompt.accepted",
            {"run_id": "run_1", "prompt": "do it"},
        )
        second, previous = event(2, "run.started", {"run_id": "run_1"}, previous)
        third, previous = event(3, "run.completed", {"run_id": "run_1"}, previous)
        fourth, _ = event(
            4,
            "run.resume.requested",
            {"run_id": "run_1", "reason": "manual"},
            previous,
        )

        replay = replay_events(
            run_id="run_1",
            events=[first, second, third, fourth],
        )

        self.assertEqual(replay["errors"][0]["kind"], "invalid_transition")

    def test_detects_hash_chain_break(self) -> None:
        first, previous = event(
            1,
            "user.prompt.accepted",
            {"run_id": "run_1", "prompt": "do it"},
        )
        second, _ = event(2, "run.started", {"run_id": "run_1"}, previous)
        second["payload"] = {"run_id": "tampered"}

        replay = replay_events(run_id="run_1", events=[first, second])

        self.assertFalse(replay["hash_chain_valid"])
        self.assertEqual(replay["errors"][0]["kind"], "invalid_hash_chain")


if __name__ == "__main__":
    unittest.main()
