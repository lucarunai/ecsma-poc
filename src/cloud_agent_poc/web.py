from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .domain import SessionEvent
from .session_store import PostgresSessionStore


class RunCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


def event_to_sse(event: SessionEvent) -> str:
    data = {
        "id": event.id,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }
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


app = FastAPI(title="Cloud Agent PoC Web", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "web"}


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
