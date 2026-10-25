-- 002_waker_console.sql — model domain Waker, Run, Automation.
--
-- Aditif: tidak menyentuh action_log / session_turns / world_memory / quest_tasks /
-- incident_log / ai_message. Semua tabel domain diberi awalan `waker_` supaya jelas
-- mana yang milik lapisan produk dan mana yang milik jejak audit.
--
-- Id waker/run/conversation sengaja TEXT, bukan UUID: console membuat id sendiri
-- (`wk_…`, `run_…`) dan id itu sudah dipakai di berkas JSON. Memaksakan UUID berarti
-- dua identitas untuk satu objek.
--
-- Target: PostgreSQL 16.

BEGIN;

-- ---------------------------------------------------------------------------
-- Identitas: satu Waker = satu "pegawai", bukan satu mesin.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker (
    id              TEXT PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    avatar          TEXT,
    description     TEXT,
    system_prompt   TEXT NOT NULL DEFAULT '',
    engine_key      TEXT NOT NULL,          -- kunci di config/engines.json
    model           TEXT,                   -- nama model yang diterima CLI engine
    reasoning_effort TEXT,                  -- low | medium | high | xhigh | max | auto
    context_window  BIGINT,                 -- token
    workspace       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS waker_skill (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (waker_id, name)
);

CREATE TABLE IF NOT EXISTS waker_mcp_server (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    transport       TEXT NOT NULL CHECK (transport IN ('stdio', 'sse', 'http')),
    command         TEXT,
    url             TEXT,
    args            JSONB NOT NULL DEFAULT '[]'::jsonb,
    env             JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (waker_id, name)
);

-- Keputusan per tool. Absennya baris = ikut default engine, bukan "allow".
CREATE TABLE IF NOT EXISTS waker_permission (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    tool            TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('allow', 'ask', 'deny')),
    scope           JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (waker_id, tool)
);

CREATE TABLE IF NOT EXISTS waker_knowledge_source (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('file', 'url', 'text')),
    uri             TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'indexed', 'error')),
    indexed_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Percakapan
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker_conversation (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    title           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS waker_message (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES waker_conversation(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('system', 'user', 'waker', 'tool')),
    content         TEXT NOT NULL,
    run_id          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_message_conv_idx
    ON waker_message (conversation_id, id);

-- ---------------------------------------------------------------------------
-- Satu eksekusi = satu run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker_run (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES waker_conversation(id) ON DELETE SET NULL,
    engine_key      TEXT NOT NULL,
    model           TEXT,
    reasoning_effort TEXT,
    context_window  BIGINT,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded',
                                      'failed', 'cancelled', 'timed_out')),
    prompt          TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    session_ref     TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    exit_code       INTEGER,
    error_code      TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_run_waker_idx ON waker_run (waker_id, created_at DESC);
CREATE INDEX IF NOT EXISTS waker_run_status_idx ON waker_run (status);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'waker_message_run_fk'
    ) THEN
        ALTER TABLE waker_message
            ADD CONSTRAINT waker_message_run_fk
            FOREIGN KEY (run_id) REFERENCES waker_run(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS waker_run_event (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES waker_run(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

CREATE TABLE IF NOT EXISTS waker_artifact (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES waker_run(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    mime            TEXT,
    bytes           BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Otomasi
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker_workflow (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    input_schema    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (waker_id, name, version)
);

CREATE TABLE IF NOT EXISTS waker_workflow_step (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES waker_workflow(id) ON DELETE CASCADE,
    ord             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('prompt', 'tool', 'approval', 'branch')),
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    on_failure      TEXT NOT NULL DEFAULT 'stop'
                    CHECK (on_failure IN ('stop', 'continue', 'retry')),
    UNIQUE (workflow_id, ord)
);

CREATE TABLE IF NOT EXISTS waker_automation (
    id              TEXT PRIMARY KEY,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    cron            TEXT NOT NULL,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    overlap_policy  TEXT NOT NULL DEFAULT 'skip'
                    CHECK (overlap_policy IN ('skip', 'queue', 'replace')),
    workflow_id     TEXT REFERENCES waker_workflow(id) ON DELETE SET NULL,
    prompt          TEXT,
    input           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (workflow_id IS NOT NULL OR prompt IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS waker_automation_run (
    id              TEXT PRIMARY KEY,
    automation_id   TEXT NOT NULL REFERENCES waker_automation(id) ON DELETE CASCADE,
    run_id          TEXT REFERENCES waker_run(id) ON DELETE SET NULL,
    scheduled_for   TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded',
                                      'failed', 'skipped', 'cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (automation_id, scheduled_for)
);

-- ---------------------------------------------------------------------------
-- Persetujuan manusia
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker_approval (
    id              TEXT PRIMARY KEY,
    -- NULL selama permintaan belum disetujui: run baru lahir setelah keputusan.
    run_id          TEXT REFERENCES waker_run(id) ON DELETE CASCADE,
    tool            TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    decided_by      TEXT,
    note            TEXT
);

CREATE INDEX IF NOT EXISTS waker_approval_pending_idx
    ON waker_approval (status, requested_at);

-- ---------------------------------------------------------------------------
-- Channel IM
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker_channel (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL
                    CHECK (provider IN ('dingtalk', 'feishu', 'lark', 'wecom')),
    name            TEXT NOT NULL,
    credentials_ref TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'disconnected'
                    CHECK (status IN ('disconnected', 'connected', 'error')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS waker_channel_binding (
    id              TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL REFERENCES waker_channel(id) ON DELETE CASCADE,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    external_chat_id TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'chat'
                    CHECK (mode IN ('chat', 'task')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel_id, external_chat_id)
);

CREATE TABLE IF NOT EXISTS waker_conversation_channel (
    conversation_id TEXT PRIMARY KEY REFERENCES waker_conversation(id) ON DELETE CASCADE,
    binding_id      TEXT NOT NULL REFERENCES waker_channel_binding(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Treasury: cuplikan kuota
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS waker_engine_usage (
    id              BIGSERIAL PRIMARY KEY,
    engine_key      TEXT NOT NULL,
    window_key      TEXT NOT NULL,
    remaining       NUMERIC(6, 4),
    credits         NUMERIC(12, 2),
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL DEFAULT 'probe'
                    CHECK (source IN ('probe', 'unsupported'))
);

CREATE INDEX IF NOT EXISTS waker_engine_usage_latest_idx
    ON waker_engine_usage (engine_key, window_key, measured_at DESC);

COMMIT;
