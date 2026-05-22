import unittest

from cloud_agent_poc.brain.sdk_tools import CodingToolServerFactory
from cloud_agent_poc.domain import TaskRecord
from cloud_agent_poc.sandbox_protocol import (
    SandboxRuntimeMetadata,
    SandboxToolResult,
    ToolExecutionEnvelope,
)


class SandboxStub:
    async def execute_tool(self, run_id, tool_name, args):
        return ToolExecutionEnvelope(
            execution_id="sbxexec_test",
            run_id=run_id,
            tool_call_id="toolcall_test",
            tool_name=tool_name,
            execution_status="succeeded",
            tool_result=SandboxToolResult(
                ok=True,
                summary="Read file.",
                data={"path": args["path"]},
            ),
            runtime=SandboxRuntimeMetadata(
                pod_name="sandbox-tool-test",
                pod_phase="Succeeded",
                exit_code=0,
                duration_ms=12,
            ),
        )


class StoreStub:
    def __init__(self) -> None:
        self.calls = []

    async def record_tool_execution(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["envelope"]["execution_id"]


class CodingToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_is_recorded_and_emitted_for_task(self) -> None:
        store = StoreStub()
        events = []
        factory = CodingToolServerFactory(SandboxStub(), store)
        task = TaskRecord(
            id="task_test",
            run_id="run_0123456789abcdef0123456789abcdef",
            seq=1,
            kind="model_task",
            title="Read",
            description="Read a file.",
            acceptance_criteria=[],
            status="running",
        )

        async def emit(event_type, payload):
            events.append((event_type, payload))

        envelope = await factory._execute_tool(
            task.run_id,
            task,
            emit,
            "read_workspace_file",
            {"path": "README.md"},
        )

        self.assertEqual(envelope.execution_id, "sbxexec_test")
        self.assertEqual(store.calls[0]["run_id"], task.run_id)
        self.assertEqual(store.calls[0]["task_id"], task.id)
        self.assertEqual(store.calls[0]["envelope"]["runtime"]["pod_name"], "sandbox-tool-test")
        self.assertEqual(events[0][0], "tool.execution")
        self.assertEqual(events[0][1]["task_id"], task.id)
        self.assertEqual(events[0][1]["execution"]["tool_name"], "read_workspace_file")


if __name__ == "__main__":
    unittest.main()
