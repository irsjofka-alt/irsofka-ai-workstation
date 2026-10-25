-- 009_channels.sql — channel IM: provider lokal untuk uji + webhook keluar.
--
-- Kredensial tidak disimpan di database. Kolom `credentials_ref` hanya berisi
-- NAMA variabel lingkungan yang memegang URL/webhook rahasia.

ALTER TABLE waker_channel DROP CONSTRAINT IF EXISTS waker_channel_provider_check;
ALTER TABLE waker_channel
    ADD CONSTRAINT waker_channel_provider_check
    CHECK (provider IN ('dingtalk', 'feishu', 'lark', 'wecom', 'local'));

CREATE TABLE IF NOT EXISTS waker_channel_message (
    id              BIGSERIAL PRIMARY KEY,
    channel_id      TEXT NOT NULL REFERENCES waker_channel(id) ON DELETE CASCADE,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    external_chat_id TEXT NOT NULL,
    waker_id        TEXT,
    run_id          TEXT,
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_channel_message_idx
    ON waker_channel_message (channel_id, created_at DESC);
