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


def _container_block(manifest: str, container_name: str) -> str:
    marker = f"        - name: {container_name}"
    start = manifest.index(marker)
    next_container = manifest.find("\n        - name:", start + len(marker))
    if next_container == -1:
        return manifest[start:]
    return manifest[start:next_container]


if __name__ == "__main__":
    unittest.main()
