-- 006_approval_nullable_run.sql — permintaan izin lahir sebelum run-nya.
--
-- Tanpa ini, menyimpan permintaan izin selalu gagal karena `run_id` belum ada.

ALTER TABLE waker_approval ALTER COLUMN run_id DROP NOT NULL;
