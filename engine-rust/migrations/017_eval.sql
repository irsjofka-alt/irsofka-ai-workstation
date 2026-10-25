-- P2.3 — eval harness.
--
-- Satu baris `eval_run` = satu putaran dataset pada satu engine/model. Satu baris
-- `eval_case` = satu tugas: keluaran engine, hasil tiap pemeriksaan, dan token.
-- Tidak ada skor yang dikarang: `score` hanya passed/total tugas yang benar-benar
-- dinilai (tugas yang di-skip tidak menurunkan skor, tetapi jumlahnya dicatat).

CREATE TABLE IF NOT EXISTS eval_run (
    id            TEXT PRIMARY KEY,
    label         TEXT,
    waker_id      TEXT NOT NULL,
    engine_key    TEXT NOT NULL,
    model         TEXT,
    status        TEXT NOT NULL,
    passed        INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    score         DOUBLE PRECISION NOT NULL DEFAULT 0,
    input_tokens  BIGINT,
    output_tokens BIGINT,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS eval_case (
    id            TEXT PRIMARY KEY,
    eval_run_id   TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    status        TEXT NOT NULL,
    passed        BOOLEAN NOT NULL DEFAULT false,
    checks        JSONB NOT NULL DEFAULT '[]'::jsonb,
    output        TEXT,
    reason        TEXT,
    run_id        TEXT,
    input_tokens  BIGINT,
    output_tokens BIGINT,
    duration_ms   BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_case_run ON eval_case (eval_run_id);
CREATE INDEX IF NOT EXISTS idx_eval_run_created ON eval_run (created_at DESC);
