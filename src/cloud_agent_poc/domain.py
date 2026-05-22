from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PlannedTask:
    title: str
    description: str
    acceptance_criteria: list[str]


@dataclass(frozen=True)
class TaskRecord:
    id: str
    run_id: str
    seq: int
    kind: str
    title: str
    description: str
    acceptance_criteria: list[str]
    status: str


@dataclass(frozen=True)
class TaskAttemptRecord:
    id: str
    run_id: str
    task_id: str
    attempt_no: int
    status: str
    claude_session_id: str | None = None
    resume_from_session_id: str | None = None


@dataclass(frozen=True)
class AgentTaskResult:
    status: str
    summary: str
    claude_session_id: str | None = None


@dataclass(frozen=True)
class RunRecord:
    id: str
    session_id: str
    prompt: str
    status: str


@dataclass(frozen=True)
class SessionEvent:
    id: int
    session_id: str
    run_id: str | None
    task_id: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class Workspace:
    path: str
