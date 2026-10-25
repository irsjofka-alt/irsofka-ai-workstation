-- 007_workflow_runs.sql — eksekusi WakerFlow.
--
-- Definisi alur ada di `waker_workflow` + `waker_workflow_step` (migrasi 002).
-- Di sini yang ditambahkan adalah riwayat eksekusinya: langkah mana yang sedang
-- berjalan, log per langkah, dan status akhir.

CREATE TABLE IF NOT EXISTS waker_workflow_run (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES waker_workflow(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'waiting_approval',
                                      'succeeded', 'failed', 'cancelled')),
    current_step    INTEGER NOT NULL DEFAULT 0,
    input           JSONB NOT NULL DEFAULT '{}'::jsonb,
    log             JSONB NOT NULL DEFAULT '[]'::jsonb,
    error           TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_workflow_run_idx
    ON waker_workflow_run (workflow_id, created_at DESC);
