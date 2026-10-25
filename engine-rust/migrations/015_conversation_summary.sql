-- Ringkasan bergulir per percakapan.
--
-- `summary_covered_id` adalah id pesan terakhir yang sudah diringkas. Pesan
-- setelah id itu masih dikirim utuh (N pesan terakhir), sehingga prompt tidak
-- perlu membawa seluruh riwayat.

ALTER TABLE waker_conversation ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE waker_conversation ADD COLUMN IF NOT EXISTS summary_covered_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE waker_conversation ADD COLUMN IF NOT EXISTS summary_model TEXT;
ALTER TABLE waker_conversation ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ;
