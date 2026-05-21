from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..domain import PlannedTask, Workspace


class AgentTaskPlanner:
    """Uses Claude Agent SDK to split the coding request into model tasks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def plan(self, prompt: str, workspace: Workspace) -> list[PlannedTask]:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Run `uv sync` first."
            ) from exc

        options = ClaudeAgentOptions(
            tools=[],
            cwd=workspace.path,
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
            if isinstance(message, ResultMessage):
                structured_output = getattr(message, "structured_output", None)
                if structured_output is None:
                    result = getattr(message, "result", None)
                    if isinstance(result, str):
                        structured_output = json.loads(result)
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
        return planned_tasks

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
- The implementation agent has Claude Agent SDK file tools and coding MCP tools
  for cloning GitHub repositories, creating branches, running Python unittest,
  committing, pushing, and creating pull requests.
- Keep the task list small for this PoC.
- Each task must be concrete, ordered, and have concise acceptance criteria.
""".strip()
