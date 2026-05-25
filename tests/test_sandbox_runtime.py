import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cloud_agent_poc.brain.processes import CommandResult
from cloud_agent_poc.config import Settings
from cloud_agent_poc.sandbox_manager import (
    KubernetesTaskSandboxSessionRunner,
    KubernetesToolPodRunner,
    SandboxManagerError,
)
from cloud_agent_poc.sandbox_protocol import (
    SandboxSessionCreateRequest,
    ToolExecutionRequest,
)
from cloud_agent_poc.sandbox_runtime import execute_runtime_request
from cloud_agent_poc.sandbox_tools import _command_data


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
            self.assertEqual(envelope.runtime.runtime_profile, "direct")
            self.assertEqual(envelope.runtime.isolation, "process")
            self.assertEqual(envelope.runtime.resource_limits["ephemeral_storage"], "1Gi")
            self.assertEqual(envelope.runtime.resource_limits["timeout_seconds"], 180)
            self.assertEqual(envelope.runtime.resource_limits["tool_output_bytes"], 4000)
            self.assertEqual(envelope.runtime.resource_limits["workspace_bytes"], 104857600)
            self.assertEqual(envelope.runtime.workspace["file_count"], 1)
            self.assertGreater(envelope.runtime.workspace["bytes"], 0)

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

    async def test_write_tool_rejects_workspace_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            settings = replace(_settings(workspace), sandbox_workspace_bytes_limit=5)
            envelope = await execute_runtime_request(
                ToolExecutionRequest(
                    execution_id="sbxexec_quota",
                    run_id="run_0123456789abcdef0123456789abcdef",
                    tool_call_id="toolcall_quota",
                    tool_name="write_workspace_file",
                    args={"path": "too-large.txt", "content": "123456"},
                ),
                settings=settings,
                workspace_path=workspace,
            )

            self.assertEqual(envelope.execution_status, "succeeded")
            self.assertTrue(envelope.tool_result)
            self.assertFalse(envelope.tool_result.ok)
            self.assertIn("Workspace size limit exceeded", envelope.tool_result.summary)
            self.assertFalse(workspace.joinpath("too-large.txt").exists())

    async def test_read_tool_rejects_symlink_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("target.txt").write_text("secret", encoding="utf-8")
            workspace.joinpath("link.txt").symlink_to("target.txt")

            envelope = await execute_runtime_request(
                ToolExecutionRequest(
                    execution_id="sbxexec_symlink",
                    run_id="run_0123456789abcdef0123456789abcdef",
                    tool_call_id="toolcall_symlink",
                    tool_name="read_workspace_file",
                    args={"path": "link.txt"},
                ),
                settings=_settings(workspace),
                workspace_path=workspace,
            )

            self.assertEqual(envelope.execution_status, "succeeded")
            self.assertTrue(envelope.tool_result)
            self.assertFalse(envelope.tool_result.ok)
            self.assertIn("Symlink workspace files are not allowed", envelope.tool_result.summary)

    async def test_command_output_is_capped_with_evidence(self) -> None:
        settings = replace(_settings(Path("/tmp/workspace")), sandbox_tool_output_bytes_limit=4)
        data = _command_data(
            CommandResult(
                command=["python", "-m", "unittest"],
                returncode=1,
                stdout="abcdef",
                stderr="123456",
            ),
            settings=settings,
        )

        self.assertEqual(data["stdout"], "cdef")
        self.assertEqual(data["stderr"], "3456")
        self.assertTrue(data["stdout_truncated"])
        self.assertTrue(data["stderr_truncated"])
        self.assertEqual(data["stdout_original_bytes"], 6)
        self.assertEqual(data["stderr_original_bytes"], 6)
        self.assertEqual(data["output_limit_bytes"], 4)


class SandboxPodManifestTests(unittest.TestCase):
    def test_github_token_is_never_injected_into_sandbox_tool_pods(self) -> None:
        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))
        run_id = "run_0123456789abcdef0123456789abcdef"
        workspace_path = f"/sandboxes/users/Luca/{run_id}"

        file_manifest = runner._pod_manifest(
            "sandbox-tool-file",
            ToolExecutionRequest(
                run_id=run_id,
                tool_call_id="toolcall_file",
                tool_name="read_workspace_file",
                args={"path": "README.md"},
                execution_id="sbxexec_file",
                workspace_path=workspace_path,
            ),
        )
        clone_manifest = runner._pod_manifest(
            "sandbox-tool-clone",
            ToolExecutionRequest(
                run_id=run_id,
                tool_call_id="toolcall_clone",
                tool_name="clone_github_repository",
                args={"repository_url": "https://github.com/lucarunai/demo", "source_branch": "test"},
                execution_id="sbxexec_clone",
                workspace_path=workspace_path,
            ),
        )

        file_env = file_manifest["spec"]["containers"][0]["env"]
        clone_env = clone_manifest["spec"]["containers"][0]["env"]
        self.assertNotIn("GITHUB_TOKEN", [item["name"] for item in file_env])
        self.assertNotIn("GITHUB_TOKEN", [item["name"] for item in clone_env])
        self.assertEqual(
            clone_manifest["spec"]["containers"][0]["volumeMounts"][0]["subPath"],
            f"users/Luca/{run_id}",
        )
        self.assertEqual(
            clone_manifest["spec"]["containers"][0]["volumeMounts"][0]["mountPath"],
            f"/workspace/{run_id}",
        )
        workspace_env = {
            item["name"]: item["value"]
            for item in clone_env
            if "value" in item
        }
        self.assertEqual(
            workspace_env["SANDBOX_WORKSPACE_PATH"],
            f"/workspace/{run_id}",
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
        self.assertEqual(manifest["spec"]["activeDeadlineSeconds"], 180)

    def test_sandbox_tool_pod_has_security_context_resources_and_tmp(self) -> None:
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

        container = manifest["spec"]["containers"][0]
        security_context = container["securityContext"]
        self.assertIs(security_context["runAsNonRoot"], True)
        self.assertEqual(security_context["runAsUser"], 10001)
        self.assertEqual(security_context["runAsGroup"], 10001)
        self.assertIs(security_context["allowPrivilegeEscalation"], False)
        self.assertEqual(security_context["capabilities"]["drop"], ["ALL"])
        self.assertEqual(
            security_context["seccompProfile"],
            {"type": "RuntimeDefault"},
        )
        self.assertEqual(
            container["resources"],
            {
                "requests": {
                    "cpu": "100m",
                    "memory": "128Mi",
                    "ephemeral-storage": "128Mi",
                },
                "limits": {
                    "cpu": "500m",
                    "memory": "512Mi",
                    "ephemeral-storage": "1Gi",
                },
            },
        )
        mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
        volumes = {volume["name"]: volume for volume in manifest["spec"]["volumes"]}
        self.assertEqual(mounts["tmp"]["mountPath"], "/tmp")
        self.assertEqual(volumes["tmp"], {"name": "tmp", "emptyDir": {}})

    def test_sandbox_tool_request_is_encoded_in_environment(self) -> None:
        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))
        run_id = "run_0123456789abcdef0123456789abcdef"
        workspace_path = f"/sandboxes/users/Luca/{run_id}"

        manifest = runner._pod_manifest(
            "sandbox-tool-write",
            ToolExecutionRequest(
                run_id=run_id,
                tool_call_id="toolcall_write",
                tool_name="write_workspace_file",
                args={"path": "hello.py", "content": "print('hello')\n"},
                execution_id="sbxexec_write",
                workspace_path=workspace_path,
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
        self.assertEqual(decoded_request["run_id"], run_id)
        self.assertEqual(decoded_request["workspace_path"], workspace_path)
        self.assertEqual(decoded_request["args"]["path"], "hello.py")
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_sandbox_tool_pod_rejects_workspace_outside_root(self) -> None:
        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))

        with self.assertRaisesRegex(SandboxManagerError, "escaped sandbox root"):
            runner._pod_manifest(
                "sandbox-tool-file",
                ToolExecutionRequest(
                    run_id="run_0123456789abcdef0123456789abcdef",
                    tool_call_id="toolcall_file",
                    tool_name="read_workspace_file",
                    args={"path": "README.md"},
                    execution_id="sbxexec_file",
                    workspace_path="/tmp/outside/run_0123456789abcdef0123456789abcdef",
                ),
            )

    def test_task_sandbox_session_pod_has_daemon_contract(self) -> None:
        runner = object.__new__(KubernetesTaskSandboxSessionRunner)
        runner.settings = _settings(Path("/sandboxes"))
        run_id = "run_0123456789abcdef0123456789abcdef"

        manifest = runner._pod_manifest(
            "sandbox-task-session",
            SandboxSessionCreateRequest(
                sandbox_session_id="sbxsess_0123456789abcdef0123456789abcdef",
                run_id=run_id,
                task_id="task_test",
                task_attempt_id="taskattempt_test",
                workspace_path=f"/sandboxes/users/Luca/{run_id}",
            ),
        )

        metadata = manifest["metadata"]
        spec = manifest["spec"]
        container = spec["containers"][0]
        env = {
            item["name"]: item["value"]
            for item in container["env"]
            if "value" in item
        }
        self.assertEqual(metadata["labels"]["app"], "cloud-agent-task-sandbox")
        self.assertEqual(
            metadata["labels"]["cloud-agent-sandbox-session-id"],
            "sbxsess_0123456789abcdef0123456789abcdef",
        )
        self.assertIs(spec["automountServiceAccountToken"], False)
        self.assertEqual(spec["activeDeadlineSeconds"], 900)
        self.assertEqual(
            container["command"],
            [
                "uvicorn",
                "cloud_agent_poc.sandbox_daemon:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
            ],
        )
        self.assertEqual(env["SANDBOX_WORKSPACE_PATH"], f"/workspace/{run_id}")
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(
            container["volumeMounts"][0]["subPath"],
            f"users/Luca/{run_id}",
        )
        self.assertEqual(container["securityContext"]["runAsUser"], 10001)
        self.assertEqual(container["resources"]["limits"]["memory"], "512Mi")


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
        self.assertEqual(envelope.runtime.runtime_profile, "kubernetes_container")
        self.assertEqual(envelope.runtime.isolation, "container")
        self.assertEqual(envelope.runtime.network_policy, "sandbox-tool-default-deny")
        self.assertEqual(envelope.runtime.egress_policy, "default-deny")
        self.assertEqual(envelope.runtime.resource_limits["ephemeral_storage"], "1Gi")
        self.assertEqual(envelope.runtime.workspace["path"], "/sandboxes")
        self.assertIsNotNone(envelope.runtime.duration_ms)
        self.assertIn("HTTP 404", envelope.failure_message or "")
        self.assertEqual(deleted_pods, ["sandbox-tool-crash"])

    async def test_kubernetes_runner_sanitizes_execution_id_for_pod_name(self) -> None:
        class DummyClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

        runner = object.__new__(KubernetesToolPodRunner)
        runner.settings = _settings(Path("/sandboxes"))
        runner.namespace = "cloud-agent-poc"
        runner._client = lambda: DummyClient()

        created_pods = []
        deleted_pods = []

        async def create_pod(_client, pod_name, _request):
            created_pods.append(pod_name)

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
            tool_call_id="toolcall_runtime_evidence",
            tool_name="write_workspace_file",
            args={"path": "hello.txt", "content": "hello\n"},
            execution_id="sbxexec_runtime_evidence",
        )

        envelope = await runner.execute(request, workspace_path=Path("/sandboxes"))

        self.assertEqual(envelope.runtime.pod_name, "sandbox-tool-runtime-evidence")
        self.assertEqual(created_pods, ["sandbox-tool-runtime-evidence"])
        self.assertEqual(deleted_pods, ["sandbox-tool-runtime-evidence"])


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
