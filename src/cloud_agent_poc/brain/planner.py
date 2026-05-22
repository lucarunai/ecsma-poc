from __future__ import annotations

import json
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
                    if isinstance(block, TextBlock) and block.text.strip() and emit:
                        await emit("planner.message", {"text": block.text})
            if isinstance(message, ResultMessage):
                structured_output = getattr(message, "structured_output", None)
                if structured_output is None:
                    result = getattr(message, "result", None)
                    if isinstance(result, str):
                        structured_output = self._parse_json_candidate(result)
        if not structured_output:
            raise RuntimeError("Planner agent did not return structured tasks.")
        return self.parse_plan(structured_output)

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
    def _is_report_only_task(title: str) -> bool:
        normalized = " ".join(title.lower().split())
        return normalized.startswith(AgentTaskPlanner.REPORT_ONLY_TITLES)

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
