-- 019_chat_availability.sql — worker yang tidak dipakai untuk sesi percakapan.
--
-- Decider, Verifier, dan worker otomasi tetap ada di Worker Management, tetapi
-- tidak ditawarkan di @Worker: mereka dipanggil lewat automation atau grup.
-- Kolom ini hanya memengaruhi pemilih percakapan; run dan automation tidak
-- membacanya.

ALTER TABLE waker ADD COLUMN IF NOT EXISTS chat_available BOOLEAN NOT NULL DEFAULT TRUE;
