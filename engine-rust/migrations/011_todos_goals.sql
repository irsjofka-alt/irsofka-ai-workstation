-- 011_todos_goals.sql — daftar kerja dan target di dalam grup.
--
-- Goal satu per grup (target percakapan), todo banyak per grup dan bisa
-- ditautkan ke worker yang mengerjakannya.

CREATE TABLE IF NOT EXISTS waker_goal (
    group_id        TEXT PRIMARY KEY REFERENCES waker_group(id) ON DELETE CASCADE,
    objective       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'achieved', 'abandoned')),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS waker_todo (
    id              TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL REFERENCES waker_group(id) ON DELETE CASCADE,
    waker_id        TEXT REFERENCES waker(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'done', 'dropped')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_todo_group_idx
    ON waker_todo (group_id, status, created_at);
