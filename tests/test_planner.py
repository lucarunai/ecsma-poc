import unittest
from types import SimpleNamespace

from cloud_agent_poc.brain.planner import AgentTaskPlanner
from cloud_agent_poc.domain import PlannedTask


def _planned_task(title: str, description: str) -> PlannedTask:
    return PlannedTask(
        title=title,
        description=description,
        acceptance_criteria=[description],
    )


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

    def test_filters_unrequested_test_and_publish_tasks(self) -> None:
        tasks = AgentTaskPlanner._filter_unrequested_optional_tasks(
            [
                _planned_task(
                    "Clone repository and create demo-1 branch",
                    "Clone main and create demo-1.",
                ),
                _planned_task(
                    "Implement hello world Python application",
                    "Add the hello world application.",
                ),
                _planned_task(
                    "Add unittest for hello world application",
                    "Add tests for the hello world behavior.",
                ),
                _planned_task(
                    "Commit and push demo-1 branch",
                    "Commit local changes and push the branch.",
                ),
                _planned_task(
                    "Open pull request from demo-1 to main",
                    "Create a pull request.",
                ),
            ],
            (
                "Based on https://github.com/lucarunai/demo main branch, "
                "create new branch demo-1, build a simple hello world Python "
                "application."
            ),
        )

        self.assertEqual(
            [task.title for task in tasks],
            [
                "Clone repository and create demo-1 branch",
                "Implement hello world Python application",
            ],
        )

    def test_branch_named_test_is_not_a_testing_request(self) -> None:
        self.assertFalse(
            AgentTaskPlanner._prompt_requests_testing(
                "Based on repo branch test, build a simple hello world app."
            )
        )

    def test_clone_branch_test_task_is_not_filtered_as_testing(self) -> None:
        tasks = AgentTaskPlanner._filter_unrequested_optional_tasks(
            [
                _planned_task(
                    "Clone branch test and create demo-test",
                    "Clone https://github.com/lucarunai/demo branch test, then create new branch demo-test.",
                )
            ],
            "clone https://github.com/lucarunai/demo branch test, then create new branch demo-test",
        )

        self.assertEqual(
            [task.title for task in tasks],
            ["Clone branch test and create demo-test"],
        )

    def test_explicit_test_and_pr_requests_are_kept(self) -> None:
        tasks = AgentTaskPlanner._filter_unrequested_optional_tasks(
            [
                _planned_task("Implement app", "Add the app."),
                _planned_task("Add tests", "Add unit tests."),
                _planned_task("Open pull request", "Open a PR to main."),
            ],
            "Build the app, add tests, and open a pull request to main.",
        )

        self.assertEqual(
            [task.title for task in tasks],
            ["Implement app", "Add tests", "Open pull request"],
        )

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

    def test_result_error_uses_http_status_before_success_subtype(self) -> None:
        error = AgentTaskPlanner._result_error_text(
            SimpleNamespace(
                errors=[],
                result=None,
                api_error_status=529,
                subtype="success",
            )
        )

        self.assertEqual(error, "Planner agent error: API request failed with HTTP 529.")


if __name__ == "__main__":
    unittest.main()
