from __future__ import annotations

from datetime import datetime, timedelta, timezone

SESSION_TTL_DAYS = 7
RUN_RETENTION_DAYS = 7
RUN_LEASE_SECONDS = 60
RUN_HEARTBEAT_INTERVAL_SECONDS = 20
RUN_MAX_ATTEMPTS = 5

FAILURE_KIND_BRAIN_CRASH = "brain_crash"
FAILURE_KIND_MODEL_ERROR = "model_error"

RUN_STATUS_TRANSITIONS = {
    "queued": {"running"},
    "resume_queued": {"running", "failed"},
    "running": {"completed", "blocked", "failed", "resume_queued"},
    "blocked": {"resume_queued"},
    "failed": {"resume_queued"},
    "completed": set(),
}


class InvalidRunStatusTransition(ValueError):
    pass


def validate_run_status_transition(current_status: str, next_status: str) -> None:
    if current_status == next_status:
        return
    if current_status not in RUN_STATUS_TRANSITIONS:
        raise InvalidRunStatusTransition(
            f"Unknown current run status: {current_status}."
        )
    if next_status not in RUN_STATUS_TRANSITIONS:
        raise InvalidRunStatusTransition(f"Unknown target run status: {next_status}.")
    if next_status not in RUN_STATUS_TRANSITIONS[current_status]:
        raise InvalidRunStatusTransition(
            f"Invalid run status transition: {current_status} -> {next_status}."
        )


def session_expiration_deadline(
    now: datetime | None = None,
) -> datetime:
    return _utcnow(now) + timedelta(days=SESSION_TTL_DAYS)


def run_retention_deadline(
    now: datetime | None = None,
) -> datetime:
    return _utcnow(now) + timedelta(days=RUN_RETENTION_DAYS)


def _utcnow(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)
