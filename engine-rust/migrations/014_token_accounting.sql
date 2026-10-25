-- 014_token_accounting.sql — angka token dan gaya jawaban per worker.
--
-- Token hanya diisi bila engine melaporkannya (agy/qoder JSON). NULL berarti
-- "tidak dilaporkan", bukan nol.

ALTER TABLE waker ADD COLUMN IF NOT EXISTS answer_style TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE waker ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER;

ALTER TABLE waker_run ADD COLUMN IF NOT EXISTS input_tokens BIGINT;
ALTER TABLE waker_run ADD COLUMN IF NOT EXISTS output_tokens BIGINT;
ALTER TABLE waker_run ADD COLUMN IF NOT EXISTS cache_read_tokens BIGINT;
