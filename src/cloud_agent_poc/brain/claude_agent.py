from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..config import Settings
from ..domain import AgentTaskResult, TaskRecord, Workspace
from .sdk_tools import CodingToolServerFactory

EmitAgentEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
RecordClaudeSession = Callable[[str], Awaitable[None]]


class ClaudeCodingAgent:
    CODING_MCP_TOOLS = [
        "mcp__coding__read_workspace_file",
        "mcp__coding__write_workspace_file",
        "mcp__coding__edit_workspace_file",
        "mcp__coding__glob_workspace_files",
        "mcp__coding__grep_workspace_files",
        "mcp__coding__clone_github_repository",
        "mcp__coding__checkout_git_branch",
        "mcp__coding__create_git_branch",
        "mcp__coding__git_status",
        "mcp__coding__git_diff_stat",
        "mcp__coding__run_python_unittest",
        "mcp__coding__commit_git_changes",
        "mcp__coding__push_current_git_branch",
        "mcp__coding__create_github_pull_request",
    ]
    DENIED_TASK_TOOLS = [
        "Bash",
        "ToolSearch",
        "Task",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskUpdate",
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
    ]

    def __init__(
        self,
        settings: Settings,
        tool_servers: CodingToolServerFactory,
    ) -> None:
        self.settings = settings
        self.tool_servers = tool_servers

    async def implement(
        self,
        *,
        prompt: str,
        task: TaskRecord,
        task_attempt_id: str,
        resume_session_id: str | None = None,
        recovery_context: str | None = None,
        emit: EmitAgentEvent,
        record_claude_session: RecordClaudeSession,
    ) -> AgentTaskResult:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                SystemMessage,
                TextBlock,
                ToolUseBlock,
                query,
            )
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Run `uv sync` first."
            ) from exc

        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=self.CODING_MCP_TOOLS,
            disallowed_tools=self.DENIED_TASK_TOOLS,
            mcp_servers={
                "coding": self.tool_servers.create(task, task_attempt_id, emit)
            },
            strict_mcp_config=True,
            include_partial_messages=False,
            model=self.settings.claude_model,
            output_format={
                "type": "json_schema",
                "schema": self._task_result_schema(),
            },
            permission_mode="dontAsk",
            setting_sources=[],
            skills=[],
            resume=resume_session_id,
            max_turns=24,
        )
        final_result: AgentTaskResult | None = None
        claude_session_id = resume_session_id
        async for message in query(
            prompt=self._implementation_prompt(prompt, task, recovery_context),
            options=options,
        ):
            await emit(
                "sdk.message",
                {
                    "message_type": type(message).__name__,
                },
            )
            if (
                isinstance(message, SystemMessage)
                and message.subtype == "init"
                and isinstance(message.data, dict)
                and message.data.get("session_id")
            ):
                claude_session_id = str(message.data["session_id"])
                await record_claude_session(claude_session_id)
                await emit(
                    "agent.session.started",
                    {
                        "task_attempt_id": task_attempt_id,
                        "claude_session_id": claude_session_id,
                    },
                )
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        await emit("agent.message", {"text": block.text})
                    if isinstance(block, ToolUseBlock):
                        await emit(
                            "tool.started",
                            {
                                "tool_name": block.name,
                                "input": self._safe_tool_input(block.input),
                            },
                        )
            if isinstance(message, ResultMessage):
                claude_session_id = message.session_id or claude_session_id
                structured_output = getattr(message, "structured_output", None)
                if structured_output:
                    final_result = self.parse_task_result(
                        structured_output,
                        claude_session_id=claude_session_id,
                    )
                    await emit(
                        "agent.result",
                        {
                            "status": final_result.status,
                            "summary": final_result.summary,
                            "claude_session_id": final_result.claude_session_id,
                        },
                    )
        if final_result is None:
            raise RuntimeError("Task agent did not return a structured task result.")
        return final_result

    def find_transcript_path(
        self,
        *,
        workspace: Workspace,
        claude_session_id: str,
    ) -> Path | None:
        exact_path = (
            self.settings.claude_config_dir
            / "projects"
            / self._project_key(workspace.path)
            / f"{claude_session_id}.jsonl"
        )
        if exact_path.exists():
            return exact_path
        project_root = self.settings.claude_config_dir / "projects"
        if not project_root.exists():
            return None
        matches = sorted(project_root.glob(f"**/{claude_session_id}.jsonl"))
        return matches[-1] if matches else None

    @staticmethod
    def parse_task_result(
        structured_output: dict[str, Any],
        *,
        claude_session_id: str | None = None,
    ) -> AgentTaskResult:
        status = str(structured_output.get("status", "")).strip().lower()
        summary = str(structured_output.get("summary", "")).strip()
        if status not in {"completed", "blocked", "failed"}:
            raise ValueError("Task agent returned an invalid task status.")
        if not summary:
            raise ValueError("Task agent returned an empty task summary.")
        return AgentTaskResult(
            status=status,
            summary=summary,
            claude_session_id=claude_session_id,
        )

    @staticmethod
    def _task_result_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["completed", "blocked", "failed"],
                },
                "summary": {"type": "string"},
            },
            "required": ["status", "summary"],
        }

    @staticmethod
    def _safe_tool_input(tool_input: Any) -> Any:
        if isinstance(tool_input, dict):
            return {
                key: value
                for key, value in tool_input.items()
                if key.lower() not in {"token", "authorization", "password"}
            }
        return tool_input

    @staticmethod
    def _project_key(workspace_path: str) -> str:
        return workspace_path.replace("/", "-")

    @staticmethod
    def _implementation_prompt(
        prompt: str,
        task: TaskRecord,
        recovery_context: str | None = None,
    ) -> str:
        recovery = ""
        if recovery_context:
            recovery = f"""

Recovery context:
{recovery_context}

Resume the current task from durable workspace state. Inspect current state
before repeating a side-effecting operation that may have partially completed.
"""
        return f"""
You are the implementation agent for a controlled Python coding PoC.

User request:
{prompt.strip()}

Current planned task:
{task.seq}. {task.title}

Task detail:
{task.description}

Task acceptance criteria:
{ClaudeCodingAgent._criteria_text(task)}

Work only inside the current run workspace.

For this V0 workflow:
- Complete the current planned task while keeping the whole request coherent.
- Use the coding MCP tools for every sandbox workspace action: repository
  cloning, file inspection, file edits, file search, Git branch management,
  Python unittest execution, commits, pushes, and pull requests.
- Add or update standard-library unittest coverage when this task needs it.
- Prefer small files and keep dependencies out unless the repository already
  requires them.
- Inspect the repository before editing.
- Do not attempt local Brain filesystem actions. Use sandbox workspace tools
  for code edits and keep GitHub credentials out of files.
- Return status `completed` only when this task's acceptance criteria are met.
- Return status `blocked` when a missing prerequisite, missing access, or a
  failed backend tool prevents this task from proceeding. Do not keep retrying
  the same blocked operation or work around failed repository setup by writing
  files outside the requested checkout.
- Return status `failed` when the task was attempted and cannot be completed
  safely without a platform fix.
- Finish with a concise summary of changed files and tests run.
{recovery}
""".strip()

    @staticmethod
    def _criteria_text(task: TaskRecord) -> str:
        if not task.acceptance_criteria:
            return "- Complete the task description."
        return "\n".join(f"- {criterion}" for criterion in task.acceptance_criteria)
