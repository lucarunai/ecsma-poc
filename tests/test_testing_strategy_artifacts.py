import stat
import unittest
from pathlib import Path


class TestingStrategyArtifactTests(unittest.TestCase):
    def test_session_security_testing_strategy_document_exists(self) -> None:
        strategy = Path("upgrades/session-security-testing-strategy.zh.md").read_text()

        self.assertIn("Session Management 测试矩阵", strategy)
        self.assertIn("Security Management 测试矩阵", strategy)
        self.assertIn("K8s Runtime Smoke Tests", strategy)
        self.assertIn("Chaos / Recovery / Security Drills", strategy)
        self.assertIn("./scripts/k8s-smoke-v2.sh", strategy)
        self.assertIn("./scripts/k8s-security-verify-v2.sh", strategy)
        self.assertIn("./scripts/k8s-chaos-drill-v2.sh", strategy)

    def test_v2_runtime_verification_scripts_are_executable(self) -> None:
        for script_path in [
            Path("scripts/k8s-smoke-v2.sh"),
            Path("scripts/k8s-security-verify-v2.sh"),
            Path("scripts/k8s-chaos-drill-v2.sh"),
        ]:
            with self.subTest(script_path=str(script_path)):
                mode = script_path.stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, f"{script_path} is not executable")

    def test_v2_smoke_script_checks_workspace_and_core_services(self) -> None:
        script = Path("scripts/k8s-smoke-v2.sh").read_text()

        self.assertIn("cloud-agent-poc-v2", script)
        self.assertIn("cloud-agent-brain", script)
        self.assertIn("postgres-data", script)
        self.assertIn("sandbox-workspaces", script)
        self.assertIn("X-User-Id", script)
        self.assertIn("/internal/workspaces", script)
        self.assertIn("/sandboxes/users", script)

    def test_v2_security_script_checks_rbac_and_secret_boundaries(self) -> None:
        script = Path("scripts/k8s-security-verify-v2.sh").read_text()

        self.assertIn("auth can-i", script)
        self.assertIn("get secrets", script)
        self.assertIn("--subresource=exec", script)
        self.assertIn("networkpolicy sandbox-tool-default-deny", script)
        self.assertIn("cloud-agent-sandbox-tool", script)
        self.assertIn("GITHUB_TOKEN", script)
        self.assertIn("ANTHROPIC_API_KEY", script)
        self.assertIn("automountServiceAccountToken", script)
        self.assertIn("runAsNonRoot", script)
        self.assertIn("capabilities", script)
        self.assertIn("ephemeral-storage", script)

    def test_v2_chaos_script_covers_recovery_and_ownership_drills(self) -> None:
        script = Path("scripts/k8s-chaos-drill-v2.sh").read_text()

        self.assertIn("brain-crash", script)
        self.assertIn("sandbox-pod-crash", script)
        self.assertIn("ownership", script)
        self.assertIn("run.lease.expired", script)
        self.assertIn("claimed_by", script)
        self.assertIn("failed", script)
        self.assertIn("orphaned", script)
        self.assertIn("404", script)


if __name__ == "__main__":
    unittest.main()
