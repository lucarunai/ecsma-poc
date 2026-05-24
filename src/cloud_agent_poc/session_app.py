from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .domain import PlannedTask, SessionEvent
from .ownership import DEFAULT_USER_ID, normalize_user_id
from .session_store import PostgresSessionStore


class RunCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class RunUpdateRequest(BaseModel):
    status: str
    error_message: str | None = None
    started: bool = False
    ended: bool = False
    metadata: dict[str, Any] | None = None
    acceptance_criteria: list[dict[str, Any]] | None = None


class RunClaimRequest(BaseModel):
    worker_id: str | None = Field(default=None, max_length=200)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class RunHeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class RunAttemptLimitRequest(BaseModel):
    max_attempts: int = Field(default=5, ge=1, le=100)


class PlannedTaskRequest(BaseModel):
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)


class TasksCreateRequest(BaseModel):
    tasks: list[PlannedTaskRequest]


class TaskUpdateRequest(BaseModel):
    status: str
    started: bool = False
    ended: bool = False
    result_summary: str | None = None


class TaskAttemptCreateRequest(BaseModel):
    run_id: str
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class TaskAttemptUpdateRequest(BaseModel):
    status: str
    claude_session_id: str | None = None
    failure_kind: str | None = Field(default=None, max_length=100)
    failure_reason: str | None = None
    ended: bool = False


class ToolCallCreateRequest(BaseModel):
    run_id: str
    task_id: str
    task_attempt_id: str | None = None
    tool_name: str
    tool_input: dict[str, Any]


class ToolCallUpdateRequest(BaseModel):
    status: str
    latest_execution_id: str | None = None
    failure_kind: str | None = None
    ended: bool = False


class ApprovalCreateRequest(BaseModel):
    run_id: str
    task_id: str
    task_attempt_id: str | None = None
    tool_call_id: str
    tool_name: str
    tool_input: dict[str, Any]
    reason: str
    requested_by: str | None = Field(default=None, max_length=200)


class ApprovalDecisionRequest(BaseModel):
    decision: str
    decided_by: str | None = Field(default="web-ui", max_length=200)
    decision_reason: str | None = None


class EventCreateRequest(BaseModel):
    session_id: str
    run_id: str | None = None
    task_id: str | None = None
    event_type: str
    payload: dict[str, Any]
    schema_version: str | None = None
    actor_type: str = Field(default="system", max_length=80)
    actor_id: str | None = Field(default=None, max_length=200)


class AgentTranscriptCreateRequest(BaseModel):
    run_id: str
    task_id: str | None = None
    provider: str
    provider_session_id: str
    artifact_path: str
    checksum: str
    size_bytes: int


class TaskHandoffCreateRequest(BaseModel):
    session_id: str
    run_id: str
    from_task_id: str
    to_task_id: str | None = None
    status: str
    summary: str
    payload: dict[str, Any]
    claude_session_id: str | None = None
    transcript_id: str | None = None


class ToolExecutionCreateRequest(BaseModel):
    run_id: str
    task_id: str | None = None
    task_attempt_id: str | None = None
    failure_kind: str | None = None
    envelope: dict[str, Any]


class RetentionArchiveResponse(BaseModel):
    runs_archived: int
    sessions_archived: int


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "task_id": event.task_id,
        "user_id": event.user_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
        "seq": event.seq,
        "schema_version": event.schema_version,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "payload_hash": event.payload_hash,
        "previous_event_hash": event.previous_event_hash,
        "event_hash": event.event_hash,
    }


def event_to_sse(event: SessionEvent) -> str:
    data = event_to_dict(event)
    return (
        f"id: {event.id}\n"
        f"event: {event.event_type}\n"
        f"data: {json.dumps(data, ensure_ascii=True)}\n\n"
    )


settings = Settings.from_env()
store = PostgresSessionStore(settings.database_url)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await store.initialize_schema()
    yield


app = FastAPI(title="Cloud Agent PoC Session Layer", lifespan=lifespan)


def _user_id_from_header(x_user_id: str | None = Header(default=None)) -> str:
    return normalize_user_id(x_user_id or DEFAULT_USER_ID)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "session"}


@app.post("/api/sessions")
async def create_session(user_id: str = Header(default=DEFAULT_USER_ID, alias="X-User-Id")) -> dict[str, str]:
    user_id = normalize_user_id(user_id)
    session_id = await store.create_session(user_id=user_id)
    await store.append_event(
        session_id=session_id,
        event_type="session.created",
        payload={"session_id": session_id, "user_id": user_id},
    )
    return {"session_id": session_id, "user_id": user_id}


@app.post("/api/sessions/{session_id}/runs")
async def create_run(
    session_id: str,
    body: RunCreateRequest,
    user_id: str = Header(default=DEFAULT_USER_ID, alias="X-User-Id"),
) -> dict[str, str]:
    user_id = normalize_user_id(user_id)
    try:
        run_id = await store.create_run(
            session_id,
            body.prompt,
            idempotency_key=body.idempotency_key,
            user_id=user_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Session was not found.") from exc
    await store.append_event(
        session_id=session_id,
        run_id=run_id,
        event_type="user.prompt.accepted",
        payload={"prompt": body.prompt, "run_id": run_id, "user_id": user_id},
    )
    return {"session_id": session_id, "run_id": run_id, "status": "queued"}


@app.get("/api/runs/{run_id}")
async def get_run(
    run_id: str,
    user_id: str = Header(default=DEFAULT_USER_ID, alias="X-User-Id"),
) -> dict:
    run = await store.get_run(run_id, user_id=normalize_user_id(user_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run was not found.")
    return run


@app.post("/api/runs/{run_id}/wake")
async def wake_run(
    run_id: str,
    user_id: str = Header(default=DEFAULT_USER_ID, alias="X-User-Id"),
) -> dict[str, str]:
    run = await store.wake_run(run_id, user_id=normalize_user_id(user_id))
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Only blocked or failed runs can be woken.",
        )
    await store.append_event(
        session_id=run["session_id"],
        run_id=run_id,
        event_type="run.resume.requested",
        payload={"run_id": run_id, "reason": "tool_failure_resume"},
    )
    await store.append_event(
        session_id=run["session_id"],
        run_id=run_id,
        event_type="run.wake.requested",
        payload={"run_id": run_id, "reason": "tool_failure_resume"},
    )
    return {"run_id": run_id, "status": "resume_queued"}


@app.get("/api/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    after_event_id: int = Query(default=0, ge=0),
    user_id: str = Query(default=DEFAULT_USER_ID),
) -> StreamingResponse:
    user_id = normalize_user_id(user_id)

    async def stream() -> AsyncIterator[str]:
        latest_event_id = after_event_id
        seconds_without_events = 0
        while True:
            events = await store.list_events(
                session_id,
                latest_event_id,
                user_id=user_id,
            )
            if events:
                seconds_without_events = 0
                for event in events:
                    latest_event_id = event.id
                    yield event_to_sse(event)
                continue
            await asyncio.sleep(1)
            seconds_without_events += 1
            if seconds_without_events >= 15:
                seconds_without_events = 0
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/internal/runs/claim", response_model=None)
async def claim_next_queued_run(body: RunClaimRequest | None = None):
    body = body or RunClaimRequest()
    run = await store.claim_next_queued_run(
        worker_id=body.worker_id,
        lease_seconds=body.lease_seconds,
    )
    if run is None:
        return Response(status_code=204)
    await store.append_event(
        session_id=run.session_id,
        run_id=run.id,
        event_type="run.claimed",
        payload={
            "run_id": run.id,
            "worker_id": body.worker_id,
            "lease_seconds": body.lease_seconds,
        },
        actor_type="brain_worker",
        actor_id=body.worker_id,
    )
    return {
        "id": run.id,
        "session_id": run.session_id,
        "user_id": run.user_id,
        "prompt": run.prompt,
        "status": run.status,
    }


@app.post("/internal/runs/requeue-expired-leases")
async def requeue_expired_run_leases() -> dict[str, Any]:
    expired_runs = await store.requeue_expired_run_leases()
    for run in expired_runs:
        await store.append_event(
            session_id=run["session_id"],
            run_id=run["id"],
            event_type="run.lease.expired",
            payload={
                "run_id": run["id"],
                "claimed_by": run.get("claimed_by"),
                "last_heartbeat_at": (
                    run["last_heartbeat_at"].isoformat()
                    if run.get("last_heartbeat_at") is not None
                    else None
                ),
            },
        )
        for tool_call in run.get("orphaned_tool_calls", []):
            await store.append_event(
                session_id=run["session_id"],
                run_id=run["id"],
                task_id=tool_call.get("task_id"),
                event_type="tool.call.orphaned",
                payload={
                    "task_id": tool_call.get("task_id"),
                    "tool_call_id": tool_call.get("id"),
                    "tool_name": tool_call.get("tool_name"),
                    "failure_kind": tool_call.get("failure_kind"),
                    "reason": "run_lease_expired",
                },
            )
        for approval in run.get("expired_approvals", []):
            await store.append_event(
                session_id=run["session_id"],
                run_id=run["id"],
                task_id=approval.get("task_id"),
                event_type="approval.expired",
                payload={
                    "approval_id": approval.get("id"),
                    "run_id": approval.get("run_id"),
                    "task_id": approval.get("task_id"),
                    "task_attempt_id": approval.get("task_attempt_id"),
                    "tool_call_id": approval.get("tool_call_id"),
                    "tool_name": approval.get("tool_name"),
                    "status": approval.get("status"),
                    "decision_reason": approval.get("decision_reason"),
                },
            )
        await store.append_event(
            session_id=run["session_id"],
            run_id=run["id"],
            event_type="run.resume.requested",
            payload={"run_id": run["id"], "reason": "run_lease_expired"},
        )
    return {"runs_requeued": len(expired_runs)}


@app.post("/internal/runs/fail-exhausted-attempts")
async def fail_runs_over_attempt_limit(
    body: RunAttemptLimitRequest,
) -> dict[str, Any]:
    exhausted_runs = await store.fail_runs_over_attempt_limit(
        max_attempts=body.max_attempts,
    )
    for run in exhausted_runs:
        await store.append_event(
            session_id=run["session_id"],
            run_id=run["id"],
            event_type="run.resume.exhausted",
            payload={
                "run_id": run["id"],
                "attempt_count": run.get("attempt_count"),
                "max_attempts": body.max_attempts,
            },
        )
        await store.append_event(
            session_id=run["session_id"],
            run_id=run["id"],
            event_type="run.failed",
            payload={
                "run_id": run["id"],
                "error": "Run exceeded maximum automatic resume attempts.",
            },
        )
    return {"runs_failed": len(exhausted_runs)}


@app.post("/internal/runs/{run_id}/heartbeat")
async def heartbeat_run_lease(
    run_id: str,
    body: RunHeartbeatRequest,
) -> dict[str, Any]:
    ok = await store.heartbeat_run_lease(
        run_id=run_id,
        worker_id=body.worker_id,
        lease_seconds=body.lease_seconds,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Run lease was not refreshed.")
    return {"status": "ok"}


@app.patch("/internal/runs/{run_id}")
async def update_run(run_id: str, body: RunUpdateRequest) -> dict[str, str]:
    try:
        await store.update_run(
            run_id,
            body.status,
            error_message=body.error_message,
            started=body.started,
            ended=body.ended,
            metadata=body.metadata,
            acceptance_criteria=body.acceptance_criteria,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/internal/retention/archive")
async def archive_expired_state() -> RetentionArchiveResponse:
    return RetentionArchiveResponse(**await store.archive_expired_state())


@app.post("/internal/runs/{run_id}/tasks")
async def create_tasks(run_id: str, body: TasksCreateRequest) -> dict[str, list[dict]]:
    planned_tasks = [
        PlannedTask(
            title=task.title,
            description=task.description,
            acceptance_criteria=task.acceptance_criteria,
        )
        for task in body.tasks
    ]
    tasks = await store.create_tasks(run_id, planned_tasks)
    return {"tasks": [task.__dict__ for task in tasks]}


@app.patch("/internal/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdateRequest) -> dict[str, str]:
    await store.update_task(
        task_id,
        body.status,
        started=body.started,
        ended=body.ended,
        result_summary=body.result_summary,
    )
    return {"status": "ok"}


@app.post("/internal/tasks/{task_id}/attempts")
async def create_task_attempt(
    task_id: str,
    body: TaskAttemptCreateRequest,
) -> dict[str, Any]:
    attempt = await store.create_task_attempt(
        run_id=body.run_id,
        task_id=task_id,
        lease_seconds=body.lease_seconds,
    )
    return attempt.__dict__


@app.patch("/internal/task-attempts/{attempt_id}")
async def update_task_attempt(
    attempt_id: str,
    body: TaskAttemptUpdateRequest,
) -> dict[str, str]:
    await store.update_task_attempt(
        attempt_id,
        body.status,
        claude_session_id=body.claude_session_id,
        failure_kind=body.failure_kind,
        failure_reason=body.failure_reason,
        ended=body.ended,
    )
    return {"status": "ok"}


@app.post("/internal/tool-calls")
async def create_tool_call(body: ToolCallCreateRequest) -> dict[str, str]:
    tool_call_id = await store.create_tool_call(
        run_id=body.run_id,
        task_id=body.task_id,
        task_attempt_id=body.task_attempt_id,
        tool_name=body.tool_name,
        tool_input=body.tool_input,
    )
    return {"tool_call_id": tool_call_id}


@app.patch("/internal/tool-calls/{tool_call_id}")
async def update_tool_call(
    tool_call_id: str,
    body: ToolCallUpdateRequest,
) -> dict[str, str]:
    await store.update_tool_call(
        tool_call_id,
        body.status,
        latest_execution_id=body.latest_execution_id,
        failure_kind=body.failure_kind,
        ended=body.ended,
    )
    return {"status": "ok"}


@app.post("/internal/approval-requests")
async def create_approval_request(body: ApprovalCreateRequest) -> dict[str, Any]:
    return await store.create_approval_request(
        run_id=body.run_id,
        task_id=body.task_id,
        task_attempt_id=body.task_attempt_id,
        tool_call_id=body.tool_call_id,
        tool_name=body.tool_name,
        tool_input=body.tool_input,
        reason=body.reason,
        requested_by=body.requested_by,
    )


@app.get("/internal/approval-requests/{approval_id}")
async def get_approval_request(approval_id: str) -> dict[str, Any]:
    approval = await store.get_approval_request(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request was not found.")
    return approval


@app.post("/internal/approval-requests/{approval_id}/expire")
async def expire_approval_request(approval_id: str) -> dict[str, Any]:
    approval = await store.expire_approval_request(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request was not pending.")
    return approval


@app.post("/api/approvals/{approval_id}/decision")
async def decide_approval_request(
    approval_id: str,
    body: ApprovalDecisionRequest,
    user_id: str = Header(default=DEFAULT_USER_ID, alias="X-User-Id"),
) -> dict[str, Any]:
    if body.decision not in {"approved", "denied"}:
        raise HTTPException(
            status_code=422,
            detail="Approval decision must be approved or denied.",
        )
    approval = await store.decide_approval_request(
        approval_id,
        decision=body.decision,
        decided_by=body.decided_by,
        decision_reason=body.decision_reason,
        user_id=normalize_user_id(user_id),
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request was not found.")
    await store.append_event(
        session_id=approval["session_id"],
        run_id=approval["run_id"],
        task_id=approval["task_id"],
        event_type=f"approval.{body.decision}",
        payload={
            "approval_id": approval["id"],
            "run_id": approval["run_id"],
            "task_id": approval["task_id"],
            "task_attempt_id": approval["task_attempt_id"],
            "tool_call_id": approval["tool_call_id"],
            "tool_name": approval["tool_name"],
            "status": approval["status"],
            "decided_by": approval["decided_by"],
            "decision_reason": approval["decision_reason"],
        },
        actor_type="human",
        actor_id=body.decided_by,
    )
    return approval


@app.post("/internal/events")
async def append_event(body: EventCreateRequest) -> dict[str, Any]:
    event = await store.append_event(
        session_id=body.session_id,
        run_id=body.run_id,
        task_id=body.task_id,
        event_type=body.event_type,
        payload=body.payload,
        schema_version=body.schema_version,
        actor_type=body.actor_type,
        actor_id=body.actor_id,
    )
    return event_to_dict(event)


@app.get("/internal/runs/{run_id}/replay")
async def replay_run(
    run_id: str,
    user_id: str = Header(default=DEFAULT_USER_ID, alias="X-User-Id"),
) -> dict[str, Any]:
    report = await store.get_run_replay_report(
        run_id,
        user_id=normalize_user_id(user_id),
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Run was not found.")
    return report


@app.post("/internal/agent-transcripts")
async def record_agent_transcript(
    body: AgentTranscriptCreateRequest,
) -> dict[str, str]:
    transcript_id = await store.record_agent_transcript(
        run_id=body.run_id,
        task_id=body.task_id,
        provider=body.provider,
        provider_session_id=body.provider_session_id,
        artifact_path=body.artifact_path,
        checksum=body.checksum,
        size_bytes=body.size_bytes,
    )
    return {"transcript_id": transcript_id}


@app.post("/internal/task-handoffs")
async def create_task_handoff(body: TaskHandoffCreateRequest) -> dict[str, int]:
    handoff_id = await store.create_task_handoff(
        session_id=body.session_id,
        run_id=body.run_id,
        from_task_id=body.from_task_id,
        to_task_id=body.to_task_id,
        status=body.status,
        summary=body.summary,
        payload=body.payload,
        claude_session_id=body.claude_session_id,
        transcript_id=body.transcript_id,
    )
    return {"handoff_id": handoff_id}


@app.post("/internal/tool-executions")
async def record_tool_execution(body: ToolExecutionCreateRequest) -> dict[str, str]:
    execution_id = await store.record_tool_execution(
        run_id=body.run_id,
        task_id=body.task_id,
        task_attempt_id=body.task_attempt_id,
        failure_kind=body.failure_kind,
        envelope=body.envelope,
    )
    return {"execution_id": execution_id}


@app.get("/internal/runs/{run_id}/recovery-bundle")
async def run_recovery_bundle(run_id: str) -> dict[str, Any]:
    bundle = await store.get_run_recovery_bundle(run_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Run was not found.")
    return bundle
