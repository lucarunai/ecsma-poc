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

    def test_run_panel_renders_replay_report(self) -> None:
        html = files("cloud_agent_poc").joinpath("static/index.html").read_text()

        self.assertIn('id="replay-status"', html)
        self.assertIn('id="replay-json"', html)
        self.assertIn('id="replay-refresh"', html)
        self.assertIn('/api/runs/${currentRunId}/replay', html)
        self.assertIn("Replay consistent", html)

    def test_ui_renders_human_approval_controls(self) -> None:
        html = files("cloud_agent_poc").joinpath("static/index.html").read_text()

        self.assertIn('id="approval-panel"', html)
        self.assertIn('id="approval-list"', html)
        self.assertIn("approval.requested", html)
        self.assertIn("approval.approved", html)
        self.assertIn("approval.denied", html)
        self.assertIn('/api/approvals/${approvalId}/decision', html)
        self.assertIn("Approve", html)
        self.assertIn("Deny", html)

    def test_ui_exposes_user_switcher_and_sends_user_scope(self) -> None:
        html = files("cloud_agent_poc").joinpath("static/index.html").read_text()

        self.assertIn('id="user-id"', html)
        self.assertIn('id="switch-user"', html)
        self.assertIn('"X-User-Id"', html)
        self.assertIn("currentUserId()", html)
        self.assertIn("encodeURIComponent(currentUserId())", html)
        self.assertIn("resetSessionState", html)


if __name__ == "__main__":
    unittest.main()
