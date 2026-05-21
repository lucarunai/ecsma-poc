from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .brain.claude_agent import ClaudeCodingAgent
from .brain.github_workflow import GitHubWorkflowService
from .brain.orchestrator import RunOrchestrator
from .brain.planner import AgentTaskPlanner
from .brain.sdk_tools import CodingToolServerFactory
from .brain.worker import BrainWorker
from .config import Settings
from .session_client import SessionLayerClient


settings = Settings.from_env()
store = SessionLayerClient(settings.session_layer_url)
github = GitHubWorkflowService(settings)
orchestrator = RunOrchestrator(
    store=store,
    planner=AgentTaskPlanner(settings),
    agent=ClaudeCodingAgent(
        settings,
        CodingToolServerFactory(github),
    ),
    github=github,
)
worker = BrainWorker(store=store, orchestrator=orchestrator)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker.run_forever(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Cloud Agent PoC Brain", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": "brain"}
