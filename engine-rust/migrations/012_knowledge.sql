-- 012_knowledge.sql — basis pengetahuan lokal (kata kunci, bukan vektor).
--
-- `waker_id` NULL berarti entri berlaku untuk semua worker. Pencarian memakai
-- skor kata kunci; tidak ada embedding, dan console mengatakannya terus terang.

CREATE TABLE IF NOT EXISTS waker_knowledge (
    id          TEXT PRIMARY KEY,
    waker_id    TEXT REFERENCES waker(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_knowledge_scope_idx
    ON waker_knowledge (waker_id, created_at DESC);
