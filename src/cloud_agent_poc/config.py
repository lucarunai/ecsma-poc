from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    github_repo_url: str
    github_source_branch: str
    github_target_branch: str
    github_token: str | None
    workspace_root: Path
    claude_model: str | None
    git_author_name: str
    git_author_email: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/ecsma_poc",
            ),
            github_repo_url=os.getenv(
                "GITHUB_REPO_URL",
                "https://github.com/lucarunai/demo.git",
            ),
            github_source_branch=os.getenv("GITHUB_SOURCE_BRANCH", "test"),
            github_target_branch=os.getenv("GITHUB_TARGET_BRANCH", "main"),
            github_token=os.getenv("GITHUB_TOKEN"),
            workspace_root=Path(
                os.getenv("WORKSPACE_ROOT", "/private/tmp/ecsma-poc-workspaces")
            ),
            claude_model=os.getenv("CLAUDE_MODEL"),
            git_author_name=os.getenv("GIT_AUTHOR_NAME", "Cloud Agent PoC"),
            git_author_email=os.getenv(
                "GIT_AUTHOR_EMAIL",
                "cloud-agent-poc@example.local",
            ),
        )
