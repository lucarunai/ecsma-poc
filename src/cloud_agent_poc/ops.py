from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


OPS_RUN_SUMMARY_SCHEMA = "ops_run_summary.v1"
SUPPORT_BUNDLE_SCHEMA = "support_bundle.v1"
OPS_METRIC_SNAPSHOT_SCHEMA = "ops_metric_snapshot.v1"
OPS_ALERTS_SCHEMA = "ops_alerts.v1"

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
]


def build_ops_run_summary(
    *,
    run: dict[str, Any],
    tasks: list[dict[str, Any]],
    task_attempts: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    tool_executions: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    replay_report: dict[str, Any] | None,
) -> dict[str, Any]:
    first_failure = _first_failure(
        task_attempts=task_attempts,
        tool_calls=tool_calls,
        tool_executions=tool_executions,
        events=events,
    )
    return {
        "schema_version": OPS_RUN_SUMMARY_SCHEMA,
        "run_id": run["id"],
        "session_id": run["session_id"],
        "user_id": run.get("user_id"),
        "status": run["status"],
        "prompt": _text_fingerprint(run.get("prompt")),
        "created_at": _iso(run.get("created_at")),
        "started_at": _iso(run.get("started_at")),
        "ended_at": _iso(run.get("ended_at")),
        "duration_ms": _duration_ms(run.get("started_at"), run.get("ended_at")),
        "task_counts": _status_counts(tasks),
        "task_attempt_counts": _status_counts(task_attempts),
        "tool_counts": _status_counts(tool_calls),
        "tool_execution_counts": _execution_counts(tool_executions),
        "approval_summary": _approval_summary(approvals),
        "recovery_summary": _recovery_summary(run, events, task_attempts, tool_calls),
        "failure_summary": first_failure,
        "sandbox_summary": _sandbox_summary(tool_executions),
        "handoff_summary": {
            "total": len(handoffs),
            "latest": _latest_summary(handoffs),
        },
        "replay_summary": _replay_summary(replay_report),
    }


def build_support_bundle(
    *,
    run: dict[str, Any],
    tasks: list[dict[str, Any]],
    task_attempts: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    tool_executions: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    replay_report: dict[str, Any] | None,
    visibility: str = "internal",
) -> dict[str, Any]:
    visibility = "customer" if visibility == "customer" else "internal"
    summary = build_ops_run_summary(
        run=run,
        tasks=tasks,
        task_attempts=task_attempts,
        tool_calls=tool_calls,
        tool_executions=tool_executions,
        approvals=approvals,
        handoffs=handoffs,
        events=events,
        replay_report=replay_report,
    )
    return {
        "schema_version": SUPPORT_BUNDLE_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "visibility": visibility,
        "triage": _triage(summary),
        "run_summary": summary,
        "run": _sanitize_run(run, visibility=visibility),
        "tasks": [_sanitize_record(task, visibility=visibility) for task in tasks],
        "task_attempts": [
            _sanitize_record(attempt, visibility=visibility)
            for attempt in task_attempts
        ],
        "tool_calls": [
            _sanitize_tool_call(tool_call, visibility=visibility)
            for tool_call in tool_calls
        ],
        "tool_executions": [
            _sanitize_tool_execution(execution, visibility=visibility)
            for execution in tool_executions
        ],
        "approvals": [
            _sanitize_approval(approval, visibility=visibility)
            for approval in approvals
        ],
        "handoffs": [
            _sanitize_handoff(handoff, visibility=visibility)
            for handoff in handoffs
        ],
        "replay_report": _sanitize_value(replay_report or {}, visibility=visibility),
        "event_timeline": [
            _sanitize_event(event, visibility=visibility)
            for event in events[-200:]
        ],
        "redaction": {
            "profile": visibility,
            "rules": [
                "secret-looking values",
                "authorization headers",
                "long text previews",
                "customer-visible prompt and file content minimization",
            ],
        },
    }


def build_ops_metric_snapshot(
    *,
    user_id: str,
    window_hours: int,
    runs: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    task_attempts: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    tool_executions: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    replay_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    completed_runs = [run for run in runs if run.get("status") == "completed"]
    failed_runs = [
        run for run in runs if run.get("status") in {"failed", "blocked"}
    ]
    run_durations = [
        duration
        for duration in (
            _duration_ms(run.get("started_at"), run.get("ended_at"))
            for run in runs
        )
        if duration is not None
    ]
    tool_durations = _tool_execution_durations(tool_executions)
    event_counts = Counter(str(event.get("event_type")) for event in events)
    failure_kinds = Counter(
        str(row.get("failure_kind"))
        for row in [*task_attempts, *tool_calls, *tool_executions]
        if row.get("failure_kind")
    )
    tool_failures = Counter(
        str(call.get("tool_name"))
        for call in tool_calls
        if call.get("status") in {"failed", "orphaned"} or call.get("failure_kind")
    )
    return {
        "schema_version": OPS_METRIC_SNAPSHOT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "window_hours": window_hours,
        "run_counts": {
            **_status_counts(runs),
            "success_rate": _ratio(len(completed_runs), len(runs)),
            "failure_rate": _ratio(len(failed_runs), len(runs)),
        },
        "run_duration_ms": _duration_stats(run_durations),
        "task_counts": _status_counts(tasks),
        "task_attempt_counts": _status_counts(task_attempts),
        "tool_counts": {
            **_status_counts(tool_calls),
            "by_tool": _counter_by_key(tool_calls, "tool_name"),
            "failures_by_tool": dict(sorted(tool_failures.items())),
        },
        "tool_execution_counts": _execution_counts(tool_executions),
        "tool_duration_ms": _duration_stats(tool_durations),
        "failure_kinds": dict(sorted(failure_kinds.items())),
        "approval_summary": _approval_summary(approvals),
        "recovery_summary": {
            "resume_requested_count": event_counts.get("run.resume.requested", 0),
            "resume_started_count": event_counts.get("run.resume.started", 0),
            "lease_expired_count": event_counts.get("run.lease.expired", 0),
            "brain_crash_count": _failure_kind_count(task_attempts, "brain_crash"),
            "orphaned_tool_calls": sum(
                1 for call in tool_calls if call.get("status") == "orphaned"
            ),
        },
        "sandbox_summary": _sandbox_summary(tool_executions),
        "replay_summary": _replay_metric_summary(replay_reports),
        "recent_runs": [_recent_run_summary(run) for run in runs[:20]],
    }


def build_ops_alerts(
    *,
    metric_snapshot: dict[str, Any],
) -> dict[str, Any]:
    alerts = []
    replay = metric_snapshot.get("replay_summary", {})
    recovery = metric_snapshot.get("recovery_summary", {})
    approvals = metric_snapshot.get("approval_summary", {})
    sandbox = metric_snapshot.get("sandbox_summary", {})
    failure_kinds = metric_snapshot.get("failure_kinds", {})
    run_counts = metric_snapshot.get("run_counts", {})

    if replay.get("drift_count", 0) > 0 or replay.get("hash_chain_invalid_count", 0) > 0:
        alerts.append(
            _alert(
                "replay_drift",
                "critical",
                "Replay drift or invalid hash chain detected.",
                "Inspect replay reports before trusting materialized state.",
                replay,
            )
        )
    if recovery.get("lease_expired_count", 0) > 0:
        alerts.append(
            _alert(
                "brain_lease_expired",
                "high",
                "Brain lease expiration occurred in the selected window.",
                "Check Brain worker health and recovery events.",
                recovery,
            )
        )
    sandbox_errors = int(failure_kinds.get("sandbox_runtime_error", 0))
    if sandbox_errors > 0:
        alerts.append(
            _alert(
                "sandbox_runtime_error",
                "high",
                "Sandbox runtime errors occurred.",
                "Inspect failed tool execution envelopes and pod phases.",
                {"sandbox_runtime_error": sandbox_errors},
            )
        )
    if approvals.get("pending", 0) > 0 or approvals.get("expired", 0) > 0:
        alerts.append(
            _alert(
                "approval_attention",
                "medium",
                "Human approval requests require attention.",
                "Review pending or expired approval requests.",
                approvals,
            )
        )
    if sandbox.get("output_truncated_count", 0) > 0:
        alerts.append(
            _alert(
                "output_truncated",
                "medium",
                "Some sandbox outputs were truncated.",
                "Consider whether output caps are hiding useful validation evidence.",
                {"output_truncated_count": sandbox["output_truncated_count"]},
            )
        )
    if run_counts.get("failure_rate", 0) and run_counts["failure_rate"] >= 0.25:
        alerts.append(
            _alert(
                "run_failure_rate",
                "medium",
                "Run failure rate is elevated in the selected window.",
                "Inspect failure kind distribution and recent failed runs.",
                {
                    "failure_rate": run_counts["failure_rate"],
                    "total": run_counts.get("total", 0),
                },
            )
        )

    return {
        "schema_version": OPS_ALERTS_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user_id": metric_snapshot.get("user_id"),
        "window_hours": metric_snapshot.get("window_hours"),
        "status": "healthy" if not alerts else "attention_required",
        "alert_count": len(alerts),
        "alerts": alerts,
    }


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("status", "unknown")) for row in rows)
    result = {"total": len(rows)}
    result.update(dict(sorted(counts.items())))
    return result


def _counter_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(
        sorted(
            Counter(str(row.get(key) or "unknown") for row in rows).items()
        )
    )


def _execution_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("execution_status", "unknown")) for row in rows)
    result = {"total": len(rows)}
    result.update(dict(sorted(counts.items())))
    return result


def _approval_summary(approvals: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _status_counts(approvals)
    latencies = [
        _duration_ms(row.get("created_at"), row.get("decided_at"))
        for row in approvals
        if row.get("created_at") and row.get("decided_at")
    ]
    counts["decision_latency_ms_avg"] = (
        round(sum(latencies) / len(latencies)) if latencies else None
    )
    return counts


def _recovery_summary(
    run: dict[str, Any],
    events: list[dict[str, Any]],
    task_attempts: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    event_types = Counter(str(event.get("event_type")) for event in events)
    return {
        "attempt_count": int(run.get("attempt_count") or 0),
        "task_attempt_count": len(task_attempts),
        "resume_requested_count": event_types.get("run.resume.requested", 0),
        "resume_started_count": event_types.get("run.resume.started", 0),
        "lease_expired_count": event_types.get("run.lease.expired", 0),
        "brain_crash_attempts": _failure_kind_count(
            task_attempts,
            "brain_crash",
        ),
        "orphaned_tool_calls": sum(
            1 for call in tool_calls if call.get("status") == "orphaned"
        ),
    }


def _sandbox_summary(tool_executions: list[dict[str, Any]]) -> dict[str, Any]:
    runtimes = Counter()
    phases = Counter()
    output_truncated = 0
    workspace_bytes_max = 0
    durations = []
    for execution in tool_executions:
        envelope = execution.get("envelope") or {}
        runtime = envelope.get("runtime") or {}
        runtimes.update([str(runtime.get("runtime_profile") or "unknown")])
        phases.update([str(runtime.get("pod_phase") or "unknown")])
        output = runtime.get("output") or {}
        if output.get("stdout_truncated") or output.get("stderr_truncated"):
            output_truncated += 1
        workspace = runtime.get("workspace") or {}
        if isinstance(workspace.get("bytes"), int):
            workspace_bytes_max = max(workspace_bytes_max, workspace["bytes"])
        if isinstance(runtime.get("duration_ms"), int):
            durations.append(runtime["duration_ms"])
    return {
        "runtime_profiles": dict(sorted(runtimes.items())),
        "pod_phases": dict(sorted(phases.items())),
        "output_truncated_count": output_truncated,
        "workspace_bytes_max": workspace_bytes_max,
        "duration_ms_avg": round(sum(durations) / len(durations)) if durations else None,
        "duration_ms_max": max(durations) if durations else None,
    }


def _tool_execution_durations(tool_executions: list[dict[str, Any]]) -> list[int]:
    durations = []
    for execution in tool_executions:
        runtime = (execution.get("envelope") or {}).get("runtime") or {}
        duration = runtime.get("duration_ms")
        if isinstance(duration, int):
            durations.append(duration)
    return durations


def _duration_stats(durations: list[int]) -> dict[str, Any]:
    if not durations:
        return {"avg": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(durations)
    return {
        "avg": round(sum(ordered) / len(ordered)),
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "max": max(ordered),
    }


def _percentile(ordered: list[int], percentile: float) -> int:
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _replay_metric_summary(replay_reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked_runs": len(replay_reports),
        "drift_count": sum(1 for report in replay_reports if not report.get("consistent")),
        "hash_chain_invalid_count": sum(
            1 for report in replay_reports if not report.get("hash_chain_valid")
        ),
        "invalid_replay_count": sum(
            1 for report in replay_reports if not report.get("replay_valid")
        ),
    }


def _recent_run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("id"),
        "session_id": run.get("session_id"),
        "status": run.get("status"),
        "created_at": _iso(run.get("created_at")),
        "started_at": _iso(run.get("started_at")),
        "ended_at": _iso(run.get("ended_at")),
        "duration_ms": _duration_ms(run.get("started_at"), run.get("ended_at")),
        "attempt_count": run.get("attempt_count") or 0,
        "prompt": _text_fingerprint(run.get("prompt")),
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _alert(
    alert_type: str,
    severity: str,
    summary: str,
    next_action: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": alert_type,
        "severity": severity,
        "summary": summary,
        "next_action": next_action,
        "evidence": evidence,
    }


def _first_failure(
    *,
    task_attempts: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    tool_executions: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    failed_execution = next(
        (
            execution
            for execution in tool_executions
            if execution.get("execution_status") != "succeeded"
            or execution.get("failure_kind")
        ),
        None,
    )
    if failed_execution:
        return {
            "failure_kind": failed_execution.get("failure_kind")
            or "sandbox_runtime_error",
            "failed_component": "sandbox",
            "first_failed_task_id": failed_execution.get("task_id"),
            "first_failed_tool_call_id": failed_execution.get("tool_call_id"),
            "message": _preview(
                (failed_execution.get("envelope") or {}).get("failure_message")
            ),
        }
    failed_tool = next(
        (
            tool_call
            for tool_call in tool_calls
            if tool_call.get("status") in {"failed", "orphaned"}
            or tool_call.get("failure_kind")
        ),
        None,
    )
    if failed_tool:
        return {
            "failure_kind": failed_tool.get("failure_kind") or "tool_error",
            "failed_component": "tool",
            "first_failed_task_id": failed_tool.get("task_id"),
            "first_failed_tool_call_id": failed_tool.get("id"),
            "message": None,
        }
    failed_attempt = next(
        (
            attempt
            for attempt in task_attempts
            if attempt.get("status") == "failed" or attempt.get("failure_kind")
        ),
        None,
    )
    if failed_attempt:
        return {
            "failure_kind": failed_attempt.get("failure_kind") or "model_error",
            "failed_component": "brain",
            "first_failed_task_id": failed_attempt.get("task_id"),
            "first_failed_tool_call_id": None,
            "message": _preview(failed_attempt.get("failure_reason")),
        }
    failed_event = next(
        (
            event
            for event in events
            if "failed" in str(event.get("event_type"))
            or "blocked" in str(event.get("event_type"))
        ),
        None,
    )
    if failed_event:
        payload = failed_event.get("payload") or {}
        return {
            "failure_kind": payload.get("failure_kind") or "unknown",
            "failed_component": "event",
            "first_failed_task_id": failed_event.get("task_id"),
            "first_failed_tool_call_id": payload.get("tool_call_id"),
            "message": _preview(
                payload.get("error") or payload.get("summary") or payload.get("text")
            ),
        }
    return {
        "failure_kind": None,
        "failed_component": None,
        "first_failed_task_id": None,
        "first_failed_tool_call_id": None,
        "message": None,
    }


def _replay_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "available": False,
            "replay_valid": None,
            "hash_chain_valid": None,
            "consistent": None,
            "error_count": None,
            "difference_count": None,
        }
    return {
        "available": True,
        "replay_valid": report.get("replay_valid"),
        "hash_chain_valid": report.get("hash_chain_valid"),
        "consistent": report.get("consistent"),
        "error_count": len(report.get("errors") or []),
        "difference_count": len(report.get("differences") or []),
    }


def _triage(summary: dict[str, Any]) -> dict[str, Any]:
    failure = summary["failure_summary"]
    replay = summary["replay_summary"]
    if replay.get("available") and not replay.get("consistent"):
        return {
            "category": "replay_drift",
            "severity": "high",
            "customer_action_required": False,
            "platform_action_required": True,
            "summary": "Replay state does not match materialized run state.",
            "next_actions": [
                "Inspect replay report differences.",
                "Check session_events hash chain.",
            ],
        }
    if summary["approval_summary"].get("pending", 0):
        return {
            "category": "awaiting_approval",
            "severity": "medium",
            "customer_action_required": True,
            "platform_action_required": False,
            "summary": "The run is waiting for a human approval decision.",
            "next_actions": ["Approve or deny the pending high-risk tool request."],
        }
    if failure.get("failure_kind"):
        component = failure.get("failed_component") or "platform"
        return {
            "category": failure["failure_kind"],
            "severity": "medium",
            "customer_action_required": component == "approval",
            "platform_action_required": component != "approval",
            "summary": f"The run has a {component} failure: {failure['failure_kind']}.",
            "next_actions": [
                "Inspect ops run summary.",
                "Inspect failed task/tool evidence.",
                "Review replay consistency.",
            ],
        }
    return {
        "category": "healthy" if summary["status"] == "completed" else summary["status"],
        "severity": "low",
        "customer_action_required": False,
        "platform_action_required": False,
        "summary": f"Run status is {summary['status']}.",
        "next_actions": [],
    }


def _sanitize_run(run: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    sanitized = _sanitize_record(run, visibility=visibility)
    sanitized["prompt"] = _text_fingerprint(run.get("prompt"))
    return sanitized


def _sanitize_tool_call(row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    sanitized = _sanitize_record(row, visibility=visibility)
    if "input" in sanitized:
        sanitized["input"] = _sanitize_value(row.get("input"), visibility=visibility)
    return sanitized


def _sanitize_tool_execution(row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    sanitized = _sanitize_record(row, visibility=visibility)
    if "envelope" in sanitized:
        sanitized["envelope"] = _sanitize_value(row.get("envelope"), visibility=visibility)
    return sanitized


def _sanitize_approval(row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    sanitized = _sanitize_record(row, visibility=visibility)
    if "tool_input" in sanitized:
        sanitized["tool_input"] = _sanitize_value(
            row.get("tool_input"),
            visibility=visibility,
        )
    return sanitized


def _sanitize_handoff(row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    sanitized = _sanitize_record(row, visibility=visibility)
    if "payload" in sanitized:
        sanitized["payload"] = _sanitize_value(row.get("payload"), visibility=visibility)
    return sanitized


def _sanitize_event(row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    sanitized = _sanitize_record(row, visibility=visibility)
    if "payload" in sanitized:
        sanitized["payload"] = _sanitize_value(row.get("payload"), visibility=visibility)
    return sanitized


def _sanitize_record(row: dict[str, Any], *, visibility: str) -> dict[str, Any]:
    return {
        key: _sanitize_value(value, visibility=visibility)
        for key, value in row.items()
    }


def _sanitize_value(value: Any, *, visibility: str) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _sanitize_value_by_key(str(key), nested, visibility=visibility)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item, visibility=visibility) for item in value[:200]]
    if isinstance(value, str):
        redacted = _redact_secrets(value)
        if visibility == "customer":
            return _preview(redacted, limit=240)
        return _preview(redacted, limit=2000)
    return value


def _sanitize_value_by_key(key: str, value: Any, *, visibility: str) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("token", "secret", "password", "authorization")):
        return "[redacted]"
    if visibility == "customer" and lowered in {"content", "body", "prompt"}:
        return _text_fingerprint(value)
    return _sanitize_value(value, visibility=visibility)


def _redact_secrets(text: str) -> str:
    redacted = text.replace("[redacted-github-auth-header]", "[redacted]")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def _text_fingerprint(value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    return {
        "preview": _preview(_redact_secrets(text), limit=240),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "bytes": len(text.encode("utf-8")),
    }


def _preview(value: Any, *, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated {len(text) - limit} chars]"


def _failure_kind_count(rows: list[dict[str, Any]], failure_kind: str) -> int:
    return sum(1 for row in rows if row.get("failure_kind") == failure_kind)


def _latest_summary(handoffs: list[dict[str, Any]]) -> str | None:
    if not handoffs:
        return None
    return _preview(handoffs[-1].get("summary"))


def _duration_ms(started_at: Any, ended_at: Any) -> int | None:
    if not isinstance(started_at, datetime) or not isinstance(ended_at, datetime):
        return None
    return max(0, round((ended_at - started_at).total_seconds() * 1000))


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
