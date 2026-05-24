import unittest
from importlib.resources import files
from pathlib import Path


class SessionSchemaContractTests(unittest.TestCase):
    def test_schema_declares_idempotency_and_retention_fields(self) -> None:
        schema = files("cloud_agent_poc").joinpath("schema.sql").read_text()

        self.assertIn("expires_at TIMESTAMPTZ", schema)
        self.assertIn("archived_at TIMESTAMPTZ", schema)
        self.assertIn("idempotency_key TEXT", schema)
        self.assertIn("retention_until TIMESTAMPTZ", schema)
        self.assertIn("archive_reason TEXT", schema)
        self.assertIn("claimed_by TEXT", schema)
        self.assertIn("claim_expires_at TIMESTAMPTZ", schema)
        self.assertIn("last_heartbeat_at TIMESTAMPTZ", schema)
        self.assertIn("attempt_count INTEGER", schema)
        self.assertIn("failure_kind TEXT", schema)
        self.assertIn("heartbeat_expires_at TIMESTAMPTZ", schema)
        self.assertIn("runs_session_id_idempotency_key_idx", schema)
        self.assertIn("runs_claim_expires_at_idx", schema)
        self.assertIn("task_attempts_heartbeat_expires_at_idx", schema)

    def test_task_attempt_heartbeat_and_failure_kind_are_durable(self) -> None:
        store_source = Path("src/cloud_agent_poc/session_store.py").read_text()

        self.assertIn("UPDATE task_attempts", store_source)
        self.assertIn("heartbeat_expires_at = NOW()", store_source)
        self.assertIn("failure_kind = %s", store_source)
        self.assertIn("FAILURE_KIND_BRAIN_CRASH", store_source)
        self.assertIn("status = 'orphaned'", store_source)
        self.assertIn("fail_runs_over_attempt_limit", store_source)
        self.assertIn("attempt_count >= %s", store_source)

    def test_retention_uses_archive_not_hard_delete(self) -> None:
        store_source = Path("src/cloud_agent_poc/session_store.py").read_text()
        app_source = Path("src/cloud_agent_poc/session_app.py").read_text()
        archive_method = store_source[
            store_source.index("async def archive_expired_state"):
        ]

        self.assertIn("UPDATE runs", archive_method)
        self.assertIn("UPDATE sessions", archive_method)
        self.assertIn("archived_at = NOW()", archive_method)
        self.assertNotIn("DELETE FROM runs", archive_method)
        self.assertNotIn("DELETE FROM sessions", archive_method)
        self.assertIn('/internal/retention/archive', app_source)
        self.assertNotIn('/internal/retention/cleanup', app_source)


if __name__ == "__main__":
    unittest.main()
