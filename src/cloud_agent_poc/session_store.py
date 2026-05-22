from __future__ import annotations

import json
from importlib.resources import files
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .domain import PlannedTask, RunRecord, SessionEvent, TaskRecord


class PostgresSessionStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def initialize_schema(self) -> None:
        schema = files("cloud_agent_poc").joinpath("schema.sql").read_text(
            encoding="utf-8"
        )
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(schema)

    async def create_session(self) -> str:
        session_id = f"sess_{uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                "INSERT INTO sessions (id) VALUES (%s)",
                (session_id,),
            )
        return session_id

    async def create_run(self, session_id: str, prompt: str) -> str:
        run_id = f"run_{uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                INSERT INTO runs (id, session_id, prompt, status)
                VALUES (%s, %s, %s, 'queued')
                """,
                (run_id, session_id, prompt),
            )
        return run_id

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                SELECT id, session_id, prompt, status, metadata, started_at, ended_at,
                       error_message, created_at
                FROM runs
                WHERE id = %s
                """,
                (run_id,),
            )
            return await cursor.fetchone()

    async def claim_next_queued_run(self) -> RunRecord | None:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    WITH next_run AS (
                        SELECT id
                        FROM runs
                        WHERE status = 'queued'
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, NOW())
                    FROM next_run
                    WHERE runs.id = next_run.id
                    RETURNING runs.id, runs.session_id, runs.prompt, runs.status
                    """
                )
                row = await cursor.fetchone()
        return RunRecord(**row) if row else None

    async def update_run(
        self,
        run_id: str,
        status: str,
        *,
        error_message: str | None = None,
        started: bool = False,
        ended: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {})
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                UPDATE runs
                SET status = %s,
                    error_message = COALESCE(%s, error_message),
                    started_at = CASE WHEN %s THEN COALESCE(started_at, NOW())
                                      ELSE started_at END,
                    ended_at = CASE WHEN %s THEN NOW() ELSE ended_at END,
                    metadata = metadata || %s::jsonb
                WHERE id = %s
                """,
                (status, error_message, started, ended, metadata_json, run_id),
            )

    async def create_tasks(
        self,
        run_id: str,
        planned_tasks: list[PlannedTask],
    ) -> list[TaskRecord]:
        tasks: list[TaskRecord] = []
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            for seq, planned_task in enumerate(planned_tasks, start=1):
                task_id = f"task_{uuid4().hex}"
                await conn.execute(
                    """
                    INSERT INTO tasks
                        (id, run_id, seq, kind, title, description,
                         acceptance_criteria, status)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s::jsonb, 'pending')
                    """,
                    (
                        task_id,
                        run_id,
                        seq,
                        "model_task",
                        planned_task.title,
                        planned_task.description,
                        json.dumps(planned_task.acceptance_criteria),
                    ),
                )
                tasks.append(
                    TaskRecord(
                        id=task_id,
                        run_id=run_id,
                        seq=seq,
                        kind="model_task",
                        title=planned_task.title,
                        description=planned_task.description,
                        acceptance_criteria=planned_task.acceptance_criteria,
                        status="pending",
                    )
                )
        return tasks

    async def update_task(
        self,
        task_id: str,
        status: str,
        *,
        started: bool = False,
        ended: bool = False,
        result_summary: str | None = None,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                UPDATE tasks
                SET status = %s,
                    result_summary = COALESCE(%s, result_summary),
                    started_at = CASE WHEN %s THEN COALESCE(started_at, NOW())
                                      ELSE started_at END,
                    ended_at = CASE WHEN %s THEN NOW() ELSE ended_at END
                WHERE id = %s
                """,
                (status, result_summary, started, ended, task_id),
            )

    async def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> SessionEvent:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO session_events
                    (session_id, run_id, task_id, event_type, payload)
                VALUES
                    (%s, %s, %s, %s, %s::jsonb)
                RETURNING id, session_id, run_id, task_id, event_type, payload,
                          created_at
                """,
                (session_id, run_id, task_id, event_type, json.dumps(payload)),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return the appended event.")
        return SessionEvent(**row)

    async def record_agent_transcript(
        self,
        *,
        run_id: str,
        task_id: str | None,
        provider: str,
        provider_session_id: str,
        artifact_path: str,
        checksum: str,
        size_bytes: int,
    ) -> str:
        transcript_id = f"transcript_{uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                INSERT INTO agent_transcripts
                    (id, run_id, task_id, provider, provider_session_id,
                     artifact_path, checksum, size_bytes)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    transcript_id,
                    run_id,
                    task_id,
                    provider,
                    provider_session_id,
                    artifact_path,
                    checksum,
                    size_bytes,
                ),
            )
        return transcript_id

    async def create_task_handoff(
        self,
        *,
        session_id: str,
        run_id: str,
        from_task_id: str,
        to_task_id: str | None = None,
        status: str,
        summary: str,
        claude_session_id: str | None = None,
        next_resume_session_id: str | None = None,
        transcript_id: str | None = None,
    ) -> int:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO task_handoffs
                    (session_id, run_id, from_task_id, to_task_id, status,
                     summary, claude_session_id, next_resume_session_id,
                     transcript_id)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    session_id,
                    run_id,
                    from_task_id,
                    to_task_id,
                    status,
                    summary,
                    claude_session_id,
                    next_resume_session_id,
                    transcript_id,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return the created handoff.")
        return int(row["id"])

    async def record_tool_execution(
        self,
        *,
        run_id: str,
        task_id: str | None,
        envelope: dict[str, Any],
    ) -> str:
        execution_id = str(envelope["execution_id"])
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                INSERT INTO tool_executions
                    (execution_id, run_id, task_id, tool_call_id, tool_name,
                     execution_status, envelope)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    execution_id,
                    run_id,
                    task_id,
                    envelope["tool_call_id"],
                    envelope["tool_name"],
                    envelope["execution_status"],
                    json.dumps(envelope),
                ),
            )
        return execution_id

    async def list_events(
        self,
        session_id: str,
        after_event_id: int,
    ) -> list[SessionEvent]:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                SELECT id, session_id, run_id, task_id, event_type, payload,
                       created_at
                FROM session_events
                WHERE session_id = %s AND id > %s
                ORDER BY id ASC
                """,
                (session_id, after_event_id),
            )
            rows = await cursor.fetchall()
        return [SessionEvent(**row) for row in rows]
