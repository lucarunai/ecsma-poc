from __future__ import annotations

import hashlib
import json
from typing import Any

SESSION_EVENT_SCHEMA = "session_event.v1"
RUN_ACCEPTANCE_CRITERIA_SCHEMA = "run_acceptance_criteria.v1"
TASK_HANDOFF_SCHEMA = "task_handoff.v1"
TOOL_EXECUTION_ENVELOPE_SCHEMA = "tool_execution_envelope.v1"
RECOVERY_CONTEXT_SCHEMA = "recovery_context.v1"

REPLAYABLE_EVENT_REQUIRED_FIELDS = {
    "user.prompt.accepted": {"run_id", "prompt"},
    "run.started": {"run_id"},
    "run.resumed": {"run_id"},
    "run.completed": {"run_id"},
    "run.blocked": {"run_id"},
    "run.failed": {"run_id"},
    "run.resume.requested": {"run_id", "reason"},
    "run.resume.exhausted": {"run_id"},
    "tasks.created": {"run_id", "tasks"},
    "task.started": {"task_id"},
    "task.completed": {"task_id"},
    "task.blocked": {"task_id"},
    "task.failed": {"task_id"},
    "tool.call.requested": {"task_id", "tool_call_id", "tool_name", "input"},
    "tool.call.failed": {"task_id", "tool_call_id", "tool_name"},
    "tool.call.orphaned": {"task_id", "tool_call_id", "tool_name"},
    "tool.execution": {"task_id", "execution"},
    "approval.requested": {"approval_id", "task_id", "tool_call_id", "tool_name"},
    "approval.approved": {"approval_id", "tool_call_id", "tool_name"},
    "approval.denied": {"approval_id", "tool_call_id", "tool_name"},
    "approval.expired": {"approval_id", "tool_call_id", "tool_name"},
}


class ContractValidationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_version_for_event(event_type: str) -> str:
    return f"{event_type}.v1"


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    required_fields = REPLAYABLE_EVENT_REQUIRED_FIELDS.get(event_type, set())
    missing = sorted(field for field in required_fields if field not in payload)
    if missing:
        raise ContractValidationError(
            f"{event_type} payload is missing required fields: {', '.join(missing)}."
        )


def validate_task_handoff(payload: dict[str, Any]) -> None:
    _require_schema(payload, TASK_HANDOFF_SCHEMA)
    _require_fields(payload, {"run_id", "from_task", "planned_task_results"})


def validate_recovery_context(payload: dict[str, Any]) -> None:
    _require_schema(payload, RECOVERY_CONTEXT_SCHEMA)
    _require_fields(payload, {"current_task", "prior_attempts", "resume_guidance"})


def validate_tool_execution_envelope(payload: dict[str, Any]) -> None:
    schema_version = payload.get("schema_version", TOOL_EXECUTION_ENVELOPE_SCHEMA)
    if schema_version != TOOL_EXECUTION_ENVELOPE_SCHEMA:
        raise ContractValidationError(
            f"Expected {TOOL_EXECUTION_ENVELOPE_SCHEMA}, got {schema_version}."
        )
    _require_fields(
        payload,
        {"execution_id", "run_id", "tool_call_id", "tool_name", "execution_status"},
    )


def event_payload_hash(payload: dict[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def event_hash(
    *,
    previous_event_hash: str | None,
    event_type: str,
    schema_version: str,
    seq: int,
    payload_hash: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "previous_event_hash": previous_event_hash,
                "event_type": event_type,
                "schema_version": schema_version,
                "seq": seq,
                "payload_hash": payload_hash,
            }
        )
    )


def _require_schema(payload: dict[str, Any], expected_schema: str) -> None:
    actual_schema = payload.get("schema_version")
    if actual_schema != expected_schema:
        raise ContractValidationError(
            f"Expected schema_version {expected_schema}, got {actual_schema}."
        )


def _require_fields(payload: dict[str, Any], fields: set[str]) -> None:
    missing = sorted(field for field in fields if field not in payload)
    if missing:
        raise ContractValidationError(
            f"Payload is missing required fields: {', '.join(missing)}."
        )
