from datetime import datetime, timezone
import unittest

from cloud_agent_poc.session_policy import (
    InvalidRunStatusTransition,
    run_retention_deadline,
    session_expiration_deadline,
    validate_run_status_transition,
)


class RunStatusPolicyTests(unittest.TestCase):
    def test_allows_expected_run_status_transitions(self) -> None:
        for current_status, next_status in (
            ("queued", "running"),
            ("running", "completed"),
            ("running", "blocked"),
            ("running", "failed"),
            ("running", "resume_queued"),
            ("blocked", "resume_queued"),
            ("failed", "resume_queued"),
            ("resume_queued", "running"),
            ("resume_queued", "failed"),
            ("running", "running"),
            ("completed", "completed"),
        ):
            with self.subTest(current_status=current_status, next_status=next_status):
                validate_run_status_transition(current_status, next_status)

    def test_rejects_completed_run_reactivation(self) -> None:
        for next_status in ("running", "resume_queued", "failed", "blocked"):
            with self.subTest(next_status=next_status):
                with self.assertRaisesRegex(
                    InvalidRunStatusTransition,
                    f"completed -> {next_status}",
                ):
                    validate_run_status_transition("completed", next_status)

    def test_rejects_unknown_run_statuses(self) -> None:
        with self.assertRaisesRegex(InvalidRunStatusTransition, "Unknown current"):
            validate_run_status_transition("paused", "running")
        with self.assertRaisesRegex(InvalidRunStatusTransition, "Unknown target"):
            validate_run_status_transition("running", "paused")


class RetentionPolicyTests(unittest.TestCase):
    def test_session_and_run_deadlines_default_to_seven_days(self) -> None:
        now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(session_expiration_deadline(now).day, 30)
        self.assertEqual(run_retention_deadline(now).day, 30)


if __name__ == "__main__":
    unittest.main()
