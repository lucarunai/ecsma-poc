import asyncio
import unittest

from cloud_agent_poc.brain.worker import BrainWorker
from cloud_agent_poc.domain import RunRecord


class WorkerStoreStub:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.claims: list[dict] = []
        self.heartbeats: list[dict] = []
        self._claimed = False

    async def requeue_expired_run_leases(self) -> dict:
        self.operations.append("requeue_expired_run_leases")
        return {"runs_requeued": 1}

    async def fail_runs_over_attempt_limit(self, **kwargs) -> dict:
        self.operations.append("fail_runs_over_attempt_limit")
        return {"runs_failed": 0, "kwargs": kwargs}

    async def claim_next_queued_run(self, **kwargs):
        self.operations.append("claim_next_queued_run")
        self.claims.append(kwargs)
        if self._claimed:
            return None
        self._claimed = True
        return RunRecord(
            id="run_test",
            session_id="session_test",
            prompt="Do work.",
            status="running",
        )

    async def heartbeat_run_lease(self, **kwargs) -> bool:
        self.operations.append("heartbeat_run_lease")
        self.heartbeats.append(kwargs)
        return True


class SlowOrchestrator:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.executions: list[dict] = []

    async def execute(self, **kwargs) -> None:
        self.executions.append(kwargs)
        await asyncio.sleep(0.03)
        self.stop_event.set()


class BrainWorkerLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_requeues_expired_leases_before_claiming(self) -> None:
        stop_event = asyncio.Event()
        store = WorkerStoreStub()
        orchestrator = SlowOrchestrator(stop_event)
        worker = BrainWorker(
            store=store,
            orchestrator=orchestrator,
            poll_interval_seconds=0.01,
            worker_id="worker-test",
            run_lease_seconds=60,
            heartbeat_interval_seconds=0.01,
        )

        await worker.run_forever(stop_event)

        self.assertEqual(
            store.operations[:3],
            [
                "requeue_expired_run_leases",
                "fail_runs_over_attempt_limit",
                "claim_next_queued_run",
            ],
        )
        self.assertEqual(store.claims[0]["worker_id"], "worker-test")
        self.assertEqual(store.claims[0]["lease_seconds"], 60)

    async def test_worker_heartbeats_while_orchestrator_runs(self) -> None:
        stop_event = asyncio.Event()
        store = WorkerStoreStub()
        worker = BrainWorker(
            store=store,
            orchestrator=SlowOrchestrator(stop_event),
            poll_interval_seconds=0.01,
            worker_id="worker-test",
            run_lease_seconds=90,
            heartbeat_interval_seconds=0.01,
        )

        await worker.run_forever(stop_event)

        self.assertTrue(store.heartbeats)
        self.assertEqual(store.heartbeats[0]["run_id"], "run_test")
        self.assertEqual(store.heartbeats[0]["worker_id"], "worker-test")
        self.assertEqual(store.heartbeats[0]["lease_seconds"], 90)


if __name__ == "__main__":
    unittest.main()
