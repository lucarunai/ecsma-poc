import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cloud_agent_poc.brain.github_workflow import (
    GitHubWorkflowError,
    GitHubWorkflowService,
    github_slug_from_url,
)
from cloud_agent_poc.brain.processes import CommandResult
from cloud_agent_poc.config import Settings
from cloud_agent_poc.domain import Workspace


class GitHubSlugTests(unittest.TestCase):
    def test_parses_https_repo_url(self) -> None:
        self.assertEqual(
            github_slug_from_url("https://github.com/lucarunai/demo.git"),
            "lucarunai/demo",
        )

    def test_parses_ssh_repo_url(self) -> None:
        self.assertEqual(
            github_slug_from_url("git@github.com:lucarunai/demo.git"),
            "lucarunai/demo",
        )

    def test_rejects_non_github_repo_url(self) -> None:
        with self.assertRaises(GitHubWorkflowError):
            github_slug_from_url("https://example.com/lucarunai/demo.git")


class GitHubWorkflowCloneTests(unittest.TestCase):
    def test_clone_uses_backend_auth_header_when_token_is_available(self) -> None:
        command = self._clone_command(github_token="secret-token")

        self.assertEqual(command[:2], ["git", "-c"])
        self.assertIn("http.https://github.com/.extraheader=AUTHORIZATION: basic ", command[2])
        self.assertNotIn("secret-token", " ".join(command))
        self.assertEqual(command[3:7], ["clone", "--branch", "test", "--single-branch"])

    def test_clone_keeps_public_clone_command_when_token_is_missing(self) -> None:
        command = self._clone_command(github_token=None)

        self.assertEqual(command[:5], ["git", "clone", "--branch", "test", "--single-branch"])

    def _clone_command(self, *, github_token: str | None) -> list[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = GitHubWorkflowService(self._settings(Path(temp_dir), github_token))
            result = CommandResult(["git"], 0, "cloned", "")
            with patch(
                "cloud_agent_poc.brain.github_workflow.run_command",
                new=AsyncMock(return_value=result),
            ) as run_command:
                asyncio.run(
                    service.clone_repository(
                        workspace=Workspace(path=temp_dir),
                        repository_url="https://github.com/lucarunai/demo.git",
                        source_branch="test",
                    )
                )
            return run_command.await_args.args[0]

    @staticmethod
    def _settings(workspace_root: Path, github_token: str | None) -> Settings:
        return Settings(
            database_url="postgresql://unused",
            session_layer_url="http://unused",
            sandbox_layer_url="http://sandbox",
            github_broker_url="http://github-broker",
            github_repo_url="https://github.com/lucarunai/demo.git",
            github_source_branch="test",
            github_target_branch="main",
            github_token=github_token,
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


class GitHubWorkflowCheckoutTests(unittest.TestCase):
    def test_checkout_fetches_existing_branch_before_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = GitHubWorkflowService(
                GitHubWorkflowCloneTests._settings(Path(temp_dir), "secret-token")
            )
            with patch(
                "cloud_agent_poc.brain.github_workflow.run_command",
                new=AsyncMock(
                    side_effect=[
                        CommandResult(["git"], 0, "fetched", ""),
                        CommandResult(["git"], 0, "checked out", ""),
                    ]
                ),
            ) as run_command:
                asyncio.run(
                    service.checkout_branch(
                        workspace=Workspace(path=temp_dir),
                        branch_name="test",
                    )
                )

        fetch_command = run_command.await_args_list[0].args[0]
        checkout_command = run_command.await_args_list[1].args[0]
        self.assertEqual(fetch_command[:2], ["git", "-c"])
        self.assertIn("extraheader=AUTHORIZATION: basic ", fetch_command[2])
        self.assertEqual(
            fetch_command[3:],
            ["fetch", "origin", "refs/heads/test:refs/remotes/origin/test"],
        )
        self.assertEqual(
            checkout_command,
            ["git", "checkout", "-B", "test", "origin/test"],
        )


if __name__ == "__main__":
    unittest.main()
