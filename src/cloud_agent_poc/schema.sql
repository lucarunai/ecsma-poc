CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL,
    result_summary TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, seq)
);

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS session_events (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_transcripts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    provider_session_id TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS task_handoffs (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    from_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    to_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    claude_session_id TEXT,
    transcript_id TEXT REFERENCES agent_transcripts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE task_handoffs
    ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    claude_session_id TEXT,
    failure_reason TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    task_attempt_id TEXT REFERENCES task_attempts(id) ON DELETE SET NULL,
    tool_name TEXT NOT NULL,
    input JSONB NOT NULL,
    status TEXT NOT NULL,
    latest_execution_id TEXT,
    failure_kind TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tool_executions (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    task_attempt_id TEXT REFERENCES task_attempts(id) ON DELETE SET NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    failure_kind TEXT,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE tool_executions
    ADD COLUMN IF NOT EXISTS task_attempt_id TEXT
    REFERENCES task_attempts(id) ON DELETE SET NULL;

ALTER TABLE tool_executions
    ADD COLUMN IF NOT EXISTS failure_kind TEXT;

CREATE INDEX IF NOT EXISTS session_events_session_id_id_idx
    ON session_events (session_id, id);

CREATE INDEX IF NOT EXISTS tasks_run_id_seq_idx
    ON tasks (run_id, seq);

CREATE INDEX IF NOT EXISTS runs_status_created_at_idx
    ON runs (status, created_at);

CREATE INDEX IF NOT EXISTS agent_transcripts_run_id_task_id_idx
    ON agent_transcripts (run_id, task_id);

CREATE INDEX IF NOT EXISTS task_handoffs_run_id_created_at_idx
    ON task_handoffs (run_id, created_at);

CREATE INDEX IF NOT EXISTS task_attempts_task_id_attempt_no_idx
    ON task_attempts (task_id, attempt_no DESC);

CREATE INDEX IF NOT EXISTS task_attempts_run_id_created_at_idx
    ON task_attempts (run_id, created_at);

CREATE INDEX IF NOT EXISTS tool_calls_task_id_created_at_idx
    ON tool_calls (task_id, created_at);

CREATE INDEX IF NOT EXISTS tool_calls_run_id_created_at_idx
    ON tool_calls (run_id, created_at);

CREATE INDEX IF NOT EXISTS tool_executions_task_id_created_at_idx
    ON tool_executions (task_id, created_at);

CREATE INDEX IF NOT EXISTS tool_executions_run_id_created_at_idx
    ON tool_executions (run_id, created_at);
