-- 008_workflow_versions.sql — versi alur dan retry per langkah.
--
-- Setiap perubahan definisi alur menyimpan snapshot penuh. Rollback berarti
-- menyalin snapshot lama kembali menjadi langkah aktif, bukan menebak dari diff.

ALTER TABLE waker_workflow_step ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS waker_workflow_version (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES waker_workflow(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    snapshot        JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, version)
);

CREATE INDEX IF NOT EXISTS waker_workflow_version_idx
    ON waker_workflow_version (workflow_id, version DESC);
