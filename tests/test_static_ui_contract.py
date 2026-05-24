import unittest
from importlib.resources import files


class StaticUiContractTests(unittest.TestCase):
    def test_run_panel_renders_raw_run_acceptance_json(self) -> None:
        html = files("cloud_agent_poc").joinpath("static/index.html").read_text()

        self.assertIn('id="run-plan"', html)
        self.assertIn('id="run-criteria-json"', html)
        self.assertIn("run_acceptance_criteria", html)
        self.assertIn("JSON.stringify(runCriteria, null, 2)", html)

    def test_run_panel_does_not_render_task_progress_badges(self) -> None:
        html = files("cloud_agent_poc").joinpath("static/index.html").read_text()

        self.assertNotIn("runTaskStatuses", html)
        self.assertNotIn("runPlanStatus", html)
        self.assertNotIn("run-plan-list", html)

    def test_run_submit_includes_idempotency_key(self) -> None:
        html = files("cloud_agent_poc").joinpath("static/index.html").read_text()

        self.assertIn("idempotencyKey", html)
        self.assertIn("idempotency_key", html)
        self.assertIn("crypto.randomUUID", html)
        self.assertIn("run.lease.expired", html)
        self.assertIn("run.resume.context_loaded", html)
        self.assertIn("run.resume.exhausted", html)


if __name__ == "__main__":
    unittest.main()
