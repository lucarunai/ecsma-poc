from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from ..domain import PlannedTask, Workspace

EmitPlannerEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


class AgentTaskPlanner:
    """Uses Claude Agent SDK to split the coding request into model tasks."""

    REPORT_ONLY_TITLES = (
        "report ",
        "summarize ",
        "summary ",
        "communicate ",
        "present ",
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def plan(
        self,
        prompt: str,
        workspace: Workspace,
        *,
        emit: EmitPlannerEvent | None = None,
    ) -> list[PlannedTask]:
        del workspace
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                TextBlock,
                query,
            )
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Run `uv sync` first."
            ) from exc

        options = ClaudeAgentOptions(
            tools=[],
            include_partial_messages=False,
            model=self.settings.claude_model,
            max_turns=8,
            output_format={
                "type": "json_schema",
                "schema": self._task_schema(),
            },
            permission_mode="dontAsk",
            setting_sources=[],
            skills=[],
        )
        structured_output: dict[str, Any] | None = None
        last_api_error_text: str | None = None
        async for message in query(
            prompt=self._planning_prompt(prompt),
            options=options,
        ):
            if emit:
                await emit(
                    "planner.sdk.message",
                    {"message_type": type(message).__name__},
                )
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if not isinstance(block, TextBlock) or not block.text.strip():
                        continue
                    if block.text.strip().lower().startswith("api error:"):
                        last_api_error_text = block.text.strip()
                    if emit:
                        await emit("planner.message", {"text": block.text})
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(
                        self._result_error_text(message, last_api_error_text)
                    )
                structured_output = getattr(message, "structured_output", None)
                if structured_output is None:
                    result = getattr(message, "result", None)
                    if isinstance(result, str):
                        structured_output = self._parse_json_candidate(result)
        if not structured_output:
            raise RuntimeError("Planner agent did not return structured tasks.")
        return self._filter_unrequested_optional_tasks(
            self.parse_plan(structured_output),
            prompt,
        )

    @staticmethod
    def parse_plan(structured_output: dict[str, Any]) -> list[PlannedTask]:
        tasks = structured_output.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("Planner output must include at least one task.")
        planned_tasks: list[PlannedTask] = []
        for task in tasks[:8]:
            title = str(task.get("title", "")).strip()
            description = str(task.get("description", "")).strip()
            criteria = task.get("acceptance_criteria", [])
            if not title or not description or not isinstance(criteria, list):
                raise ValueError("Planner returned an invalid task.")
            if AgentTaskPlanner._is_report_only_task(title):
                continue
            planned_tasks.append(
                PlannedTask(
                    title=title,
                    description=description,
                    acceptance_criteria=[
                        str(criterion).strip()
                        for criterion in criteria
                        if str(criterion).strip()
                    ][:5],
                )
            )
        if not planned_tasks:
            raise ValueError("Planner returned only report-only tasks.")
        return planned_tasks

    @staticmethod
    def _parse_json_candidate(candidate: str) -> dict[str, Any] | None:
        text = candidate.strip()
        if not text:
            return None
        if text.lower().startswith("api error:"):
            raise RuntimeError(f"Planner agent API error: {text}")
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Planner agent returned invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Planner agent JSON must be an object.")
        return parsed

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
        return f"Planner agent error: {error_text}"

    @staticmethod
    def _is_report_only_task(title: str) -> bool:
        normalized = " ".join(title.lower().split())
        return normalized.startswith(AgentTaskPlanner.REPORT_ONLY_TITLES)

    @staticmethod
    def _filter_unrequested_optional_tasks(
        planned_tasks: list[PlannedTask],
        prompt: str,
    ) -> list[PlannedTask]:
        allow_testing = AgentTaskPlanner._prompt_requests_testing(prompt)
        allow_publish = AgentTaskPlanner._prompt_requests_publish(prompt)
        filtered: list[PlannedTask] = []
        for task in planned_tasks:
            text = " ".join(
                [
                    task.title,
                    task.description,
                    " ".join(task.acceptance_criteria),
                ]
            ).lower()
            if not allow_testing and AgentTaskPlanner._looks_like_test_only_task(text):
                continue
            if not allow_publish and AgentTaskPlanner._looks_like_publish_task(text):
                continue
            filtered.append(task)
        if not filtered:
            raise ValueError("Planner returned only unrequested optional tasks.")
        return filtered

    @staticmethod
    def _prompt_requests_testing(prompt: str) -> bool:
        text = " ".join(prompt.lower().split())
        testing_patterns = (
            r"\b(add|write|create|include|run|execute)\s+(unit\s+)?tests?\b",
            r"\bwith\s+(unit\s+)?tests?\b",
            r"\bunit\s+tests?\b",
            r"\bunittest\b",
            r"\bpytest\b",
            r"\bcoverage\b",
            r"\btesting\b",
        )
        return any(re.search(pattern, text) for pattern in testing_patterns)

    @staticmethod
    def _prompt_requests_publish(prompt: str) -> bool:
        text = " ".join(prompt.lower().split())
        publish_patterns = (
            r"\bcommit\b",
            r"\bpush\b",
            r"\bpull\s+request\b",
            r"\bopen\s+(a\s+)?pr\b",
            r"\bcreate\s+(a\s+)?pr\b",
            r"\bmerge\s+request\b",
            r"\bpublish\b",
        )
        return any(re.search(pattern, text) for pattern in publish_patterns)

    @staticmethod
    def _looks_like_test_only_task(text: str) -> bool:
        return bool(
            re.search(
                r"\b(add|write|create|include|run|execute)\s+(unit\s+)?tests?\b",
                text,
            )
            or re.search(r"\bwith\s+(unit\s+)?tests?\b", text)
            or re.search(r"\bunittest\b|\bpytest\b|\bcoverage\b", text)
        )

    @staticmethod
    def _looks_like_publish_task(text: str) -> bool:
        return bool(
            re.search(r"\bcommit\b|\bpush\b|\bpull\s+request\b", text)
            or re.search(r"\b(open|create)\s+(a\s+)?pr\b", text)
            or re.search(r"\bpublish\b|\bmerge\s+request\b", text)
        )

    @staticmethod
    def _task_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tasks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "acceptance_criteria": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 5,
                            },
                        },
                        "required": [
                            "title",
                            "description",
                            "acceptance_criteria",
                        ],
                    },
                }
            },
            "required": ["tasks"],
        }

    @staticmethod
    def _planning_prompt(prompt: str) -> str:
        return f"""
You are the planner brain for a coding-agent platform PoC.

Split the user's request into ordered tasks that an implementation agent can
complete one at a time.

User request:
{prompt.strip()}

Planning rules:
- Include repository, testing, Git, and pull-request tasks when the user asks
  for those outcomes.
- Do not add standalone testing, commit, push, or pull-request tasks unless the
  user explicitly asks for those outcomes. For example, a branch named `test`
  is just a branch name, not a request to add tests.
- The implementation agent has controlled coding MCP tools that operate inside
  a Sandbox Layer for file inspection and edits, GitHub repository operations,
  Python unittest, commits, pushes, and pull requests.
- Do not create a task just to report, summarize, communicate, or present the
  outcome to the user. Task result summaries and Session Events already do that.
- Treat requested operations as requested outcomes. Do not make a failed
  requested operation an acceptance criterion unless the user explicitly asks
  to test, capture, or report that failure.
- Keep the task list small for this PoC.
- Each task must be concrete, ordered, and have concise acceptance criteria.
""".strip()
