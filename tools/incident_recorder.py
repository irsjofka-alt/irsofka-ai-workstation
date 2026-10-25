#!/usr/bin/env python3
"""
Incident Recorder & Auto-Skill Synthesizer
Mencatat kegagalan, dan jika pola error terulang >= 5x,
secara otomatis menciptakan SKILL baru di brain/skills/learned/
dan memperbarui catalog SKILLS.md.
"""

import os
import re
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".ai-station" / "tools"))
from db_state import get_db_connection  # noqa: E402


def is_pg_engine(engine):
    return str(engine).upper() == "POSTGRESQL"

AI_STATION = Path.home() / ".ai-station"
BRAIN_DIR = AI_STATION / "brain"
INCIDENT_FILE = BRAIN_DIR / "memory" / "incidents" / "error_tracker.json"
SKILLS_DIR = BRAIN_DIR / "skills"
LEARNED_DIR = SKILLS_DIR / "learned"
SKILLS_CATALOG = SKILLS_DIR / "SKILLS.md"
THRESHOLD = 5


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sql_record(signature, description, count_at_least=1):
    """Catat insiden ke tabel incident_log (sumber kebenaran, bukan JSON saja).

    Fungsi ini dulunya hanya menulis error_tracker.json, sementara GUI dan semua alat
    membaca tabel incident_log di PostgreSQL — itulah sebabnya tabel itu tetap kosong
    meskipun fitur 'self-healing' diklaim sudah jadi.
    """
    try:
        conn, engine = get_db_connection()
        cur = conn.cursor()
        if is_pg_engine(engine):
            cur.execute(
                """INSERT INTO incident_log (error_signature, first_seen, last_seen,
                                            repeat_count, last_error_text, resolved)
                   VALUES (%s, %s, %s, %s, %s, FALSE)
                   ON CONFLICT (error_signature) DO UPDATE SET
                       last_seen = EXCLUDED.last_seen,
                       repeat_count = incident_log.repeat_count + 1,
                       last_error_text = EXCLUDED.last_error_text""",
                (signature[:255], now(), now(), count_at_least, (description or "")[:4000]))
            cur.execute("SELECT repeat_count FROM incident_log WHERE error_signature = %s",
                        (signature[:255],))
            row = cur.fetchone()
        else:
            cur.execute(
                """INSERT INTO incident_log (error_signature, first_seen, last_seen,
                                            repeat_count, last_error_text, resolved)
                   VALUES (?, ?, ?, ?, ?, 0)
                   ON CONFLICT (error_signature) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       repeat_count = incident_log.repeat_count + 1,
                       last_error_text = excluded.last_error_text""",
                (signature[:255], now(), now(), count_at_least, (description or "")[:4000]))
            cur.execute("SELECT repeat_count FROM incident_log WHERE error_signature = ?",
                        (signature[:255],))
            row = cur.fetchone()
        conn.commit()
        conn.close()
        return int(row[0]) if row else count_at_least
    except Exception as exc:  # noqa: BLE001
        print(f"[incident] GAGAL mencatat ke SQL: {exc}", file=sys.stderr)
        return None


def sql_register_skill(skill_id, name, description, file_path):
    """Daftarkan skill hasil belajar ke skills_inventory supaya benar-benar terhitung."""
    try:
        conn, engine = get_db_connection()
        cur = conn.cursor()
        ph = "%s" if is_pg_engine(engine) else "?"
        cur.execute(
            f"""INSERT INTO skills_inventory (id, name, category, level, description,
                                             file_path, auto_learned, use_count, created_at, updated_at)
                VALUES ({ph}, {ph}, 'learned', 1, {ph}, {ph}, TRUE, 0, {ph}, {ph})
                ON CONFLICT (id) DO NOTHING""",
            (skill_id[:50], name[:120], description[:1000], str(file_path), now(), now()))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[incident] GAGAL mendaftarkan skill ke SQL: {exc}", file=sys.stderr)
        return False


def load_tracker():
    if INCIDENT_FILE.exists():
        try:
            with open(INCIDENT_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": "1.0", "threshold_auto_skill": THRESHOLD, "incidents": {}}


def save_tracker(data):
    INCIDENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INCIDENT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def safe_name(text):
    """Nama yang aman untuk berkas, URL, dan id SQL.

    Signature insiden mengandung ':' (mis. 'exit1:python3_PY_import'). ext4 memang
    menerima titik dua, tapi shell glob, URL file://, dan sebagian alat tidak — jadi
    dibersihkan sekali di sini, di tempat nama itu lahir.
    """
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(text)).strip("_")[:60] or "insiden"


def append_index_row(path, locate, bullet, row, must_contain, section=None):
    """File `row` under one named section of an index, and say whether the file really references it.

    Two arguments, two jobs, and the first version of this function confused them:
      * `section` — a regex matched against a HEADING line, naming the section the row belongs to.
      * `locate`  — a regex matching an ENTRY line of that index (a `|…|` table row, or a `- [ … ]`
        bullet), i.e. the shape this index uses, not a heading.
    Round 14 found `locate` being used as if it named a heading (so any heading with a table nearby
    won); round 16 found the same argument still ignored on the bullet branch, the section end
    detected with a bare `startswith("#")` that markdown's up-to-three-space indented headings slip
    past, and the "already indexed" test scanning the WHOLE file, so a row in some other section
    silenced the writer forever. A renumbered heading must never become a silent no-op again — that
    silence is what left two learned skills unindexed in the first place.

    Returns True only when the TARGET SECTION contains a row naming `must_contain`, after the write.
    """
    import re as _re
    target = Path(path)
    if not target.is_file():
        print(f"[!] indeks tidak ada: {target} — skill TIDAK terdafar di sana", file=sys.stderr)
        return False
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    head_pat = _re.compile(r"^\s{0,3}#{1,6}\s")            # markdown allows up to 3 leading spaces
    sec_pat = _re.compile(section) if section else None
    entry_pat = _re.compile(locate) if locate else None

    def _is_row(ln):
        s = ln.strip()
        return (s.startswith("|") or s.startswith("- [") or s.startswith("* [")) and must_contain in s

    # Section bounds: from the heading (or the top of the file when no section is named) to the next
    # heading of any level, or end of file.
    start = 0
    if sec_pat:
        start = next((i for i, ln in enumerate(lines)
                      if head_pat.search(ln) and sec_pat.search(ln)), None)
        if start is None:
            print(f"[!] bagian {section!r} tidak ditemukan di {target} — tidak menulis apa pun",
                  file=sys.stderr)
            return False
    stop = len(lines)
    for k in range(start + 1, len(lines)):
        if head_pat.search(lines[k]):
            stop = k
            break

    if any(_is_row(ln) for ln in lines[start:stop]):
        return True                          # already filed, in this section

    if bullet == "|":
        idx = next((k for k in range(start + 1, stop)
                    if lines[k].lstrip().startswith("|") and lines[k].count("|") >= 2), None)
        if idx is None:
            print(f"[!] tidak ada tabel di bagian {section!r} dari {target} — tidak menulis",
                  file=sys.stderr)
            return False
        end = idx
        while end < stop and lines[end].lstrip().startswith("|"):
            end += 1
    else:
        # The END of the entry run inside this section, so the row joins its siblings instead of
        # splitting the block or landing past the next heading.
        idx = None
        end = None
        for k in range(start + 1, stop):
            if entry_pat and entry_pat.search(lines[k]):
                if idx is None:
                    idx = k
                end = k + 1
        if idx is None:
            # A section with no entries yet is a normal thing to write into — a catalog section that was
            # just created, or the first learned skill of a new checkout. Refusing there is the same
            # mistake as the arbitrary window: the file's shape decides whether the machine can record
            # anything. Land directly under the heading instead, past blank lines.
            end = start + 1
            while end < stop and not lines[end].strip():
                end += 1
        else:
            while end > (idx + 1) and not lines[end - 1].strip():
                end -= 1
    lines.insert(end, row)
    try:
        target.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""))
    except OSError as exc:
        print(f"[!] gagal menulis {target}: {exc}", file=sys.stderr)
        return False
    after = target.read_text(encoding="utf-8").splitlines()
    a_start = next((i for i, ln in enumerate(after)
                    if head_pat.search(ln) and sec_pat and sec_pat.search(ln)), 0) if sec_pat else 0
    a_stop = len(after)
    for k in range(a_start + 1, len(after)):
        if head_pat.search(after[k]):
            a_stop = k
            break
    return any(_is_row(ln) for ln in after[a_start:a_stop])


def generate_skill(incident_id, incident_data):
    LEARNED_DIR.mkdir(parents=True, exist_ok=True)
    skill_file = LEARNED_DIR / f"{safe_name(incident_id)}.md"

    title = incident_id.replace("_", " ").title()
    content = f"""# Learned Skill: {title}

Dokumen skill ini di-generate secara otomatis oleh AI Workstation setelah error terulang {incident_data['count']} kali.

## 1. Gejala / Masalah yang Terjadi
{incident_data.get('description', 'Deskripsi masalah tidak tersedia.')}

## 2. Solusi Permanen / Best Practice
{incident_data.get('solution', 'Lakukan verifikasi environment dan jalankan prosedur mitigasi.')}

## 3. Metadata Insiden
- Terakhir Terjadi: {incident_data.get('last_seen')}
- Total Terulang: {incident_data.get('count')} kali
- Status: BELUM DICEKA (baris ini diisi setelah indeks ditulis; kalau masih tertulis begini,
  berarti generatornya mati sebelum sempat mencatat — itu sendiri adalah kegagalan yang harus dilaporkan)
"""
    skill_file.write_text(content)
    print(f"[+] Berhasil membuat file skill baru: {skill_file}")

    # The status line is filled in AFTER the indices are written, because "mitigated" is a claim about
    # them and cannot be printed before they exist. The first version of this block put the two
    # variables inside the f-string above, which was an UnboundLocalError on every call — found by the
    # round-13 reviewer, reproduced here, and the reason the generator now has a selftest that actually
    # calls it instead of reading it.

    # Register the file in BOTH indices. Two defects were measured here on 2026-10-16, in the two
    # learned skills that no index ever mentioned (`exit1_timeout_300_python3.md` and
    # `exit1_timeout_90_python3.md`, incidents repeated 6x and 9x, each marked `resolved` and each
    # claimed in its own text to be "Auto-Mitigated ke Master Skills"):
    #   * the target heading was the LITERAL string "### 5. Learned Skills (Self-Generated) (…)"; the
    #     master catalog numbers that section "### 8." today, so the substring test failed, the append
    #     was skipped, and NOTHING was printed — a write that silently did nothing, reported as success
    #     by the surrounding prints.
    #   * the entry was labelled `f"{incident_id}.md"`, the RAW signature with its ':', while the file on
    #     disk is `safe_name(incident_id)`. So even a successful append pointed at a filename that does
    #     not exist, and the dedupe test (`skill_file.name not in catalog_text`) could never see it.
    # The heading is now matched by pattern instead of by number, the label is the real filename, and a
    # missing heading returns False — which the skill's own status line then reports, because a claim of
    # "mitigated" that no index backs is exactly the status dishonesty §5 forbids.
    desc = (incident_data.get("description") or title)[:80]
    ok_pack = append_index_row(
        LEARNED_DIR / "SKILL.md", r"^\|.*\|\s*$", "|",
        f"| `{skill_file.name}` | {desc} |", skill_file.name, section=r"(?i)learned skills|hasil belajar")
    ok_master = append_index_row(
        SKILLS_CATALOG, r"^\s*[-*]\s+\[", "-",
        f"- [`{skill_file.name}`](file://{skill_file}) — Solusi auto-generated untuk: {desc}...",
        skill_file.name, section=r"(?i)learned skills|hasil belajar")
    if ok_pack:
        print(f"[+] Indeks pack diperbarui: {LEARNED_DIR / 'SKILL.md'}")
    if ok_master:
        print(f"[+] Master SKILLS.md diperbarui: {SKILLS_CATALOG}")
    status = ("- Status: TERINDEKS (pack + master)\n"
              if (ok_pack and ok_master) else
              f"- Status: BERKAS DITULIS, TIDAK TERINDEKS (pack: {'yes' if ok_pack else 'NO'}, "
              f"master: {'yes' if ok_master else 'NO'}) — engine yang membaca indeks tidak akan "
              "menemukan solusi ini\n")
    body = skill_file.read_text(encoding="utf-8").split("- Status: BELUM DICEKA")[0]
    skill_file.write_text(body + status)

    # Daftarkan ke skills_inventory supaya skill ini TERHITUNG sebagai memori yang belajar.
    # Tanpa langkah ini, klaim 'self-healing' tidak pernah bisa dibuktikan dari database.
    skill_id = f"learned_{safe_name(incident_id)}"[:50]
    if sql_register_skill(skill_id, title,
                          incident_data.get("description", title)[:1000], skill_file):
        print("[+] skills_inventory diperbarui (auto_learned=true).")

    try:
        conn, engine = get_db_connection()
        cur = conn.cursor()
        ph = "%s" if str(engine).upper() == "POSTGRESQL" else "?"
        cur.execute(f"UPDATE incident_log SET resolved = {'TRUE' if str(engine).upper() == 'POSTGRESQL' else '1'}, "
                    f"auto_skill_id = {ph} WHERE error_signature = {ph}",
                    (skill_id, incident_id[:255]))
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[incident] gagal menandai insiden terselesaikan: {exc}", file=sys.stderr)

    # Kirim notifikasi desktop ke user di Pop!_OS
    try:
        subprocess.Popen([
            "notify-send", "-a", "AI Workstation Core",
            "🧠 Skill Baru Terbentuk Otomatis!",
            f"Error '{incident_id}' terulang 5x dan telah dipelajari menjadi skill permanen."
        ])
    except Exception:
        pass


def record_incident(incident_id, description, solution=""):
    tracker = load_tracker()
    incidents = tracker.setdefault("incidents", {})

    # SQL adalah sumber kebenaran jumlah; JSON hanya cermin yang bisa dibaca manusia.
    sql_count = sql_record(incident_id, description)

    if incident_id not in incidents:
        incidents[incident_id] = {
            "count": 1,
            "description": description,
            "solution": solution,
            "first_seen": now(),
            "last_seen": now(),
            "converted_to_skill": False
        }
    else:
        incidents[incident_id]["count"] += 1
        incidents[incident_id]["last_seen"] = now()
        if description:
            incidents[incident_id]["description"] = description
        if solution:
            incidents[incident_id]["solution"] = solution

    count = sql_count if sql_count else incidents[incident_id]["count"]
    incidents[incident_id]["count"] = count
    print(f"[*] Insiden '{incident_id}' tercatat. (Frekuensi: {count}/{THRESHOLD}"
          f"{' dari SQL' if sql_count else ', SQL tidak terjangkau'})")

    if count >= THRESHOLD and not incidents[incident_id].get("converted_to_skill", False):
        print(f"[!] AMBANG BATAS {THRESHOLD}X TERCAPAI: Memicu pembentukan skill otomatis...")
        generate_skill(incident_id, incidents[incident_id])
        incidents[incident_id]["converted_to_skill"] = True

    save_tracker(tracker)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        i_id = sys.argv[1]
        i_desc = sys.argv[2]
        i_sol = sys.argv[3] if len(sys.argv) > 3 else "Gunakan metode fallback yang sesuai."
        record_incident(i_id, i_desc, i_sol)
    elif len(sys.argv) == 2 and sys.argv[1] == "list":
        t = load_tracker()
        print(json.dumps(t, indent=2))
    else:
        print("Usage: incident_recorder.py <incident_id> <description> [solution]")
        print("       incident_recorder.py list")
