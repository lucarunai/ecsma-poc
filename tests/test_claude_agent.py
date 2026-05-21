import unittest

from cloud_agent_poc.brain.claude_agent import ClaudeCodingAgent


class ClaudeCodingAgentTests(unittest.TestCase):
    def test_parses_blocked_task_result(self) -> None:
        result = ClaudeCodingAgent.parse_task_result(
            {
                "status": "blocked",
                "summary": "Repository checkout requires access.",
            },
            claude_session_id="session-123",
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.summary, "Repository checkout requires access.")
        self.assertEqual(result.claude_session_id, "session-123")

    def test_rejects_unknown_task_status(self) -> None:
        with self.assertRaises(ValueError):
            ClaudeCodingAgent.parse_task_result(
                {
                    "status": "skipped",
                    "summary": "Not part of the contract.",
                }
            )


if __name__ == "__main__":
    unittest.main()
