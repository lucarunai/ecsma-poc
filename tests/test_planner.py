import unittest

from cloud_agent_poc.brain.planner import AgentTaskPlanner


class AgentTaskPlannerTests(unittest.TestCase):
    def test_parses_model_generated_tasks(self) -> None:
        tasks = AgentTaskPlanner.parse_plan(
            {
                "tasks": [
                    {
                        "title": "Add greeting implementation",
                        "description": "Create the Python hello-world entry point.",
                        "acceptance_criteria": ["Greeting returns Hello World."],
                    },
                    {
                        "title": "Cover greeting behavior",
                        "description": "Add unittest coverage for the greeting.",
                        "acceptance_criteria": ["Unit tests pass."],
                    },
                ]
            }
        )
        self.assertEqual(
            [task.title for task in tasks],
            ["Add greeting implementation", "Cover greeting behavior"],
        )
        self.assertEqual(tasks[0].acceptance_criteria, ["Greeting returns Hello World."])

    def test_drops_report_only_task(self) -> None:
        tasks = AgentTaskPlanner.parse_plan(
            {
                "tasks": [
                    {
                        "title": "Checkout branch test",
                        "description": "Switch to the requested existing branch.",
                        "acceptance_criteria": ["Branch test is checked out."],
                    },
                    {
                        "title": "Report outcome to user",
                        "description": "Summarize the checkout result.",
                        "acceptance_criteria": ["User sees the result."],
                    },
                ]
            }
        )

        self.assertEqual([task.title for task in tasks], ["Checkout branch test"])

    def test_empty_result_is_not_parsed_as_json(self) -> None:
        self.assertIsNone(AgentTaskPlanner._parse_json_candidate(""))

    def test_parses_fenced_json_candidate(self) -> None:
        parsed = AgentTaskPlanner._parse_json_candidate(
            '```json\n{"tasks": []}\n```'
        )

        self.assertEqual(parsed, {"tasks": []})

    def test_api_error_result_is_reported_directly(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "workspace API usage limits"):
            AgentTaskPlanner._parse_json_candidate(
                "API Error: 400 You have reached your specified workspace API usage limits."
            )


if __name__ == "__main__":
    unittest.main()
