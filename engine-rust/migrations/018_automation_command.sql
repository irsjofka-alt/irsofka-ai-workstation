-- 018_automation_command.sql — automation yang menjalankan perintah, bukan prompt.
--
-- Sebagian pekerjaan terjadwal tidak butuh model: backup, rotasi log, pembersihan.
-- Menjalankannya lewat LLM membuat hasilnya tidak deterministik dan memakan kuota.
-- `kind='command'` menjalankan `command` di workspace worker dan mencatat statusnya
-- seperti automation biasa; `kind='prompt'` tetap jalur lama.

ALTER TABLE waker_automation ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'prompt';
ALTER TABLE waker_automation ADD COLUMN IF NOT EXISTS command TEXT;

-- Constraint lama mewajibkan prompt atau workflow; automation `command` tidak punya
-- keduanya. Bentuk barunya tetap menolak baris kosong untuk setiap jenis.
ALTER TABLE waker_automation DROP CONSTRAINT IF EXISTS waker_automation_check;
ALTER TABLE waker_automation ADD CONSTRAINT waker_automation_check CHECK (
    (kind = 'command' AND command IS NOT NULL AND btrim(command) <> '')
    OR (workflow_id IS NOT NULL)
    OR (prompt IS NOT NULL)
);
