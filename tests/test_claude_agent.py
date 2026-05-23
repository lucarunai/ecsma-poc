import unittest
from types import SimpleNamespace

from cloud_agent_poc.brain.claude_agent import ClaudeCodingAgent
from cloud_agent_poc.domain import TaskRecord


class ClaudeCodingAgentTests(unittest.TestCase):
    def test_parses_blocked_task_result(self) -> None:
        result = ClaudeCodingAgent.parse_task_result(
            {
                "status": "blocked",
                "summary": "Repository checkout requires access.",
                "criteria_results": [
                    {
                        "criterion": "Repository is checked out.",
                        "status": "blocked",
                        "evidence": "checkout_git_branch returned branch not found.",
                    }
                ],
                "verification": {
                    "commands_run": [
                        {
                            "tool": "checkout_git_branch",
                            "result": "failed",
                            "summary": "Branch was not found.",
                        }
                    ],
                    "notes": "No file changes were made.",
                },
            },
            claude_session_id="session-123",
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.summary, "Repository checkout requires access.")
        self.assertEqual(result.claude_session_id, "session-123")
        self.assertEqual(result.criteria_results[0]["status"], "blocked")
        self.assertEqual(
            result.verification["commands_run"][0]["tool"],
            "checkout_git_branch",
        )

    def test_rejects_unknown_task_status(self) -> None:
        with self.assertRaises(ValueError):
            ClaudeCodingAgent.parse_task_result(
                {
                    "status": "skipped",
                    "summary": "Not part of the contract.",
                }
            )

    def test_prompt_passes_handoff_json_into_fresh_query(self) -> None:
        prompt = ClaudeCodingAgent._implementation_prompt(
            "Edit the checkout.",
            TaskRecord(
                id="task_2",
                run_id="run",
                seq=2,
                kind="model_task",
                title="Edit repo",
                description="Add hello.py.",
                acceptance_criteria=["hello.py exists."],
                status="pending",
            ),
            [
                {
                    "from_task": {"id": "task_1", "seq": 1, "title": "Clone repo"},
                    "status": "completed",
                    "summary": "Repository cloned.",
                }
            ],
            [
                {
                    "task_id": "task_1",
                    "task_seq": 1,
                    "title": "Clone repo",
                    "acceptance_criteria": ["Repository cloned."],
                },
                {
                    "task_id": "task_2",
                    "task_seq": 2,
                    "title": "Edit repo",
                    "acceptance_criteria": ["hello.py exists."],
                },
            ],
        )

        self.assertIn("Durable handoff context", prompt)
        self.assertIn("Run acceptance criteria (global constitution)", prompt)
        self.assertIn('"acceptance_criteria": [', prompt)
        self.assertIn('"previous_task_detail"', prompt)
        self.assertIn('"summary": "Repository cloned."', prompt)
        self.assertIn("fresh model session", prompt)

    def test_completed_task_requires_all_criteria_passed(self) -> None:
        with self.assertRaisesRegex(ValueError, "before all acceptance criteria passed"):
            ClaudeCodingAgent.parse_task_result(
                {
                    "status": "completed",
                    "summary": "Done.",
                    "criteria_results": [
                        {
                            "criterion": "hello.py exists.",
                            "status": "not_verified",
                            "evidence": "No file inspection was run.",
                        }
                    ],
                    "verification": {"notes": "No verification was run."},
                },
                task=TaskRecord(
                    id="task_1",
                    run_id="run",
                    seq=1,
                    kind="model_task",
                    title="Create app",
                    description="Add hello.py.",
                    acceptance_criteria=["hello.py exists."],
                    status="pending",
                ),
            )

    def test_criteria_results_must_match_task_criteria_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "must match acceptance criteria order"):
            ClaudeCodingAgent.parse_task_result(
                {
                    "status": "blocked",
                    "summary": "Wrong criterion text.",
                    "criteria_results": [
                        {
                            "criterion": "A paraphrased criterion.",
                            "status": "blocked",
                            "evidence": "The criterion text was paraphrased.",
                        }
                    ],
                    "verification": {"notes": "No verification was run."},
                },
                task=TaskRecord(
                    id="task_1",
                    run_id="run",
                    seq=1,
                    kind="model_task",
                    title="Create app",
                    description="Add hello.py.",
                    acceptance_criteria=["hello.py exists."],
                    status="pending",
                ),
            )

    def test_result_error_prefers_assistant_api_error_text(self) -> None:
        error = ClaudeCodingAgent._result_error_text(
            SimpleNamespace(
                errors=[],
                result=None,
                api_error_status=500,
                subtype="success",
            ),
            "API Error: 500 Internal server error.",
        )

        self.assertEqual(error, "Task agent error: API Error: 500 Internal server error.")


if __name__ == "__main__":
    unittest.main()
