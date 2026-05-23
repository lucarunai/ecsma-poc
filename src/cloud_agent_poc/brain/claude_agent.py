from __future__ import annotations

import json
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
        handoffs: list[dict[str, Any]] | None = None,
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
            max_turns=24,
        )
        final_result: AgentTaskResult | None = None
        claude_session_id: str | None = None
        last_api_error_text: str | None = None
        async for message in query(
            prompt=self._implementation_prompt(
                prompt,
                task,
                handoffs or [],
                recovery_context,
            ),
            options=options,
        ):
            await emit(
                "sdk.message",
                self._sdk_message_payload(message),
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
                        if block.text.strip().lower().startswith("api error:"):
                            last_api_error_text = block.text.strip()
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
                if message.is_error:
                    raise RuntimeError(
                        self._result_error_text(message, last_api_error_text)
                    )
                structured_output = getattr(message, "structured_output", None)
                if structured_output:
                    final_result = self.parse_task_result(
                        structured_output,
                        task=task,
                        claude_session_id=claude_session_id,
                    )
                    await emit(
                        "agent.result",
                        {
                            "status": final_result.status,
                            "summary": final_result.summary,
                            "criteria_results": final_result.criteria_results,
                            "verification": final_result.verification,
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
        task: TaskRecord | None = None,
        claude_session_id: str | None = None,
    ) -> AgentTaskResult:
        status = str(structured_output.get("status", "")).strip().lower()
        summary = str(structured_output.get("summary", "")).strip()
        if status not in {"completed", "blocked", "failed"}:
            raise ValueError("Task agent returned an invalid task status.")
        if not summary:
            raise ValueError("Task agent returned an empty task summary.")
        criteria_results = ClaudeCodingAgent._parse_criteria_results(
            structured_output.get("criteria_results"),
            task.acceptance_criteria if task is not None else None,
        )
        if status == "completed" and any(
            result["status"] != "passing" for result in criteria_results
        ):
            raise ValueError(
                "Task agent marked completed before all acceptance criteria passed."
            )
        verification = ClaudeCodingAgent._parse_verification(
            structured_output.get("verification"),
        )
        return AgentTaskResult(
            status=status,
            summary=summary,
            claude_session_id=claude_session_id,
            criteria_results=criteria_results,
            verification=verification,
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
                "criteria_results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "criterion": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "passing",
                                    "failing",
                                    "not_verified",
                                    "blocked",
                                ],
                            },
                            "evidence": {"type": "string"},
                        },
                        "required": ["criterion", "status", "evidence"],
                    },
                },
                "verification": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "commands_run": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "tool": {"type": "string"},
                                    "command": {"type": "string"},
                                    "returncode": {"type": "integer"},
                                    "result": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "output_summary": {"type": "string"},
                                    "url": {"type": "string"},
                                },
                                "required": ["tool", "result"],
                            },
                        },
                        "changed_files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "path": {"type": "string"},
                                    "change": {"type": "string"},
                                    "summary": {"type": "string"},
                                },
                                "required": ["path"],
                            },
                        },
                        "published_artifacts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "type": {"type": "string"},
                                    "url": {"type": "string"},
                                    "target_branch": {"type": "string"},
                                    "source_branch": {"type": "string"},
                                },
                                "required": ["type"],
                            },
                        },
                        "notes": {"type": "string"},
                    },
                },
            },
            "required": ["status", "summary", "criteria_results", "verification"],
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
    def _parse_criteria_results(
        raw_results: Any,
        acceptance_criteria: list[str] | None,
    ) -> list[dict[str, str]]:
        if raw_results is None and not acceptance_criteria:
            return []
        if not isinstance(raw_results, list):
            raise ValueError("Task agent did not return criteria results.")
        results: list[dict[str, str]] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                raise ValueError("Task agent returned an invalid criteria result.")
            criterion = str(raw_result.get("criterion", "")).strip()
            result_status = str(raw_result.get("status", "")).strip().lower()
            evidence = str(raw_result.get("evidence", "")).strip()
            if result_status not in {"passing", "failing", "not_verified", "blocked"}:
                raise ValueError("Task agent returned an invalid criterion status.")
            if not criterion or not evidence:
                raise ValueError("Task agent returned incomplete criterion evidence.")
            results.append(
                {
                    "criterion": criterion,
                    "status": result_status,
                    "evidence": evidence,
                }
            )
        if acceptance_criteria is None:
            return results
        expected = [
            criterion.strip()
            for criterion in acceptance_criteria
            if criterion.strip()
        ]
        if len(results) != len(expected):
            raise ValueError(
                "Task agent criteria result count does not match acceptance criteria."
            )
        for index, expected_criterion in enumerate(expected):
            if results[index]["criterion"] != expected_criterion:
                raise ValueError(
                    "Task agent criteria results must match acceptance criteria order."
                )
        return results

    @staticmethod
    def _parse_verification(raw_verification: Any) -> dict[str, Any]:
        if raw_verification is None:
            return {}
        if not isinstance(raw_verification, dict):
            raise ValueError("Task agent returned invalid verification details.")
        commands_run = raw_verification.get("commands_run", [])
        changed_files = raw_verification.get("changed_files", [])
        published_artifacts = raw_verification.get("published_artifacts", [])
        notes = str(raw_verification.get("notes", "")).strip()
        if (
            not isinstance(commands_run, list)
            or not isinstance(changed_files, list)
            or not isinstance(published_artifacts, list)
        ):
            raise ValueError("Task agent returned invalid verification details.")
        verification: dict[str, Any] = {}
        normalized_commands = ClaudeCodingAgent._parse_command_evidence(commands_run)
        if normalized_commands:
            verification["commands_run"] = normalized_commands
        normalized_files = ClaudeCodingAgent._parse_changed_files(changed_files)
        if normalized_files:
            verification["changed_files"] = normalized_files
        normalized_artifacts = ClaudeCodingAgent._parse_published_artifacts(
            published_artifacts
        )
        if normalized_artifacts:
            verification["published_artifacts"] = normalized_artifacts
        if notes:
            verification["notes"] = notes
        return verification

    @staticmethod
    def _parse_command_evidence(commands_run: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for raw_command in commands_run:
            if not isinstance(raw_command, dict):
                raise ValueError("Task agent returned invalid command evidence.")
            tool = str(raw_command.get("tool", "")).strip()
            result = str(raw_command.get("result", "")).strip().lower()
            if not tool or not result:
                raise ValueError("Task agent returned incomplete command evidence.")
            command: dict[str, Any] = {"tool": tool, "result": result}
            for key in ("command", "summary", "output_summary", "url"):
                value = str(raw_command.get(key, "")).strip()
                if value:
                    command[key] = value
            returncode = raw_command.get("returncode")
            if isinstance(returncode, int):
                command["returncode"] = returncode
            normalized.append(command)
        return normalized

    @staticmethod
    def _parse_changed_files(changed_files: list[Any]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for raw_file in changed_files:
            if not isinstance(raw_file, dict):
                raise ValueError("Task agent returned invalid changed file evidence.")
            path = str(raw_file.get("path", "")).strip()
            if not path:
                raise ValueError("Task agent returned a changed file without a path.")
            item = {"path": path}
            for key in ("change", "summary"):
                value = str(raw_file.get(key, "")).strip()
                if value:
                    item[key] = value
            normalized.append(item)
        return normalized

    @staticmethod
    def _parse_published_artifacts(
        published_artifacts: list[Any],
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for raw_artifact in published_artifacts:
            if not isinstance(raw_artifact, dict):
                raise ValueError("Task agent returned invalid published artifact.")
            artifact_type = str(raw_artifact.get("type", "")).strip()
            if not artifact_type:
                raise ValueError("Task agent returned an artifact without a type.")
            item = {"type": artifact_type}
            for key in ("url", "target_branch", "source_branch"):
                value = str(raw_artifact.get(key, "")).strip()
                if value:
                    item[key] = value
            normalized.append(item)
        return normalized

    @staticmethod
    def _sdk_message_payload(message: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"message_type": type(message).__name__}
        for field in ("subtype", "is_error", "api_error_status"):
            value = getattr(message, field, None)
            if value is not None:
                payload[field] = value
        return payload

    @staticmethod
    def _result_error_text(
        message: Any,
        last_api_error_text: str | None = None,
    ) -> str:
        errors = getattr(message, "errors", None) or []
        error_text = "; ".join(
            str(error).strip() for error in errors if str(error).strip()
        )
        if not error_text:
            result = getattr(message, "result", None)
            if isinstance(result, str) and result.strip():
                error_text = result.strip()
        if not error_text and last_api_error_text:
            error_text = last_api_error_text
        api_error_status = getattr(message, "api_error_status", None)
        if not error_text and api_error_status is not None:
            error_text = f"API request failed with HTTP {api_error_status}."
        if not error_text:
            subtype = getattr(message, "subtype", None) or "unknown"
            error_text = f"Claude Code returned error result subtype `{subtype}`."
        return f"Task agent error: {error_text}"

    @staticmethod
    def _project_key(workspace_path: str) -> str:
        return workspace_path.replace("/", "-")

    @staticmethod
    def _implementation_prompt(
        prompt: str,
        task: TaskRecord,
        handoffs: list[dict[str, Any]] | None = None,
        recovery_context: str | None = None,
    ) -> str:
        handoff = ""
        if handoffs:
            handoff_context = ClaudeCodingAgent._handoff_context(handoffs)
            handoff = f"""

Durable handoff context from earlier task queries:
{json.dumps(handoff_context, ensure_ascii=True, indent=2)}

Use the run progress snapshot, prior task summaries, and previous task detail
with the current workspace state as context. This query is a fresh model session;
do not assume access to an earlier model conversation. Detailed
verification is only provided for the immediately previous task; older tasks are
summarized to avoid stale context.
"""
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
{handoff}

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
- Return one `criteria_results` item for each acceptance criterion in the exact
  same order and with the exact same criterion text.
- Mark a criterion as `passing` only when this task has concrete tool evidence or
  current workspace evidence. Use `not_verified`, `failing`, or `blocked` instead
  of guessing.
- Return status `blocked` when a missing prerequisite, missing access, or a
  failed backend tool prevents this task from proceeding. Do not keep retrying
  the same blocked operation or work around failed repository setup by writing
  files outside the requested checkout.
- Return status `failed` when the task was attempted and cannot be completed
  safely without a platform fix.
- Include sparse `verification` evidence. Use `commands_run` for tool/command
  evidence, `changed_files` only when files changed, `published_artifacts` only
  when something such as a pull request was published, and omit empty arrays.
- Finish with a concise summary of changed files and tests run.
{recovery}
""".strip()

    @staticmethod
    def _criteria_text(task: TaskRecord) -> str:
        if not task.acceptance_criteria:
            return "- Complete the task description."
        return "\n".join(f"- {criterion}" for criterion in task.acceptance_criteria)

    @staticmethod
    def _handoff_context(handoffs: list[dict[str, Any]]) -> dict[str, Any]:
        latest_handoff = handoffs[-1] if handoffs else {}
        previous_task_detail = latest_handoff.get("latest_completed_task") or (
            latest_handoff if handoffs else None
        )
        context: dict[str, Any] = {}
        if latest_handoff.get("planned_task_results"):
            context["planned_task_results"] = latest_handoff["planned_task_results"]
        previous_task_summaries = [
            ClaudeCodingAgent._previous_task_summary(handoff)
            for handoff in handoffs[:-1]
        ]
        if previous_task_summaries:
            context["previous_task_summaries"] = previous_task_summaries
        if previous_task_detail:
            context["previous_task_detail"] = previous_task_detail
        return context

    @staticmethod
    def _previous_task_summary(handoff: dict[str, Any]) -> dict[str, Any]:
        detail = handoff.get("latest_completed_task")
        if isinstance(detail, dict):
            summary = {
                "task_id": detail.get("task_id"),
                "task_seq": detail.get("task_seq"),
                "title": detail.get("title"),
                "status": handoff.get("status"),
                "summary": handoff.get("summary"),
            }
            verification_summary = ClaudeCodingAgent._verification_summary(
                detail.get("verification"),
            )
            if verification_summary:
                summary["verification_summary"] = verification_summary
            return summary
        return {
            "from_task": handoff.get("from_task", {}),
            "status": handoff.get("status"),
            "summary": handoff.get("summary"),
        }

    @staticmethod
    def _verification_summary(raw_verification: Any) -> dict[str, Any]:
        if not isinstance(raw_verification, dict):
            return {}
        summary: dict[str, Any] = {}
        commands = raw_verification.get("commands_run")
        if isinstance(commands, list) and commands:
            summary["commands_run"] = [
                command.get("tool")
                for command in commands
                if isinstance(command, dict) and command.get("tool")
            ]
        changed_files = raw_verification.get("changed_files")
        if isinstance(changed_files, list) and changed_files:
            summary["changed_files"] = [
                item.get("path")
                for item in changed_files
                if isinstance(item, dict) and item.get("path")
            ]
        artifacts = raw_verification.get("published_artifacts")
        if isinstance(artifacts, list) and artifacts:
            summary["published_artifacts"] = [
                {
                    key: artifact[key]
                    for key in ("type", "url")
                    if isinstance(artifact, dict) and artifact.get(key)
                }
                for artifact in artifacts
                if isinstance(artifact, dict)
            ]
        return {key: value for key, value in summary.items() if value}
