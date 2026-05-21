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


if __name__ == "__main__":
    unittest.main()
