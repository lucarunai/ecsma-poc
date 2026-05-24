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


def requires_human_approval(tool_name: str) -> bool:
    return tool_name in HUMAN_APPROVAL_TOOLS


def approval_reason_for_tool(tool_name: str) -> str:
    return TOOL_APPROVAL_REASONS.get(
        tool_name,
        "This tool has an external side effect and requires human approval.",
    )
