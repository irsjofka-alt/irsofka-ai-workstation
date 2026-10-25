-- 003_waker_run_content.sql — kolom yang dibutuhkan penyimpanan run.
--
-- `content` memisahkan pesan pengguna dari prompt yang sudah dilebur dengan system
-- prompt; riwayat chat menampilkan yang pertama, engine menerima yang kedua.
-- ALTER ... IF NOT EXISTS membuat migrasi ini aman dijalankan berulang.

ALTER TABLE waker ADD COLUMN IF NOT EXISTS reasoning_effort TEXT;
ALTER TABLE waker ADD COLUMN IF NOT EXISTS context_window BIGINT;

ALTER TABLE waker_run ADD COLUMN IF NOT EXISTS reasoning_effort TEXT;
ALTER TABLE waker_run ADD COLUMN IF NOT EXISTS context_window BIGINT;
ALTER TABLE waker_run ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT '';
