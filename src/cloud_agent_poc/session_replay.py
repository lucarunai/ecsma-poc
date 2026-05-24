from __future__ import annotations

from typing import Any

from .session_contracts import (
    event_hash,
    event_payload_hash,
    validate_event_payload,
)
from .session_policy import RUN_STATUS_TRANSITIONS

TASK_STATUS_TRANSITIONS = {
    "pending": {"running"},
    "running": {"completed", "blocked", "failed", "resume_queued"},
    "blocked": {"resume_queued"},
    "failed": {"resume_queued"},
    "resume_queued": {"running", "failed"},
    "completed": set(),
}

TOOL_CALL_STATUS_TRANSITIONS = {
    "requested": {"running", "awaiting_approval", "succeeded", "failed", "orphaned"},
    "awaiting_approval": {"approved", "failed", "orphaned"},
    "approved": {"running", "succeeded", "failed", "orphaned"},
    "running": {"succeeded", "failed", "orphaned"},
    "succeeded": set(),
    "failed": set(),
    "orphaned": {"requested"},
}


def replay_and_compare(
    *,
    run_id: str,
    events: list[dict[str, Any]],
    materialized: dict[str, Any],
) -> dict[str, Any]:
    replay = replay_events(run_id=run_id, events=events)
    differences = compare_replayed_state(replay["state"], materialized)
    return {
        "schema_version": "replay_report.v1",
        "run_id": run_id,
        "replay_valid": not replay["errors"],
        "hash_chain_valid": replay["hash_chain_valid"],
        "consistent": not replay["errors"] and not differences,
        "errors": replay["errors"],
        "differences": differences,
        "replayed_state": replay["state"],
        "materialized_state": materialized,
    }


def replay_events(*, run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run": {"id": run_id, "status": None},
        "tasks": {},
        "tool_calls": {},
    }
    errors: list[dict[str, Any]] = []
    hash_chain_valid = True
    previous_hash: str | None = None
    ordered_events = sorted(
        events,
        key=lambda event: (
            event.get("seq") if event.get("seq") is not None else 10**12,
            event.get("id", 0),
        ),
    )

    for event in ordered_events:
        event_type = str(event.get("event_type"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        try:
            validate_event_payload(event_type, payload)
        except ValueError as exc:
            errors.append(_error("invalid_contract", event, str(exc)))
            continue
        event_hash_value = event.get("event_hash")
        if event_hash_value:
            expected_payload_hash = event_payload_hash(payload)
            expected_event_hash = event_hash(
                previous_event_hash=previous_hash,
                event_type=event_type,
                schema_version=str(event.get("schema_version")),
                seq=int(event.get("seq") or 0),
                payload_hash=expected_payload_hash,
            )
            if (
                event.get("payload_hash") != expected_payload_hash
                or event.get("previous_event_hash") != previous_hash
                or event_hash_value != expected_event_hash
            ):
                hash_chain_valid = False
                errors.append(
                    _error("invalid_hash_chain", event, "Event hash chain is broken.")
                )
            previous_hash = str(event_hash_value)

        if event_type == "user.prompt.accepted":
            _transition_run(state, errors, event, "queued")
        elif event_type in {"run.started", "run.resumed", "run.claimed"}:
            _transition_run(state, errors, event, "running")
        elif event_type == "run.resume.requested":
            _transition_run(state, errors, event, "resume_queued")
        elif event_type == "run.resume.exhausted":
            _transition_run(state, errors, event, "failed")
        elif event_type == "run.completed":
            _transition_run(state, errors, event, "completed")
        elif event_type == "run.blocked":
            _transition_run(state, errors, event, "blocked")
        elif event_type == "run.failed":
            _transition_run(state, errors, event, "failed")
        elif event_type == "tasks.created":
            for task in payload.get("tasks", []):
                if not isinstance(task, dict) or "id" not in task:
                    errors.append(
                        _error("invalid_contract", event, "tasks.created has invalid task.")
                    )
                    continue
                state["tasks"][task["id"]] = {
                    "id": task["id"],
                    "seq": task.get("seq"),
                    "title": task.get("title"),
                    "status": "pending",
                }
        elif event_type.startswith("task."):
            task_id = payload.get("task_id")
            if task_id:
                target = event_type.replace("task.", "")
                if target == "started":
                    target = "running"
                if target in {"running", "completed", "blocked", "failed"}:
                    _transition_task(state, errors, event, str(task_id), target)
        elif event_type == "tool.call.requested":
            tool_call_id = str(payload["tool_call_id"])
            state["tool_calls"][tool_call_id] = {
                "id": tool_call_id,
                "task_id": payload.get("task_id"),
                "tool_name": payload.get("tool_name"),
                "status": "requested",
            }
        elif event_type == "tool.call.failed":
            _transition_tool_call(
                state,
                errors,
                event,
                str(payload["tool_call_id"]),
                "failed",
            )
        elif event_type == "tool.call.orphaned":
            _transition_tool_call(
                state,
                errors,
                event,
                str(payload["tool_call_id"]),
                "orphaned",
            )
        elif event_type == "approval.requested":
            _transition_tool_call(
                state,
                errors,
                event,
                str(payload["tool_call_id"]),
                "awaiting_approval",
            )
        elif event_type == "approval.approved":
            _transition_tool_call(
                state,
                errors,
                event,
                str(payload["tool_call_id"]),
                "approved",
            )
        elif event_type in {"approval.denied", "approval.expired"}:
            _transition_tool_call(
                state,
                errors,
                event,
                str(payload["tool_call_id"]),
                "failed",
            )
        elif event_type == "tool.execution":
            execution = payload.get("execution") or {}
            tool_call_id = str(execution.get("tool_call_id"))
            target = (
                "succeeded"
                if execution.get("execution_status") == "succeeded"
                and not payload.get("failure_kind")
                else "failed"
            )
            _transition_tool_call(state, errors, event, tool_call_id, target)

    _check_invariants(state, errors)
    return {
        "state": state,
        "errors": errors,
        "hash_chain_valid": hash_chain_valid,
    }


def compare_replayed_state(
    replayed_state: dict[str, Any],
    materialized: dict[str, Any],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    run = materialized.get("run") or {}
    _compare_status(
        differences,
        kind="run",
        item_id=str(run.get("id") or replayed_state["run"].get("id")),
        replay_status=replayed_state["run"].get("status"),
        table_status=run.get("status"),
    )
    materialized_tasks = {task["id"]: task for task in materialized.get("tasks", [])}
    for task_id, replayed_task in replayed_state["tasks"].items():
        table_task = materialized_tasks.get(task_id)
        _compare_status(
            differences,
            kind="task",
            item_id=task_id,
            replay_status=replayed_task.get("status"),
            table_status=table_task.get("status") if table_task else None,
        )
    materialized_tools = {
        tool_call["id"]: tool_call for tool_call in materialized.get("tool_calls", [])
    }
    for tool_call_id, replayed_tool in replayed_state["tool_calls"].items():
        table_tool = materialized_tools.get(tool_call_id)
        _compare_status(
            differences,
            kind="tool_call",
            item_id=tool_call_id,
            replay_status=replayed_tool.get("status"),
            table_status=table_tool.get("status") if table_tool else None,
        )
    return differences


def _transition_run(
    state: dict[str, Any],
    errors: list[dict[str, Any]],
    event: dict[str, Any],
    target_status: str,
) -> None:
    current = state["run"].get("status")
    if current is None:
        state["run"]["status"] = target_status
        return
    if current == target_status:
        return
    if target_status not in RUN_STATUS_TRANSITIONS.get(str(current), set()):
        errors.append(
            _error(
                "invalid_transition",
                event,
                f"Invalid run transition: {current} -> {target_status}.",
            )
        )
        return
    state["run"]["status"] = target_status


def _transition_task(
    state: dict[str, Any],
    errors: list[dict[str, Any]],
    event: dict[str, Any],
    task_id: str,
    target_status: str,
) -> None:
    task = state["tasks"].setdefault(task_id, {"id": task_id, "status": "pending"})
    current = task.get("status")
    if current == target_status:
        return
    if target_status not in TASK_STATUS_TRANSITIONS.get(str(current), set()):
        errors.append(
            _error(
                "invalid_transition",
                event,
                f"Invalid task transition for {task_id}: {current} -> {target_status}.",
            )
        )
        return
    task["status"] = target_status


def _transition_tool_call(
    state: dict[str, Any],
    errors: list[dict[str, Any]],
    event: dict[str, Any],
    tool_call_id: str,
    target_status: str,
) -> None:
    if not tool_call_id or tool_call_id == "None":
        errors.append(_error("invalid_contract", event, "Missing tool_call_id."))
        return
    tool_call = state["tool_calls"].setdefault(
        tool_call_id,
        {"id": tool_call_id, "status": "requested"},
    )
    current = tool_call.get("status")
    if current == target_status:
        return
    if target_status not in TOOL_CALL_STATUS_TRANSITIONS.get(str(current), set()):
        errors.append(
            _error(
                "invalid_transition",
                event,
                (
                    f"Invalid tool call transition for {tool_call_id}: "
                    f"{current} -> {target_status}."
                ),
            )
        )
        return
    tool_call["status"] = target_status


def _check_invariants(
    state: dict[str, Any],
    errors: list[dict[str, Any]],
) -> None:
    if state["run"].get("status") == "completed":
        incomplete_tasks = [
            task_id
            for task_id, task in state["tasks"].items()
            if task.get("status") != "completed"
        ]
        if incomplete_tasks:
            errors.append(
                {
                    "kind": "invariant_violation",
                    "message": "Completed run has incomplete tasks.",
                    "ids": incomplete_tasks,
                }
            )


def _compare_status(
    differences: list[dict[str, Any]],
    *,
    kind: str,
    item_id: str,
    replay_status: str | None,
    table_status: str | None,
) -> None:
    if replay_status != table_status:
        differences.append(
            {
                "kind": kind,
                "id": item_id,
                "field": "status",
                "event_state": replay_status,
                "table_state": table_status,
            }
        )


def _error(kind: str, event: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "event_id": event.get("id"),
        "event_type": event.get("event_type"),
        "message": message,
    }
