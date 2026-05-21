from __future__ import annotations

import asyncio

from ..session_store import PostgresSessionStore
from .orchestrator import RunOrchestrator


class BrainWorker:
    def __init__(
        self,
        *,
        store: PostgresSessionStore,
        orchestrator: RunOrchestrator,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.poll_interval_seconds = poll_interval_seconds

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                claimed_run = await self.store.claim_next_queued_run()
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
            await self.orchestrator.execute(
                session_id=claimed_run.session_id,
                run_id=claimed_run.id,
                prompt=claimed_run.prompt,
            )
