import unittest

from cloud_agent_poc.session_contracts import (
    ContractValidationError,
    TASK_HANDOFF_SCHEMA,
    TOOL_EXECUTION_ENVELOPE_SCHEMA,
    event_hash,
    event_payload_hash,
    validate_task_handoff,
    validate_tool_execution_envelope,
)


class SessionContractsTests(unittest.TestCase):
    def test_event_hash_is_stable_for_key_order(self) -> None:
        payload_a = {"b": 2, "a": 1}
        payload_b = {"a": 1, "b": 2}

        self.assertEqual(event_payload_hash(payload_a), event_payload_hash(payload_b))

    def test_event_hash_depends_on_previous_hash(self) -> None:
        payload_hash = event_payload_hash({"run_id": "run_1"})

        first_hash = event_hash(
            previous_event_hash=None,
            event_type="run.started",
            schema_version="run.started.v1",
            seq=1,
            payload_hash=payload_hash,
        )
        second_hash = event_hash(
            previous_event_hash="previous",
            event_type="run.started",
            schema_version="run.started.v1",
            seq=1,
            payload_hash=payload_hash,
        )

        self.assertNotEqual(first_hash, second_hash)

    def test_validates_task_handoff_contract(self) -> None:
        validate_task_handoff(
            {
                "schema_version": TASK_HANDOFF_SCHEMA,
                "run_id": "run_1",
                "from_task": {"id": "task_1"},
                "planned_task_results": [],
            }
        )

    def test_rejects_invalid_tool_execution_contract(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_tool_execution_envelope(
                {
                    "schema_version": TOOL_EXECUTION_ENVELOPE_SCHEMA,
                    "execution_id": "exec_1",
                }
            )


if __name__ == "__main__":
    unittest.main()
