-- 005_approval_gate.sql — gerbang persetujuan manusia.
--
-- `approval_mode` menentukan apakah sebuah waker boleh langsung berjalan:
--   auto    -> jalan seperti biasa
--   require -> setiap run (chat, run-now, automation) menunggu persetujuan
--
-- Permintaan izin menyimpan parameter run di `payload`, sehingga menyetujui
-- berarti benar-benar menjalankan hal yang ditampilkan — bukan sekadar menandai.

ALTER TABLE waker ADD COLUMN IF NOT EXISTS approval_mode TEXT NOT NULL DEFAULT 'auto';

ALTER TABLE waker_approval ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE waker_approval ADD COLUMN IF NOT EXISTS origin TEXT;

CREATE INDEX IF NOT EXISTS waker_approval_status_idx
    ON waker_approval (status, requested_at DESC);
