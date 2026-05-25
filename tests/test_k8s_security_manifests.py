import unittest
from pathlib import Path


class KubernetesSecurityManifestTests(unittest.TestCase):
    def test_cloud_agent_image_runs_as_non_root_user(self) -> None:
        dockerfile = Path("Dockerfile").read_text()

        self.assertIn("HOME=/home/cloudagent", dockerfile)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", dockerfile)
        self.assertIn("addgroup -S -g 10001 cloudagent", dockerfile)
        self.assertIn("adduser -S -D -u 10001", dockerfile)
        self.assertIn("chown -R cloudagent:cloudagent /home/cloudagent /app", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)

    def test_v2_long_running_workloads_have_resource_requests_and_limits(self) -> None:
        expectations = {
            "k8s-v2/brain.yaml": ("brain", "cpu: 250m", "memory: 512Mi", 'cpu: "1"', "memory: 1Gi"),
            "k8s-v2/web.yaml": ("web", "cpu: 100m", "memory: 128Mi", "cpu: 500m", "memory: 512Mi"),
            "k8s-v2/session.yaml": ("session", "cpu: 100m", "memory: 128Mi", "cpu: 500m", "memory: 512Mi"),
            "k8s-v2/sandbox.yaml": ("sandbox", "cpu: 100m", "memory: 128Mi", "cpu: 500m", "memory: 512Mi"),
            "k8s-v2/github-broker.yaml": ("github-broker", "cpu: 100m", "memory: 128Mi", "cpu: 500m", "memory: 512Mi"),
            "k8s-v2/postgres.yaml": ("postgres", "cpu: 100m", "memory: 256Mi", "cpu: 500m", "memory: 512Mi"),
        }

        for manifest_path, expected in expectations.items():
            container_name, request_cpu, request_memory, limit_cpu, limit_memory = expected
            with self.subTest(manifest_path=manifest_path):
                container_block = _container_block(
                    Path(manifest_path).read_text(),
                    container_name,
                )
                self.assertIn("resources:", container_block)
                self.assertIn("requests:", container_block)
                self.assertIn(request_cpu, container_block)
                self.assertIn(request_memory, container_block)
                self.assertIn("limits:", container_block)
                self.assertIn(limit_cpu, container_block)
                self.assertIn(limit_memory, container_block)

    def test_v2_cloud_agent_workloads_have_non_root_security_context(self) -> None:
        expectations = {
            "k8s-v2/brain.yaml": "brain",
            "k8s-v2/web.yaml": "web",
            "k8s-v2/session.yaml": "session",
            "k8s-v2/sandbox.yaml": "sandbox",
            "k8s-v2/github-broker.yaml": "github-broker",
        }

        for manifest_path, container_name in expectations.items():
            with self.subTest(manifest_path=manifest_path):
                manifest = Path(manifest_path).read_text()
                container_block = _container_block(manifest, container_name)
                self.assertIn("runAsNonRoot: true", manifest)
                self.assertIn("runAsUser: 10001", manifest)
                self.assertIn("runAsGroup: 10001", manifest)
                self.assertIn("fsGroup: 10001", manifest)
                self.assertIn("seccompProfile:", manifest)
                self.assertIn("type: RuntimeDefault", manifest)
                self.assertIn("allowPrivilegeEscalation: false", container_block)
                self.assertIn("capabilities:", container_block)
                self.assertIn("- ALL", container_block)

    def test_v2_cloud_agent_workloads_do_not_use_root_claude_config(self) -> None:
        configmap = Path("k8s-v2/configmap.yaml").read_text()
        brain = Path("k8s-v2/brain.yaml").read_text()

        self.assertIn("CLAUDE_CONFIG_DIR: /home/cloudagent/.claude", configmap)
        self.assertIn("value: /home/cloudagent/.claude/brain/$(POD_NAME)", brain)
        self.assertIn("mountPath: /home/cloudagent/.claude", brain)
        self.assertNotIn("/root/.claude", configmap)
        self.assertNotIn("/root/.claude", brain)

    def test_v2_non_kubernetes_client_workloads_disable_service_account_token(self) -> None:
        for manifest_path in [
            "k8s-v2/brain.yaml",
            "k8s-v2/web.yaml",
            "k8s-v2/session.yaml",
            "k8s-v2/github-broker.yaml",
        ]:
            with self.subTest(manifest_path=manifest_path):
                self.assertIn(
                    "automountServiceAccountToken: false",
                    Path(manifest_path).read_text(),
                )

    def test_v2_postgres_uses_persistent_volume_claim(self) -> None:
        postgres = Path("k8s-v2/postgres.yaml").read_text()

        self.assertIn("kind: PersistentVolumeClaim", postgres)
        self.assertIn("name: postgres-data", postgres)
        self.assertIn("persistentVolumeClaim:", postgres)
        self.assertIn("claimName: postgres-data", postgres)
        self.assertNotIn("emptyDir: {}", postgres)

    def test_v2_sandbox_manager_initializes_workspace_permissions(self) -> None:
        sandbox = Path("k8s-v2/sandbox.yaml").read_text()

        self.assertIn("initContainers:", sandbox)
        self.assertIn("name: sandbox-workspace-permissions", sandbox)
        self.assertIn("mkdir -p /sandboxes/users", sandbox)
        self.assertIn("chown -R 10001:10001 /sandboxes", sandbox)
        self.assertIn("chmod -R g+rwX /sandboxes", sandbox)
        init_container_block = _container_block(sandbox, "sandbox-workspace-permissions")
        self.assertIn("runAsNonRoot: false", init_container_block)
        self.assertIn("runAsUser: 0", init_container_block)
        self.assertIn("allowPrivilegeEscalation: false", init_container_block)
        self.assertIn("mountPath: /sandboxes", init_container_block)

    def test_v2_sandbox_config_declares_runtime_policy_defaults(self) -> None:
        configmap = Path("k8s-v2/configmap.yaml").read_text()

        self.assertIn("SANDBOX_EPHEMERAL_STORAGE_REQUEST: 128Mi", configmap)
        self.assertIn("SANDBOX_EPHEMERAL_STORAGE_LIMIT: 1Gi", configmap)
        self.assertIn("SANDBOX_NETWORK_POLICY_NAME: sandbox-tool-default-deny", configmap)
        self.assertIn(
            "SANDBOX_SESSION_NETWORK_POLICY_NAME: sandbox-session-default-deny",
            configmap,
        )
        self.assertIn("SANDBOX_EGRESS_POLICY: default-deny", configmap)
        self.assertIn('SANDBOX_SESSION_TIMEOUT_SECONDS: "900"', configmap)
        self.assertIn('SANDBOX_TOOL_OUTPUT_BYTES_LIMIT: "4000"', configmap)
        self.assertIn('SANDBOX_RUNTIME_LOG_BYTES_LIMIT: "65536"', configmap)
        self.assertIn('SANDBOX_WORKSPACE_BYTES_LIMIT: "104857600"', configmap)
        self.assertIn('SANDBOX_WORKSPACE_FILE_LIMIT: "10000"', configmap)

    def test_v2_sandbox_tool_network_policy_defaults_to_deny(self) -> None:
        network_policy = Path("k8s-v2/network-policy.yaml").read_text()

        self.assertIn("kind: NetworkPolicy", network_policy)
        self.assertIn("name: sandbox-tool-default-deny", network_policy)
        self.assertIn("namespace: cloud-agent-poc-v2", network_policy)
        self.assertIn("podSelector:", network_policy)
        self.assertIn("app: cloud-agent-sandbox-tool", network_policy)
        self.assertIn("policyTypes:", network_policy)
        self.assertIn("- Ingress", network_policy)
        self.assertIn("- Egress", network_policy)
        self.assertNotIn("ipBlock:", network_policy)
        self.assertNotIn("namespaceSelector:", network_policy)
        self.assertNotIn("podSelector: {}", network_policy)

    def test_v2_task_sandbox_session_network_policy_allows_only_manager_ingress(self) -> None:
        network_policy = Path("k8s-v2/network-policy.yaml").read_text()

        self.assertIn("name: sandbox-session-default-deny", network_policy)
        self.assertIn("app: cloud-agent-task-sandbox", network_policy)
        self.assertIn("app: cloud-agent-sandbox", network_policy)
        self.assertIn("port: 8080", network_policy)


def _container_block(manifest: str, container_name: str) -> str:
    marker = f"\n        - name: {container_name}\n"
    start = manifest.index(marker) + 1
    next_container = manifest.find("\n        - name:", start + len(marker))
    if next_container == -1:
        return manifest[start:]
    return manifest[start:next_container]


if __name__ == "__main__":
    unittest.main()
