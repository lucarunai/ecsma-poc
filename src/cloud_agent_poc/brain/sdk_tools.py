from __future__ import annotations

from typing import Any

from ..domain import Workspace
from .github_workflow import GitHubWorkflowError, GitHubWorkflowService


class CodingToolServerFactory:
    def __init__(self, github: GitHubWorkflowService) -> None:
        self.github = github

    def create(self, workspace: Workspace) -> Any:
        try:
            from claude_agent_sdk import create_sdk_mcp_server, tool
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Run `uv sync` first."
            ) from exc

        @tool(
            "clone_github_repository",
            "Clone a GitHub repository branch into the empty run workspace. "
            "Use before repository file edits.",
            {"repository_url": str, "source_branch": str},
        )
        async def clone_github_repository(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.clone_repository(
                    workspace=workspace,
                    repository_url=args["repository_url"],
                    source_branch=args["source_branch"],
                )
                return self._result(
                    "Repository cloned.",
                    {
                        "repository_url": args["repository_url"],
                        "source_branch": args["source_branch"],
                        "output": result.summary,
                    },
                )
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool(
            "create_git_branch",
            "Create and check out a Git branch for repository changes.",
            {"branch_name": str},
        )
        async def create_git_branch(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.create_branch(
                    workspace=workspace,
                    branch_name=args["branch_name"],
                )
                return self._result(
                    "Git branch created.",
                    {"branch_name": args["branch_name"], "output": result.summary},
                )
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool("git_status", "Read the current Git branch and working tree status.", {})
        async def git_status(_: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.status(workspace)
                return self._result(result.summary, {"output": result.summary})
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool("git_diff_stat", "Read a summary of unstaged Git changes.", {})
        async def git_diff_stat(_: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.diff(workspace)
                return self._result(result.summary, {"output": result.summary})
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool(
            "run_python_unittest",
            "Run Python standard-library unittest discovery from a workspace "
            "directory such as `tests` or `.`.",
            {"start_directory": str},
        )
        async def run_python_unittest(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.run_unittest(
                    workspace=workspace,
                    start_directory=args["start_directory"],
                )
                payload = {
                    "command": result.command,
                    "returncode": result.returncode,
                    "output": result.summary,
                }
                if result.returncode != 0:
                    return self._error("Python unittest failed.", payload)
                return self._result("Python unittest passed.", payload)
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool(
            "commit_git_changes",
            "Stage all workspace changes and create a Git commit.",
            {"commit_message": str},
        )
        async def commit_git_changes(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.commit_changes(
                    workspace=workspace,
                    commit_message=args["commit_message"],
                )
                return self._result("Git commit created.", {"output": result.summary})
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool(
            "push_current_git_branch",
            "Push the current Git branch to its GitHub origin using backend credentials.",
            {},
        )
        async def push_current_git_branch(_: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self.github.push_current_branch(workspace)
                return self._result("Git branch pushed.", {"output": result.summary})
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        @tool(
            "create_github_pull_request",
            "Create a GitHub pull request from the current pushed branch.",
            {"target_branch": str, "title": str, "body": str},
        )
        async def create_github_pull_request(args: dict[str, Any]) -> dict[str, Any]:
            try:
                pull_request = await self.github.create_pull_request(
                    workspace=workspace,
                    target_branch=args["target_branch"],
                    title=args["title"],
                    body=args["body"],
                )
                return self._result(
                    f"Pull request created: {pull_request['url']}",
                    pull_request,
                )
            except GitHubWorkflowError as exc:
                return self._error(str(exc))

        return create_sdk_mcp_server(
            name="coding",
            version="0.1.0",
            tools=[
                clone_github_repository,
                create_git_branch,
                git_status,
                git_diff_stat,
                run_python_unittest,
                commit_git_changes,
                push_current_git_branch,
                create_github_pull_request,
            ],
        )

    @staticmethod
    def _result(text: str, structured_content: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured_content,
        }

    @classmethod
    def _error(
        cls,
        text: str,
        structured_content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = cls._result(text, structured_content or {"error": text})
        result["isError"] = True
        return result
