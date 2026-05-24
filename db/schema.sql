CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS expires_at
    TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days');

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
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
    claude_session_id TEXT,
    next_resume_session_id TEXT,
    transcript_id TEXT REFERENCES agent_transcripts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE INDEX IF NOT EXISTS session_events_session_id_id_idx
    ON session_events (session_id, id);

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
