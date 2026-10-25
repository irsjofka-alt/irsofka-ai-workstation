-- 013_knowledge_embedding.sql — vektor untuk pengetahuan lokal.
--
-- NULL berarti entri belum punya embedding dan tetap bisa dicari lewat kata kunci.
-- Tidak ada dependensi database vektor; cosine dihitung di Rust.

ALTER TABLE waker_knowledge ADD COLUMN IF NOT EXISTS embedding JSONB;
