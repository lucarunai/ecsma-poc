from __future__ import annotations

import json
from importlib.resources import files
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .domain import PlannedTask, RunRecord, SessionEvent, TaskAttemptRecord, TaskRecord
from .session_policy import (
    FAILURE_KIND_BRAIN_CRASH,
    RUN_LEASE_SECONDS,
    RUN_MAX_ATTEMPTS,
    validate_run_status_transition,
)


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

    async def create_run(
        self,
        session_id: str,
        prompt: str,
        *,
        idempotency_key: str | None = None,
    ) -> str:
        run_id = f"run_{uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO runs (id, session_id, prompt, status, idempotency_key)
                VALUES (%s, %s, %s, 'queued', %s)
                ON CONFLICT (session_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING id
                """,
                (run_id, session_id, prompt, idempotency_key),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return the created run.")
        return str(row[0])

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                SELECT id, session_id, prompt, status, idempotency_key,
                       acceptance_criteria, metadata, started_at, ended_at,
                       error_message, retention_until, archived_at,
                       archive_reason, claimed_by, claim_expires_at,
                       last_heartbeat_at, attempt_count, created_at
                FROM runs
                WHERE id = %s
                """,
                (run_id,),
            )
            return await cursor.fetchone()

    async def claim_next_queued_run(
        self,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 60,
    ) -> RunRecord | None:
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
                        WHERE status IN ('queued', 'resume_queued')
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, NOW()),
                        claimed_by = %s,
                        claim_expires_at = NOW() + (%s * INTERVAL '1 second'),
                        last_heartbeat_at = NOW(),
                        attempt_count = attempt_count + 1
                    FROM next_run
                    WHERE runs.id = next_run.id
                    RETURNING runs.id, runs.session_id, runs.prompt, runs.status,
                              runs.acceptance_criteria
                    """,
                    (worker_id, lease_seconds),
                )
                row = await cursor.fetchone()
        return RunRecord(**row) if row else None

    async def fail_runs_over_attempt_limit(
        self,
        *,
        max_attempts: int = RUN_MAX_ATTEMPTS,
    ) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    UPDATE runs
                    SET status = 'failed',
                        error_message = 'Run exceeded maximum automatic resume attempts.',
                        ended_at = NOW(),
                        claimed_by = NULL,
                        claim_expires_at = NULL
                    WHERE status IN ('queued', 'resume_queued')
                      AND attempt_count >= %s
                    RETURNING id, session_id, attempt_count
                    """,
                    (max_attempts,),
                )
                exhausted_runs = await cursor.fetchall()
                for run in exhausted_runs:
                    await conn.execute(
                        """
                        UPDATE tasks
                        SET status = 'failed',
                            result_summary = 'Run exceeded maximum automatic resume attempts.',
                            ended_at = NOW()
                        WHERE id = (
                            SELECT id
                            FROM tasks
                            WHERE run_id = %s
                              AND status IN ('pending', 'resume_queued', 'running')
                            ORDER BY seq ASC
                            LIMIT 1
                        )
                        """,
                        (run["id"],),
                    )
        return [dict(run) for run in exhausted_runs]

    async def heartbeat_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> bool:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    UPDATE runs
                    SET last_heartbeat_at = NOW(),
                        claim_expires_at = NOW() + (%s * INTERVAL '1 second')
                    WHERE id = %s
                      AND status = 'running'
                      AND claimed_by = %s
                    RETURNING id
                    """,
                    (lease_seconds, run_id, worker_id),
                )
                if await cursor.fetchone() is None:
                    return False
                await conn.execute(
                    """
                    UPDATE task_attempts
                    SET last_heartbeat_at = NOW(),
                        heartbeat_expires_at = NOW() + (%s * INTERVAL '1 second')
                    WHERE run_id = %s
                      AND status = 'running'
                    """,
                    (lease_seconds, run_id),
                )
                return True

    async def requeue_expired_run_leases(self) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    WITH expired AS (
                        SELECT id, session_id, claimed_by, last_heartbeat_at
                        FROM runs
                        WHERE status = 'running'
                          AND claim_expires_at < NOW()
                        FOR UPDATE
                    )
                    UPDATE runs
                    SET status = 'resume_queued',
                        error_message = 'Run lease expired before Brain heartbeat.',
                        ended_at = NULL,
                        claimed_by = NULL,
                        claim_expires_at = NULL
                    FROM expired
                    WHERE runs.id = expired.id
                    RETURNING runs.id, runs.session_id, expired.claimed_by,
                              expired.last_heartbeat_at
                    """
                )
                expired_runs = await cursor.fetchall()
                for run in expired_runs:
                    await conn.execute(
                        """
                        UPDATE tasks
                        SET status = 'resume_queued',
                            ended_at = NULL
                        WHERE id = (
                            SELECT id
                            FROM tasks
                            WHERE run_id = %s
                              AND status = 'running'
                            ORDER BY seq ASC
                            LIMIT 1
                        )
                        """,
                        (run["id"],),
                    )
                    await conn.execute(
                        """
                        UPDATE task_attempts
                        SET status = 'failed',
                            failure_kind = %s,
                            failure_reason = 'Run lease expired before Brain heartbeat.',
                            ended_at = NOW()
                        WHERE run_id = %s
                          AND status = 'running'
                        """,
                        (FAILURE_KIND_BRAIN_CRASH, run["id"]),
                    )
                    await conn.execute(
                        """
                        UPDATE tool_calls
                        SET status = 'orphaned',
                            failure_kind = %s,
                            ended_at = NOW()
                        WHERE run_id = %s
                          AND status IN ('requested', 'running')
                        """,
                        (FAILURE_KIND_BRAIN_CRASH, run["id"]),
                    )
        return [dict(run) for run in expired_runs]

    async def update_run(
        self,
        run_id: str,
        status: str,
        *,
        error_message: str | None = None,
        started: bool = False,
        ended: bool = False,
        metadata: dict[str, Any] | None = None,
        acceptance_criteria: list[dict[str, Any]] | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {})
        acceptance_criteria_json = (
            json.dumps(acceptance_criteria)
            if acceptance_criteria is not None
            else None
        )
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            async with conn.transaction():
                current_cursor = await conn.execute(
                    "SELECT status FROM runs WHERE id = %s FOR UPDATE",
                    (run_id,),
                )
                current = await current_cursor.fetchone()
                if current is None:
                    raise ValueError("Run was not found.")
                validate_run_status_transition(str(current["status"]), status)
                await conn.execute(
                    """
                    UPDATE runs
                    SET status = %s,
                        error_message = COALESCE(%s, error_message),
                        started_at = CASE WHEN %s THEN COALESCE(started_at, NOW())
                                          ELSE started_at END,
                        ended_at = CASE WHEN %s THEN NOW() ELSE ended_at END,
                        metadata = metadata || %s::jsonb,
                        acceptance_criteria = CASE
                            WHEN %s::jsonb IS NULL THEN acceptance_criteria
                            ELSE %s::jsonb
                            END,
                    claimed_by = CASE
                        WHEN %s IN ('completed', 'blocked', 'failed')
                        THEN NULL
                        ELSE claimed_by
                        END,
                    claim_expires_at = CASE
                        WHEN %s IN ('completed', 'blocked', 'failed')
                        THEN NULL
                        ELSE claim_expires_at
                        END
                    WHERE id = %s
                    """,
                    (
                        status,
                        error_message,
                        started,
                        ended,
                        metadata_json,
                        acceptance_criteria_json,
                        acceptance_criteria_json,
                        status,
                        status,
                        run_id,
                    ),
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

    async def get_tasks(self, run_id: str) -> list[TaskRecord]:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                SELECT id, run_id, seq, kind, title, description,
                       acceptance_criteria, status
                FROM tasks
                WHERE run_id = %s
                ORDER BY seq ASC
                """,
                (run_id,),
            )
            rows = await cursor.fetchall()
        return [TaskRecord(**row) for row in rows]

    async def create_task_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
        lease_seconds: int = RUN_LEASE_SECONDS,
    ) -> TaskAttemptRecord:
        attempt_id = f"taskattempt_{uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO task_attempts
                    (id, run_id, task_id, attempt_no, status,
                     last_heartbeat_at, heartbeat_expires_at)
                SELECT %s, %s, %s, COALESCE(MAX(attempt_no), 0) + 1, 'running',
                       NOW(), NOW() + (%s * INTERVAL '1 second')
                FROM task_attempts
                WHERE task_id = %s
                RETURNING id, run_id, task_id, attempt_no, status,
                          claude_session_id, failure_kind, last_heartbeat_at,
                          heartbeat_expires_at
                """,
                (attempt_id, run_id, task_id, lease_seconds, task_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return the created task attempt.")
        return TaskAttemptRecord(**row)

    async def update_task_attempt(
        self,
        attempt_id: str,
        status: str,
        *,
        claude_session_id: str | None = None,
        failure_kind: str | None = None,
        failure_reason: str | None = None,
        ended: bool = False,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                UPDATE task_attempts
                SET status = %s,
                    claude_session_id = COALESCE(%s, claude_session_id),
                    failure_kind = COALESCE(%s, failure_kind),
                    failure_reason = COALESCE(%s, failure_reason),
                    ended_at = CASE WHEN %s THEN NOW() ELSE ended_at END
                WHERE id = %s
                """,
                (
                    status,
                    claude_session_id,
                    failure_kind,
                    failure_reason,
                    ended,
                    attempt_id,
                ),
            )

    async def create_tool_call(
        self,
        *,
        run_id: str,
        task_id: str,
        task_attempt_id: str | None,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        tool_call_id = f"toolcall_{uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                INSERT INTO tool_calls
                    (id, run_id, task_id, task_attempt_id, tool_name, input, status)
                VALUES
                    (%s, %s, %s, %s, %s, %s::jsonb, 'requested')
                """,
                (
                    tool_call_id,
                    run_id,
                    task_id,
                    task_attempt_id,
                    tool_name,
                    json.dumps(tool_input),
                ),
            )
        return tool_call_id

    async def update_tool_call(
        self,
        tool_call_id: str,
        status: str,
        *,
        latest_execution_id: str | None = None,
        failure_kind: str | None = None,
        ended: bool = False,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                UPDATE tool_calls
                SET status = %s,
                    latest_execution_id = COALESCE(%s, latest_execution_id),
                    failure_kind = COALESCE(%s, failure_kind),
                    ended_at = CASE WHEN %s THEN NOW() ELSE ended_at END
                WHERE id = %s
                """,
                (status, latest_execution_id, failure_kind, ended, tool_call_id),
            )

    async def wake_run(self, run_id: str) -> dict[str, Any] | None:
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    UPDATE runs
                    SET status = 'resume_queued',
                        ended_at = NULL,
                        error_message = NULL
                    WHERE id = %s
                      AND status IN ('blocked', 'failed')
                    RETURNING id, session_id, prompt, status
                    """,
                    (run_id,),
                )
                run = await cursor.fetchone()
                if run is None:
                    return None
                await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'resume_queued',
                        ended_at = NULL
                    WHERE id = (
                        SELECT id
                        FROM tasks
                        WHERE run_id = %s
                          AND status IN ('blocked', 'failed', 'running')
                        ORDER BY seq ASC
                        LIMIT 1
                    )
                    """,
                    (run_id,),
                )
        return run

    async def archive_expired_state(self) -> dict[str, int]:
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            async with conn.transaction():
                archived_runs_cursor = await conn.execute(
                    """
                    UPDATE runs
                    SET archived_at = NOW(),
                        archive_reason = 'retention_expired'
                    WHERE retention_until < NOW()
                      AND archived_at IS NULL
                      AND status IN ('completed', 'blocked', 'failed')
                    RETURNING id
                    """
                )
                archived_runs = await archived_runs_cursor.fetchall()
                archived_sessions_cursor = await conn.execute(
                    """
                    UPDATE sessions
                    SET status = 'archived',
                        archived_at = NOW(),
                        updated_at = NOW()
                    WHERE expires_at < NOW()
                      AND status = 'active'
                      AND archived_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM runs
                          WHERE runs.session_id = sessions.id
                            AND runs.status IN ('queued', 'running', 'resume_queued')
                      )
                    RETURNING id
                    """
                )
                archived_sessions = await archived_sessions_cursor.fetchall()
        return {
            "runs_archived": len(archived_runs),
            "sessions_archived": len(archived_sessions),
        }

    async def get_run_recovery_bundle(self, run_id: str) -> dict[str, Any] | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        tasks = await self.get_tasks(run_id)
        async with await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:
            attempt_cursor = await conn.execute(
                """
                SELECT id, run_id, task_id, attempt_no, status, claude_session_id,
                       failure_kind, failure_reason, last_heartbeat_at,
                       heartbeat_expires_at
                FROM task_attempts
                WHERE run_id = %s
                ORDER BY created_at DESC
                LIMIT 8
                """,
                (run_id,),
            )
            tool_cursor = await conn.execute(
                """
                SELECT id, run_id, task_id, task_attempt_id, tool_name, input,
                       status, latest_execution_id, failure_kind, created_at,
                       ended_at
                FROM tool_calls
                WHERE run_id = %s
                ORDER BY created_at DESC
                LIMIT 12
                """,
                (run_id,),
            )
            handoff_cursor = await conn.execute(
                """
                SELECT task_handoffs.id, task_handoffs.from_task_id,
                       task_handoffs.to_task_id, task_handoffs.status,
                       task_handoffs.summary, task_handoffs.payload,
                       task_handoffs.created_at
                FROM task_handoffs
                WHERE task_handoffs.run_id = %s
                ORDER BY task_handoffs.created_at ASC
                LIMIT 24
                """,
                (run_id,),
            )
            attempts = await attempt_cursor.fetchall()
            tool_calls = await tool_cursor.fetchall()
            handoffs = await handoff_cursor.fetchall()
        return {
            "run": run,
            "tasks": [task.__dict__ for task in tasks],
            "task_attempts": attempts,
            "tool_calls": tool_calls,
            "task_handoffs": handoffs,
        }

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
        payload: dict[str, Any],
        claude_session_id: str | None = None,
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
                     summary, payload, claude_session_id, transcript_id)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    session_id,
                    run_id,
                    from_task_id,
                    to_task_id,
                    status,
                    summary,
                    json.dumps(payload),
                    claude_session_id,
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
        task_attempt_id: str | None,
        failure_kind: str | None,
        envelope: dict[str, Any],
    ) -> str:
        execution_id = str(envelope["execution_id"])
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute(
                """
                INSERT INTO tool_executions
                    (execution_id, run_id, task_id, task_attempt_id, tool_call_id,
                     tool_name, execution_status, failure_kind, envelope)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    execution_id,
                    run_id,
                    task_id,
                    task_attempt_id,
                    envelope["tool_call_id"],
                    envelope["tool_name"],
                    envelope["execution_status"],
                    failure_kind,
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
