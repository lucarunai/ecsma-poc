import unittest
from datetime import datetime, timezone

from cloud_agent_poc.ops import (
    OPS_ALERTS_SCHEMA,
    OPS_METRIC_SNAPSHOT_SCHEMA,
    OPS_RUN_SUMMARY_SCHEMA,
    SUPPORT_BUNDLE_SCHEMA,
    build_ops_alerts,
    build_ops_metric_snapshot,
    build_ops_run_summary,
    build_support_bundle,
)


class OpsSummaryTests(unittest.TestCase):
    def test_builds_run_summary_from_operational_records(self) -> None:
        started = datetime(2026, 5, 24, 1, 0, tzinfo=timezone.utc)
        ended = datetime(2026, 5, 24, 1, 0, 2, tzinfo=timezone.utc)
        summary = build_ops_run_summary(
            run={
                "id": "run_1",
                "session_id": "sess_1",
                "user_id": "Luca",
                "prompt": "clone repo",
                "status": "failed",
                "started_at": started,
                "ended_at": ended,
                "created_at": started,
                "attempt_count": 2,
            },
            tasks=[
                {"id": "task_1", "status": "completed"},
                {"id": "task_2", "status": "failed"},
            ],
            task_attempts=[
                {
                    "id": "attempt_1",
                    "task_id": "task_2",
                    "status": "failed",
                    "failure_kind": "model_error",
                }
            ],
            tool_calls=[
                {
                    "id": "toolcall_1",
                    "task_id": "task_2",
                    "status": "failed",
                    "failure_kind": "sandbox_runtime_error",
                }
            ],
            tool_executions=[
                {
                    "execution_id": "sbxexec_1",
                    "task_id": "task_2",
                    "tool_call_id": "toolcall_1",
                    "execution_status": "failed",
                    "failure_kind": "sandbox_runtime_error",
                    "envelope": {
                        "failure_message": "pod failed",
                        "runtime": {
                            "runtime_profile": "kubernetes_container",
                            "pod_phase": "Failed",
                            "duration_ms": 1200,
                            "workspace": {"bytes": 42},
                            "output": {"stdout_truncated": True},
                        },
                    },
                }
            ],
            approvals=[{"id": "approval_1", "status": "approved"}],
            handoffs=[{"summary": "task 1 complete"}],
            events=[
                {"event_type": "run.resume.requested"},
                {"event_type": "run.lease.expired"},
            ],
            replay_report={
                "replay_valid": True,
                "hash_chain_valid": True,
                "consistent": True,
                "errors": [],
                "differences": [],
            },
        )

        self.assertEqual(summary["schema_version"], OPS_RUN_SUMMARY_SCHEMA)
        self.assertEqual(summary["duration_ms"], 2000)
        self.assertEqual(summary["task_counts"]["completed"], 1)
        self.assertEqual(summary["tool_execution_counts"]["failed"], 1)
        self.assertEqual(summary["failure_summary"]["failure_kind"], "sandbox_runtime_error")
        self.assertEqual(summary["failure_summary"]["failed_component"], "sandbox")
        self.assertEqual(summary["recovery_summary"]["lease_expired_count"], 1)
        self.assertEqual(summary["sandbox_summary"]["output_truncated_count"], 1)
        self.assertTrue(summary["replay_summary"]["consistent"])

    def test_support_bundle_redacts_customer_visible_sensitive_content(self) -> None:
        bundle = build_support_bundle(
            run={
                "id": "run_1",
                "session_id": "sess_1",
                "user_id": "Luca",
                "prompt": "use token=secret-value to clone",
                "status": "completed",
                "started_at": None,
                "ended_at": None,
                "created_at": None,
                "attempt_count": 1,
            },
            tasks=[],
            task_attempts=[],
            tool_calls=[
                {
                    "id": "toolcall_1",
                    "status": "succeeded",
                    "input": {"content": "ghp_abcdefghijklmnopqrstuvwxyz"},
                }
            ],
            tool_executions=[],
            approvals=[],
            handoffs=[],
            events=[
                {
                    "id": 1,
                    "event_type": "user.prompt.accepted",
                    "payload": {"prompt": "password=hunter2"},
                }
            ],
            replay_report=None,
            visibility="customer",
        )

        self.assertEqual(bundle["schema_version"], SUPPORT_BUNDLE_SCHEMA)
        self.assertEqual(bundle["visibility"], "customer")
        rendered = str(bundle)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("hunter2", rendered)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", rendered)
        self.assertIn("sha256", rendered)

    def test_builds_metric_snapshot_and_alerts(self) -> None:
        started = datetime(2026, 5, 24, 1, 0, tzinfo=timezone.utc)
        ended = datetime(2026, 5, 24, 1, 0, 3, tzinfo=timezone.utc)
        snapshot = build_ops_metric_snapshot(
            user_id="Luca",
            window_hours=24,
            runs=[
                {
                    "id": "run_1",
                    "session_id": "sess_1",
                    "prompt": "run tests",
                    "status": "failed",
                    "started_at": started,
                    "ended_at": ended,
                    "created_at": started,
                    "attempt_count": 1,
                }
            ],
            tasks=[{"id": "task_1", "status": "failed"}],
            task_attempts=[
                {"id": "attempt_1", "status": "failed", "failure_kind": "brain_crash"}
            ],
            tool_calls=[
                {
                    "id": "toolcall_1",
                    "tool_name": "run_python_unittest",
                    "status": "failed",
                    "failure_kind": "sandbox_runtime_error",
                }
            ],
            tool_executions=[
                {
                    "execution_id": "sbxexec_1",
                    "tool_name": "run_python_unittest",
                    "execution_status": "failed",
                    "failure_kind": "sandbox_runtime_error",
                    "envelope": {
                        "runtime": {
                            "runtime_profile": "kubernetes_container",
                            "pod_phase": "Failed",
                            "duration_ms": 1500,
                            "workspace": {"bytes": 12},
                            "output": {"stdout_truncated": True},
                        }
                    },
                }
            ],
            approvals=[{"id": "approval_1", "status": "pending"}],
            events=[{"event_type": "run.lease.expired"}],
            replay_reports=[
                {
                    "consistent": False,
                    "hash_chain_valid": True,
                    "replay_valid": True,
                }
            ],
        )
        alerts = build_ops_alerts(metric_snapshot=snapshot)

        self.assertEqual(snapshot["schema_version"], OPS_METRIC_SNAPSHOT_SCHEMA)
        self.assertEqual(snapshot["run_counts"]["failure_rate"], 1.0)
        self.assertEqual(snapshot["tool_counts"]["failures_by_tool"]["run_python_unittest"], 1)
        self.assertEqual(snapshot["replay_summary"]["drift_count"], 1)
        self.assertEqual(alerts["schema_version"], OPS_ALERTS_SCHEMA)
        self.assertEqual(alerts["status"], "attention_required")
        self.assertGreaterEqual(alerts["alert_count"], 4)
        self.assertIn("replay_drift", {alert["type"] for alert in alerts["alerts"]})


if __name__ == "__main__":
    unittest.main()
