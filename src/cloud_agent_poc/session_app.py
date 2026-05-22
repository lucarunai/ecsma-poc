from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .domain import PlannedTask, SessionEvent
from .session_store import PostgresSessionStore


class RunCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class RunUpdateRequest(BaseModel):
    status: str
    error_message: str | None = None
    started: bool = False
    ended: bool = False
    metadata: dict[str, Any] | None = None


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
    resume_from_session_id: str | None = None


class TaskAttemptUpdateRequest(BaseModel):
    status: str
    claude_session_id: str | None = None
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


class EventCreateRequest(BaseModel):
    session_id: str
    run_id: str | None = None
    task_id: str | None = None
    event_type: str
    payload: dict[str, Any]


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
    claude_session_id: str | None = None
    next_resume_session_id: str | None = None
    transcript_id: str | None = None


class ToolExecutionCreateRequest(BaseModel):
    run_id: str
    task_id: str | None = None
    task_attempt_id: str | None = None
    failure_kind: str | None = None
    envelope: dict[str, Any]


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
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


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "session"}


@app.post("/api/sessions")
async def create_session() -> dict[str, str]:
    session_id = await store.create_session()
    await store.append_event(
        session_id=session_id,
        event_type="session.created",
        payload={"session_id": session_id},
    )
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/runs")
async def create_run(
    session_id: str,
    body: RunCreateRequest,
) -> dict[str, str]:
    try:
        run_id = await store.create_run(session_id, body.prompt)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Session was not found.") from exc
    await store.append_event(
        session_id=session_id,
        run_id=run_id,
        event_type="user.prompt.accepted",
        payload={"prompt": body.prompt, "run_id": run_id},
    )
    return {"session_id": session_id, "run_id": run_id, "status": "queued"}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run was not found.")
    return run


@app.post("/api/runs/{run_id}/wake")
async def wake_run(run_id: str) -> dict[str, str]:
    run = await store.wake_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Only blocked or failed runs can be woken.",
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
) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        latest_event_id = after_event_id
        seconds_without_events = 0
        while True:
            events = await store.list_events(session_id, latest_event_id)
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
async def claim_next_queued_run():
    run = await store.claim_next_queued_run()
    if run is None:
        return Response(status_code=204)
    return {
        "id": run.id,
        "session_id": run.session_id,
        "prompt": run.prompt,
        "status": run.status,
    }


@app.patch("/internal/runs/{run_id}")
async def update_run(run_id: str, body: RunUpdateRequest) -> dict[str, str]:
    await store.update_run(
        run_id,
        body.status,
        error_message=body.error_message,
        started=body.started,
        ended=body.ended,
        metadata=body.metadata,
    )
    return {"status": "ok"}


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
        resume_from_session_id=body.resume_from_session_id,
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


@app.post("/internal/events")
async def append_event(body: EventCreateRequest) -> dict[str, Any]:
    event = await store.append_event(
        session_id=body.session_id,
        run_id=body.run_id,
        task_id=body.task_id,
        event_type=body.event_type,
        payload=body.payload,
    )
    return event_to_dict(event)


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
        claude_session_id=body.claude_session_id,
        next_resume_session_id=body.next_resume_session_id,
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
