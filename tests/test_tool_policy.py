import unittest

from cloud_agent_poc.tool_policy import resolve_runtime_policy


class RuntimePolicyTests(unittest.TestCase):
    def test_trusted_github_tool_is_broker_only(self) -> None:
        decision = resolve_runtime_policy(
            "push_current_git_branch",
            {},
            task_sandbox_available=True,
        )

        self.assertEqual(decision.policy, "broker_only")
        self.assertIn("broker", decision.reason)

    def test_workspace_tool_defaults_to_task_attempt_sandbox(self) -> None:
        decision = resolve_runtime_policy(
            "write_workspace_file",
            {"path": "hello.py"},
            task_sandbox_available=True,
        )

        self.assertEqual(decision.policy, "task_attempt_sandbox")

    def test_workspace_tool_falls_back_when_task_sandbox_is_unavailable(self) -> None:
        decision = resolve_runtime_policy(
            "write_workspace_file",
            {"path": "hello.py"},
            task_sandbox_available=False,
        )

        self.assertEqual(decision.policy, "tool_execution_sandbox")

    def test_agent_can_only_request_stronger_clean_room_isolation(self) -> None:
        decision = resolve_runtime_policy(
            "write_workspace_file",
            {"path": "hello.py", "clean_room": True},
            task_sandbox_available=True,
        )
        broker_decision = resolve_runtime_policy(
            "clone_github_repository",
            {"clean_room": True},
            task_sandbox_available=True,
        )

        self.assertEqual(decision.policy, "tool_execution_sandbox")
        self.assertEqual(broker_decision.policy, "broker_only")


if __name__ == "__main__":
    unittest.main()
