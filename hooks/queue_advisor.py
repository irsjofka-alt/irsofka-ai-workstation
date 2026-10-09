"""Penasehat antrean — memberi tahu CLI apa yang sedang menunggu di SQL.

Alasannya konkret, bukan arsitektural: perintah operator masuk lewat Dashboard ke PostgreSQL, dan
sebuah sesi yang sudah TERBUKA tidak memuat hook baru — hook dibaca saat sesi mulai. Itu terukur
2026-10-08 11:05: item 86 antre jam 11:00:59 dan tidak ada satu pun tembakan hook tercatat setelah
10:50:25, padahal sesi ini berhenti beberapa kali. Jadi "IDLE" yang sebenarnya adalah *sesi yang
tidak tahu ada pekerjaan*, bukan mesin yang tidak bisa mengirim.

Fungsi ini menutup bagian itu tanpa tangan: ia hanya MEMBACA dan menempelkan hasilnya sebagai
`additionalContext` pada peristiwa `SessionStart` dan `UserPromptSubmit`. Tidak ada klaim, tidak ada
kirim, tidak ada `decision` — sebuah nasihat yang bisa mengubah keadaan adalah tangan yang menyamar
menjadi catatan.

IDLE/WORKING tidak lagi disimpulkan di jalur ini: CLI yang memakainya yang melaporkan. Deteksi
dari LUAR tetap milik `work_order.py heartbeat()` (dua sinyal independent, dan `UNKNOWN` bukan
`idle`), dan jalur itu yang berhak mengetuk pane — sesudah di-`arm` dari TTY.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".ai-station" / "tools"))

MAX_LISTED = 5
# F9.1: tiga angka yang disuapkan ke prompt, bukan satu paragraf tentang apa yang sedang terjadi.
ENV_EXIT_HISTORY = 5
ENV_PROCESSES_LISTED = 6
BASELINE_DIR = Path.home() / ".ai-station" / "logs" / "tabs"
# Mesin milik sesi yang memanggil, dibaca dari argv supaya satu berkas bisa terdaftar di dua CLI.
# Dipakai untuk MEMILAH laporan, bukan untuk memilih pekerjaan: menampilkan antrean milik mesin lain
# membuat engine ini seolah-olah punya sesuatu untuk dikerjakan.
THIS_ENGINE = (sys.argv[1] if len(sys.argv) > 1 else "qoder")


def read_input() -> dict:
    raw = (sys.stdin.read() or "").strip()
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def session_cwd(data: dict):
    raw = data.get("cwd")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    paths = data.get("workspacePaths")
    if isinstance(paths, list) and paths and isinstance(paths[0], str) and paths[0].strip():
        return paths[0].strip()
    return None


def select_rows(rows, cwd):
    """Baris yang memang untuk sesi ini — memakai resolver yang sama dengan hook dan drainer.

    Dipisah dan murni supaya bisa diuji dengan daftar sungguhan. Versi sebelumnya hanya
    MEMBERI TANDA "[proyek lain]" dan memperlakukan `ws_path` kosong sebagai "milik semua proyek",
    sementara hook menolak baris seperti itu di proyek mana pun selain REPO: nasihat yang bilang
    "ini untukmu" lalu ditolak mesin adalah cara membuat operator mengira perintahnya hilang.
    """
    import work_order as wo
    here = None
    if cwd:
        try:
            here = Path(cwd).expanduser().resolve(strict=False)
        except OSError:
            here = None
    if here is None:
        # Sesi tidak melaporkan proyeknya. Melaporkan semua baris milik mesin ini akan menyuruh
        # engine mengerjakan antrean yang akan ditolak hook-nya sendiri, dan dari kursinya itu
        # terlihat seperti perintah yang hilang. Yang jujur adalah diam.
        return []
    keep = []
    for r in rows:
        if (r.get("cli_engine") or "").strip() != THIS_ENGINE:
            continue
        if wo.resolve_workspace(r) != here:
            continue
        keep.append(r)
    return keep


def env_exit_codes(conn, engine, wo, limit=ENV_EXIT_HISTORY):
    """Perintah terakhir mesin ini beserta ANGKA KELUARNYA — dibaca dari action_log.

    Bagian pertama dari F9.1. Yang diminta adalah keadaan yang deterministik, jadi yang dibaca
    adalah kolom `exit_code`, bukan ringkasan yang ditulis engine tentang keberhasilannya.

    `kind='command_exit'`, BUKAN `'command'`. Terukur 2026-10-09 pada 3254 baris `command` milik
    qoder: NOL di antaranya punya exit_code, sementara 3272 baris `command_exit` punya semuanya.
    Filter yang salah ini tidak menghasilkan error — ia menghasilkan daftar kosong, dan daftar
    kosong pada laporan "perintah terakhir" terbaca persis seperti "belum ada perintah".

    Tapi `command_exit.summary` sendiri berisi `aborted=False out_chars=1975`, BUKAN perintahnya:
    teks perintah hidup di baris `command` yang mendahuluinya. Jadi keduanya diambil lalu dipasangkan
    di sini. Versi pertama menampilkan ringkasan baris exit saja, sehingga laporannya berbunyi
    "perintah terakhir: aborted=False out_chars=568 exit=0" — angka keluar tanpa tahu perintah mana,
    yaitu keadaan yang tidak bisa dipakai untuk memutuskan apa pun.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    try:
        rows = wo.run(conn, engine,
                      f"SELECT kind, summary, tool, exit_code, duration_ms FROM action_log "
                      f"WHERE engine={ph} AND kind IN ('command','command_exit') "
                      f"ORDER BY ts DESC LIMIT {int(limit) * 4}",
                      (THIS_ENGINE,))
    except Exception as exc:  # noqa: BLE001 — ledger yang tidak terbaca DILAPORKAN, tidak ditebak
        return [f"action_log tidak terbaca ({type(exc).__name__}) — UNKNOWN"]
    # Urutan baris adalah DESC waktu, jadi `command_exit` mendahului `command` pasangannya di
    # daftar ini. Versi pertama mengumpulkan `command` ke `pending` lalu membacanya saat ketemu
    # exit — terbalik, dan hasilnya adalah label dari perintah yang salah: laporan yang lebih
    # buruk dari tidak ada laporan, karena bentuknya meyakinkan.
    out, following = [], None
    for r in rows:
        if r["kind"] == "command_exit":
            if r.get("exit_code") is None:
                continue
            following = r
            continue
        if following is None:
            continue
        label = (r.get("summary") or following.get("tool") or "?")[:58]
        out.append(f"{label} -> exit={following['exit_code']}")
        following = None
        if len(out) >= limit:
            break
    return out


def env_processes(cw):
    """Pohon proses: siapa yang menetap, siapa yang nyasar, beserta RSS.

    Bagian kedua dari F10.4/§4 — `call_workers.audit(cut_now=False)` adalah satu-satunya pembaca
    keadaan itu, dan `cut_now=False` di sini bukan kehati-hatian kosong: hook yang berjalan di
    setiap prompt lalu MEMATIKAN proses adalah tangan yang menyamar sebagai catatan, dan ia akan
    memotong milik operator sendiri.
    """
    try:
        a = cw.audit(cut_now=False)
    except Exception as exc:  # noqa: BLE001
        return [f"audit tidak terbaca ({type(exc).__name__}) — UNKNOWN, bukan kosong"]
    if not a.get("ok", True):
        return ["audit menolak: tabel proses tidak terbaca — UNKNOWN, bukan kosong"]
    out = []
    # `persistent` berisi {"pane": ..., "pid": ...} — bukan nama mesin. Kolomnya dibaca dari
    # keluaran audit sungguhan, bukan dari dugaan bentuk: versi pertama mengambil `identity`/`comm`
    # yang tidak ada dan menghasilkan "menetap=?" empat kali, yaitu laporan yang terlihat seperti
    # pengukuran padahal hanya berisi tanda tanya.
    persistent = sorted({f"{p.get('pane')}#{p.get('pid')}" for p in (a.get("persistent") or [])})
    strays = a.get("strays") or []
    out.append("menetap=" + (",".join(persistent[:ENV_PROCESSES_LISTED]) or "tidak ada"))
    out.append(f"nyasar={len(strays)} ({int(a.get('stray_mb') or 0)} MB)")
    # Ringkasan, BUKAN isi. `protected` adalah daftar dict berisi cmdline, path exe, rss, age dan
    # alasan — menempelkannya apa adanya mengirim ~1,5 KB repr Python ke SETIAP prompt, yaitu
    # persis konteks yang kita sedang hemat, dan tidak ada satu pun angka di dalamnya yang bisa
    # dibaca orang. Yang menentukan keputusan hanyalah: berapa, mesin apa, dan berapa besar.
    prot = a.get("protected") or []
    if prot:
        by_bin = {}
        for p in prot:
            b = str(p.get("binary") or "?")
            e = by_bin.setdefault(b, [0, 0])
            e[0] += 1
            e[1] += int(float(p.get("rss_mb") or 0))
        out.append("terlindungi=" + ", ".join(f"{k}x{v[0]}~{v[1]}MB"
                                              for k, v in sorted(by_bin.items())))
    inflight = a.get("in_flight")
    if inflight is not None:
        out.append(f"sedang-berjalan={len(inflight)}")
    return out


def env_pane_delta(cv, engine):
    """Apa yang BERUBAH di pane ini sejak pembacaan terakhir, sebagai hitungan baris + hash.

    Bagian ketiga dari F9.1. Delta, bukan isi: menempelkan 31 baris pane ke setiap prompt
    memboroskan konteks yang sedang kita hemat, dan yang dibutuhkan engine adalah "layar berubah
    sebanyak ini" untuk tahu apakah ia sedang sendirian di pane itu.

    Baseline ditulis ke logs/tabs/<engine>.pane_env — sebatas itu hak tulis hook ini, dan itu
    sebabnya jalur ini tidak pernah menyentuh $HOME di luar logs/.
    """
    try:
        text = cv.capture_pane(engine)
    except Exception as exc:  # noqa: BLE001
        return [f"pane tidak terbaca ({type(exc).__name__}) — UNKNOWN, bukan diam"]
    if text is None:
        return ["pane tidak terbaca — UNKNOWN, bukan diam"]
    import hashlib
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    digest = hashlib.sha256("\n".join(lines).encode("utf-8", "replace")).hexdigest()[:8]
    path = BASELINE_DIR / f"{engine}.pane_env"
    prev = None
    try:
        if path.is_file():
            prev = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        prev = None
    try:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    if not prev:
        return [f"{len(lines)} baris terlihat, belum ada pembacaan sebelumnya (baseline={digest})"]
    old = set(prev)
    new = set(lines)
    return [f"+{len(new - old)}/-{len(old - new)} baris sejak pembacaan terakhir (baseline={digest})"]


def build_env_note(engine_name):
    """Rangkai pengukur F9.1 + peringatan F9.2 jadi satu blok. None kalau tidak ada yang terbaca.

    Blok ini BUKAN perintah dan BUKAN narasi: tiap barisnya adalah angka dari sebuah alat ukur.
    Kegagalan satu pengukur tidak menghapus yang lain — itu penting karena keadaan "tidak ada
    angka" harus bisa dibedakan dari "ada angka dan angkanya nol".

    Koneksi ledger ditutup SEKALI di akhir, lewat finally. Versi pertama menutupnya di tengah
    badan fungsi, jadi blok anti-pola yang datang kemudian memanggil dengan koneksi mati dan
    selalu melapor "tidak terukur" — kegagalan yang terlihat seperti keadaan sistem, padahal
    bentuk fungsi.
    """
    import cross_verify as cv
    import call_workers as cw
    import work_order as wo
    try:
        conn, engine = wo.connect()
    except Exception:  # noqa: BLE001 — tanpa ledger, bagian berbasis data tidak ada, yang lain tetap
        conn, engine = None, None
    sections = []
    try:
        try:
            if conn is not None:
                ex = env_exit_codes(conn, engine, wo)
                if ex:
                    sections.append("exit-code terakhir:\n"
                                    + "\n".join(f"  · {x}" for x in ex))
                bad = wo.failing_shapes(conn, engine)
                if bad:
                    sections.append("anti-pola (jangan ulangi bentuk ini tanpa mengubah sesuatu):\n"
                                    + "\n".join(
                                        f"  · {wo.shape_warning(b['shape'])} [{b['fails']}x]"
                                        for b in bad))
            else:
                sections.append("exit-code: ledger tidak terhubung — UNKNOWN, bukan tidak ada")
        except Exception as exc:  # noqa: BLE001 — satu pembacaan gagal tidak menghapus yang lain
            sections.append(f"ledger: tidak terukur ({type(exc).__name__})")
        try:
            sections.append("proses: " + " · ".join(env_processes(cw)))
        except Exception as exc:  # noqa: BLE001
            sections.append(f"proses: tidak terukur ({type(exc).__name__})")
        try:
            sections.append("delta pane: " + " · ".join(env_pane_delta(cv, engine_name)))
        except Exception as exc:  # noqa: BLE001
            sections.append(f"delta pane: tidak terukur ({type(exc).__name__})")
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
    if not sections:
        return None
    return "KEADAAN LINGKUNGAN (terukur, bukan narasi):\n" + "\n".join(sections)


def build_note(cwd: str) -> str | None:
    """Baca antrean dan rendahkan menjadi catatan. None berarti tidak ada yang perlu dikatakan."""
    if not cwd:
        # Bukan "tidak ada yang untukmu" — itu pernyataan tentang proyek yang tidak kita ketahui.
        # Yang jujur adalah tidak berkata apa pun.
        return None
    import work_order as wo
    conn, engine = wo.connect()
    try:
        return _note_from(conn, engine, cwd, wo)
    finally:
        # Ditemukan audit Gemini 2026-10-08 12:4x: hook ini dipanggil SETIAP prompt dan setiap
        # panggilan membuka koneksi yang tidak pernah ditutup. Tidak meledak hari ini karena
        # pool PostgreSQL sabar; ia akan meledak di sesi yang panjang, dan itu jenis kegagalan
        # yang datang sendiri sambil kita tidak melihatnya.
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _note_from(conn, engine, cwd, wo):
    ap = wo.autopilot(conn, engine) or {}
    # `ready_items`, bukan SQL sendiri. Ini kesalahan yang baru saja dikutuk putaran lima dan
    # kulakukan lagi di berkas yang sama: query `status='PENDING'` milik sendiri, tanpa `norm()`,
    # tanpa lease, tanpa cooldown. Bedanya kali ini ketahuan HIDUP — penasihat melaporkan baris
    # #86 dan #88 yang sudah COMPLETED ke sesi ini, persis saat ia dipasang sebagai pagar.
    ready = wo.ready_items(conn, engine, respect_due=True, origin=wo.ORIGIN_OPERATOR)
    mine_all = [x for x in ready if norm_ok(x)]
    if not mine_all:
        return None
    mine = select_rows(mine_all, cwd)
    # Dua alasan berbeda tidak boleh dapat satu label: baris milik MESIN lain dan baris milik
    # mesin ini tapi PROYEK lain adalah dua hal berbeda, dan menyebut keduanya "MESIN LAIN"
    # membuat operator menyalahkan mesin yang salah.
    other_engine = [x for x in mine_all if (x.get("cli_engine") or "").strip() != THIS_ENGINE]
    other_project = [x for x in mine_all
                     if (x.get("cli_engine") or "").strip() == THIS_ENGINE and x not in mine]
    return render_note((ap.get("mode") or "OFF").upper(), ap.get("level"),
                       mine, other_engine, other_project)


def norm_ok(row):
    """Baris siap yang benar-benar menunggu manusia, bukan yang sedang dikerjakan mesin."""
    return (row.get("status") or "").upper() == "PENDING"


def render_note(mode, level, mine, other_engine, other_project):
    """Bentuk kalimatnya. Terpisah dan murni supaya bisa diuji tanpa menyentuh database.

    `build_note` membuka koneksi sungguhan; mengujinya dari selftest berarti hasilnya bergantung
    pada apa yang kebetulan ada di antrean produksi hari ini.
    """
    lines = [f"ANTREAN OPERATOR untuk {THIS_ENGINE} (mode {mode}, tangan {level}):"]
    if not mine:
        lines.append("  tidak ada yang untukmu")
    for row in mine[:MAX_LISTED]:
        lines.append(f"  #{row['id']} {str(row['title'])[:90]}")
    if len(mine) > MAX_LISTED:
        lines.append(f"  ... {len(mine) - MAX_LISTED} lagi untuk {THIS_ENGINE}")
    # Dua alasan berbeda tidak boleh dapat satu label: baris milik MESIN lain dan baris milik
    # mesin ini tapi PROYEK lain adalah dua hal berbeda, dan menyebut keduanya "MESIN LAIN"
    # membuat operator menyalahkan mesin yang salah.
    if other_engine:
        lines.append(f"  ({len(other_engine)} baris menunggu untuk MESIN LAIN — jangan diambil)")
    if other_project:
        lines.append(f"  ({len(other_project)} baris milik mesin ini tapi PROYEK LAIN — "
                     "akan ditolak hook, bukan hilang)")
    # LAPORAN, bukan perintah. Versi sebelumnya menyuruh "kerjakan yang untukmu" untuk baris yang
    # belum diklaim siapa pun — jalur samping di luar disiplin: tidak ada lease, tidak ada cek
    # pemutus, tidak ada cek working-tree kotor. Yang menyalurkan pekerjaan adalah klaim.
    lines.append("Ini LAPORAN, bukan perintah: jangan mulai mengerjakan baris mana pun dari sini. "
                 "Yang menyalurkan pekerjaan adalah hook pada batas giliran, dan itu hanya lewat "
                 "jalur klaim — pemutus malam, aturan tree kotor, dan satu-perintah-aktif "
                 "ditegakkan di sana, bukan di catatan ini.")
    if mode == "OFF":
        lines.append("Mode OFF: tidak ada yang akan disalurkan sampai sakelar dipindahkan "
                     "oleh manusia dari Dashboard.")
    return "\n".join(lines)


def main() -> int:
    data = read_input()
    cwd = session_cwd(data) or ""
    # F9.1 berjalan PARALEL dengan catatan antrean dan tidak bergantung padanya: judul itemnya
    # sendiri menyebut "(jalan paralel, tidak bergantung antrean)". Kalau blok lingkungan ditempel
    # ke dalam build_note, maka sesi dengan antrean kosong — keadaan normal, dan keadaan di mana
    # justru keadaan lingkungan paling dibutuhkan — tidak menerima apa pun.
    parts = []
    try:
        note = build_note(cwd)
        if note:
            parts.append(note)
    except Exception:  # noqa: BLE001 — nasihat yang gagal tidak boleh menghentikan seseorang
        pass
    try:
        env = build_env_note(THIS_ENGINE)
        if env:
            parts.append(env)
    except Exception:  # noqa: BLE001
        pass
    if not parts:
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": data.get("hook_event_name") or "SessionStart",
        "additionalContext": "\n\n".join(parts)}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
