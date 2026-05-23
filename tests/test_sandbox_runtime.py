import base64
import json
import tempfile
import unittest
from pathlib import Path

from cloud_agent_poc.config import Settings
from cloud_agent_poc.sandbox_manager import KubernetesToolPodRunner, SandboxManagerError
from cloud_agent_poc.sandbox_protocol import ToolExecutionRequest
from cloud_agent_poc.sandbox_runtime import execute_runtime_request


class SandboxRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_tool_returns_execution_envelope_and_writes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            envelope = await execute_runtime_request(
                ToolExecutionRequest(
                    execution_id="sbxexec_test",
                    run_id="run_0123456789abcdef0123456789abcdef",
                    tool_call_id="toolcall_test",
                    tool_name="write_workspace_file",
                    args={"path": "hello.py", "content": "print('hello')\n"},
                ),
                settings=_settings(workspace),
                workspace_path=workspace,
            )

            self.assertEqual(envelope.execution_status, "succeeded")
            self.assertTrue(envelope.tool_result)
            self.assertTrue(envelope.tool_result.ok)
            self.assertEqual(workspace.joinpath("hello.py").read_text(), "print('hello')\n")

    async def test_file_escape_returns_tool_error_without_runtime_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            envelope = await execute_runtime_request(
                ToolExecutionRequest(
                    execution_id="sbxexec_test",
                    run_id="run_0123456789abcdef0123456789abcdef",
                    tool_call_id="toolcall_test",
                    tool_name="read_workspace_file",
                    args={"path": "../outside.txt"},
                ),
                settings=_settings(workspace),
                workspace_path=workspace,
            )

            self.assertEqual(envelope.execution_status, "succeeded")
            self.assertTrue(envelope.tool_result)
            self.assertFalse(envelope.tool_result.ok)
            self.assertIn("escaped workspace", envelope.tool_result.summary)


class SandboxPodManifestTests(unittest.TestCase):
    def test_github_token_is_never_injected_into_sandbox_tool_pods(self) -> None:
        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))

        file_manifest = runner._pod_manifest(
            "sandbox-tool-file",
            ToolExecutionRequest(
                run_id="run_0123456789abcdef0123456789abcdef",
                tool_call_id="toolcall_file",
                tool_name="read_workspace_file",
                args={"path": "README.md"},
                execution_id="sbxexec_file",
            ),
        )
        clone_manifest = runner._pod_manifest(
            "sandbox-tool-clone",
            ToolExecutionRequest(
                run_id="run_0123456789abcdef0123456789abcdef",
                tool_call_id="toolcall_clone",
                tool_name="clone_github_repository",
                args={"repository_url": "https://github.com/lucarunai/demo", "source_branch": "test"},
                execution_id="sbxexec_clone",
            ),
        )

        file_env = file_manifest["spec"]["containers"][0]["env"]
        clone_env = clone_manifest["spec"]["containers"][0]["env"]
        self.assertNotIn("GITHUB_TOKEN", [item["name"] for item in file_env])
        self.assertNotIn("GITHUB_TOKEN", [item["name"] for item in clone_env])
        self.assertEqual(
            clone_manifest["spec"]["containers"][0]["volumeMounts"][0]["subPath"],
            "run_0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(
            clone_manifest["spec"]["containers"][0]["volumeMounts"][0]["mountPath"],
            "/workspace/run_0123456789abcdef0123456789abcdef",
        )
        workspace_env = {
            item["name"]: item["value"]
            for item in clone_env
            if "value" in item
        }
        self.assertEqual(
            workspace_env["SANDBOX_WORKSPACE_PATH"],
            "/workspace/run_0123456789abcdef0123456789abcdef",
        )

    def test_sandbox_tool_pod_disables_service_account_token(self) -> None:
        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))

        manifest = runner._pod_manifest(
            "sandbox-tool-file",
            ToolExecutionRequest(
                run_id="run_0123456789abcdef0123456789abcdef",
                tool_call_id="toolcall_file",
                tool_name="read_workspace_file",
                args={"path": "README.md"},
                execution_id="sbxexec_file",
            ),
        )

        self.assertIs(manifest["spec"]["automountServiceAccountToken"], False)
        self.assertEqual(manifest["spec"]["restartPolicy"], "Never")

    def test_sandbox_tool_request_is_encoded_in_environment(self) -> None:
        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))

        manifest = runner._pod_manifest(
            "sandbox-tool-write",
            ToolExecutionRequest(
                run_id="run_0123456789abcdef0123456789abcdef",
                tool_call_id="toolcall_write",
                tool_name="write_workspace_file",
                args={"path": "hello.py", "content": "print('hello')\n"},
                execution_id="sbxexec_write",
            ),
        )

        env = {
            item["name"]: item["value"]
            for item in manifest["spec"]["containers"][0]["env"]
            if "value" in item
        }
        decoded_request = json.loads(
            base64.b64decode(env["SANDBOX_TOOL_REQUEST_B64"]).decode()
        )

        self.assertEqual(decoded_request["tool_name"], "write_workspace_file")
        self.assertEqual(decoded_request["run_id"], "run_0123456789abcdef0123456789abcdef")
        self.assertEqual(decoded_request["args"]["path"], "hello.py")
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)


class SandboxKubernetesRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_kubernetes_runner_returns_failed_envelope_when_pod_disappears(self) -> None:
        class DummyClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))
        runner.namespace = "cloud-agent-poc"
        runner._client = lambda: DummyClient()

        deleted_pods = []

        async def create_pod(_client, _pod_name, _request):
            return None

        async def wait_for_pod(_client, pod_name):
            raise SandboxManagerError(
                f'Sandbox Pod lookup failed with HTTP 404: pods "{pod_name}" not found'
            )

        async def delete_pod(_client, pod_name):
            deleted_pods.append(pod_name)

        runner._create_pod = create_pod
        runner._wait_for_pod = wait_for_pod
        runner._delete_pod = delete_pod

        request = ToolExecutionRequest(
            run_id="run_0123456789abcdef0123456789abcdef",
            tool_call_id="toolcall_crash",
            tool_name="run_python_unittest",
            args={"start_directory": "."},
            execution_id="sbxexec_crash",
        )

        envelope = await runner.execute(request, workspace_path=Path("/sandboxes"))

        self.assertEqual(envelope.execution_status, "failed")
        self.assertEqual(envelope.execution_id, "sbxexec_crash")
        self.assertEqual(envelope.runtime.pod_name, "sandbox-tool-crash")
        self.assertEqual(envelope.runtime.pod_phase, "Unknown")
        self.assertIsNotNone(envelope.runtime.duration_ms)
        self.assertIn("HTTP 404", envelope.failure_message or "")
        self.assertEqual(deleted_pods, ["sandbox-tool-crash"])


def _settings(workspace_root: Path) -> Settings:
    return Settings(
            database_url="postgresql://unused",
            session_layer_url="http://unused",
            sandbox_layer_url="http://sandbox",
            github_broker_url="http://github-broker",
            github_repo_url="https://github.com/lucarunai/demo.git",
        github_source_branch="test",
        github_target_branch="main",
        github_token=None,
        workspace_root=workspace_root,
        claude_config_dir=workspace_root / ".claude",
        claude_model=None,
        git_author_name="Cloud Agent PoC",
        git_author_email="cloud-agent-poc@example.local",
        sandbox_execution_mode="direct",
            sandbox_runtime_image="cloud-agent-poc:local",
            sandbox_tool_timeout_seconds=180,
            sandbox_workspace_claim="sandbox-workspaces",
        )


if __name__ == "__main__":
    unittest.main()
