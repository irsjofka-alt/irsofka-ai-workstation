-- 010_groups.sql — percakapan multi-worker dengan SOP.
--
-- Grup punya daftar anggota berurutan dan satu teks SOP yang disisipkan ke setiap
-- prompt. Urutan anggota menentukan urutan menjawab, sehingga hasilnya bisa
-- direproduksi.

CREATE TABLE IF NOT EXISTS waker_group (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    sop             TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS waker_group_member (
    group_id        TEXT NOT NULL REFERENCES waker_group(id) ON DELETE CASCADE,
    waker_id        TEXT NOT NULL REFERENCES waker(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'member',
    ord             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_id, waker_id)
);

CREATE TABLE IF NOT EXISTS waker_group_message (
    id              BIGSERIAL PRIMARY KEY,
    group_id        TEXT NOT NULL REFERENCES waker_group(id) ON DELETE CASCADE,
    waker_id        TEXT,
    role            TEXT NOT NULL CHECK (role IN ('user', 'waker', 'system')),
    content         TEXT NOT NULL,
    run_id          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS waker_group_message_idx
    ON waker_group_message (group_id, id);
