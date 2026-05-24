from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from ..session_policy import (
    RUN_HEARTBEAT_INTERVAL_SECONDS,
    RUN_LEASE_SECONDS,
    RUN_MAX_ATTEMPTS,
)
from .orchestrator import RunOrchestrator

if TYPE_CHECKING:
    from ..session_client import SessionLayerClient
    from ..session_store import PostgresSessionStore


class BrainWorker:
    def __init__(
        self,
        *,
        store: PostgresSessionStore | SessionLayerClient,
        orchestrator: RunOrchestrator,
        poll_interval_seconds: float = 1.0,
        worker_id: str | None = None,
        run_lease_seconds: int = RUN_LEASE_SECONDS,
        heartbeat_interval_seconds: float = RUN_HEARTBEAT_INTERVAL_SECONDS,
        max_run_attempts: int = RUN_MAX_ATTEMPTS,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_id = worker_id or _default_worker_id()
        self.run_lease_seconds = run_lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.max_run_attempts = max_run_attempts

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.store.requeue_expired_run_leases()
                await self.store.fail_runs_over_attempt_limit(
                    max_attempts=self.max_run_attempts,
                )
                claimed_run = await self.store.claim_next_queued_run(
                    worker_id=self.worker_id,
                    lease_seconds=self.run_lease_seconds,
                )
            except Exception:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            if claimed_run is None:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                self._heartbeat_until_stopped(claimed_run.id, heartbeat_stop)
            )
            try:
                await self.orchestrator.execute(
                    session_id=claimed_run.session_id,
                    run_id=claimed_run.id,
                    prompt=claimed_run.prompt,
                )
            finally:
                heartbeat_stop.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_until_stopped(
        self,
        run_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.heartbeat_interval_seconds,
                )
                continue
            except TimeoutError:
                pass
            try:
                ok = await self.store.heartbeat_run_lease(
                    run_id=run_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.run_lease_seconds,
                )
            except Exception:
                continue
            if not ok:
                return


def _default_worker_id() -> str:
    hostname = os.getenv("HOSTNAME") or "brain-worker"
    return f"{hostname}-{uuid4().hex[:8]}"
