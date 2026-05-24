from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .session_client import SessionLayerClient


class RunCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    idempotency_key: str | None = Field(default=None, max_length=128)


settings = Settings.from_env()
session_layer = SessionLayerClient(settings.session_layer_url)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
    session_id = await session_layer.create_session()
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/runs")
async def create_run(
    session_id: str,
    body: RunCreateRequest,
) -> dict[str, str]:
    try:
        run_id = await session_layer.create_run(
            session_id,
            body.prompt,
            idempotency_key=body.idempotency_key,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session was not found.") from exc
        raise
    return {"session_id": session_id, "run_id": run_id, "status": "queued"}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = await session_layer.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run was not found.")
    return run


@app.post("/api/runs/{run_id}/wake")
async def wake_run(run_id: str) -> dict:
    try:
        return await session_layer.wake_run(run_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 409}:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=exc.response.json().get("detail", "Run wake failed."),
            ) from exc
        raise


@app.get("/api/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    after_event_id: int = Query(default=0, ge=0),
) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        url = (
            f"{settings.session_layer_url}/api/sessions/{session_id}/events"
            f"?after_event_id={after_event_id}"
        )
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    yield chunk
                    await asyncio.sleep(0)

    return StreamingResponse(stream(), media_type="text/event-stream")
