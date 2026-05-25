from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TRUSTED_GITHUB_TOOLS = {
    "clone_github_repository",
    "checkout_git_branch",
    "push_current_git_branch",
    "create_github_pull_request",
}

HUMAN_APPROVAL_TOOLS = {
    "push_current_git_branch",
    "create_github_pull_request",
}

TOOL_APPROVAL_REASONS = {
    "push_current_git_branch": "Push publishes local branch state to the remote GitHub repository.",
    "create_github_pull_request": "Pull request creation publishes a review artifact on GitHub.",
}

RuntimePolicy = Literal[
    "broker_only",
    "task_attempt_sandbox",
    "tool_execution_sandbox",
]


@dataclass(frozen=True)
class RuntimePolicyDecision:
    policy: RuntimePolicy
    reason: str


def requires_human_approval(tool_name: str) -> bool:
    return tool_name in HUMAN_APPROVAL_TOOLS


def approval_reason_for_tool(tool_name: str) -> str:
    return TOOL_APPROVAL_REASONS.get(
        tool_name,
        "This tool has an external side effect and requires human approval.",
    )


def resolve_runtime_policy(
    tool_name: str,
    tool_input: dict[str, object] | None = None,
    *,
    task_sandbox_available: bool = True,
) -> RuntimePolicyDecision:
    """Decide the runtime boundary for a tool call.

    The harness owns this decision. Agent-provided input can only request a
    stronger clean-room boundary; it cannot downgrade broker or sandbox rules.
    """

    if tool_name in TRUSTED_GITHUB_TOOLS:
        return RuntimePolicyDecision(
            policy="broker_only",
            reason="trusted_github_tools_execute_in_secret_holding_broker",
        )
    if _requests_clean_room(tool_input or {}):
        return RuntimePolicyDecision(
            policy="tool_execution_sandbox",
            reason="tool_input_requested_clean_room_isolation",
        )
    if not task_sandbox_available:
        return RuntimePolicyDecision(
            policy="tool_execution_sandbox",
            reason="task_attempt_sandbox_unavailable",
        )
    return RuntimePolicyDecision(
        policy="task_attempt_sandbox",
        reason="default_workspace_tool_policy",
    )


def _requests_clean_room(tool_input: dict[str, object]) -> bool:
    if tool_input.get("clean_room") is True:
        return True
    if tool_input.get("force_clean_room") is True:
        return True
    return tool_input.get("sandbox_policy") == "tool_execution_sandbox"
