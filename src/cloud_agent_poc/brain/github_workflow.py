from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from ..config import Settings
from ..domain import Workspace
from .processes import CommandResult, run_command


class GitHubWorkflowError(RuntimeError):
    pass


def github_slug_from_url(repo_url: str) -> str:
    patterns = [
        r"^https://github\.com/(?P<slug>[^/]+/[^/.]+)(?:\.git)?/?$",
        r"^git@github\.com:(?P<slug>[^/]+/[^/.]+)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<slug>[^/]+/[^/.]+)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, repo_url)
        if match:
            return match.group("slug")
    raise GitHubWorkflowError(f"Unsupported GitHub repository URL: {repo_url}")


class GitHubWorkflowService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def create_workspace(self, run_id: str) -> Workspace:
        self.settings.workspace_root.mkdir(parents=True, exist_ok=True)
        workspace_path = self.settings.workspace_root / run_id
        workspace_path.mkdir(parents=True, exist_ok=False)
        return Workspace(path=str(workspace_path))

    async def clone_repository(
        self,
        *,
        workspace: Workspace,
        repository_url: str,
        source_branch: str,
    ) -> CommandResult:
        github_slug_from_url(repository_url)
        workspace_path = Path(workspace.path)
        if any(workspace_path.iterdir()):
            raise GitHubWorkflowError(
                "Workspace is not empty. Clone must run before repository edits."
            )
        clone_command = ["git"]
        auth_header = self._github_auth_header()
        if auth_header:
            clone_command.extend(["-c", auth_header])
        clone_command.extend(
            [
                "clone",
                "--branch",
                source_branch,
                "--single-branch",
                repository_url,
                ".",
            ]
        )
        clone = await run_command(clone_command, cwd=workspace_path)
        self._ensure_success(clone, "git clone")
        return clone

    async def create_branch(
        self,
        *,
        workspace: Workspace,
        branch_name: str,
    ) -> CommandResult:
        self._validate_branch_name(branch_name)
        result = await run_command(
            ["git", "checkout", "-b", branch_name],
            cwd=Path(workspace.path),
        )
        self._ensure_success(result, "git checkout")
        return result

    async def status(self, workspace: Workspace) -> CommandResult:
        result = await run_command(
            ["git", "status", "--short", "--branch"],
            cwd=Path(workspace.path),
        )
        self._ensure_success(result, "git status")
        return result

    async def diff(self, workspace: Workspace) -> CommandResult:
        result = await run_command(
            ["git", "diff", "--stat"],
            cwd=Path(workspace.path),
        )
        self._ensure_success(result, "git diff")
        return result

    async def run_unittest(
        self,
        *,
        workspace: Workspace,
        start_directory: str,
    ) -> CommandResult:
        self._validate_relative_path(start_directory)
        result = await run_command(
            [
                "python3",
                "-m",
                "unittest",
                "discover",
                "-v",
                "-s",
                start_directory,
            ],
            cwd=Path(workspace.path),
        )
        return result

    async def commit_changes(
        self,
        *,
        workspace: Workspace,
        commit_message: str,
    ) -> CommandResult:
        workspace_path = Path(workspace.path)
        await self._git_config(workspace_path)
        add = await run_command(["git", "add", "-A"], cwd=workspace_path)
        self._ensure_success(add, "git add")

        diff = await run_command(
            ["git", "diff", "--cached", "--quiet"],
            cwd=workspace_path,
        )
        if diff.returncode == 0:
            raise GitHubWorkflowError("Agent produced no staged changes to publish.")
        if diff.returncode not in {0, 1}:
            self._ensure_success(diff, "git diff")

        commit = await run_command(
            ["git", "commit", "-m", commit_message.strip()[:200]],
            cwd=workspace_path,
        )
        self._ensure_success(commit, "git commit")
        return commit

    async def push_current_branch(self, workspace: Workspace) -> CommandResult:
        workspace_path = Path(workspace.path)
        branch = await self.current_branch(workspace)
        push = await self._push(workspace_path, branch)
        self._ensure_success(push, "git push")
        return push

    async def current_branch(self, workspace: Workspace) -> str:
        branch = await run_command(
            ["git", "branch", "--show-current"],
            cwd=Path(workspace.path),
        )
        self._ensure_success(branch, "git branch")
        current_branch = branch.stdout.strip()
        if not current_branch:
            raise GitHubWorkflowError("Current Git branch is empty.")
        return current_branch

    async def current_origin(self, workspace: Workspace) -> str:
        origin = await run_command(
            ["git", "remote", "get-url", "origin"],
            cwd=Path(workspace.path),
        )
        self._ensure_success(origin, "git remote get-url origin")
        return origin.stdout.strip()

    async def _git_config(self, workspace_path: Path) -> None:
        name = await run_command(
            ["git", "config", "user.name", self.settings.git_author_name],
            cwd=workspace_path,
        )
        self._ensure_success(name, "git config user.name")
        email = await run_command(
            ["git", "config", "user.email", self.settings.git_author_email],
            cwd=workspace_path,
        )
        self._ensure_success(email, "git config user.email")

    async def create_pull_request(
        self,
        *,
        workspace: Workspace,
        target_branch: str,
        title: str,
        body: str,
    ) -> dict[str, int | str]:
        if not self.settings.github_token:
            raise GitHubWorkflowError("GITHUB_TOKEN is required to create a PR.")

        slug = github_slug_from_url(await self.current_origin(workspace))
        work_branch = await self.current_branch(workspace)
        payload = json.dumps(
            {
                "title": title.strip()[:240],
                "head": work_branch,
                "base": target_branch.strip(),
                "body": body.strip(),
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{slug}/pulls",
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.settings.github_token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise GitHubWorkflowError(
                f"GitHub PR creation failed with HTTP {exc.code}: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubWorkflowError(f"GitHub PR creation failed: {exc}") from exc
        return {"url": body["html_url"], "number": body["number"]}

    async def _push(self, workspace_path: Path, branch: str) -> CommandResult:
        if not self.settings.github_token:
            raise GitHubWorkflowError("GITHUB_TOKEN is required to push a branch.")
        return await run_command(
            [
                "git",
                "-c",
                self._github_auth_header(required=True),
                "push",
                "-u",
                "origin",
                branch,
            ],
            cwd=workspace_path,
        )

    def _github_auth_header(self, *, required: bool = False) -> str | None:
        if not self.settings.github_token:
            if required:
                raise GitHubWorkflowError("GITHUB_TOKEN is required for GitHub auth.")
            return None
        auth = base64.b64encode(
            f"x-access-token:{self.settings.github_token}".encode("utf-8")
        ).decode("ascii")
        return f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth}"

    @staticmethod
    def _ensure_success(result: CommandResult, operation: str) -> None:
        if result.returncode == 0:
            return
        raise GitHubWorkflowError(
            f"{operation} failed with code {result.returncode}: {result.summary}"
        )

    @staticmethod
    def _validate_branch_name(branch_name: str) -> None:
        if not re.match(r"^[A-Za-z0-9._/-]{1,180}$", branch_name):
            raise GitHubWorkflowError("Branch name contains unsupported characters.")
        if branch_name.startswith(("-", "/")) or ".." in branch_name:
            raise GitHubWorkflowError("Branch name is not safe for this PoC.")

    @staticmethod
    def _validate_relative_path(path: str) -> None:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise GitHubWorkflowError("Test path must stay inside the workspace.")
