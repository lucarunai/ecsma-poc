CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'demo-user',
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'demo-user';

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS expires_at
    TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days');

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL DEFAULT 'demo-user',
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    error_message TEXT,
    retention_until TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    archived_at TIMESTAMPTZ,
    archive_reason TEXT,
    claimed_by TEXT,
    claim_expires_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'demo-user';

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS retention_until
    TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days');

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS archive_reason TEXT;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS claimed_by TEXT;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

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
    user_id TEXT NOT NULL DEFAULT 'demo-user',
    event_type TEXT NOT NULL,
    seq INTEGER,
    schema_version TEXT NOT NULL DEFAULT 'session_event.v1',
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_id TEXT,
    payload JSONB NOT NULL,
    payload_hash TEXT,
    previous_event_hash TEXT,
    event_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'demo-user';

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS seq INTEGER;

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT 'session_event.v1';

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS actor_type TEXT NOT NULL DEFAULT 'system';

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS actor_id TEXT;

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS payload_hash TEXT;

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS previous_event_hash TEXT;

ALTER TABLE session_events
    ADD COLUMN IF NOT EXISTS event_hash TEXT;

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
    failure_kind TEXT,
    failure_reason TEXT,
    last_heartbeat_at TIMESTAMPTZ,
    heartbeat_expires_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, attempt_no)
);

ALTER TABLE task_attempts
    ADD COLUMN IF NOT EXISTS failure_kind TEXT;

ALTER TABLE task_attempts
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

ALTER TABLE task_attempts
    ADD COLUMN IF NOT EXISTS heartbeat_expires_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS sandbox_sessions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    task_attempt_id TEXT REFERENCES task_attempts(id) ON DELETE SET NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime_profile TEXT,
    pod_name TEXT,
    workspace_path TEXT,
    failure_kind TEXT,
    failure_reason TEXT,
    last_heartbeat_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL DEFAULT 'demo-user',
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    task_attempt_id TEXT REFERENCES task_attempts(id) ON DELETE SET NULL,
    tool_call_id TEXT REFERENCES tool_calls(id) ON DELETE SET NULL,
    tool_name TEXT NOT NULL,
    tool_input JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT,
    decided_by TEXT,
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ
);

ALTER TABLE approval_requests
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'demo-user';

CREATE TABLE IF NOT EXISTS tool_executions (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    task_attempt_id TEXT REFERENCES task_attempts(id) ON DELETE SET NULL,
    sandbox_session_id TEXT REFERENCES sandbox_sessions(id) ON DELETE SET NULL,
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

ALTER TABLE tool_executions
    ADD COLUMN IF NOT EXISTS sandbox_session_id TEXT
    REFERENCES sandbox_sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS session_events_session_id_id_idx
    ON session_events (session_id, id);

CREATE INDEX IF NOT EXISTS sessions_user_id_created_at_idx
    ON sessions (user_id, created_at);

CREATE INDEX IF NOT EXISTS runs_user_id_status_created_at_idx
    ON runs (user_id, status, created_at);

CREATE INDEX IF NOT EXISTS session_events_user_id_session_id_id_idx
    ON session_events (user_id, session_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS session_events_run_id_seq_idx
    ON session_events (run_id, seq)
    WHERE run_id IS NOT NULL AND seq IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS session_events_session_scope_seq_idx
    ON session_events (session_id, seq)
    WHERE run_id IS NULL AND seq IS NOT NULL;

CREATE INDEX IF NOT EXISTS tasks_run_id_seq_idx
    ON tasks (run_id, seq);

CREATE INDEX IF NOT EXISTS runs_status_created_at_idx
    ON runs (status, created_at);

CREATE INDEX IF NOT EXISTS runs_claim_expires_at_idx
    ON runs (claim_expires_at)
    WHERE status = 'running';

CREATE UNIQUE INDEX IF NOT EXISTS runs_session_id_idempotency_key_idx
    ON runs (session_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS agent_transcripts_run_id_task_id_idx
    ON agent_transcripts (run_id, task_id);

CREATE INDEX IF NOT EXISTS task_handoffs_run_id_created_at_idx
    ON task_handoffs (run_id, created_at);

CREATE INDEX IF NOT EXISTS task_attempts_task_id_attempt_no_idx
    ON task_attempts (task_id, attempt_no DESC);

CREATE INDEX IF NOT EXISTS task_attempts_run_id_created_at_idx
    ON task_attempts (run_id, created_at);

CREATE INDEX IF NOT EXISTS task_attempts_heartbeat_expires_at_idx
    ON task_attempts (heartbeat_expires_at)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS sandbox_sessions_run_id_created_at_idx
    ON sandbox_sessions (run_id, created_at);

CREATE INDEX IF NOT EXISTS sandbox_sessions_task_attempt_id_idx
    ON sandbox_sessions (task_attempt_id);

CREATE INDEX IF NOT EXISTS sandbox_sessions_status_expires_at_idx
    ON sandbox_sessions (status, expires_at);

CREATE INDEX IF NOT EXISTS tool_calls_task_id_created_at_idx
    ON tool_calls (task_id, created_at);

CREATE INDEX IF NOT EXISTS tool_calls_run_id_created_at_idx
    ON tool_calls (run_id, created_at);

CREATE INDEX IF NOT EXISTS approval_requests_run_id_created_at_idx
    ON approval_requests (run_id, created_at);

CREATE INDEX IF NOT EXISTS approval_requests_status_created_at_idx
    ON approval_requests (status, created_at);

CREATE INDEX IF NOT EXISTS approval_requests_user_id_status_created_at_idx
    ON approval_requests (user_id, status, created_at);

CREATE INDEX IF NOT EXISTS tool_executions_task_id_created_at_idx
    ON tool_executions (task_id, created_at);

CREATE INDEX IF NOT EXISTS tool_executions_run_id_created_at_idx
    ON tool_executions (run_id, created_at);

CREATE INDEX IF NOT EXISTS tool_executions_sandbox_session_id_idx
    ON tool_executions (sandbox_session_id);
