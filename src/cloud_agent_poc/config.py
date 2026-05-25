from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_layer_url: str
    sandbox_layer_url: str
    github_broker_url: str
    github_repo_url: str
    github_source_branch: str
    github_target_branch: str
    github_token: str | None
    workspace_root: Path
    claude_config_dir: Path
    claude_model: str | None
    git_author_name: str
    git_author_email: str
    sandbox_execution_mode: str
    sandbox_runtime_image: str
    sandbox_tool_timeout_seconds: int
    sandbox_workspace_claim: str
    sandbox_ephemeral_storage_request: str = "128Mi"
    sandbox_ephemeral_storage_limit: str = "1Gi"
    sandbox_network_policy_name: str = "sandbox-tool-default-deny"
    sandbox_session_network_policy_name: str = "sandbox-session-default-deny"
    sandbox_egress_policy: str = "default-deny"
    sandbox_session_timeout_seconds: int = 900
    sandbox_tool_output_bytes_limit: int = 4000
    sandbox_runtime_log_bytes_limit: int = 65536
    sandbox_workspace_bytes_limit: int = 104_857_600
    sandbox_workspace_file_limit: int = 10_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/ecsma_poc",
            ),
            session_layer_url=os.getenv(
                "SESSION_LAYER_URL",
                "http://localhost:8002",
            ).rstrip("/"),
            sandbox_layer_url=os.getenv(
                "SANDBOX_LAYER_URL",
                "http://localhost:8003",
            ).rstrip("/"),
            github_broker_url=os.getenv(
                "GITHUB_BROKER_URL",
                "http://localhost:8004",
            ).rstrip("/"),
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
            claude_config_dir=Path(
                os.getenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
            ),
            claude_model=os.getenv("CLAUDE_MODEL"),
            git_author_name=os.getenv("GIT_AUTHOR_NAME", "Cloud Agent PoC"),
            git_author_email=os.getenv(
                "GIT_AUTHOR_EMAIL",
                "cloud-agent-poc@example.local",
            ),
            sandbox_execution_mode=os.getenv("SANDBOX_EXECUTION_MODE", "direct"),
            sandbox_runtime_image=os.getenv(
                "SANDBOX_RUNTIME_IMAGE",
                "cloud-agent-poc:local",
            ),
            sandbox_tool_timeout_seconds=int(
                os.getenv("SANDBOX_TOOL_TIMEOUT_SECONDS", "180")
            ),
            sandbox_workspace_claim=os.getenv(
                "SANDBOX_WORKSPACE_CLAIM",
                "sandbox-workspaces",
            ),
            sandbox_ephemeral_storage_request=os.getenv(
                "SANDBOX_EPHEMERAL_STORAGE_REQUEST",
                "128Mi",
            ),
            sandbox_ephemeral_storage_limit=os.getenv(
                "SANDBOX_EPHEMERAL_STORAGE_LIMIT",
                "1Gi",
            ),
            sandbox_network_policy_name=os.getenv(
                "SANDBOX_NETWORK_POLICY_NAME",
                "sandbox-tool-default-deny",
            ),
            sandbox_session_network_policy_name=os.getenv(
                "SANDBOX_SESSION_NETWORK_POLICY_NAME",
                "sandbox-session-default-deny",
            ),
            sandbox_egress_policy=os.getenv(
                "SANDBOX_EGRESS_POLICY",
                "default-deny",
            ),
            sandbox_session_timeout_seconds=int(
                os.getenv("SANDBOX_SESSION_TIMEOUT_SECONDS", "900")
            ),
            sandbox_tool_output_bytes_limit=int(
                os.getenv("SANDBOX_TOOL_OUTPUT_BYTES_LIMIT", "4000")
            ),
            sandbox_runtime_log_bytes_limit=int(
                os.getenv("SANDBOX_RUNTIME_LOG_BYTES_LIMIT", "65536")
            ),
            sandbox_workspace_bytes_limit=int(
                os.getenv("SANDBOX_WORKSPACE_BYTES_LIMIT", "104857600")
            ),
            sandbox_workspace_file_limit=int(
                os.getenv("SANDBOX_WORKSPACE_FILE_LIMIT", "10000")
            ),
        )
