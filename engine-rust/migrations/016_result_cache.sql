-- Cache hasil identik untuk tugas terjadwal (tanpa tool/shell).
--
-- Kunci = hash(engine, model, prompt lengkap). Prompt lengkap sudah memuat
-- system prompt, gaya, pengetahuan, dan riwayat, sehingga perubahan konteks
-- otomatis menghasilkan kunci berbeda. TTL diperiksa saat membaca.

CREATE TABLE IF NOT EXISTS waker_result_cache (
    cache_key       TEXT PRIMARY KEY,
    engine_key      TEXT NOT NULL,
    model           TEXT,
    output          TEXT NOT NULL,
    input_tokens    BIGINT,
    output_tokens   BIGINT,
    hits            BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS waker_result_cache_created_idx
    ON waker_result_cache (created_at);
