-- 004_automation_schedule.sql — kolom jadwal untuk automation.
--
-- `next_run_at` disimpan, bukan dihitung ulang tiap detak: penjadwal cukup
-- membandingkan satu kolom, dan console bisa menampilkan kapan tugas berikutnya
-- tanpa menebak.

ALTER TABLE waker_automation ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ;
ALTER TABLE waker_automation ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS waker_automation_due_idx
    ON waker_automation (enabled, next_run_at);

CREATE INDEX IF NOT EXISTS waker_automation_run_idx
    ON waker_automation_run (automation_id, scheduled_for DESC);
