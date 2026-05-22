import unittest

from cloud_agent_poc.brain.sdk_tools import CodingToolServerFactory
from cloud_agent_poc.domain import TaskRecord
from cloud_agent_poc.sandbox_client import SandboxLayerError
from cloud_agent_poc.sandbox_protocol import (
    SandboxRuntimeMetadata,
    SandboxToolResult,
    ToolExecutionEnvelope,
)


class SandboxStub:
    async def execute_tool(self, run_id, tool_name, args, **_):
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
        self.tool_calls = []
        self.tool_call_updates = []

    async def create_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs)
        return "toolcall_test"

    async def update_tool_call(self, tool_call_id, status, **kwargs):
        self.tool_call_updates.append((tool_call_id, status, kwargs))

    async def record_tool_execution(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["envelope"]["execution_id"]


class ToolErrorSandboxStub(SandboxStub):
    async def execute_tool(self, run_id, tool_name, args, **_):
        return ToolExecutionEnvelope(
            execution_id="sbxexec_failed",
            run_id=run_id,
            tool_call_id="toolcall_test",
            tool_name=tool_name,
            execution_status="succeeded",
            tool_result=SandboxToolResult(
                ok=False,
                summary="Git clone failed.",
                data={"path": args["path"]},
            ),
            runtime=SandboxRuntimeMetadata(
                pod_name="sandbox-tool-failed",
                pod_phase="Succeeded",
                exit_code=0,
                duration_ms=16,
            ),
        )


class RuntimeErrorSandboxStub(SandboxStub):
    async def execute_tool(self, run_id, tool_name, args, **_):
        return ToolExecutionEnvelope(
            execution_id="sbxexec_crashed",
            run_id=run_id,
            tool_call_id="toolcall_test",
            tool_name=tool_name,
            execution_status="failed",
            tool_result=None,
            failure_message="Sandbox Pod lookup failed with HTTP 404.",
            runtime=SandboxRuntimeMetadata(
                pod_name="sandbox-tool-crashed",
                pod_phase="Unknown",
                exit_code=None,
                duration_ms=25,
            ),
        )


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
            task_attempt_id="attempt_test",
        )

        self.assertEqual(envelope.execution_id, "sbxexec_test")
        self.assertEqual(store.calls[0]["run_id"], task.run_id)
        self.assertEqual(store.calls[0]["task_id"], task.id)
        self.assertEqual(store.calls[0]["envelope"]["runtime"]["pod_name"], "sandbox-tool-test")
        self.assertEqual(events[0][0], "tool.call.requested")
        self.assertEqual(events[1][0], "tool.execution")
        self.assertEqual(events[1][1]["task_id"], task.id)
        self.assertEqual(events[1][1]["execution"]["tool_name"], "read_workspace_file")

    async def test_tool_error_is_recorded_before_returning_to_agent(self) -> None:
        store = StoreStub()
        events = []
        factory = CodingToolServerFactory(ToolErrorSandboxStub(), store)
        task = TaskRecord(
            id="task_test",
            run_id="run_0123456789abcdef0123456789abcdef",
            seq=1,
            kind="model_task",
            title="Clone",
            description="Clone a branch.",
            acceptance_criteria=[],
            status="running",
        )

        async def emit(event_type, payload):
            events.append((event_type, payload))

        with self.assertRaises(SandboxLayerError):
            await factory._execute_tool(
                task.run_id,
                task,
                emit,
                "clone_github_repository",
                {"path": "."},
                task_attempt_id="attempt_test",
            )

        self.assertEqual(store.calls[0]["failure_kind"], "tool_error")
        self.assertEqual(store.tool_call_updates[0][1], "failed")
        self.assertEqual(store.tool_call_updates[0][2]["failure_kind"], "tool_error")
        self.assertEqual(events[1][0], "tool.execution")
        self.assertEqual(events[1][1]["failure_kind"], "tool_error")

    async def test_runtime_failure_envelope_is_recorded_before_raising(self) -> None:
        store = StoreStub()
        events = []
        factory = CodingToolServerFactory(RuntimeErrorSandboxStub(), store)
        task = TaskRecord(
            id="task_test",
            run_id="run_0123456789abcdef0123456789abcdef",
            seq=1,
            kind="model_task",
            title="Verify",
            description="Run tests.",
            acceptance_criteria=[],
            status="running",
        )

        async def emit(event_type, payload):
            events.append((event_type, payload))

        with self.assertRaises(SandboxLayerError):
            await factory._execute_tool(
                task.run_id,
                task,
                emit,
                "run_python_unittest",
                {"start_directory": "."},
                task_attempt_id="attempt_test",
            )

        self.assertEqual(store.calls[0]["failure_kind"], "sandbox_runtime_error")
        self.assertEqual(
            store.calls[0]["envelope"]["runtime"]["pod_name"],
            "sandbox-tool-crashed",
        )
        self.assertEqual(store.tool_call_updates[0][1], "failed")
        self.assertEqual(
            store.tool_call_updates[0][2]["failure_kind"],
            "sandbox_runtime_error",
        )
        self.assertEqual(events[1][0], "tool.execution")
        self.assertEqual(events[1][1]["failure_kind"], "sandbox_runtime_error")


if __name__ == "__main__":
    unittest.main()
