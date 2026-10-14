"""Work order — satu-satunya modul yang punya kosakata tugas: klaim, lease, bukti, dan antrean manusia.

Roadmap adalah prosa; mesin tidak bisa memilih pekerjaan dari prosa. Modul ini mengubah tiap
butir menjadi baris yang bisa dibaca: `claim` atomik (dua engine tidak mengerjakan item sama),
`lease` yang kedaluwarsa supaya tugas tidak terkunci selamanya, dan `evidence` yang wajib berisi
perintah yang benar-benar lolos sebelum sebuah item boleh disebut COMPLETED.

Autopilot (`F10.8`) hanya mengubah SIAPA yang memicu, bukan APA yang boleh dikerjakan: status
mesin tersimpan di tabel `autopilot_state`, dan jalur resume membaca kolom itu lebih dulu.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_state import get_db_connection, DB_SQLITE_PATH  # noqa: E402

# Kosakata status. IN_PROGRESS dibiarkan utuh pada baris lama: menulis ulang sejarah laporan
# adalah cara tercepat membuat ledger tidak lagi dipercaya (§12, "reports are derived").
STATUS = ("PENDING", "CLAIMED", "WORKING", "BLOCKED", "HUMAN", "PARKED",
          "COMPLETED", "FAILED", "UNAVAILABLE")
ALIAS = {"IN_PROGRESS": "CLAIMED", "DONE": "COMPLETED"}

LEASE_SECONDS = 900          # satu item tanpa perpanjangan lease 15 menit dianggap lepas
MIN_SAMPLE_GAP = 1           # detik; di bawahnya "pane tidak berubah" bukan pembacaan apa pun
MAX_RESUME = 3               # item yang butuh 4 sentuhan bukan tertidur, tapi macet
# Batas injeksi per baris. `MAX_RESUME` menjaga CONTINUE ke item yang SUDAH dipegang; yang ini
# menjaga perintah BARU yang disuntikkan ke sesi yang berhenti. Tanpa batas, satu baris yang tidak
# pernah di-complete (misal mesinnya tersangkut di antrean model) akan disuntik ulang setiap kali
# lease 900 dtk-nya kedaluwarsa, selamanya, dan tidak ada satu pun angka yang melaporkan itu.
MAX_INJECT = 3
# Alasan penolakan yang boleh dibatalkan oleh bukti "mesin baru saja diam". Hanya keluarga
# "sedang sibuk": semuanya akan lolos kalau pane sudah idle, jadi menahannya sampai jeda habis
# hanya menambah latensi tanpa menambah keamanan.
BUSY_RETRY_REASONS = ("PANE_NOT_QUIET", "ENGINE_BUSY")
EVIDENCE_LIMIT = 900
# Circuit breaker (temuan reviewer Gemini, 2026-10-08). Tanpa ini sebuah item yang gagal dengan
# exit code cepat akan diklaim-lepas-gagal lagi tanpa henti, dan kuota mingguan habis dalam
# hitungan menit sementara mesin terlihat sangat sibuk.
REPO = Path.home() / ".ai-station"
NIGHT_MAX_FAILURES = 3
NIGHT_MAX_ITEMS = 25
# Dispatch verifikasi per jendela ON. Lebih kecil daripada batas item dengan sengaja: satu item
# yang sulit bisa memanggil verifikasi berkali-kali, dan yang mau dihentikan adalah pengeluaran
# kuota, bukan banyaknya pekerjaan yang kelar.
NIGHT_MAX_VERIFICATIONS = 8
FAILURE_LOOKBACK = 12 * 3600   # detik; rentetan di luar ini bukan tanggungan malam ini
# Tiga keadaan mesin, dan ketiganya tertulis sebagai SATU kata di satu kolom. `SEMI` = otomatis
# dengan orang di meja (hanya perintah yang dia tulis sendiri yang diambil); `ON` = mesin ini
# milik engine, termasuk antrean roadmap; `OFF` = tidak ada yang bergerak.
#
# `OFF` pernah dibuang dari kosakata atas perintah operator 2026-10-08 ("OFF dibuang saja, hanya
# SEMI dan FULL AUTO"), dan berhenti saat itu jadi kata yang menutup jendela TANPA menulis mode
# baru. Hasilnya terukur pada 2026-10-09: orang menekan berhenti, `mode` tetap terbaca "SEMI",
# dan Dashboard menulis `tercatat: SEMI` di atas mesin yang sudah mati — dua kata untuk satu
# keadaan, persis yang dilarang §12. Operator membalik perintahnya sendiri pagi itu: "buat 3 panel
# saja (SEMI) (OFF) (FULL AUTO) agar tidak ambigu, jadi jelas OFF ya off". Jadi OFF kembali
# sebagai state yang disimpan, bukan sebagai efek samping dari sebuah jendela di masa lalu.
#
# `STOP_WORDS` tetap ada karena tangan harus bisa berhenti dari shell tanpa membuka Dashboard, dan
# sebuah typo harus tetap berarti berhenti. Kata-kata itu sekarang alias menuju `OFF`, bukan
# keadaan keempat.
MODES = ("SEMI", "OFF", "ON")
OFF_MODE = "OFF"
STOP_WORDS = ("STOP", "BERHENTI", "MATI")
_MEASURE = object()          # pembeda "belum diukur" dari "diukur tapi gagal dibaca"


def _banner(action, detail, urgent=False):
    """Satu-satunya jalur modul ini ke desktop. Tidak pernah melempar; kegagalan dilaporkan apa ada.

    Kegagalan kirim TIDAK dibiarkan hilang. Operator meminta notifikasi justru supaya tidak perlu
    membuka konsol untuk tahu ada yang menunggunya; kalau jalurnya mati dalam diam — dan mesin ini
    sudah membuktikan satu jalur notifikasi bisa mati diam selama berhari-hari karena binernya tidak
    ada — maka satu-satunya pengganti perhatian itu menjadi tidak terlihat persis saat ia dibutuhkan.
    Jadi kegagalan ditulis ke log malam, dan nama peristiwanya dibuat sendiri: BANNER_FAILED.
    """
    try:
        import notify
        out = notify.notify_event(action, detail, urgent=urgent)
    except Exception as exc:  # noqa: BLE001 — banner yang gagal bukan alasan untuk menggagalkan kerja
        # Jejak ditulis DI SINI juga. Cabang ini adalah kegagalan yang paling mungkin terjadi di
        # mesin ini — `notify-send` hilang selama berhari-hari tanpa satu pun jalur yang mengaku —
        # dan kalau import module-nya saja gagal lalu hanya mengembalikan dict, satu-satunya saksi
        # adalah pemanggil yang tidak membacanya.
        _trace_banner_failure(action, f"{type(exc).__name__}: {exc}", BANNER_LOG)
        return {"sent": False, "err": f"{type(exc).__name__}: {exc}"}
    if not out.get("sent"):
        _trace_banner_failure(action, out.get("err") or out, BANNER_LOG)
    return out


def _trace_banner_failure(action, why, events_path=None):
    """Satu baris `banner_failed` — supaya 'tidak ada notifikasi' bisa dibedakan dari 'tidak ada yang perlu'.

    Jalur berkasnya bisa dipindah supaya tes tidak menulis ke berkas peristiwa sungguhan: sebuah tes
    yang meninggalkan jejak `banner_failed` di berkas nyata akan dibaca gerbang sebagai keadaan
    produksi, dan itu bukti palsu yang dibuat oleh alat yang seharusnya membuktikan.
    """
    try:
        import datetime as _dt
        ev = Path(events_path or (REPO / "logs" / "station_events.jsonl"))
        ev.parent.mkdir(parents=True, exist_ok=True)
        with ev.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _dt.datetime.now().astimezone().isoformat(
                timespec="seconds"), "kind": "banner_failed", "event": str(action)[:60],
                "why": str(why)[:200]}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — tidak ada jalur kedua untuk mengeluh
        pass


banner = _banner   # sambatan uji — pola yang sama dipakai `tree_dirty` di atas
BANNER_LOG = None  # jalur peristiwa jejak banner; None = berkas nyata, disuntik oleh selftest


def _deliver_now(conn, engine, item_id):
    """Antarkan satu perintah yang barusan ditulis, lewat jalur drainer yang sama dengan tick."""
    import drainer
    return drainer.deliver_now(conn, engine, int(item_id))


deliver_now = _deliver_now   # sambatan uji — lihat `check("jalur kirim disuntik ...")` di selftest


def _mute_banners():
    """Pasang sambatan uji; kembalikan (daftar panggilan, fungsi asli).

    Yang direkam bukan hanya "jangan kirim": panggilan yang tercatat membuat jalur banner bisa
    DIUJI seperti jalur lain — tes bahwa pertanyaan ESCALATED benar-benar menghasilkan satu
    banner, dan bukan tes hening yang lulus karena tidak ada yang memantau sama sekali.
    """
    calls = []
    orig = globals()["banner"]

    def fake(action, detail="", urgent=False):
        calls.append((action, str(detail), bool(urgent)))
        return {"sent": False, "muted": True}

    globals()["banner"] = fake
    return calls, orig


def tree_dirty(repo=None) -> bool | None:
    """True/False dari git; None kalau git tidak bisa dibaca — dan None BUKAN berarti bersih.

    `repo` hanya ada supaya gerbang push bisa diuji pada pohon yang angkanya diketahui, bukan pada
    repo nyata yang berubah setiap kali seseorang commit. Tanpa itu, tes "tree bersih tidak boleh
    menyebut tree kotor" mengadili keadaan mesin yang tidak bisa ia perintahkan.
    """
    try:
        import subprocess
        r = subprocess.run(["git", "-C", str(repo or REPO), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return bool(r.stdout.strip())


def items_done_in_window(conn, engine, on_epoch):
    """Berapa item COMPLETED sejak jendela ON dibuka — diturunkan dari ledger, bukan dihitung orang.

    Bug yang diikat di sini diukur dua kali, karena keduanya lolos tanpa suara: `%%s` menghasilkan
    teks `'%s'`, dan `strftime()` sendiri juga mengembalikan TEKS. SQLite membandingkan teks dengan
    integer memakai aturan "integer selalu lebih kecil dari teks", jadi `strftime('%s', …) >= 12345`
    benar untuk SEMUA baris — jendela yang tampak menyaring padahal tidak menyaring apa pun. CAST
    memaksa perbandingan terjadi di tanah angka; `unixepoch()` setara di SQLite >= 3.38 tapi tidak
    ada jaminan versi sistem yang jadi fallback, jadi bentuk lama yang dipaksa yang dipakai.

    PostgreSQL menyimpan jebakan yang sama dengan ukuran berbeda, dan jebakannya justru yang
    ditemukan terakhir: `completed_at` adalah TIMESTAMP tanpa zona — ditulis lewat
    CURRENT_TIMESTAMP dalam waktu lokal — dan `EXTRACT(EPOCH FROM <timestamp>)` memperlakukan
    nilai tanpa zona sebagai UTC. Terukur 2026-10-08: selisihnya +25 200 detik, tepat sebesar
    offset Asia/Jakarta; bentuk `::timestamptz` menyamakannya dengan `time.time()` sampai detik.
    Cast itu bukan hiasan — tanpanya tiap item yang baru selesai malam ini terlihat berada di
    masa depan, dan anggaran item per jendela membaca angka yang salah tanpa pernah bilang.

    Batas yang diterima apa adanya: `completed_at` beresolusi satu detik, jadi baris yang selesai
    pada detik yang sama dengan dibukanya jendela ikut terhitung (>=). Anggaran semalam adalah
    bilangan puluhan, dan selisih satu item pada detik perataan tidak mengubah keputusan apa pun;
    membuat jendela terlihat lebih tajam dari alat ukurnya sendiri hanyalah cara lain mengarang
    laporan.
    """
    if not on_epoch:
        return 0
    rows = run(conn, engine,
               "SELECT COUNT(*) AS n FROM quest_tasks WHERE status='COMPLETED' "
               "AND EXTRACT(EPOCH FROM completed_at::timestamptz) >= %s"
               if engine == "POSTGRESQL" else
               "SELECT COUNT(*) AS n FROM quest_tasks WHERE status='COMPLETED' "
               "AND CAST(strftime('%s', completed_at) AS INTEGER) >= ?",
               (int(on_epoch),))
    return int(rows[0]["n"]) if rows else 0


def consecutive_failures(conn, engine):
    """Kegagalan BERURUTAN sejak keberhasilan terakhir — bukan jumlah kegagalan semalam.

    Perbedaannya adalah seluruh isi fungsi ini, dan ia ditemukan karena labelnya menipu:
    `night_state` melaporkan "kegagalan berturut-turut" sambil menghitung SETIAP baris
    FAILED/PARKED dalam 12 jam. Dalam satu malam panjang, tiga kegagalan yang tidak saling
    berhubungan — item A gagal, lalu B dan C selesai dengan baik, lalu D gagal — sudah cukup
    untuk menutup seluruh loop, dan loop yang tertutup berarti komputer diam sampai pagi.
    Itulah kegagalan yang ingin dicegah operator, dan pagar yang salah bentuk inilah yang
    menyebabkannya.

    Yang benar-benar harus menghentikan sebuah loop adalah *rentetan* kegagalan tanpa satu pun
    keberhasilan di antaranya: itu tanda mesin mengunyah baris yang sama atau gerbang yang tidak
    akan pernah lolos, bukan tanda bahwa satu item ternyata sulit.

    Dua hal yang membuat hitungan ini mudah salah bentuk, keduanya terukur di selftest:

    1. `completed_at` beresolusi SATU DETIK. Memakai `>` membuang kegagalan pada detik yang sama
       dengan keberhasilan terakhir; memakai `>=` menghitung SEMUA kegagalan di detik itu,
       termasuk yang terjadi SEBELUM keberhasilan. Dua-duanya salah dan dua-duanya diam. Maka
       urutannya (detik, id) — `id` naik bersama tulisan, jadi ia memecah seri ke arah yang
       benar: kegagalan yang ditulis sesudah keberhasilan terakhir dihitung, yang sebelumnya tidak.
    Batas yang jujur: `id` adalah urutan PEMBUATAN baris, bukan urutan penyelesaian. Dua baris
    yang selesai pada detik yang sama bisa berurutan terbalik dari id-nya — tugas lama yang baru
    sekarang gagal, tugas baru yang lebih dulu selesai — dan pada detik itu juga hitungannya bisa
    meleset satu. Yang tidak boleh dilakukan adalah menutup mata terhadap kemungkinan ini: sebuah
    pagar yang dikira tepat padahal hanya kira-kira akan dipakai sebagai alasan untuk tidak
    membangun pagar lain. Di luar satu detik itu urutannya benar, dan `FAILURE_LOOKBACK` menjaga
    agar kesalahan seukuran satu detik tidak pernah mengendap menjadi keadaan permanen.

    2. Tanpa batas tengok, tiga kegagalan dari dua minggu lalu yang tidak pernah diikuti
       keberhasilan mana pun akan menutup loop hari ini juga — dan loop yang tertutup berarti
       komputer diam sampai pagi, kegagalan yang persis ingin dicegah operator. `FAILURE_LOOKBACK`
       membuat pagar hanya melihat yang masih mungkin terjadi malam ini.
    """
    ts = ("EXTRACT(EPOCH FROM completed_at::timestamptz)::bigint"
          if engine == "POSTGRESQL" else "CAST(strftime('%s', completed_at) AS INTEGER)")
    ph = "%s" if engine == "POSTGRESQL" else "?"
    last_win = run(conn, engine,
                   f"SELECT {ts} AS e, id FROM quest_tasks WHERE status='COMPLETED' "
                   # Filter ini bukan hiasan dan hilangnya terukur: `ORDER BY e DESC` menempatkan
                   # NULL PALING AWAL di kedua backend, jadi satu baris COMPLETED warisan tanpa
                   # `completed_at` menjadi "keberhasilan terakhir" dengan e NULL dan id besar.
                   # `int(None or 0)` mengubahnya menjadi 0, dan rentetan pun dihitung seolah tidak
                   # pernah ada yang berhasil — pemutus menutup loop yang sedang bekerja. Ini
                   # justru kegagalan yang pagar ini klaim cegah.
                   f"AND completed_at IS NOT NULL ORDER BY e DESC, id DESC LIMIT 1")
    since_e = int((last_win[0]["e"] if last_win else 0) or 0)
    since_id = int((last_win[0]["id"] if last_win else 0) or 0)
    rows = run(conn, engine,
               f"SELECT COUNT(*) AS n FROM quest_tasks WHERE status IN ('FAILED','PARKED') "
               f"AND {ts} >= {ph} "
               f"AND ({ts} > {ph} OR ({ts} = {ph} AND id > {ph}))",
               (now() - FAILURE_LOOKBACK, since_e, since_e, since_id))
    return int(rows[0]["n"]) if rows else 0


def night_state(conn, engine, dirty=_MEASURE):
    """Apakah malam ini masih aman mengklaim pekerjaan baru.

    Tiga hal yang boleh menutup loop, dan ketiganya harus berarti "berhenti akan lebih aman
    daripada lanjut": jendela yang lewat, rentetan kegagalan tanpa keberhasilan, dan batas item
    per sesi. Yang TIDAK termasuk di sini: satu baris yang gagal. Itu urusan `cooldown` baris itu
    sendiri — ia ditunda dan antrean melanjutkan ke baris berikutnya, karena menghentikan semalam
    gara-gara satu item sulit adalah cara mengubah satu kegagalan menjadi nol pekerjaan.
    """
    ap = autopilot(conn, engine) or {}
    dirty = tree_dirty() if dirty is _MEASURE else dirty
    on_since = int((ap or {}).get("on_epoch") or 0)
    until = int((ap or {}).get("until_epoch") or 0)
    n_fail = consecutive_failures(conn, engine)
    done = int((ap or {}).get("items_done") or 0)
    why = []
    # Jendela ON harus punya batas: `until_epoch` NULL di bawah ON berarti 0, dan 0 < sekarang,
    # jadi ON tanpa menit adalah ON yang mati seketika — itu memang aturannya (tidak ada lari
    # tanpa batas waktu). SEMI boleh berdiri tanpa jendela karena orangnya sedang di meja; tapi
    # kalau SEMI DIBUKA dengan menit (misalnya soak tiga jam yang diminta operator), jendela itu
    # juga menutup loop sendiri — dan kata berhenti (`off`) menutupnya dengan menulis now().
    # `<=`, bukan `<`. Kata berhenti menulis `until_epoch = sekarang`, dan pemeriksaan pada detik
    # yang sama akan menyebut rem itu "belum kedaluwarsa" — satu detik di mana mesin masih boleh
    # mengklaim padahal orang sudah menyuruhnya berhenti. Tidak ada alasan untuk meninggalkan satu
    # detik itu terbuka.
    #
    # `on_since` TIDAK lagi ikut menahan klausa ini. Baris baru lahir dengan `on_epoch` NULL, jadi
    # syarat sebelumnya membuat ON tanpa jendela terbaca sebagai "tidak ada jendela untuk kedaluwarsa"
    # — yaitu ON tanpa batas waktu, tepat yang dilarang aturan "ON expires by itself". Sekarang ON
    # tanpa jendela adalah jendela yang BELUM dibuka, dan itu menutup loop, bukan membukanya.
    if ap.get("mode") == OFF_MODE:
        # OFF adalah SEBUAH KEADAAN, bukan jendela yang kedaluwarsa. Kalimat "jendela OFF lewat"
        # menyiratkan ada jendela yang tinggal dibuka lagi, padahal yang orang minta adalah mesin
        # berhenti. Kata `(dipilih)` ikut ditulis karena kedua permukaan mencetak daftar ini di
        # samping label "BREAKER OPEN": seorang operator yang menekan berhenti sendiri tidak boleh
        # dibacakan penolakan seolah-olah mesinnya gagal.
        why.append(f"mode OFF (dipilih) — {ap.get('off_reason') or 'tidak ada alasan berhenti tercatat'}")
    elif ap.get("mode") in MODES and ((until > 0 and until <= now())
                                    or (ap.get("mode") == "ON" and until <= 0)):
        why.append(f"jendela {ap.get('mode')} {'lewat' if until > 0 else 'belum dibuka'}")
    if n_fail >= NIGHT_MAX_FAILURES:
        why.append(f"{n_fail} kegagalan beruntun tanpa satu pun keberhasilan "
                   f"(batas {NIGHT_MAX_FAILURES})")
    # Dua klausul tree dipisah dari sisanya, BUKAN karena ia kurang penting, melainkan karena ia
    # menahan hal yang berbeda. Bahaya "tree kotor" adalah MENIMBUN pekerjaan mesin di atas berkas
    # orang yang belum selesai — dan itu terjadi di jalur commit/push, bukan di jalur baca-jawab.
    # `dirty_why` membuat perbedaan itu bisa ditegakkan per-baris di `claim()`, dan `blocking`
    # menjaga `ok`/`why` yang dibaca semua permukaan lama tetap seperti adanya.
    dirty_why = []
    if dirty is True:
        dirty_why.append("working tree kotor")
    if dirty is None:
        dirty_why.append("git tidak terbaca — UNKNOWN, bukan bersih")
    why += dirty_why
    # C dari keputusan 46 (dijawab `resolver-byok`/gemini-3.8-flash, setelah dua tingkat agy gagal):
    # SATU kata untuk "anggaran malam" punya dua sumber — konstanta NIGHT_MAX_ITEMS di kode dan
    # kolom `autopilot_state.item_budget` yang disunting operator dari Station. Terukur 04:2x:
    # kolom berisi 8, `items_done_in_window` sudah 14, dan `claim()` tetap mengabulkan karena yang
    # menahan hanyalah konstanta — jadi Dashboard melaporkan angka yang tidak mengikat dan operator
    # membaca "sisa -6" sebagai keadaan yang sebenarnya tidak ada. Yang menang adalah yang
    # LEBIH KETAT, dan alasan yang dilaporkan menyebut number yang sebenarnya menjatuhkan pemutus,
    # bukan yang paling mudah dituduh.
    budget_col = int((ap or {}).get("item_budget") or 0)
    cap = NIGHT_MAX_ITEMS if budget_col <= 0 else min(NIGHT_MAX_ITEMS, budget_col)
    if done >= cap:
        if budget_col > 0 and budget_col == NIGHT_MAX_ITEMS:
            # Kedua sumber menyebut angka yang sama, dan menyebut salah satunya akan menuding
            # pihak yang tidak sendirian bertanggung jawab — persis kelas teks yang membuat orang
            # salah menaikkan angka.
            sumber = f"kolom item_budget == konstanta NIGHT_MAX_ITEMS == {budget_col}"
        elif budget_col > 0 and budget_col < NIGHT_MAX_ITEMS:
            sumber = "kolom item_budget lebih ketat"
        else:
            sumber = "konstanta NIGHT_MAX_ITEMS lebih ketat" if budget_col > NIGHT_MAX_ITEMS \
                else "konstanta NIGHT_MAX_ITEMS (kolom tidak diisi)"
        why.append(f"batas {cap} item per sesi terlampaui ({sumber}; done={done})")
    return {"mode": (ap or {}).get("mode"), "failures_run": n_fail, "items_done": done,
            "item_cap": cap,
            "lookback_seconds": FAILURE_LOOKBACK,
            "tree_dirty": dirty, "ok": not why, "why": why,
            "blocking": [w for w in why if w not in dirty_why], "dirty_why": dirty_why}


def norm(status: str | None) -> str:
    return ALIAS.get(status or "", status or "PENDING")


# --- skema -----------------------------------------------------------------
# Dua jalur seperti sisa workstation: CREATE untuk instalasi baru, ALTER untuk yang lama.
# Waktu disimpan sebagai epoch BIGINT, bukan TIMESTAMP, karena SQLite menulis CURRENT_TIMESTAMP
# dalam UTC sementara PostgreSQL menulis waktu lokal — lease yang membandingkan keduanya akan
# salah selama tujuh jam dan salahnya diam.
CREATE_PG = [
    """CREATE TABLE IF NOT EXISTS quest_tasks (
        id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL,
        status VARCHAR(50) DEFAULT 'PENDING', model_assigned VARCHAR(100),
        cli_engine VARCHAR(50), summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS decisions (
        id SERIAL PRIMARY KEY, question TEXT NOT NULL, options TEXT,
        weight VARCHAR(20) DEFAULT 'heavy', rung_used VARCHAR(50), engine_key VARCHAR(50),
        model_id VARCHAR(120), answer TEXT, state VARCHAR(20) DEFAULT 'OPEN',
        note TEXT, created_epoch BIGINT, resolved_epoch BIGINT,
        item_id BIGINT, answered_by VARCHAR(60))""",
    """CREATE TABLE IF NOT EXISTS autopilot_state (
        id SMALLINT PRIMARY KEY DEFAULT 1, mode VARCHAR(10) DEFAULT 'SEMI',
        on_epoch BIGINT, until_epoch BIGINT, item_budget INT DEFAULT 12,
        items_done INT DEFAULT 0, off_reason TEXT, changed_by VARCHAR(60) DEFAULT 'operator',
        level VARCHAR(16) DEFAULT 'observe', level_until BIGINT)""",
    # Sama seperti salinan SQLite-nya dan dengan alasan yang sama: `path` adalah identitasnya,
    # dan tidak ada kolom yang mengklaim sedang aktif. UNIQUE membuat satu proyek tidak bisa
    # terdaftar dua kali di bawah dua nama, yang adalah cara lain membuat laporan berbeda sendiri.
    """CREATE TABLE IF NOT EXISTS workspaces (
        id SERIAL PRIMARY KEY, display_name VARCHAR(200) NOT NULL,
        path VARCHAR(600) NOT NULL UNIQUE, guidelines TEXT, created_epoch BIGINT)""",
]
CREATE_SQLITE = [
    """CREATE TABLE IF NOT EXISTS quest_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        status TEXT DEFAULT 'PENDING', model_assigned TEXT, cli_engine TEXT,
        summary TEXT, created_at TEXT, completed_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, options TEXT,
        weight TEXT DEFAULT 'heavy', rung_used TEXT, engine_key TEXT, model_id TEXT,
        answer TEXT, state TEXT DEFAULT 'OPEN', note TEXT,
        created_epoch INTEGER, resolved_epoch INTEGER, item_id INTEGER, answered_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS autopilot_state (
        id INTEGER PRIMARY KEY CHECK (id = 1), mode TEXT DEFAULT 'SEMI',
        on_epoch INTEGER, until_epoch INTEGER, item_budget INTEGER DEFAULT 12,
        items_done INTEGER DEFAULT 0, off_reason TEXT, changed_by TEXT DEFAULT 'operator',
        level TEXT DEFAULT 'observe', level_until INTEGER)""",
    # Daftar proyek. Tabel ini TIDAK menjawab "proyek mana yang sedang aktif" — jawaban itu
    # tinggal di satu tempat seperti sebelumnya, symlink brain/workspaces/current_project yang
    # ditulis brain_bridge. Menaruh flag `active` di sini akan membuat workstation punya dua
    # pernyataan yang boleh berbeda tentang identitas proyek, dan engine yang harus memilih di
    # antaranya adalah engine yang berhalusinasi tentang direktori mana yang sedang ia kerjakan.
    """CREATE TABLE IF NOT EXISTS workspaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE, guidelines TEXT, created_epoch INTEGER)""",
]
NEW_COLUMNS = [
    ("quest_tasks", "phase", "VARCHAR(20)", "TEXT"),
    ("quest_tasks", "check_command", "TEXT", "TEXT"),
    ("quest_tasks", "depends_on", "TEXT", "TEXT"),      # daftar id terpisah koma, portabel
    ("quest_tasks", "claimed_by", "VARCHAR(60)", "TEXT"),
    ("quest_tasks", "lease_epoch", "BIGINT", "INTEGER"),
    ("quest_tasks", "evidence", "TEXT", "TEXT"),
    ("quest_tasks", "resume_count", "INT DEFAULT 0", "INTEGER DEFAULT 0"),
    # `inject_count` BUKAN `resume_count`. Yang terakhir menghitung ketikan CONTINUE ke pane yang
    # sudah memegang item; ini menghitung berapa kali satu baris disuntikkan sebagai perintah BARU.
    # Satu kata untuk dua hal membuat anggaran yang dibaca orang tidak sama dengan anggaran yang
    # mesin pakai, jadi keduanya punya kolom sendiri-sendiri, seperti yang dituntut §12.
    ("quest_tasks", "inject_count", "INT DEFAULT 0", "INTEGER DEFAULT 0"),
    # F10.5: peringkat memori. Nullable berarti "belum dinilai", BUKAN nol — bedanya dibaca
    # session_ingestor sebagai "catatan baru, angkat ke depan", dan angka 0 akan menenggelamkannya.
    ("world_memory", "rank_score", "INTEGER", "INTEGER"),
    ("quest_tasks", "escalation_reason", "TEXT", "TEXT"),
    ("quest_tasks", "due_epoch", "BIGINT", "INTEGER"),
    # Tangga tangan drainer (F10.3) tinggal di baris autopilot yang sama dengan sakelarnya:
    # `mode` menjawab SIAPA yang memegang mesin, `level` menjawab JAUH MANA tangan boleh
    # bergerak. Dua tabel untuk satu sakelar adalah dua laporan yang boleh berbeda (§12).
    ("autopilot_state", "level", "VARCHAR(16)", "TEXT"),
    ("autopilot_state", "level_until", "BIGINT", "INTEGER"),
    # Sebuah pertanyaan harus bisa ditelusuri ke PEKERJAAN yang menunggunya, bukan ke sesi yang
    # kebetulan sedang terbuka. `item_id` adalah penghubung itu: pertanyaan tanpa item tidak bisa
    # membangunkan apa pun, dan jawaban tanpa `answered_by` tidak bisa dibedakan dari jawaban mesin.
    ("decisions", "item_id", "BIGINT", "INTEGER"),
    ("decisions", "answered_by", "VARCHAR(60)", "TEXT"),
    # `origin` menjawab "siapa yang menulis baris antrean ini" dan hanya punya empat isi yang
    # bisa dibuktikan: 'roadmap' (ditulis `seed` dari ROADMAP_ITEMS), 'operator' (ditulis `queue`
    # dari Dashboard), 'idle' (ditulis drainer sendiri ketika ON tidak menemukan apa pun dan
    # ledger menunjukkan utang terukur — lihat ORIGIN_IDLE), dan 'legacy' (baris yang sudah ada
    # sebelum kolom ini ada — asalnya tidak diketahui, dan menebak 'roadmap' akan membuat SEMI
    # mengaku mengerjakan perintah orang yang bukan perintahnya).
    # Nilainya didefinisikan di sini, di kolom, satu kali (§12).
    ("quest_tasks", "origin", "VARCHAR(16)", "TEXT"),
    # `ws_path` adalah proyek baris ini — direktori tempat gerbangnya dijalankan. KOSONG berarti
    # hari kemarin persis: `REPO`. Itulah sebabnya kolom ini boleh ditambahkan tanpa menyentuh
    # satu pun baris lama: tidak ada yang dipaksa punya proyek sebelum ia memang punya satu.
    ("quest_tasks", "ws_path", "TEXT", "TEXT"),
]

# Nilai `origin` untuk baris yang sudah ada ketika kolomnya belum ada. Ini pernyataan tentang
# sejarah, bukan tentang perilaku: 'legacy' tetap ditawarkan ke ON (sama seperti sebelum kolom ini
# ada) dan tetap tidak pernah diambil SEMI.
ORIGIN_LEGACY = "legacy"
ORIGIN_ROADMAP = "roadmap"
ORIGIN_OPERATOR = "operator"
# 'idle' adalah isi keempat, dan seperti tiga lainnya ia bisa dibuktikan dari barisnya sendiri:
# baris ini tidak ditulis orang dan tidak berasal dari ROADMAP_ITEMS — ia ditulis drainer ketika
# ON tidak menemukan apa pun untuk dikerjakan, dan isinya adalah utang yang DIUKUR dari ledger ini
# (kegagalan berulang tanpa jawaban, entri skill tanpa berkas). Yang tidak boleh terjadi adalah
# baris idle dibuat supaya mesin terlihat sibuk: tanpa utang terukur, jalurnya diam.
# Konsekuensi yang sengaja dipertahankan: `claim()` menolak SEMI untuk segala yang bukan
# 'operator', jadi pengayaan-diri hanya mungkin di ON — mode yang memang dititipkan ke mesin.
ORIGIN_IDLE = "idle"

# Batas sendiri untuk baris idle. `NIGHT_MAX_ITEMS` menjaga seluruh run; tanpa angka kedua ini,
# pekerjaan pengayaan-diri akan memakan anggaran item malam dan menyisakan sedikit ruang untuk
# pekerjaan yang sebenarnya — padahal yang membuat ON berguna adalah kemampuannya tetap bekerja
# ketika tidak ada yang diperintahkan. Keduanya menjaga hal berbeda, jadi keduanya dibaca.
IDLE_MAX_ITEMS = 5
IDLE_HOLDER = "drainer"


def idle_debt_menu(conn, engine, brain_root=None):
    """Utang yang bisa dibuktikan, tanpa subprocess dan tanpa mengarang pekerjaan.

    Tiga sumber, semuanya ledger milik mesin ini sendiri:
      1. signature kegagalan yang berulang tapi `incident_log.auto_skill_id` masih kosong;
      2. `skills_inventory` yang tidak menunjuk berkas apa pun;
      3. `skills_inventory` yang menunjuk berkas yang sudah tidak ada di disk.

    Ambang sumber (1) TIDAK didefinisikan ulang di sini — angka itu milik `dream_phase`, dan dua
    angka untuk satu arti adalah dua aturan yang boleh berbeda. Impornya ditunda ke dalam fungsi
    karena `dream_phase` mengimpor modul ini; lingkaran itu dipecah di sini, bukan dengan menyalin
    konstantanya.

    `ok=False` berarti TIDAK SATU PUN sumber bisa dibaca, dan itu bukan "tidak ada utang".
    Sebagian sumber gagal dengan satu sumber yang berhasil tetap `ok=True` dengan `partial`
    terisi: keputusannya adalah "beri mesin ini sesuatu yang NYATA menunggak", dan menolak
    bekerja karena satu pembacaan gagal akan mengembalikan persis diam yang jalur ini dibuat
    untuk hilangkan. Yang tidak boleh terjadi adalah mengarang pekerjaan ketika tidak ada
    sumber yang bisa membuktikannya — dan itu yang `ok=False` tahan. Pemanggil wajib diam pada
    `ok=False`, sama seperti pemutus yang menolak saat git tidak terbaca.
    """
    root = Path(brain_root) if brain_root else (REPO / "brain")
    ph = "%s" if engine == "POSTGRESQL" else "?"
    items, unreadable, sources_ok = [], [], 0

    try:
        from dream_phase import PRE_ANSWER_REPEATS as rep
    except Exception as exc:  # noqa: BLE001 — ambang tidak terbaca = sumber ini tidak boleh dipakai
        rep = None
        unreadable.append(f"ambang dream_phase ({str(exc)[:40]})")

    if rep:
        try:
            rows = run(conn, engine,
                       f"SELECT error_signature, repeat_count FROM incident_log "
                       f"WHERE repeat_count >= {ph} AND auto_skill_id IS NULL "
                       f"ORDER BY repeat_count DESC LIMIT 3", (int(rep),)) or []
            sources_ok += 1
            for r in rows:
                items.append(("incident",
                              f"Tuntaskan skill learned untuk {r['error_signature']} — gagal "
                              f"{r['repeat_count']}x dan belum ada jawabannya"))
        except Exception as exc:  # noqa: BLE001
            unreadable.append(f"incident_log ({str(exc)[:40]})")

    try:
        rows = run(conn, engine,
                   "SELECT id, name, file_path FROM skills_inventory "
                   "WHERE file_path IS NULL OR file_path = '' ORDER BY use_count DESC LIMIT 3") or []
        for r in rows:
            items.append(("skill_no_file",
                          f"Beri skill '{r['name']}' satu berkas di brain/skills dan sambungkan "
                          f"file_path-nya, atau hapus entri ini karena tidak ada isinya"))
        missing = [(r["id"], r["name"], r["file_path"]) for r in rows if r.get("file_path")]
        rows2 = run(conn, engine,
                    "SELECT id, name, file_path FROM skills_inventory "
                    "WHERE file_path IS NOT NULL AND file_path <> '' ORDER BY use_count DESC LIMIT 20") or []
        for r in rows2:
            fp = (r.get("file_path") or "").strip()
            if not fp:
                continue
            p = Path(fp).expanduser()
            if not p.is_absolute():
                p = (root / fp).resolve(strict=False)
            if not p.exists():
                items.append(("skill_dangling",
                              f"Skill '{r['name']}' menunjuk {fp} yang tidak ada di disk — "
                              f"pindahkan ke brain/skills atau koreksi jalurnya"))
        sources_ok += 1
        del missing
    except Exception as exc:  # noqa: BLE001
        unreadable.append(f"skills_inventory ({str(exc)[:40]})")

    # Sumber keempat: integritas indeks skill. Ini bukan dugaan — sesi ini sendiri menelan judul
    # seksi yang sama empat kali karena menyunting dengan heading sebagai jangkar, dan tidak ada
    # satu pun pemeriksaan yang menangkapnya sampai manusia membacanya.
    #
    # Aturan paket, lahir dari dua kali salah. Direktori adalah PAKET kalau punya `SKILL.md`
    # (subdirektorinya lalu isi paket — `scripts/`, `references/` — dan tidak dimasuki), atau punya
    # berkas `.md` tanpa `SKILL.md` (paket tanpa pintu masuk: itu temuan, bukan wadah). Direktori
    # tanpa `SKILL.md` dan tanpa `.md` tapi dengan subdirektori adalah WADAH (`legacy/`), jadi
    # dimasuki satu tingkat. Versi pertama melompati apa pun yang punya subdirektori, akibatnya
    # `l2/` — paket yang justru saya salah tulis hari ini, karena ia menyimpan `scripts/` dan
    # `references/` — tidak pernah diaudit. Detektor yang buta pada kasus yang melahirkannya bukan
    # detektor.
    def _pack_dirs(base, depth=0):
        found = []
        try:
            kids = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            return found
        for p in kids:
            try:
                has_index = (p / "SKILL.md").is_file()
                mds = sorted(p.glob("*.md"))
                subdirs = any(c.is_dir() for c in p.iterdir())
            except OSError:
                continue
            if has_index:
                found.append((p, True))
            elif mds:
                found.append((p, False))
            elif subdirs and depth < 1:
                found.extend(_pack_dirs(p, depth + 1))
        return found

    try:
        skills_root = root / "skills"
        if skills_root.is_dir():
            for pack, has_index in _pack_dirs(skills_root):
                if not has_index:
                    items.append(("skill_no_index",
                                  f"Paket skill {pack.name}/ punya berkas .md tapi tidak punya "
                                  f"SKILL.md sebagai pintu masuk, jadi tidak ada yang tahu kapan "
                                  f"harus membukanya"))
                    continue
                idx = pack / "SKILL.md"
                text = idx.read_text(encoding="utf-8", errors="replace")
                for md in sorted(pack.glob("*.md")):
                    if md.name == "SKILL.md":
                        continue
                    if md.name not in text:
                        items.append(("skill_unlisted",
                                      f"{pack.name}/{md.name} ada di disk tapi tidak disebut "
                                      f"SKILL.md — tidak akan pernah terbaca oleh mesin mana pun"))
                # Hanya nama berkonvensi paket sendiri (`NN-topik.md`) yang ditagih ke direktori
                # ini. Memindai SEMUA `.md` dalam backtick membuat rujukan ke `AGENTS.md` atau
                # `README.md` — berkas di tempat lain, bukan bagian paket — jadi temuan fiktif.
                for ref in re.findall(r"`(\d{2}-[A-Za-z0-9_\-]+\.md)`", text):
                    if not (pack / ref).is_file():
                        items.append(("skill_missing",
                                      f"SKILL.md {pack.name}/ merujuk {ref} yang tidak ada di disk"))
                lines = text.split("\n")
                for i, ln in enumerate(lines):
                    if re.match(r"^\d+\. \*\*", ln):
                        window = "\n".join(lines[max(0, i - 6):i])
                        if not re.search(r"^#{1,3} |^\d+\. ", window, re.M):
                            items.append(("skill_orphan_list",
                                          f"{pack.name}/SKILL.md punya daftar bernomor tanpa judul "
                                          f"di atasnya (baris ~{i + 1}) — suntingan yang menelan "
                                          f"heading-nya"))
            sources_ok += 1
    except Exception as exc:  # noqa: BLE001
        unreadable.append(f"brain/skills ({str(exc)[:40]})")

    return {"ok": sources_ok > 0, "why": "; ".join(unreadable) or None,
            "partial": bool(unreadable), "items": items}


def idle_budget_used(conn, backend):
    """Berapa baris idle yang sudah MEMAKAN anggaran pada jendela ON yang sedang berjalan.

    Satu definisi untuk dua pemanggil. Sebelumnya `ensure_idle_item` menghitung
    COMPLETED+FAILED+PARKED sejak `on_epoch`, sementara `queue_followup` menghitung
    HANYA COMPLETED dan TANPA batas jendela — dua aturan di bawah satu kata "anggaran".
    Yang kedua adalah bug yang menunggu jam: setelah lima baris idle selesai sepanjang
    sejarah basis data, aturan DONE->lanjutan berhenti selamanya, termasuk besok dan
    lusa, tanpa satu pun pesan yang mengatakannya.
    """
    ph = "%s" if backend == "POSTGRESQL" else "?"
    ap = autopilot(conn, backend) or {}
    on_epoch = int(ap.get("on_epoch") or 0)
    spent = run(conn, backend,
                f"SELECT COUNT(*) AS n FROM quest_tasks WHERE origin={ph} "
                f"AND status IN ('COMPLETED','FAILED','PARKED') AND "
                + (f"EXTRACT(EPOCH FROM completed_at::timestamptz) >= {ph}"
                   if backend == "POSTGRESQL"
                   else f"CAST(strftime('%s', completed_at) AS INTEGER) >= {ph}"),
                (ORIGIN_IDLE, on_epoch)) or []
    return int((spent[0].get("n") if spent else 0) or 0)


def ensure_idle_item(conn, backend, cli_engine=None, who=IDLE_HOLDER, emit=None, brain_root=None):
    """Satu baris 'idle' ketika ON tidak punya apa pun DAN ada utang yang terukur.

    `backend` adalah jenis database ("POSTGRESQL"/"SQLITE") dan `cli_engine` adalah MESIN TUJUAN
    barisnya — dua hal yang kebetulan sama-sama bernama "engine" di modul ini dan tidak boleh
    dicampur: yang satu memilih placeholder SQL, yang lain ditulis ke kolom `cli_engine`.
    Memakai yang pertama untuk yang kedua menghasilkan baris yang menunjuk ke mesin bernama
    'POSTGRESQL', yaitu mesin yang tidak ada.

    Tujuan tidak pernah ditebak dari ingatan (§12). Kalau pemanggil tidak memberikannya, ia
    diturunkan dari mesin yang paling akhir dipakai operator di ledger, dan itu pun harus ada di
    registri; kalau tetap tidak ada, jalurnya melapor dan diam.

    Lima penolakan, semuanya sebelum INSERT:
      * mode tersimpan bukan ON — `hand_new_work` sudah memeriksa, tapi ini jalur yang sama
        dipakainya, dan pagar kedua tidak boleh mengasumsikan yang pertama;
      * mesin tujuan masih memegang item ber-lease hidup — pekerjaan tidak ditumpuki;
      * sudah ada baris idle terbuka — satu cukup;
      * anggaran idle jendela ini habis — dihitung dari SEMUA baris idle yang selesai ATAU gagal,
        karena menghitung hanya COMPLETED membuat pola buat→gagal→buat-ulang tak terbatas;
      * menu utang kosong atau tidak terbaca — diam, jangan membuat kesibukan.

    Barisnya dikembalikan supaya pemanggil bisa menyerahkannya ke `hand_new_work` yang biasa:
    gerbang, ketenangan pane, direktori kerja, klaim bersyarat, dan pelepasan saat kirim gagal
    semuanya berjalan utuh. Jalur idle tidak punya pagar sendiri karena itu berarti ada dua aturan.
    """
    emit = emit or (lambda *a, **kw: None)
    ph = "%s" if backend == "POSTGRESQL" else "?"
    eng = (cli_engine or "").strip()

    try:
        ap = autopilot(conn, backend) or {}
    except Exception as exc:  # noqa: BLE001 — ledger yang tidak terbaca BUKAN berarti mode OFF
        emit("IDLE_DEBT_UNKNOWN", why=f"autopilot_state tidak terbaca ({str(exc)[:50]})")
        return None
    if str(ap.get("mode") or "").upper() != "ON":
        emit("IDLE_HELD_BY_MODE", mode=str(ap.get("mode") or "?"))
        return None
    on_epoch = int(ap.get("on_epoch") or 0)
    until = int(ap.get("until_epoch") or 0)
    if not on_epoch or until <= now():
        # Tanpa jendela yang terbaca DAN masih terbuka, "berapa idle yang sudah dipakai" tidak bisa
        # dijawab — dan menjawabnya dengan nol adalah mengizinkan loop tanpa batas. UNKNOWN bukan 0.
        emit("IDLE_DEBT_UNKNOWN", why="jendela ON tidak terbaca atau sudah lewat")
        return None

    if not eng:
        # Mesin tujuan diturunkan dari ledger, bukan dari favorit tick ini.
        try:
            rows = run(conn, backend,
                       f"SELECT cli_engine FROM quest_tasks WHERE cli_engine IS NOT NULL "
                       f"AND cli_engine <> '' AND origin={ph} ORDER BY id DESC LIMIT 1",
                       (ORIGIN_OPERATOR,)) or []
            eng = (rows[0]["cli_engine"] if rows else "").strip()
        except Exception as exc:  # noqa: BLE001
            emit("IDLE_DEBT_UNKNOWN", why=f"mesin tujuan tidak terbaca ({str(exc)[:50]})")
            return None
    if not eng:
        emit("IDLE_DEBT_UNKNOWN", why="tidak ada mesin tujuan yang bisa diturunkan dari ledger")
        return None
    known = set((semi_status(conn, backend) or {}).get("known_engines") or [])
    if known and eng not in known:
        emit("IDLE_DEBT_UNKNOWN", why=f"mesin tujuan {eng!r} tidak ada di registri")
        return None

    try:
        held = run(conn, backend,
                   f"SELECT id FROM quest_tasks WHERE cli_engine={ph} "
                   f"AND status IN ('CLAIMED','WORKING') AND COALESCE(lease_epoch,0) > {ph} "
                   f"LIMIT 1", (eng, now())) or []
    except Exception as exc:  # noqa: BLE001
        emit("IDLE_DEBT_UNKNOWN", why=f"ledger tidak terbaca ({str(exc)[:50]})")
        return None
    if held:
        emit("IDLE_ENGINE_BUSY", item=int(held[0]["id"]))
        return None

    try:
        open_idle = run(conn, backend,
                        f"SELECT id FROM quest_tasks WHERE origin={ph} "
                        f"AND status IN ('PENDING','CLAIMED','WORKING') LIMIT 1", (ORIGIN_IDLE,)) or []
    except Exception as exc:  # noqa: BLE001
        emit("IDLE_DEBT_UNKNOWN", why=f"antrean idle tidak terbaca ({str(exc)[:50]})")
        return None
    if open_idle:
        emit("IDLE_ALREADY_OPEN", item=int(open_idle[0]["id"]))
        return None

    try:
        # Semua keadaan akhir dihitung, bukan hanya COMPLETED, dan hanya dalam jendela ON ini —
        # keduanya hidup di `idle_budget_used`, satu tempat untuk dua pemanggil. Kalau hanya
        # keberhasilan yang memakan anggaran, baris idle yang gagal tidak pernah mengurangi sisa
        # dan menu yang sama dibuat ulang selamanya; kalau tanpa batas jendela, lima baris idle
        # yang selesai tahun lalu membekukan aturan ini untuk selamanya.
        used_idle = idle_budget_used(conn, backend)
    except Exception as exc:  # noqa: BLE001
        emit("IDLE_DEBT_UNKNOWN", why=f"anggaran idle tidak terbaca ({str(exc)[:50]})")
        return None
    if used_idle >= IDLE_MAX_ITEMS:
        emit("IDLE_BUDGET_SPENT", used=used_idle, cap=IDLE_MAX_ITEMS)
        return None

    menu = idle_debt_menu(conn, backend, brain_root=brain_root)
    if not menu["ok"]:
        emit("IDLE_DEBT_UNKNOWN", why=menu["why"])
        return None
    if not menu["items"]:
        # `partial` membedakan "tidak ada utang" dari "tidak ada utang yang BISA DIBACA".
        emit("IDLE_NO_DEBT" if not menu.get("partial") else "IDLE_DEBT_UNKNOWN",
             why=menu.get("why"), partial=bool(menu.get("partial")))
        return None

    kind, title = menu["items"][0]
    summary = (f"kerja idle otomatis ({kind}) — ditulis drainer karena ON tidak menemukan item "
               f"nyata dan ledger menunjukkan utang yang terukur; budget idle sisa "
               f"{IDLE_MAX_ITEMS - used_idle - 1}")
    try:
        if backend == "POSTGRESQL":
            rows = run(conn, backend,
                       "INSERT INTO quest_tasks (title, status, phase, summary, cli_engine, "
                       "origin, ws_path) VALUES (%s, 'PENDING', 'idle', %s, %s, %s, %s) "
                       "RETURNING id",
                       (title[:255], summary[:2000], eng, ORIGIN_IDLE, str(REPO)))
            new_id = int(rows[0]["id"]) if rows else None
        else:
            cur = conn.cursor()
            cur.execute("INSERT INTO quest_tasks (title, status, phase, summary, cli_engine, "
                        "origin, ws_path) VALUES (?, 'PENDING', 'idle', ?, ?, ?, ?)",
                        (title[:255], summary[:2000], eng, ORIGIN_IDLE, str(REPO)))
            conn.commit()
            new_id = cur.lastrowid
            cur.close()
    except Exception as exc:  # noqa: BLE001
        emit("IDLE_DEBT_UNKNOWN", why=f"INSERT gagal ({str(exc)[:60]})")
        return None
    if not new_id:
        emit("IDLE_DEBT_UNKNOWN", why="id tidak kembali setelah INSERT")
        return None
    emit("IDLE_QUEUED", item=new_id, kind=kind, engine=eng, used=used_idle + 1,
         cap=IDLE_MAX_ITEMS, partial=bool(menu.get("partial")))
    return get_item(conn, backend, int(new_id))

# Lebar kolom yang ditumbuhkan, bukan diganti isinya. `weight` lahir sebagai VARCHAR(10) pada
# saat kosakatanya masih muat di situ; bobot ketiga dari invarian 3 — `irreversible`, 12 huruf —
# meledak di PostgreSQL dengan StringDataRightTruncation sementara SQLite tidak pernah memeriksa
# panjang, jadi fixture offline tetap hijau. Kolom dibaca dulu; hanya yang sempit yang dilebarkan,
# supaya pemanggilan ulang tidak menulis ulang tabel yang sudah benar.
WIDENINGS = [
    ("decisions", "weight", 20),
]


def ensure_schema(conn, engine: str) -> list[str]:
    """Bangun yang belum ada; tidak pernah mengubah baris yang sudah ada."""
    cur = conn.cursor()
    log = []
    for stmt in (CREATE_PG if engine == "POSTGRESQL" else CREATE_SQLITE):
        try:
            cur.execute(stmt)
            log.append("ok " + stmt.split("(")[0].strip().split()[-1])
        except Exception as exc:  # noqa: BLE001
            log.append(f"skip {exc}")
    added = set()
    for table, column, pgtype, sqtype in NEW_COLUMNS:
        if engine == "POSTGRESQL":
            stmt = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {pgtype}"
        else:
            cols = [r[1].lower() for r in cur.execute(f"PRAGMA table_info({table})")]
            if not cols or column.lower() in cols:
                continue
            stmt = f"ALTER TABLE {table} ADD COLUMN {column} {sqtype}"
        try:
            cur.execute(stmt)
            added.add((table, column))
            log.append(f"col {table}.{column}")
        except Exception as exc:  # noqa: BLE001
            log.append(f"skip {table}.{column}: {exc}")
    if engine == "POSTGRESQL":
        # Lewat run(), bukan cur.execute().fetchone(): cursor psycopg2 mengembalikan None dari
        # execute, jadi bentuk sqlite3 di sini akan AttributeError tepat saat schema sedang
        # diperbaiki — dan ensure_schema dipanggil pada setiap start, bukan saat sepi.
        for table, column, want in WIDENINGS:
            have = run(conn, engine,
                       "SELECT character_maximum_length AS n FROM information_schema.columns "
                       "WHERE table_name=%s AND column_name=%s", (table, column))
            width = have[0]["n"] if have else None
            if width and width < want:
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR({want})")
                log.append(f"widen {table}.{column} {width}->{want}")
    if engine != "POSTGRESQL":
        cur.execute("INSERT OR IGNORE INTO autopilot_state (id) VALUES (1)")
    else:
        cur.execute("INSERT INTO autopilot_state (id) VALUES (1) ON CONFLICT DO NOTHING")
    # OFF bukan lagi sebuah mode, tapi barisnya masih ada di database yang hidup. Nilai lama
    # dipindahkan ke SEMI dengan TANGAN yang tetap turun: `off_reason` mencatat penghentian,
    # `level='observe'` sudah ditulis jalur OFF, dan jendela tidak dibuka ulang. Jadi perpindahan
    # ini tidak bisa menyalakan sesuatu yang orang matikan — ia hanya mengganti kata untuk keadaan
    # yang sama, dan tick yang membaca 'OFF' tidak lagi bangun sebagai MODE_UNKNOWN setiap 5 menit.
    try:
        cur.execute("UPDATE autopilot_state SET mode='SEMI' WHERE mode NOT IN ('SEMI','ON')")
    except Exception:  # noqa: BLE001 — kolom lama yang belum ada bukan alasan untuk menolak start
        pass
    # Baris yang lebih tua dari kolomnya tidak bisa diklasifikasikan dengan menebak. Yang bisa
    # diukur: `seed` selalu menulis `phase`, dan tidak ada jalur lain yang menulisnya — jadi baris
    # ber-phase adalah butir ROADMAP, dan sisanya benar-benar tidak diketahui asalnya. Menebak
    # 'roadmap' pada semuanya membuat SEMI mengaku mengerjakan perintah orang, dan menandai
    # semuanya 'legacy' membuat enam butir ROADMAP yang sudah hidup di sini kehilangan asalnya.
    # 'legacy' tetap ditawarkan ke ON persis seperti sebelumnya, dan tetap tidak pernah diambil SEMI.
    # Backfill adalah bagian dari PERPINDAHAN, bukan keadaan yang ditegakkan setiap start.
    # Dijalankan terus-menerus berarti setiap baris baru yang lahir tanpa `origin` — ditulis
    # pemanggil lain, skrip lama, atau selftest — diberi label "sudah ada sebelum kolomnya ada",
    # yaitu sejarah yang salah. Dan premis lama ("seed satu-satunya penulis phase") sudah
    # dipatahkan modul ini sendiri: queue_command menulis phase='operator'.
    if ("quest_tasks", "origin") in added:
        for label, clause in ((ORIGIN_ROADMAP, "phase IS NOT NULL"),
                              (ORIGIN_LEGACY, "phase IS NULL")):
            try:
                cur.execute(f"UPDATE quest_tasks SET origin='{label}' "
                            f"WHERE origin IS NULL AND {clause}")
            except Exception as exc:  # noqa: BLE001
                log.append(f"skip backfill origin: {exc}")
    conn.commit() if engine != "POSTGRESQL" else None
    cur.close()
    return log


def now() -> int:
    return int(time.time())


# --- koneksi ---------------------------------------------------------------
def connect():
    conn, engine = get_db_connection()
    return conn, engine


def run(conn, engine, sql, params=()):
    """SELECT/INSERT via satu jalur untuk dua backend.

    psycopg2 mengembalikan tuple tanpa row_factory, sementara sqlite3.Row sudah seperti dict.
    Membentuk dict dari cur.description membuat keduanya identik — tanpa itu, kode yang lolos
    seluruh selftest SQLite tetap jatuh di produksi PostgreSQL pada SELECT pertama.
    """
    cur = conn.cursor()
    # `params` tidak boleh diteruskan kalau tidak ada: psycopg2 menafsirkan setiap `%`
    # dalam SQL sebagai placeholder begitu ia menerima tuple parameter, jadi satu `LIKE
    # 'autopilot%'` tanpa parameter akan meledak dengan "tuple index out of range" — di
    # PostgreSQL saja, karena SQLite tidak mengenal interpolation itu sama sekali.
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    if engine != "POSTGRESQL":
        conn.commit()
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
    cur.close()
    return rows


def get_item(conn, engine, item_id):
    rows = run(conn, engine, "SELECT * FROM quest_tasks WHERE id = %s"
               if engine == "POSTGRESQL" else "SELECT * FROM quest_tasks WHERE id = ?",
               (item_id,))
    return rows[0] if rows else None


# --- mesin kerja ------------------------------------------------------------
def claim(conn, engine, item_id, who, dirty=_MEASURE, mode=None):
    """Ambil satu item. Gagal dengan alasan, bukan dengan kerja ganda.

    PostgreSQL punya UPDATE ... RETURNING; SQLite tidak. Keduanya harus menghasilkan keputusan
    yang sama, jadi klaim dilakukan sebagai UPDATE tersyarat lalu dibaca kembali — bukan
    'select lalu update', yang memenangkan siapa pun yang paling cepat bertanya.

    `dirty` adalah sambatan uji yang sama yang sudah dipakai `night_state`: selftest SQLite berjalan
    sementara repo NYATA sedang disunting, dan membaca git live akan membuat hasilnya bergantung pada
    siapa yang sedang menulis kode. Produksi tidak pernah mengirimnya, jadi pagar §6 tetap berdiri.
    """
    item = get_item(conn, engine, item_id)
    if not item:
        return False, f"item {item_id} tidak ada"
    state = norm(item.get("status"))
    holder = item.get("claimed_by")
    lease = int(item.get("lease_epoch") or 0)
    if state not in ("PENDING", "CLAIMED", "WORKING"):
        return False, f"status {state} tidak bisa diklaim"
    if holder and holder != who and lease > now():
        return False, f"dipegang {holder} sampai lease berakhir ({lease - now()}s lagi)"
    # `mode` boleh dibawa dari pemanggil yang SUDAH membacanya. Membaca dua kali berarti ada
    # jendela: tick memutuskan "SEMI" pada detik X dan claim membaca lagi pada detik Y, dan
    # seseorang yang memindahkan sakelar di antaranya membuat satu baris diambil dengan keranjang
    # yang bukan milik keputusan tadi. Kalau tidak dibawa, barulah dibaca di sini.
    # `mode` dari pemanggil MENAMBAL baris, tidak menggantikannya. Versi pertama membuat
    # `{"mode": ...}` saja, jadi setiap kolom baris lain hilang diam-diam bagi sisa fungsi —
    # dan `night_state()` di bawah membaca ulang baris yang sama dari database, sehingga
    # jendela ON bisa dinilai dengan mode yang berbeda dari yang dipakai pagar SEMI. Sekarang
    # satu baris, satu mode, dan kalau keduanya bertentangan itu terdengar.
    ap = autopilot(conn, engine) or {}
    # OFF dibaca SEBELUM `mode` dari pemanggil boleh menimpa apa pun. Tanpa urutan ini, satu
    # pemanggil yang mengirim mode="SEMI" sementara baris database menulis OFF akan lolos dari
    # rem — dan rem adalah satu-satunya keadaan yang tidak boleh dilewati oleh kata seseorang,
    # sebab justru untuk itu ia ada.
    if ap.get("mode") == OFF_MODE:
        return False, (f"OFF: tidak ada yang diklaim — "
                       f"{ap.get('off_reason') or 'tidak ada alasan berhenti tercatat'}")
    if mode is not None:
        wanted_mode = (mode or "").upper()
        if ap.get("mode") and ap["mode"] != wanted_mode:
            # Bukan alasan untuk menolak — pemanggil yang sah (tick, hook) memang sudah membaca
            # mode ini beberapa milidetik lalu. Tapi kalau berduaannya berbeda, itu dicatat.
            ap["mode_disagreed"] = f"db={ap.get('mode')} caller={wanted_mode}"
        ap["mode"] = wanted_mode
    if ap.get("mode") in ("ON", "SEMI"):
        # Hanya pembacaan git yang boleh dibawa naik (lihat `dirty` di docstring); jendela,
        # anggaran kegagalan, dan batas item dihitung DI SINI. Audit putaran lima menuntut dua hal
        # yang tampak bertentangan: jangan ukur dua kali, dan jangan percaya kedaluwarsa dari
        # awal tick. Keduanya cocok lewat satu pembedaan — yang mahal dan bergantung lingkungan
        # boleh diwarisi, yang merupakan keputusan tidak.
        st = night_state(conn, engine, dirty=dirty)
        # Tidak ada pengecualian mode di sini, dan itu keputusan yang sudah diambil dan diuji,
        # bukan kelalaian: lihat `working tree kotor menutup SEMI juga — §6 tidak punya mode
        # pengecualian`. §6 menahan SEMUA klaim selama tree kotor, termasuk baris yang ditulis
        # operator sendiri. Yang BOLEH berubah adalah berapa lama tree itu kotor — jawabannya di
        # `push_gate`, yang sekarang benar-benar melihat tree, bukan di sini.
        if st["why"]:
            return False, "circuit breaker: " + "; ".join(st["why"])
    if ap.get("mode") == "SEMI" and (item.get("origin") or ORIGIN_LEGACY) != ORIGIN_OPERATOR:
        # Batas SEMI ditegakkan di pintu, bukan hanya di penyaring antrean — mengikuti preseden
        # pemutus yang hidup di dalam claim(): siapa pun yang mengabaikan `next_item(origin=...)`
        # tetap menabrak angka yang sama. Item roadmap tidak hilang; ia hanya tidak boleh dikerjakan
        # saat yang memegang mesin adalah perintah orang.
        return False, (f"SEMI hanya mengerjakan perintah operator, dan item {item_id} asalnya "
                       f"{item.get('origin') or ORIGIN_LEGACY!r} — naikkan ke ON untuk antrean "
                       "roadmap")
    # Satu perintah aktif per MESIN, di bawah SEMI. Aturannya tinggal di sini, bukan di pemanggil,
    # karena audit putaran lima menemukan dua salinan yang berbeda: hook memeriksa SEBELUM mengklaim
    # dan drainer memeriksa SESUDAH lalu melepas — selisih itu cukup untuk dua perintah masuk ke
    # satu mesin.
    #
    # Kunci advisory per mesin membuat pemeriksaan di bawah benar-ben atomik. Tanpanya ia tetap
    # cek-lalu-tulis: dua pemanggil untuk dua BARIS BERBEDA dengan cli_engine sama bisa sama-sama
    # melihat "bebas" (keduanya masih PENDING) lalu sama-sama menembus UPDATE bersyaratnya sendiri,
    # karena WHERE hanya melindungi baris yang ia tulis, bukan baris tetangga. Bukan teori: hook
    # memilih dari cwd sesi sementara drainer memilih dari tab_project, dan keduanya boleh berbeda.
    #
    # `pg_advisory_lock`, bukan `_xact_`, karena koneksi ini autocommit — lock transaksi dilepas
    # tepat setelah statement pembukanya, yaitu sebelum apa pun diperiksa.
    # Batas yang diakui: SQLite tidak punya ini. Di sana penulisan diserialisasi file lock, tapi
    # jendela SELECT→UPDATE tetap ada antar proses. Hari ini tidak ada dua pemanggil bersamaan
    # (timer OFF, satu hook per mesin); itu keadaan yang harus diingat saat F10.3 menyalakan timer,
    # bukan sesuatu yang boleh diklaim sudah beres.
    engine_of = (item.get("cli_engine") or "").strip()
    lock_key = f"quest-engine:{engine_of}"
    locked = False
    if engine == "POSTGRESQL" and ap.get("mode") == "SEMI" and engine_of:
        try:
            run(conn, engine, "SELECT pg_advisory_lock(hashtext(%s))", (lock_key,))
            locked = True
        except Exception:  # noqa: BLE001 — tanpa lock pun kita masih lebih baik daripada tanpa klaim
            locked = False
    try:
        if ap.get("mode") == "SEMI" and engine_of:
            busy = run(conn, engine,
                       "SELECT id FROM quest_tasks WHERE cli_engine=%s AND id<>%s "
                       "AND status IN ('WORKING','CLAIMED') "
                       "AND COALESCE(lease_epoch,0) > %s ORDER BY id LIMIT 3"
                       if engine == "POSTGRESQL" else
                       "SELECT id FROM quest_tasks WHERE cli_engine=? AND id<>? "
                       "AND status IN ('WORKING','CLAIMED') "
                       "AND COALESCE(lease_epoch,0) > ? ORDER BY id LIMIT 3",
                       (engine_of, item_id, now()))
            if busy:
                return False, (f"ENGINE_BUSY: {engine_of} masih memegang "
                               f"{', '.join('#' + str(r['id']) for r in busy)} — SEMI menunggu "
                               "yang ini selesai sebelum memberi perintah kedua")

        ph = "%s" if engine == "POSTGRESQL" else "?"
        # UPDATE bersyarat. Versi lama `WHERE id=?` saja: dua pengklaim bisa sama-sama menang,
        # karena masing-masing menulis lalu membaca kembali NAMANYA SENDIRI — A menulis, A baca
        # (A), B menulis, B baca (B) — dan keduanya pulang membawa True.
        #
        # `COALESCE(lease_epoch,0)` bukan kerapian SQL. Tanpa itu, baris yang `claimed_by`-nya
        # terisi sambil `lease_epoch` masih NULL membuat `lease_epoch <= ?` bernilai NULL, dan
        # NULL di WHERE tidak pernah benar — baris itu TIDAK BISA DIKLAIM SELAMANYA. Pemeriksaan
        # Python di atas justru melihat `int(None or 0) = 0` dan menyebutnya "bebas": dua pembacaan
        # atas data yang sama tidak boleh memberi jawaban berbeda.
        q = (f"UPDATE quest_tasks SET status='WORKING', claimed_by={ph}, lease_epoch={ph}, "
             f"model_assigned={ph}, escalation_reason=NULL WHERE id={ph} "
             f"AND (claimed_by IS NULL OR claimed_by={ph} OR COALESCE(lease_epoch,0) <= {ph}) "
             f"AND status IN ('PENDING','CLAIMED','WORKING')")
        run(conn, engine, q, (who, now() + LEASE_SECONDS, who, item_id, who, now()))
        after = get_item(conn, engine, item_id)
        ok = after and after.get("claimed_by") == who and norm(after.get("status")) == "WORKING"
        if ok:
            return True, f"claimed by {who}"
        # Alasan yang benar, bukan dugaan yang paling nyaman. "dipegang orang lain" untuk baris
        # yang sebenarnya sudah DONE mengirim orang mencari pemegang yang tidak pernah ada.
        if not after:
            return False, f"item {item_id} hilang saat diklaim"
        if after.get("claimed_by") and after.get("claimed_by") != who:
            return False, f"dipegang {after['claimed_by']} sampai lease berakhir"
        return False, (f"status {norm(after.get('status'))} tidak bisa diklaim — "
                       "UPDATE-nya tidak menembus WHERE")
    finally:
        if locked:
            try:
                run(conn, engine, "SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,))
            except Exception:  # noqa: BLE001 — koneksi yang tertutup melepas lock sendiri
                pass


def release_claim(conn, engine, item_id, who):
    """Lepaskan klaim HANYA kalau masih milik kita dan masih berupa klaim.

    Dipindah ke modul pemilik kosakata karena dua jalur butuh pelepasan yang sama, dan audit
    menemukan `_release_claim` milik drainer menulis `WHERE id=?` tanpa syarat — cukup untuk
    menghapus klaim orang lain yang baru saja menang. `status IN (...)` ditambahkan kemudian:
    tanpa itu, pelepasan yang terlambat bisa menimpa baris yang sudah divonis COMPLETED atau
    FAILED oleh pihak lain, dan itu menulis ulang sejarah hasil pekerjaan.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    run(conn, engine,
        f"UPDATE quest_tasks SET status='PENDING', claimed_by=NULL, lease_epoch=0, "
        f"model_assigned=NULL "
        f"WHERE id={ph} AND claimed_by={ph} AND status IN ('WORKING','CLAIMED')",
        (item_id, who))
    return get_item(conn, engine, item_id)


def renew_lease(conn, engine, item_id):
    """Perpanjang lease. Fungsi ini pernah bernama `heartbeat` — satu kata untuk dua hal berbeda;
    §12 melarang itu, jadi nama itu sekarang hanya punya indera F10.7 di bawah."""
    run(conn, engine,
        "UPDATE quest_tasks SET lease_epoch=%s WHERE id=%s AND status IN ('WORKING','CLAIMED')"
        if engine == "POSTGRESQL" else
        "UPDATE quest_tasks SET lease_epoch=? WHERE id=? AND status IN ('WORKING','CLAIMED')",
        (now() + LEASE_SECONDS, item_id))
    return get_item(conn, engine, item_id)


def nudge(conn, engine, item_id):
    """Naikkan `resume_count` satu kali — dan tolak kenaikan yang melewati batas.

    Batasnya ditegakkan di sini, bukan di pemanggil, mengikuti preseden yang sama dengan circuit
    breaker yang hidup di dalam `claim()`: manusia yang mengetik perintah yang sama malam ini
    menabrak angka yang sama. Tulisan dilakukan sebagai `COALESCE(...)+1` lalu dibaca kembali,
    karena yang menentukan adalah barisnya, bukan siapa yang paling cepat bertanya (§12, F10.2).

    Yang TIDAK dilakukan fungsi ini: mengirim apa pun. Ia menghitung sentuhan, bukan menyentuh.
    """
    item = get_item(conn, engine, item_id)
    if not item:
        return False, f"item {item_id} tidak ada"
    used = int(item.get("resume_count") or 0)
    if used >= MAX_RESUME:
        return False, (f"budget resume habis ({used}/{MAX_RESUME}) — item ini macet, bukan "
                       "tertidur; empat sentuhan tidak membuatnya bergerak")
    ph = "%s" if engine == "POSTGRESQL" else "?"
    run(conn, engine,
        f"UPDATE quest_tasks SET resume_count=COALESCE(resume_count, 0) + 1 WHERE id={ph}",
        (item_id,))
    after = int((get_item(conn, engine, item_id) or {}).get("resume_count") or 0)
    if after > MAX_RESUME:
        return False, (f"naik ke {after} melewati {MAX_RESUME}: pemanggil lain sudah menyentuh "
                       "item ini lebih dulu")
    if after <= used:
        return False, f"kenaikan tidak terbaca kembali ({used} -> {after})"
    return True, f"resume {after}/{MAX_RESUME}"


def retry_now(row, at=None):
    """Bolehkah baris ini diambil SEKARANG, walaupun `due_epoch`-nya masih di masa depan?

    Jeda ditulis oleh setiap penolakan, dan sejak perbaikan ini alasannya ikut tersimpan di
    `escalation_reason`. Itu memungkinkan pembedaan yang benar, yang sebelumnya tidak bisa dibuat:
    "pane sedang sibuk" adalah alasan yang TERBUKTI sudah tidak berlaku pada detik mesin mencapai
    batas giliran — peristiwa Stop adalah buktinya. Menahan baris itu sampai jeda-nya habis berarti
    membuat mesin menunggu lima menit untuk hal yang sudah ia ketahui sendiri, dan itulah yang
    membuat qoder terasa tidak pernah menerima perintah padahal antigravity langsung.

    Alasan lain TETAP menunggu jeda-nya: direktori salah, tanpa target, injeksi habis, jendela
    ditutup. Kalau itu juga dibatalkan, satu baris yang rusak permanen dicoba lagi di setiap batas
    selamanya — dan loop yang tidak bisa dihentikan adalah tepat yang operator larang.
    """
    due = int(row.get("due_epoch") or 0)
    if due <= (at if at is not None else now()):
        return True
    reason = row.get("escalation_reason") or ""
    return any(k in reason for k in BUSY_RETRY_REASONS)


def queue_origin_for(mode):
    """Keranjang antrean yang boleh diambil SEBUAH MODE. Satu aturan, satu tempat.

    Aturan ini dulunya hidup dua kali dengan jawaban berbeda, dan itu bukan teori: jalur timer
    menulis `ORIGIN_OPERATOR if mode == "SEMI" else None`, sementara `hooks/semi_wake.py` menulis
    `origin=wo.ORIGIN_OPERATOR` tanpa syarat. Karena `level=observe` mengunci timer, satu-satunya
    jalur otomatis yang benar-benar hidup adalah Stop hook — dan hook itu buta terhadap roadmap.
    Hasilnya terukur 2026-10-09: operator memindah sakelar ke ON, menunggu, dan tidak ada satu pun
    item roadmap yang bergerak; ia harus menyuruh lanjut secara manual, yang persis melanggar kata
    "auto" pada autopilot.

    ON berarti keranjang penuh termasuk roadmap (§6). Yang TIDAK dilonggarkan fungsi ini:
    `claim()` tetap menegakkan keranjang pada saat penulisan, jadi pemanggil yang menyaring
    terlalu lebar tetap tidak bisa mengerjakan baris roadmap di bawah SEMI.
    """
    return ORIGIN_OPERATOR if (mode or "").upper() == "SEMI" else None


def row_workable_here(row_ws, session_ws):
    """Bolehkah sesi di `session_ws` mengerjakan baris yang proyeknya `row_ws`?

    Sama = boleh; itu pagar direktori yang sudah ada alasannya (tugas proyek A di ketik ke pane
    yang berdiri di proyek B membuat engine mengerjakan satu tree sambil melaporkan tree lain).

    Pengecualian yang ditambahkan 2026-10-09, dan alasannya konkret bukan kemudahan: tab Qoder dan
    Antigravity DILUNCURKAN di ~/Documents/ai-workstation karena di situ `.qoder/rules` dan kontrak
    berada, tetapi kode yang mereka garap seluruhnya ada di ~/.ai-station. `tab_project()` membaca
    kolom workspace profil — yaitu tempat peluncuran — jadi memakainya sebagai "proyek siapa yang
    dikerjakan" membuat ~/.ai-station tidak pernah cocok dengan tab mana pun, dan seluruh antrean
    roadmap diveto oleh pagar yang benar untuk proyek orang lain. Mesin-workstation memelihara
    dirinya sendiri, jadi repo-nya sendiri selalu layak baginya.

    Ini BUKAN pelonggaran umum: baris milik proyek KETIGA (mis. Creative-Studio) tetap tidak bisa
    masuk ke pane yang berdiri di workstation, dan `ws_path` baris itu tetap menentukan di mana
    gerbangnya dijalankan.
    """
    if not row_ws:
        return True
    try:
        rw = Path(str(row_ws)).expanduser().resolve(strict=False)
    except OSError:
        return False
    if rw == REPO:
        return True
    return session_ws is not None and rw == Path(str(session_ws)).expanduser().resolve(strict=False)


def nudge_inject(conn, engine, item_id):
    """Naikkan `inject_count` satu kali, dan tolak kenaikan yang melewati MAX_INJECT.

    Padanan `nudge()` untuk kata yang berbeda: `nudge` menghitung CONTINUE yang diketik ke pane
    yang sudah memegang item, yang ini menghitung perintah BARU yang disuntikkan ke sesi yang
    berhenti. Memakai satu kolom untuk dua hal membuat anggaran yang dibaca operator berbeda dari
    anggaran yang mesin pakai, dan §12 melarang itu.

    Sama seperti `nudge()`: yang dilakukan hanya menghitung. Tidak ada satu pun ketikan di sini.
    """
    item = get_item(conn, engine, item_id)
    if not item:
        return False, f"item {item_id} tidak ada"
    used = int(item.get("inject_count") or 0)
    if used >= MAX_INJECT:
        return False, (f"budget injeksi habis ({used}/{MAX_INJECT}) — baris ini sudah disuntik "
                       "dan tidak ada yang menyelesaikan; menyuntik lagi tidak menggerakkannya")
    ph = "%s" if engine == "POSTGRESQL" else "?"
    run(conn, engine,
        f"UPDATE quest_tasks SET inject_count=COALESCE(inject_count, 0) + 1 WHERE id={ph}",
        (int(item_id),))
    after = int((get_item(conn, engine, int(item_id)) or {}).get("inject_count") or 0)
    if after > MAX_INJECT:
        return False, (f"naik ke {after} melewati {MAX_INJECT}: pemanggil lain menyuntik item ini "
                       "lebih dulu")
    if after <= used:
        return False, f"kenaikan inject_count tidak terbaca kembali ({used} -> {after})"
    return True, f"inject {after}/{MAX_INJECT}"


# Kontrak lanjutan untuk FULL AUTO. Ketika sebuah item selesai dan tidak ada utang terukur yang
# menunggu, mesin TIDAK berhenti: satu putaran dari daftar ini diantrikan. Alasannya bukan
# kesibukan — dengan ON dinyalakan, satu komputer diserahkan ke mesin ini, dan jam yang tidak
# dipakai untuk menajamkan diri sendiri adalah jam yang terbuang. Yang dijaga di sini adalah
# bentuknya: tiap kontrak harus bisa DIBUKTIKAN selesai atau gagal, bukan "perbaiki terus".
# Rotasi dihitung dari berapa banyak baris idle yang sudah selesai, jadi putaran berikutnya
# mengerjakan hal yang berbeda, dan `IDLE_MAX_ITEMS` tetap langit-langit yang sama.
FOLLOWUP_CONTRACTS = [
    ("sharpen_skill",
     "Tajamkan satu paket skill: ambil paket dengan dokumen terbanyak yang tidak pernah dirujuk "
     "indeksnya, verifikasi SATU klaim teknis di dalamnya terhadap mesin ini, lalu koreksi atau "
     "tandai klaim itu sebagai belum terukur. Bukti: nama berkas + klaim + hasil pengukuran."),
    ("sharpen_brain",
     "Pertajam brain: baca ulang satu catatan keputusan lama di brain/memory, cocokkan terhadap "
     "isi database hari ini, dan tulis baris koreksi kalau kenyataan sudah bergerak. Bukti: key "
     "catatan + nomor baris DB yang membantunya."),
    ("peer_audit",
     "Diskusi dengan agen lain: kirim satu klaim yang belum diuji ke pasangan lewat ask_peer, "
     "tunggu jawabannya, lalu tandai ANSWERED atau DISPUTED dengan alasan. Bukti: id pesan + "
     "vonis."),
    ("gate_repair",
     "Perbaiki satu gerbang roadmap yang MERAH: cari quest_tasks dengan check_command, jalankan, "
     "dan tangani yang gagal — seperti item yang sedang dikerjakan, bukan seperti laporan. Bukti: "
     "angka keluar gerbang sebelum dan sesudah."),
    ("mcp_sharpen",
     "Periksa satu permukaan MCP terhadap sumbernya: bandingkan jumlah tool/aksi yang ditulis "
     "dokumen dengan yang dihitung dari kode, dan koreksi dokumennya. Bukti: kedua angka."),
]


def queue_followup(conn, backend, cli_engine, emit=None):
    """Satu baris idle berisi kontrak penajaman, dengan pagar yang sama seperti utang terukur.

    Dipanggil hanya ketika TIDAK ada utang terukur — jadi utang nyata selalu menang atas
    pekerjaan yang dipilih sendiri, dan kontrak ini adalah lantai, bukan dalih.
    """
    emit = emit or (lambda *a, **kw: None)
    ph = "%s" if backend == "POSTGRESQL" else "?"
    if not cli_engine:
        emit("IDLE_DEBT_UNKNOWN", why="item selesai tanpa cli_engine — lanjutan tidak tahu ke mana")
        return None
    try:
        open_idle = run(conn, backend,
                        f"SELECT id FROM quest_tasks WHERE origin={ph} "
                        f"AND status IN ('PENDING','CLAIMED','WORKING') LIMIT 1", (ORIGIN_IDLE,)) or []
        if open_idle:
            emit("IDLE_ALREADY_OPEN", item=int(open_idle[0]["id"]))
            return None
        used = idle_budget_used(conn, backend)
        if used >= IDLE_MAX_ITEMS:
            emit("IDLE_BUDGET_SPENT", used=used, cap=IDLE_MAX_ITEMS)
            return None
        kind, title = FOLLOWUP_CONTRACTS[used % len(FOLLOWUP_CONTRACTS)]
        summary = (f"lanjutan otomatis setelah item selesai — tidak ada utang terukur yang menunggu, "
                   f"jadi kontrak penajaman '{kind}' diantrikan; anggaran idle sisa "
                   f"{IDLE_MAX_ITEMS - used - 1}")
        if backend == "POSTGRESQL":
            rows = run(conn, backend,
                       "INSERT INTO quest_tasks (title, status, phase, summary, cli_engine, origin, "
                       "ws_path) VALUES (%s, 'PENDING', 'followup', %s, %s, %s, %s) RETURNING id",
                       (title[:255], summary[:2000], cli_engine, ORIGIN_IDLE, str(REPO)))
            new_id = int(rows[0]["id"]) if rows else None
        else:
            cur = conn.cursor()
            cur.execute("INSERT INTO quest_tasks (title, status, phase, summary, cli_engine, origin, "
                        "ws_path) VALUES (?, 'PENDING', 'followup', ?, ?, ?, ?)",
                        (title[:255], summary[:2000], cli_engine, ORIGIN_IDLE, str(REPO)))
            conn.commit()
            new_id = cur.lastrowid
            cur.close()
        if not new_id:
            return None
        emit("FOLLOWUP_QUEUED", item=int(new_id), kind=kind, used=used + 1, cap=IDLE_MAX_ITEMS)
        return get_item(conn, backend, int(new_id))
    except Exception as exc:  # noqa: BLE001
        emit("IDLE_DEBT_UNKNOWN", why=f"lanjutan tidak bisa diantrikan ({str(exc)[:60]})")
        return None


def finish(conn, engine, item_id, status, evidence=None, reason=None, followup=_MEASURE,
           emit=None):
    """COMPLETED menuntut bukti. Laporan tidak boleh ditulis tangan (§12).

    `emit` diteruskan sampai ke `queue_followup`. Tanpa itu peristiwa "DONE -> lanjut" hilang tanpa
    suara: barisnya tetap ada, tapi tidak ada satu pun log yang mengatakan mengapa baris itu muncul
    — dan baris lanjutan yang tidak bisa dijelaskan asal-usulnya persis laporan yang tidak boleh
    ditulis tangan.
    """
    if status == "COMPLETED" and not (evidence or "").strip():
        return False, "COMPLETED tanpa evidence ditolak: jalankan check_command-nya"
    item = get_item(conn, engine, item_id)
    if not item:
        return False, f"item {item_id} tidak ada"
    ph = "%s" if engine == "POSTGRESQL" else "?"
    run(conn, engine,
        f"UPDATE quest_tasks SET status={ph}, evidence={ph}, escalation_reason={ph}, "
        f"completed_at=CURRENT_TIMESTAMP WHERE id={ph}",
        (status, (evidence or "")[:EVIDENCE_LIMIT], reason, item_id))
    if status == "HUMAN" and not open_decision_for(conn, engine, item_id):
        # Item yang naik ke antrean orang tanpa pertanyaan hanyalah sebuah judul: operator membaca
        # "#33 — F10.3" dan tidak punya apa pun untuk dipilih. Pertanyaan ini ditulis oleh pemilik
        # kosakata yang sama dan ditautkan ke itemnya, dengan pilihan KOSONG — mesin yang mengarang
        # opsi untuk ambang yang baru saja ia lewati sedang menebak, dan tebakannya akan dibaca
        # sebagai opsi yang sudah kita pertimbangkan.
        decide(conn, engine,
               f"Item «{item['title']}» berhenti di ambang: {reason or 'alasan tidak tercatat'}",
               [], "heavy", item_id)
    if status == "COMPLETED" and (item.get("origin") or ORIGIN_LEGACY) != ORIGIN_IDLE:
        # DONE -> lanjut, bukan DONE -> diam. Hanya di ON: di SEMI yang menggerakkan mesin adalah
        # orang, dan pekerjaan yang dipilih sendiri di bawah mode itu adalah penyalahgunaan nama
        # yang sama. Baris idle yang selesai TIDAK mengantrikan penerusnya — itu loop tanpa rem:
        # setiap lanjutan akan melahirkan lanjutan berikutnya selamanya.
        want = followup
        if want is _MEASURE:
            ap = autopilot(conn, engine) or {}
            want = str(ap.get("mode") or "").upper() == "ON"
        if want:
            queue_followup(conn, engine, item.get("cli_engine"), emit=emit)
    return True, f"{item['title']} -> {status}"


def _record_send_events(events):
    """Simpan peristiwa kirim lewat penulis log drainer yang sudah ada — bukan jalur log kedua.

    Dinamai dan dipasang lewat `globals()` supaya selftest bisa mengarahkannya ke penampung
    sendiri. Alasannya bukan kerapian: `drainer.write_events` menulis ke berkas produksi dengan
    path absolut, jadi selftest yang memanggilnya tanpa sambatan akan mengarang baris kegagalan
    di log yang dibaca operator — kelas bug yang persis baru saja ditutup di jalur hook.
    """
    import drainer as _dr
    return _dr.write_events(events)


def note_refusal(conn, engine, item_id, why):
    """Catat MENGAPA sebuah baris belum mendarat, tanpa mengubah statusnya.

    Sebelum fungsi ini ada, `escalation_reason` hanya ditulis `finish()`. Sebuah baris yang
    ditolak gerbang (pane sibuk, jendela tertutup, direktori salah) maka tetap punya
    `escalation_reason = NULL`, persis seperti baris yang belum pernah dilihat siapa pun — dan
    operator bertanya "perintahku hilang ke mana" sementara jawabannya ada di satu-satunya kolom
    yang tidak pernah diisi. Status TETAP PENDING karena memang masih antre; yang bertambah
    hanyalah alasannya. Penolakan bukan kesimpulan, jadi tidak boleh menulis status kesimpulan.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    run(conn, engine, f"UPDATE quest_tasks SET escalation_reason={ph} WHERE id={ph}",
        ((why or "(tanpa alasan dari jalur kirim)")[:500], int(item_id)))
    return True


def verification_keys() -> list:
    """Kunci registri yang boleh dipakai sebuah run untuk memeriksa pekerjaannya sendiri.

    Dibaca dari `config/slots.yaml` + `config/engines.json` pada saat dipakai, bukan dari ingatan
    (§12). Operator menyunting kedua berkas itu dari Station; engine yang menghapal nama model akan
    terus memakai yang lama setelah ia menggantinya, dan itu bukan sekadar basi — itu keputusan
    kuota yang diambil oleh orang yang tidak sedang duduk di meja.
    """
    try:
        import master_data as md
        doc = md.document_from_files()
        slots = doc.get("slots") or {}
        active = slots.get("active_team")
        keys = []
        for team in slots.get("teams") or []:
            if team.get("id") != active:
                continue
            for role in team.get("roles") or []:
                rid = str(role.get("role_id") or "")
                if rid.startswith(("verifier", "decider", "resolver")) and role.get("registry_key"):
                    keys.append(str(role["registry_key"]))
        return sorted(set(keys))
    except Exception:  # noqa: BLE001 — registri yang tidak terbaca dilaporkan, tidak ditebak
        return []


def verifications_in_window(conn, engine, since_epoch):
    """Berapa dispatch verifikasi yang sudah dibayar sejak jendela ini dibuka, atau None.

    Diturunkan dari `action_log`, bukan dari penghitung di dalam memori: proses tick adalah
    one-shot systemd yang mati setelah satu putaran, jadi penghitung mana pun yang hidup di dalam
    proses akan selalu mulai dari nol dan tidak akan pernah menyentuh batasnya. Bentuk SQL-nya
    mengikut `items_done_in_window`: CAST di SQLite karena `strftime()` mengembalikan TEKS dan
    teks melawan integer dengan aturan "integer selalu lebih kecil", dan `EXTRACT(EPOCH …)` di
    PostgreSQL karena `ts` di sini sudah bertipe `timestamptz` — dua backend, satu arti.

    None berarti TIDAK TERBACA, dan itu bukan nol. `action_log` adalah tabel milik daemon, bukan
    milik `work_order`, jadi pada fallback SQLite tabelnya bisa tidak ada; menyebut keadaan itu
    "nol verifikasi" akan membuat batas kuota per malam terlihat ditegakkan padahal tidak pernah
    mengukur apa pun — kebalikan dari yang dibutuhkan orang yang sedang tidur.
    """
    if not since_epoch:
        return 0
    try:
        rows = run(conn, engine,
                   "SELECT COUNT(*) AS n FROM action_log WHERE kind='cross_verify' "
                   "AND EXTRACT(EPOCH FROM ts) >= %s"
                   if engine == "POSTGRESQL" else
                   "SELECT COUNT(*) AS n FROM action_log WHERE kind='cross_verify' "
                   "AND CAST(strftime('%s', ts) AS INTEGER) >= ?",
                   (int(since_epoch),))
    except Exception:  # noqa: BLE001 — tabel tidak ada = tidak terukur, BUKAN nol
        if engine == "POSTGRESQL":
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None
    return int(rows[0]["n"]) if rows else 0


def night_quota_state(conn, engine):
    """Batas kuota per malam untuk run tanpa pengawas (F10.3).

    Judul item ini menjanjikan "batas kuota per malam", dan janji itu tidak pernah ditepati:
    `drainer.py` tidak menyebut kata kuota sama sekali, sementara gerbang itemnya lolos `exit=0`
    karena cabang keduanya selalu benar pada hari timer pernah jalan. Jadi yang ditambal di sini
    bukan kosmetika — ini satu-satunya dari tiga klaim judul yang belum ada.

    Dua angka yang diukur, dan keduanya bisa menolak:

    1. **Langit-langit dispatch.** Satu jendela ON boleh melakukan paling banyak
       `NIGHT_MAX_VERIFICATIONS` pemeriksaan lintas mesin. Ini batas nyata, bukan proxy jumlah
       item: satu item yang sulit bisa memaksa sepuluh verifikasi, dan loop yang menghabiskan
       jendela mingguan untuk membuktikan dirinya adalah kegagalan yang §6 sudah namai.
    2. **Sedikitnya satu jalur verifikasi masih punya meter.** Kalau SEMUA jalur ditolak
       (Claude habis dan Gemini habis atau tidak terbaca), menjalankan item baru hanya menghasilkan
       commit yang tidak bisa berangkat — §6 sudah menetapkan jalurnya: commit lokal, push masuk
       digest pagi. Menolak memulai lebih jujur daripada bekerja semalam untuk sesuatu yang
       tidak bisa dinyatakan benar.

    Satu batasan yang dilaporkan apa adanya, bukan ditutupi: **meter `qoder` tidak terbaca** di
    mesin ini (`plan-credits` dan `org-resource-package` UNAVAILABLE, `add-on-credits` REPORTED
    tanpa angka sisa). Jadi batas ini sengaja dipasang pada verifikasi, yang meternya nyata
    (Gemini 94,8 % / Claude weekly 4,5 % terukur 2026-10-09), bukan pada mesin kerja — sebuah
    "batas kuota" yang membaca meter kosong akan selalu terlihat aman.
    """
    ap = autopilot(conn, engine) or {}
    used = verifications_in_window(conn, engine, int(ap.get("on_epoch") or 0))
    why = []
    notes = []
    if used is None:
        notes.append("dispatch verifikasi TIDAK TERHITUNG (action_log bukan milik modul ini, "
                     "tidak ada di fallback SQLite) — batas kuota per malam tidak ditegakkan pada "
                     "jalan ini; yang tetap bekerja adalah batas item dan pemutus kegagalan")
    elif used >= NIGHT_MAX_VERIFICATIONS:
        why.append(f"{used} dispatch verifikasi sudah dibuat jendela ini "
                   f"(batas {NIGHT_MAX_VERIFICATIONS})")
    keys = verification_keys()
    if not keys:
        why.append("slots.yaml/engines.json tidak terbaca — tidak ada jalur verifikasi yang bisa "
                   "dibuktikan, dan ketiadaan bukti bukan izin")
    else:
        try:
            import master_data as md
            import cross_verify as cv
            engines = (md.document_from_files().get("engines") or {}).get("engines") or {}
            refusals = []
            for k in keys:
                spec = engines.get(k)
                if not isinstance(spec, dict):
                    refusals.append(f"{k}: tidak ada di registri")
                    continue
                r = cv.quota_gate_reason(spec)
                if r:
                    refusals.append(f"{k}: {r}")
            if len(refusals) == len(keys):
                why.append("setiap jalur verifikasi ditolak meter — " + "; ".join(refusals[:3]))
        except Exception as exc:  # noqa: BLE001 — meter yang gagal dibaca adalah UNKNOWN
            why.append(f"meter kuota tidak terbaca ({type(exc).__name__}) — UNKNOWN, bukan tersedia")
    return {"used": used, "cap": NIGHT_MAX_VERIFICATIONS, "paths": keys,
            "measurable": used is not None, "notes": notes, "ok": not why, "why": why}


# Program yang angka keluar bukan-nolnya adalah JAWABAN, bukan kegagalan. Lihat `failing_shapes`.
NONZERO_IS_NORMAL = frozenset({
    "grep", "egrep", "fgrep", "rg", "diff", "cmp", "comm", "pgrep", "pidof", "which",
    "whereis", "type", "test", "[", "[[", "false", "awk", "read",
})


def command_shape(cmd):
    """Bentuk sebuah perintah, tanpa argumennya. Dasar F9.2.

    Yang ingin dikenali adalah "panggilan yang sama", bukan "teks yang sama": `pytest tests/x.py`
    dan `pytest tests/y.py` adalah dua perintah dengan satu bentuk. Karena itu flag, angka, dan
    path dibuang dan yang tersisa adalah program plus subperintah pertamanya.

    Awalan `cd <dir> &&` juga dilepas. Terukur di action_log mesin ini: hampir setiap baris
    dimulai dengan `cd ~/.ai-station && ...`, jadi kalau awalan itu ikut jadi bentuk, SEMUA
    perintah terlihat sebagai satu bentuk yang sama dan laporan anti-pola hanya akan menunjuk
    dirinya sendiri.
    """
    if not cmd:
        return ""
    text = str(cmd).strip()
    # Lepas awalan `cd ... &&` berturut-turut, lalu pembungkus waktu. Tanpa ini hampir setiap baris
    # di ledger terlihat sebagai satu bentuk yang sama, karena mesin ini menjalankan segalanya
    # lewat `cd ~/.ai-station && ...`.
    while True:
        m = re.match(r"^cd\s+\S+\s*(?:&&|;)\s*(.*)$", text, re.S)
        if not m:
            break
        text = m.group(1).strip()
    text = re.sub(r"^(?:timeout\s+[\d.]+|time)\s+", "", text)
    # Angka keluar sebuah perintah komposit adalah angka keluar SEGMEN TERAKHIR: `a; b` berakhir
    # seperti b, `a && b` juga. Mengambil segmen pertama, seperti versi pertama, menuduh
    # `echo "=== x ==="; python3 tools/y.py` sebagai bentuk `echo ===` — peringatan yang menunjuk
    # perintah yang salah lebih buruk daripada tidak ada peringatan, karena orang akan berhenti
    # membaca peringatan setelah ketemu satu yang salah.
    parts = [p for p in re.split(r"&&|\|\||;", text) if p.strip()]
    if parts:
        text = parts[-1].strip()
    # Pipeline DIKECUALIKAN, bukan dialamatkan ke salah satu tahapnya. Harness ini berjalan dengan
    # `pipefail`, jadi angka keluar yang tercatat adalah angka SELURUH pipeline: pada
    # `grep x | grep y | head -40` yang gagal adalah grep tengah, sementara segmen terakhir adalah
    # head. Mengambil segmen terakhir menuduh `head` — dan itu terukur hari ini, tiga kali, semuanya
    # positif palsu. Peringatan yang menunjuk program yang salah lebih buruk daripada tidak ada
    # peringatan: setelah yang pertama salah, orang berhenti membaca yang berikutnya.
    if "|" in text:
        return ""
    out = []
    for t in text.split():
        if t.startswith("<<"):
            break                         # heredoc: sisanya adalah isi, bukan identitas perintah
        if t.startswith("-"):
            continue                      # flag bukan identitas
        if "/" in t or "\\" in t:
            # Path diambil NAMA AKHIRNYA saja. Membuang path seluruhnya, seperti versi pertama,
            # membuat `bash bin/check_dash_order.sh` dan `bash bin/deploy_engine.sh` menyatu jadi
            # "bash" — dan laporan anti-pola lalu menuduh dua perintah berbeda sebagai pengulangan
            # yang sama. Basename tetap stabil walau direktorinya berubah.
            t = t.rstrip("/").split("/")[-1]
        if not t or re.fullmatch(r"[\d.]+", t):
            continue                      # angka telanjang (duration, port, baris) bukan identitas
        # Ledger menyimpan `summary` TERPOTONG, jadi token sering berakhir dengan kutipan menggantung:
        # `grep "def` dari perintah yang lebih panjang. Tanpa dilepas, bentuknya terbaca sebagai
        # `"def` dan dua perintah berbeda tidak akan pernah bertemu di laporan yang sama.
        t = t.strip("\"'`")
        if not t:
            continue
        out.append(t.lower())
        if len(out) >= 2:
            break
    return " ".join(out[:2])


def failing_shapes(conn, engine, limit=3, lookback_hours=24):
    """Bentuk perintah yang berulang kali keluar dengan angka bukan-nol.

    Diturunkan dari `command_exit` yang dipasangkan dengan baris `command`-nya — pembacaan yang
    sama yang dipakai F9.1, jadi tidak ada definisi kedua untuk "perintah terakhir" (§12).
    """
    span = int(lookback_hours) * 3600
    # Yang diambil HANYA exit yang bukan-nol, dan perintah pendahulunya diambil lewat jendela
    # LAG pada himpunan terfilter itu. Bentuk pertama mengambil semua baris lalu memfilter di
    # Python dengan LIMIT 800: pada sesi sepanjang ini 800 baris terakhir hanya mencakup beberapa
    # menit, jadi 119 kegagalan nyata dalam 24 jam tidak pernah masuk jendela baca dan laporannya
    # kosong — kekosongan yang terbaca sebagai "tidak ada pola buruk".
    failed = ("(kind = 'command_exit' AND exit_code IS NOT NULL AND exit_code <> 0")
    if engine == "POSTGRESQL":
        sql = ("SELECT kind, summary, exit_code, "
               "LAG(kind) OVER (ORDER BY ts) AS prev_kind, "
               "LAG(summary) OVER (ORDER BY ts) AS prev_summary "
               "FROM action_log WHERE kind = 'command' OR "
               f"{failed} AND ts >= now() - make_interval(secs => %s)) "
               "ORDER BY ts DESC LIMIT 2000")
        params = (span,)
    else:
        # SQLite tidak membawa `ts` yang bisa dikurangi interval dengan bentuk yang sama, dan
        # fallback ini hanya dipakai saat PostgreSQL mati; jendela waktu dilepas di sini dan itu
        # dilaporkan lewat `windowed=False`, supaya pemanggil tidak mengira angka ini 24 jam.
        sql = ("SELECT kind, summary, exit_code, "
               "LAG(kind) OVER (ORDER BY ts) AS prev_kind, "
               "LAG(summary) OVER (ORDER BY ts) AS prev_summary "
               "FROM action_log WHERE kind = 'command' OR "
               f"{failed}) ORDER BY ts DESC LIMIT 2000")
        params = ()
    try:
        rows = run(conn, engine, sql, params)
    except Exception:  # noqa: BLE001 — ledger tidak terbaca = tidak ada yang bisa dikatakan
        return []
    tally = {}
    for r in rows:
        if r["kind"] != "command_exit" or (r.get("prev_kind") or "") != "command":
            continue
        sh = command_shape(r.get("prev_summary"))
        if not sh:
            continue
        # Angka keluar bukan-nol BUKAN selalu kegagalan. `grep` 1 = tidak ada yang cocok,
        # `diff` 1 = berkas berbeda, `test` 1 = false — semuanya jawaban normal atas pertanyaan
        # yang memang ditanyakan. Memperlakukannya sebagai anti-pola membuat peringatan menyala
        # pada setiap sesi yang sehat, dan peringatan yang selalu menyala tidak dibaca siapa pun.
        if sh.split(" ", 1)[0] in NONZERO_IS_NORMAL:
            continue
        e = tally.setdefault(sh, {"n": 0, "last": r["exit_code"]})
        e["n"] += 1
    ranked = sorted(((k, v["n"], v["last"]) for k, v in tally.items() if v["n"] >= 2),
                    key=lambda x: (-x[1], x[0]))
    return [{"shape": k, "fails": n, "last_exit": last} for k, n, last in ranked[:int(limit)]]


def shape_warning(shape):
    """Peringatan untuk satu bentuk yang akan dijalankan, atau None."""
    if not shape:
        return None
    return (f"ANTI-PATTERN: bentuk `{shape}` pernah keluar bukan-nol di jendela ini — "
            "menjalankannya lagi tanpa mengubah apa pun adalah cara loop mengunyah baris yang sama")


def next_item(conn, engine, skip_blocked=True, respect_due=False, origin=None, for_engine=None,
              drop_stale_legacy=False):
    """Item berikutnya yang siap: semua dependensinya COMPLETED, dan lease-nya tidak dipegang orang.

    `UNAVAILABLE` tidak pernah ditawarkan, sama seperti `FAILED`: keduanya adalah kesimpulan yang
    sudah dilaporkan, bukan pekerjaan yang menunggu. Menawarkannya lagi membuat sebuah loop yang
    tampak sibuk padahal hanya mengunyah baris yang mesin sudah katakan tidak bisa dijalankan.

    `respect_due` menambahkan satu syarat lagi atas nama pemanggil yang punya ritme: `due_epoch`
    adalah kolom antrean tertunda, dan yang berhak menuliskannya adalah yang menahan dirinya sendiri
    (drainer, F10.3). Filternya tinggal di sini karena menentukan "apa berikutnya" adalah bagian
    dari kosakata antrean — kalau drainer menyusun urutannya sendiri, ada dua definisi "berikutnya"
    dan tidak ada yang bisa mengatakan yang mana yang dibaca GUI (§12).

    `origin` adalah batas antara SEMI dan FULL AUTO, dan ia disaring di fungsi yang sama untuk
    alasan yang sama. None berarti "apa pun", jadi setiap pemanggil lama berperilaku persis seperti
    sebelumnya; `ORIGIN_OPERATOR` berarti hanya perintah yang manusia tulis sendiri ke database yang
    boleh muncul — dan itulah seluruh isi kata "semi": mesinnya tetap otomatis, tetapi yang ia
    kerjakan adalah perintah orang, bukan apa pun yang kebetulan tersedia.
    """
    return next(iter(ready_items(conn, engine, skip_blocked=skip_blocked,
                                 respect_due=respect_due, origin=origin,
                                 for_engine=for_engine,
                                 drop_stale_legacy=drop_stale_legacy)), None)


def ready_items(conn, engine, skip_blocked=True, respect_due=False, origin=None, for_engine=None,
                drop_stale_legacy=False):
    """SEMUA baris yang siap, dengan aturan yang sama persis dengan `next_item`.

    `drop_stale_legacy` (keputusan 45, dijawab `qwen3.5:9b` pada rung `resolver-local`, 03:5x):
    baris `origin='legacy'` yang masih berstatus kerja tapi lease-nya 0 adalah kepala antrean yang
    mustahil selesai — `next_item` membacanya sebagai lease kedaluwarsa, jadi ia ditawarkan ulang
    SETIAP tick, selalu kena cooldown, dan menahan baris operator yang nyata. Terukur: 14 baris
    (id 222..244), dan `next_ready()` malam ini menyerahkan 223, sebuah rebrand UECP dari sebelum
    skema ini ada. Yang dibuang di sini hanya yang sedemikian: legacy PENDING biasa TETAP
    ditawarkan, dan pelaporan TIDAK memakai flag ini — baris yang ditahan harus tetap terlihat,
    sama seperti baris ber-cooldown.

    Dipisah supaya pelaporan bisa memakai daftar ini. Sebelumnya `semi_status` menyusun
    `operator_queue`-nya sendiri dengan SQL mentah `status='PENDING'` — tanpa `norm()`, tanpa
    dependensi, tanpa lease, tanpa `due_epoch` — sehingga Dashboard bisa bilang "3 menunggu"
    sementara `next_item` tidak menawarkan satu pun. Dua definisi untuk satu kata, dan yang
    dibaca operator adalah yang salah.
    """
    rows = run(conn, engine,
               "SELECT id, title, status, depends_on, claimed_by, lease_epoch, phase, due_epoch, "
               "origin, ws_path, cli_engine, escalation_reason FROM quest_tasks ORDER BY id")
    out = []
    done = {int(r["id"]) for r in rows if norm(r["status"]) == "COMPLETED"}
    for r in rows:
        state = norm(r["status"])
        if state in ("COMPLETED", "FAILED", "UNAVAILABLE"):
            continue
        if state in ("BLOCKED", "HUMAN", "PARKED") and skip_blocked:
            continue
        # KEDUA status kerja ikut, dan itu bukan kosmetik: `norm()` memetakan IN_PROGRESS warisan
        # ke CLAIMED, bukan WORKING — jadi versi pertama filter ini hanya menulis WORKING,
        # `next_ready()` tetap mengembalikan 222, dan keputusan 45 seolah sudah dikerjakan padahal
        # tidak mengubah apa pun. Yang ditahan adalah klaim tanpa lease hidup: tidak ada yang
        # memegangnya, dan tidak ada yang bisa menyelesaikannya lewat jalur ini.
        if (drop_stale_legacy and state in ("WORKING", "CLAIMED")
                and (r.get("origin") or ORIGIN_LEGACY) == ORIGIN_LEGACY
                and int(r.get("lease_epoch") or 0) <= now()):
            continue
        if origin is not None and (r.get("origin") or ORIGIN_LEGACY) != origin:
            continue
        # `for_engine` ada di sini karena pemicu yang hidup DI DALAM satu sesi tidak boleh mengambil
        # baris milik sesi lain: Stop hook Antigravity yang mengambil perintah bertarget qoder akan
        # mengerjakan pekerjaan orang lain sambil meninggalkan yang sebenarnya menunggu. Sama seperti
        # `origin`, filternya tinggal di pemilik kata "berikutnya", bukan di pemanggilnya.
        if for_engine is not None and (r.get("cli_engine") or "").strip() != for_engine:
            continue
        if respect_due and int(r.get("due_epoch") or 0) > now():
            continue
        deps = [int(x) for x in str(r.get("depends_on") or "").split(",") if x.strip()]
        missing = [d for d in deps if d not in done]
        if missing:
            continue
        if r.get("claimed_by") and int(r.get("lease_epoch") or 0) > now() and state == "WORKING":
            continue
        out.append(r)
    return out


def resolve_decision(conn, engine, decision_id, rung, engine_key, model_id, answer,
                     state="RESOLVED", note=None):
    """Catat siapa yang menjawab. Baris OPEN tanpa jawaban adalah pertanyaan yang belum dijawab.

    `answer` boleh None: jalur ESCALATED memang tidak punya jawaban, dan di sinilah pertanyaan
    yang tidak bisa diselesaikan mesin berakhir di antrean orang. Membedah None dengan slicing
    akan membuat kegagalan pertama yang paling penting justru crash alih-alih melaporkan.
    """
    if state == "RESOLVED" and not (model_id or "").strip():
        return False, "RESOLVED tanpa model_id ditolak: laporan harus menyebut yang menjawab"
    ph = "%s" if engine == "POSTGRESQL" else "?"
    run(conn, engine,
        f"UPDATE decisions SET state={ph}, rung_used={ph}, engine_key={ph}, model_id={ph}, "
        f"answer={ph}, note={ph}, resolved_epoch={ph} WHERE id={ph}",
        (state, rung, engine_key, model_id, answer[:4000] if answer else None,
         note, now(), decision_id))
    return True, f"decision {decision_id} -> {state}"


def _decision_row(conn, engine, decision_id):
    ph = "%s" if engine == "POSTGRESQL" else "?"
    rows = run(conn, engine, f"SELECT * FROM decisions WHERE id={ph}", (decision_id,))
    return rows[0] if rows else None


def answer_decision(conn, engine, decision_id, answer, by="operator", note=None):
    """Rekam jawaban MANUSIA atas satu pertanyaan. `(diterima, pesan, baris terbaru)`.

    Fungsi ini satu-satunya jalur tulis untuk operator, dan ia sengaja menyerupai `resolve_decision`
    alih-alih memforknya: kosakata `state`/`answer`/`resolved_epoch` punya satu pemilik (§12), dan
    dua jalur tulis untuk satu kolom adalah dua laporan yang boleh berbeda.

    Yang membedakan adalah siapa yang dicatat. `answered_by` mengisi tempat yang untuk mesin diisi
    `model_id`, supaya jawaban orang tidak pernah bisa disalahartikan sebagai jawaban resolver —
    kontrak §4 menegakkan pembedaan itu, dan laporan yang tidak bisa membedakannya tidak bisa
    dipakai membela diri nanti.

    Baris yang sudah dijawab DITOLAK, bukan ditimpa. Menimpa keputusan tercatat berarti menghapus
    bukti siapa yang memutuskan; kalau operator berubah pikiran, jalurnya adalah pertanyaan baru
    yang menunjuk item yang sama, bukan editing sejarah.
    """
    if not (answer or "").strip():
        return False, "jawaban kosong ditolak: pertanyaan tetap " + str(decision_id), None
    row = _decision_row(conn, engine, decision_id)
    if not row:
        return False, f"decision {decision_id} tidak ada — tidak ada yang dijawab, tidak ada yang ditulis", None
    if row.get("state") not in ("OPEN", "ESCALATED"):
        who = row.get("answered_by") or row.get("model_id") or "tidak tercatat"
        when = datetime.fromtimestamp(int(row.get("resolved_epoch") or 0)).strftime("%Y-%m-%d %H:%M")
        return False, (f"decision {decision_id} sudah {row['state']} oleh {who} sejak {when}; "
                       "ditolak — buat pertanyaan baru kalau pilihan berubah"), row
    ph = "%s" if engine == "POSTGRESQL" else "?"
    who = (by or "operator").strip()[:60]
    run(conn, engine,
        f"UPDATE decisions SET state={ph}, answer={ph}, rung_used={ph}, engine_key={ph}, "
        f"model_id={ph}, answered_by={ph}, note={ph}, resolved_epoch={ph} WHERE id={ph}",
        ("ANSWERED", answer.strip()[:4000], "human", "operator", who, who,
         (note or "")[:2000] or None, now(), decision_id))
    fresh = _decision_row(conn, engine, decision_id)
    return True, f"decision {decision_id} ANSWERED oleh {who}", fresh


def decisions_for_item(conn, engine, item_id):
    """Semua pertanyaan yang menunjuk satu item, terbuka maupun terjawab.

    Pembacanya yang menyaring `state`, bukan fungsi ini: "apa yang masih menunggu" dan "apa yang
    sudah diputuskan" adalah dua pertanyaan berbeda, dan menyatukan filternya di sini akan memaksa
    tiap pemanggil memanggil dua kali begitu salah satu berubah kebutuhan.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    return run(conn, engine,
               f"SELECT id, question, options, weight, state, answer, model_id, answered_by, "
               f"created_epoch, resolved_epoch FROM decisions WHERE item_id={ph} ORDER BY id",
               (item_id,))


def open_decision_for(conn, engine, item_id):
    """Pertanyaan yang masih menunggu untuk satu item — atau None.

    Ini yang ditanya drainer sebelum ia mengirim apa pun ke sebuah pane: menjawab dirinya sendiri
    dengan `CONTINUE` padahal yang ditunggu adalah keputusan orang adalah cara paling sopan untuk
    membakar lease orang lain.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    rows = run(conn, engine,
               f"SELECT * FROM decisions WHERE item_id={ph} AND state IN ('OPEN','ESCALATED') "
               f"ORDER BY id", (item_id,))
    return rows[0] if rows else None


def _last_action(conn, engine, who):
    """`(epoch, terbaca)` aksi terukur terakhir satu engine — dua nilai, bukan satu.

    Satu angka tidak boleh menyamar jadi dua hal. Tabel yang tidak terbaca dan engine yang memang
    belum pernah mencatat sama-sama kembali sebagai None, dan kalau keduanya berarti "tidak ada
    aksi", sinyal yang HILANG justru terbaca sebagai bukti terkuat bahwa sebuah pane menganggur.
    Invarian 5 berlaku ke dua sisi, bukan cuma sisi tmux. Yang TIDAK bisa dibedakan dari sini adalah
    "belum pernah mencatat" dan "mencatat dengan nama lain" — `claimed_by` dan kolom `engine` punya
    ruang penamaan masing-masing — jadi `heartbeat()` memperlakukan None sebagai sinyal yang tidak
    mendukung kesimpulan apa pun, bukan sebagai bukti menganggur.

    Kolomnya `ts` di kedua backend (DDL milik `session_ingestor.py`: `TIMESTAMPTZ` di Postgres,
    `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` di SQLite), tapi bentuknya beda: Postgres mengirim datetime
    ber-zona, SQLite mengirim teks UTC. Teks yang diperlakukan sebagai waktu lokal membuat umur aksi
    meleset sejauh zona waktu mesin — tujuh jam di sini — dan meleset ke masa lalu adalah arah yang
    melahirkan `AT_REST` palsu.
    """
    try:
        rows = run(conn, engine,
                   "SELECT MAX(ts) AS t FROM action_log WHERE engine=" +
                   ("%s" if engine == "POSTGRESQL" else "?"),
                   (who,))
    except Exception:  # noqa: BLE001 — tabel tidak ada / koneksi seret: itu UNKNOWN, bukan diam
        # Koneksi ini autocommit (db_state.py: conn.autocommit = True), jadi kegagalan SELECT tidak
        # meninggalkan transaksi aborted yang membuat pembacaan berikutnya ikut gagal.
        return None, False
    v = rows[0]["t"] if rows and rows[0].get("t") else None
    if v is None:
        return None, True
    if isinstance(v, str):
        try:
            v = datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None, False
    if hasattr(v, "timestamp"):
        return int(v.timestamp()), True
    # Float / Decimal / date bukan bentuk yang dijanjikan DDL mana pun. Membacanya sebagai
    # "terbaca, cuma None" akan menyamar jadi "engine ini belum berbuat apa-apa".
    return (v if isinstance(v, int) else None), isinstance(v, int)


def stalled(conn, engine, older_than=180):
    """Kandidat resume: WORKING, lease masih hidup, tidak ada aksi terukur selama `older_than`s.

    Ini hanya daftar satu sinyal. `heartbeat()` yang menambahkan sinyal kedua dan aturan UNKNOWN,
    dan hanya bacaannya yang boleh dipakai. Karena itu tiap baris membawa `action_log_readable`:
    daftar yang menyamar jadi kesimpulan adalah cara tercepat mengetik ke tengah kerja orang lain.
    """
    rows = run(conn, engine,
               "SELECT id, title, claimed_by, lease_epoch, resume_count FROM quest_tasks "
               "WHERE status IN ('WORKING','CLAIMED') AND claimed_by IS NOT NULL")
    fresh = []
    cutoff = now() - older_than
    for r in rows:
        epoch, readable = _last_action(conn, engine, r["claimed_by"])
        if readable and r.get("lease_epoch") and r["lease_epoch"] > now() \
                and (epoch is None or epoch < cutoff):
            fresh.append({**r, "last_action_epoch": epoch, "action_log_readable": True})
    return fresh


def _pane_text(capture, tab):
    """Satu pembacaan pane. `capture` milik orang lain (tmux, nanti SSH, nanti mesin lain), jadi
    bentuk kegagalannya bukan hanya None: fungsi yang melempar adalah pembacaan yang mati, dan
    PEMBACAAN YANG MATI adalah UNKNOWN — bukan pane kosong, bukan pane diam, bukan crash."""
    try:
        return capture(tab)
    except Exception:  # noqa: BLE001
        return None


def heartbeat(conn, engine, quiet_seconds=180, sample_gap=3, capture=None, busy=None):
    """WORKING / AT_REST / UNKNOWN per klaim terbuka — dua sinyal harus setuju dulu.

    Yang ditolak di sini adalah cara termudah: menyimpulkan "diam" dari satu sinyal. Sebuah CLI
    yang sedang berpikir panjang tidak menulis baris `action_log`, dan satu yang baru saja selesai
    menulis juga tidak. Jadi dibutuhkan dua pembacaan yang saling bebas — umur aksi di database dan
    teks pane yang berubah di antara dua sampel — dan `AT_REST` hanya keluar kalau KEDUANYA setuju.
    Salah menyimpulkan `AT_REST` berarti mengetik `CONTINUE` ke tengah pekerjaan orang lain.

    Invarian 5 menentukan sisanya, untuk KEDUA sisi, dan ia kejam: EMPAT keadaan berbeda semuanya
    menghasilkan nol pembacaan dan keempatnya `UNKNOWN`, bukan "diam" — pane yang tidak terbaca, tabel
    yang tidak terbaca, tabel yang terbaca tapi KOSONG untuk nama engine ini, dan dua pembacaan yang
    diambil tanpa jeda. Yang ketiga itu temuan reviewer (Oct 8, 02:05): `claimed_by` dan kolom `engine`
    di ledger adalah dua ruang penamaan yang tidak dijamin sama, jadi "tidak ada baris atas nama qoder"
    bisa berarti "ia belum berbuat apa-apa" ATAU "ledger menyimpannya dengan nama lain" — dan satu-
    satunya pembacaan yang tersisa lalu menyimpulkan diam dari satu sinyal.

    Interval karena itu ikut menjadi bukti, bukan bentuk: `sample_gap` punya lantai di jalur produksi
    (`MIN_SAMPLE_GAP`), dan 0 hanya honored untuk `capture` suntikan — tes tidak boleh tidur, produksi
    tidak boleh mengambil dua sampel di waktu yang sama. Karena itu tidak ada satu pun jalur lewat
    fungsi ini yang mengirim apa pun — fungsi ini hanya indera. Tangan milik F10.3, dan ia belum boleh
    dipakai malam ini.

    `capture`/`busy` bisa diganti supaya tes tidak membaca pane operator yang sedang hidup.
    """
    injected = capture is not None or busy is not None
    if capture is None or busy is None:
        import cross_verify as cv
        capture = capture or cv.capture_pane
        busy = busy or cv.pane_is_busy
    if sample_gap < MIN_SAMPLE_GAP and not injected:
        sample_gap = MIN_SAMPLE_GAP
    rows = run(conn, engine,
               "SELECT id, title, claimed_by, lease_epoch, resume_count FROM quest_tasks "
               "WHERE status IN ('WORKING','CLAIMED') AND claimed_by IS NOT NULL")
    if not rows:
        return []
    tabs = sorted({r["claimed_by"] for r in rows})
    before = {t: _pane_text(capture, t) for t in tabs}
    if sample_gap:
        time.sleep(sample_gap)
    after = {t: _pane_text(capture, t) for t in tabs}
    interval = sample_gap > 0

    out = []
    for r in rows:
        who = r["claimed_by"]
        epoch, log_readable = _last_action(conn, engine, who)
        quiet_s = None if epoch is None else now() - epoch
        b, a = before.get(who), after.get(who)
        pane_readable = bool(b) and bool(a)
        if not pane_readable and not log_readable:
            state, why = "UNKNOWN", "kedua sinyal mati: pane dan action_log tidak terbaca"
        elif not pane_readable:
            state, why = "UNKNOWN", "pane tidak terbaca — bukan berarti diam"
        elif not log_readable:
            state, why = "UNKNOWN", "action_log tidak terbaca: satu sinyal hilang, dua belum setuju"
        elif busy(a):
            state, why = "WORKING", "pane menampilkan spinner/esc-to-cancel"
        elif b != a:
            # Perubahan adalah bukti dengan sendirinya: dua pembacaan yang BERBEDA membuktikan gerak
            # walau diambil beruntun. Yang butuh jeda hanya kesimpulan "tidak ada perubahan".
            state, why = "WORKING", "teks pane berubah di antara dua sampel"
        elif not interval:
            state, why = "UNKNOWN", "dua pembacaan tanpa jeda: 'tidak berubah' bukan bukti"
        elif epoch is None:
            state, why = "UNKNOWN", "ledger tidak punya baris atas nama ini: diam atau nama lain"
        elif quiet_s < quiet_seconds:
            state, why = "WORKING", f"aksi terukur {quiet_s}s lalu"
        else:
            state, why = "AT_REST", f"dua sinyal setuju: aksi terakhir {quiet_s}s lalu, pane diam {sample_gap}s"
        out.append({**{k: r[k] for k in ("id", "title", "claimed_by", "resume_count")},
                    "state": state, "why": why,
                    "pane_readable": pane_readable, "action_log_readable": log_readable,
                    "last_action_epoch": epoch, "quiet_seconds": quiet_s,
                    "sample_gap": sample_gap,
                    "lease_alive": bool(r.get("lease_epoch")) and r["lease_epoch"] > now(),
                    "resume_budget_left": max(0, MAX_RESUME - int(r.get("resume_count") or 0))})
    return out


def autopilot(conn, engine, mode=None, minutes=None, budget=None, who="operator", reason=None):
    if mode is None:
        rows = run(conn, engine, "SELECT * FROM autopilot_state WHERE id=1")
        if not rows:
            return None
        row = rows[0]
        # `items_done` tidak pernah diincrement siapa pun, dan kedua permukaan membaca kolom itu
        # apa adanya: laporan "0 item malam ini" yang tetap nol walaupun ada pekerjaan selesai.
        # §12 melarang laporan ditulis tangan, jadi angka yang ditampilkan diturunkan dari ledger;
        # nilai tersimpan dilaporkan terpisah supaya perbedaan keduanya terlihat, bukan ditutupi.
        row["items_done_stored"] = row.get("items_done")
        row["items_done"] = items_done_in_window(conn, engine, int(row.get("on_epoch") or 0))
        return row
    ph = "%s" if engine == "POSTGRESQL" else "?"
    until = now() + int(minutes) * 60 if minutes else None
    wanted = (mode or "").upper()
    if wanted in STOP_WORDS:
        # Kata rem adalah ALIAS menuju OFF, bukan keadaan keempat yang hidup di kolom lain. Sebuah
        # typo ("stop", "berhenti") tetap berarti berhenti, dan yang ditulis adalah kata yang sama
        # dengan yang dibaca layar.
        wanted = OFF_MODE
    if wanted == OFF_MODE:
        # OFF ditulis sebagai MODE. Versi lama menulis jendela ke masa lalu dan MEMBIARKAN `mode`
        # apa adanya, sehingga mesin yang sudah berhenti dilaporkan sebagai SEMI. Itu bukan sekadar
        # membingungkan: perintah operator di bawah "SEMI yang sudah mati" ditolak WINDOW_CLOSED
        # sementara satu-satunya angka yang dibaca orang menyebut mode masih aktif, dan tidak ada
        # jalan untuk membedakan "saya belum menekan apa-apa" dari "saya sudah menekan berhenti".
        # Invarian 6 berlaku penuh: jalur ini tidak butuh TTY dan tidak butuh kerja sama AI.
        run(conn, engine,
            f"UPDATE autopilot_state SET mode={ph}, until_epoch={ph}, level='observe', "
            f"level_until=NULL, off_reason={ph}, changed_by={ph} WHERE id=1",
            (OFF_MODE, now(), reason or "berhenti — tangan diturunkan, jendela ditutup", who))
    elif wanted in MODES:
        # `level` ikut direset di kedua cabang. Tanpa ini, tangan yang diangkat dari TTY saat
        # keranjang masih SEMI (hanya perintah operator) otomatis berlaku untuk keranjang ON
        # (seluruh roadmap) tanpa satu pun tindakan TTY baru.
        # Jendela waktu dibuka untuk SEMI juga — bukan hanya untuk ON — karena
        # `items_done_in_window` dan pemutus malam membacanya: tanpa `on_epoch` yang benar,
        # laporan "berapa item selesai malam ini" menjadi nol selamanya di bawah SEMI, dan laporan
        # yang salah lebih berbahaya daripada laporan yang tidak ada.
        run(conn, engine,
            f"UPDATE autopilot_state SET mode={ph}, on_epoch={ph}, until_epoch={ph}, "
            f"item_budget={ph}, items_done=0, off_reason=NULL, changed_by={ph}, "
            f"level='observe', level_until=NULL WHERE id=1",
            (wanted, now(), until, budget or 12, who))
    else:
        row = autopilot(conn, engine) or {}
        # Menolak, bukan memilihkan. Mode yang tidak dikenal tidak boleh diam-diam jadi SEMI
        # (itu memperluas keranjang) atau diam-diam berhenti (itu menghapus rem yang orang sangka
        # masih ada). Yang ditulis hanya pesan penolakannya; baris tidak disentuh.
        row["refused"] = (f"mode {wanted!r} bukan kosakata: {'/'.join(MODES)} — untuk berhenti "
                          "pakai 'off' (menulis state OFF, menurunkan tangan, menutup jendela) "
                          "atau `disarm`")
        return row
    return autopilot(conn, engine)


# --- F10.3: tangga tangan drainer -------------------------------------------
# `mode` (ON/OFF) menjawab SIAPA yang memegang mesin; `level` menjawab JAUH MANA tangan boleh
# bergerak. Keduanya tinggal di baris yang sama, supaya tidak ada dua laporan yang boleh
# berbeda (§12).


LEVELS = ("observe", "dispatch", "resume")
ARM_DEFAULT_MINUTES = 45
ARM_MAX_MINUTES = 240


def has_tty() -> bool:
    """Ada terminal pengendali di belakang pemanggil ini — diukur lewat /dev/tty, bukan ditebak.

    Terukur 2026-10-08: `systemd-run --user --wait` (jalur timer) dan Bash tool milik Qoder sama-sama
    gagal dengan ENXIO, sementara tiap pane `station-*` memegang pts sendiri. Jadi tidak ada jalur
    tak-berpengawas yang bisa mempersenjatai dirinya sendiri. Yang TIDAK dijaga pagar ini: mengetik
    ke pane operator lewat send-keys — dan itu justru tangan yang sedang dijaga, jadi kontrak F10.8
    sudah melarangnya lebih dulu. Pagar ini membuat kelalaian tidak mungkin, bukan pembangkangan
    tidak mungkin; menyebutnya batas keamanan akan lebih berbahaya daripada tanpa pagar sama sekali.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return False
    os.close(fd)
    return True


def drain_level(conn, engine):
    """Tingkat tangan sebagai turunan: apa yang berlaku MENIT INI, bukan apa yang pernah ditulis."""
    try:
        rows = run(conn, engine,
                   "SELECT level, level_until, changed_by FROM autopilot_state WHERE id=1")
    except Exception:  # noqa: BLE001
        return {"level": None, "requested": None, "readable": False, "expired": None,
                "seconds_left": 0, "changed_by": None, "allows_dispatch": False,
                "allows_resume": False,
                "why": "baris autopilot tidak terbaca — tertutup karena UNKNOWN, bukan karena observe"}
    row = rows[0] if rows else {}
    want = row.get("level") or "observe"
    until = row.get("level_until")
    effective, why = want, []
    if want not in LEVELS:
        effective = "observe"
        why.append(f"level '{want}' bukan kosakata — tangga terkecil yang dipakai")
    elif want != "observe":
        if until is None:
            effective = "observe"
            why.append(f"{want} ditulis tanpa batas waktu; sakelar yang tinggal ON karena lupa "
                       "bukan sakelar (F10.8 §2)")
        elif int(until) <= now():
            effective = "observe"
            why.append(f"{want} kedaluwarsa {now() - int(until)}s lalu")
    return {"level": effective, "requested": want, "readable": True,
            "expired": effective != want, "changed_by": row.get("changed_by"),
            "seconds_left": max(0, int(until) - now()) if (until and effective != "observe") else 0,
            "allows_dispatch": effective in ("dispatch", "resume"),
            "allows_resume": effective == "resume", "why": why}


def set_level(conn, engine, level, minutes=ARM_DEFAULT_MINUTES, who="operator", tty=_MEASURE):
    """Naikkan tangan — butuh terminal pengendali. Menurunkan tangan tidak pernah butuh apa pun.

    Asimetrinya intinya, bukan kenyamanan: invarian 6 bilang jalan mati tidak boleh bergantung pada
    kerja sama AI, jadi `observe` bisa ditulis dari mana saja — termasuk timer yang sedang berlari
    dan selftest yang tidak punya tty. Yang naik tangga-lah yang harus diketik orang.
    """
    if level not in LEVELS:
        return False, f"level '{level}' bukan kosakata: {'/'.join(LEVELS)}"
    if minutes < 1 or minutes > ARM_MAX_MINUTES:
        return False, f"menit harus 1..{ARM_MAX_MINUTES} (dapat {minutes}); tanpa batas waktu ditolak"
    tty = has_tty() if tty is _MEASURE else tty
    if level != "observe" and not tty:
        return False, (f"menaikkan tangan ke '{level}' butuh terminal pengendali — /dev/tty tidak "
                       "terbuka dari proses ini. Menurunkan (`disarm`) tidak butuh tty.")
    until = None if level == "observe" else now() + int(minutes) * 60
    ph = "%s" if engine == "POSTGRESQL" else "?"
    # Menaikkan tangan juga MEMBUKA JENDELA, dan itu bukan kenyamanan melainkan penutup jebakan.
    # `off`/`disarm` menutup jendela (lihat cabang STOP_WORDS di `autopilot`); kalau `arm` hanya
    # menulis `level`, orang yang mengetik `arm dispatch` setelah memakai rem akan menemukan tangannya
    # naik sementara setiap klaim ditolak "jendela ON lewat" — rem yang hanya bisa dipakai sekali, dan
    # kegagalan yang paling sulit ditebak karena layar menunjukkan level yang benar.
    # Aturan gabungannya sengaja satu arah: jendela yang masih terbuka TIDAK pernah dipendekkan
    # (naik ke `resume` selama 20 menit tidak boleh merampas 4 jam yang orang lain sudah setujui),
    # tapi jendela yang sudah lewat atau belum pernah dibuka menjadi sejauh menit yang diketik ini.
    if level == "observe":
        run(conn, engine,
            f"UPDATE autopilot_state SET level={ph}, level_until=NULL, until_epoch={ph}, "
            f"changed_by={ph} WHERE id=1",
            (level, now(), who))
    else:
        open_from = int(((autopilot(conn, engine) or {}).get("until_epoch")) or 0)
        window_until = max(open_from, now() + int(minutes) * 60)
        window_from = now() if open_from <= now() else int(
            ((autopilot(conn, engine) or {}).get("on_epoch")) or now())
        run(conn, engine,
            f"UPDATE autopilot_state SET level={ph}, level_until={ph}, on_epoch={ph}, "
            f"until_epoch={ph}, off_reason=NULL, changed_by={ph} WHERE id=1",
            (level, until, window_from, window_until, who))
    return True, json.dumps(drain_level(conn, engine), default=str)


def decide(conn, engine, question, options=None, weight="heavy", item_id=None):
    """Catat satu pertanyaan. `options` boleh label polos atau objek dengan bukti + biaya.

    Bentuknya {label, evidence, cost} karena kontrak F10.1 menuntut resolver melihat bukti
    per pilihan, bukan hanya namanya: pertanyaan "A atau B" tanpa angka di belakangnya hanya
    memindahkan tebakan ke mesin lain.

    `weight='irreversible'` tidak pernah dikirim ke resolver — invarian 3 outranks the broker,
    dan gerbangnya ada di sini supaya pemanggil mana pun mendapatinya, bukan hanya yang ingat.

    `item_id` menghubungkan pertanyaan ke pekerjaan yang menunggu. Tanpanya, pertanyaan yang
    terjawab tidak membangunkan siapa-siapa — ia hanya menjadi arsip.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    state = "ESCALATED" if weight == "irreversible" else "OPEN"
    run(conn, engine,
        f"INSERT INTO decisions (question, options, weight, state, rung_used, note, created_epoch, "
        f"item_id) "
        f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
        (question, json.dumps(normalise_options(options))[:4000], weight, state,
         "human-only" if state == "ESCALATED" else None,
         "invarian 3: pilihan yang tidak bisa dibatalkan tidak didelegasikan ke mesin mana pun"
         if state == "ESCALATED" else None,
         now(), item_id))
    rows = run(conn, engine, "SELECT * FROM decisions ORDER BY id DESC LIMIT 1")
    # "beri tahu aku saat nanya keputusan" — permintaan operator 2026-10-08. Yang dikirim hanya
    # fakta yang barusan ditulis: id baris, item yang menunggu, dan berapa opsi yang ditawarkan.
    # Pertanyaan yang mendarat di antrean manusia tapi tidak pernah dilihat siapa pun adalah
    # pertanyaan yang membuat sebuah item berhenti selamanya, dan diamnya item itu tidak bedanya
    # dengan diamnya loop.
    row = rows[0] if rows else {}
    if state == "ESCALATED":
        try:
            n_opts = len(json.loads(row.get("options") or "[]"))
        except (TypeError, ValueError):
            n_opts = 0          # dilaporkan sebagai nol opsi, bukan sebagai angka karangan
        banner("ESCALATED",
               f"keputusan {row.get('id')} butuh manusia · item={row.get('item_id') or '?'} · "
               f"{n_opts} opsi · {question[:90]}",
               urgent=True)
    return rows


# --- F10.1 decision broker ---------------------------------------------------

RESOLVER_ROLES = ("resolver", "resolver-alt", "resolver-byok", "resolver-local")


def normalise_options(options):
    """Label polos dan objek {label, evidence, cost} masuk ke satu bentuk yang sama."""
    out = []
    for o in (options or []):
        if isinstance(o, str):
            out.append({"label": o})
        elif isinstance(o, dict):
            out.append({k: v for k, v in o.items() if k in ("label", "evidence", "cost")})
    return out


def parse_options_arg(raw):
    """`--options` menerima label polos (A|B, dan A,B,C) atau JSON ([{"label":...,"evidence":...}]).

    Pemisah koma/semicolon TIDAK ada di versi pertama, dan hilangnya itu berbiaya nyata: empat
    keputusan (41, 42, 43, 44) dikirim dengan `--options 'A,B,C'` dan tersimpan sebagai SATU
    pilihan berlabel "A,B,C". `decision_prompt` memberi nomor pilihan mulai dari 1, jadi resolver
    melihat daftar berisi satu butir sementara pertanyaan berbicara dalam huruf — ia tidak bisa
    menjawab
    dengan nomor yang ada, dan `parse_choice` hanya menerima digit. Hasil terukur: dua tingkat
    resolver mengembalikan jawaban dan keduanya dibuang parser, satu tingkat UNAVAILABLE karena
    meter basi, dan pertanyaan berakhir ESCALATED. Operator menamai ini dengan tepat:
    "kalau TIME OUT berarti tool kamu salah".
    """
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("["):
        return json.loads(s)
    parts = [p for p in re.split(r"[|\n]", s) if p.strip()]
    if len(parts) == 1:
        # Satu token: boleh jadi satu label panjang yang memang mengandung koma (biarkan apa
        # adanya), boleh jadi daftar huruf pendek yang dipisah koma. Yang terakhir adalah bentuk
        # yang dipakai orang di CLI, dan memisahkannya lebih murah daripada mengirim pertanyaan
        # yang secara struktural tidak bisa dijawab.
        toks = [t.strip() for t in re.split(r"[;,]", parts[0]) if t.strip()]
        if len(toks) >= 2 and all(len(t) <= 4 for t in toks):
            return toks
    return parts


def resolver_rungs():
    """Tangga resolver dibaca dari config/slots.yaml pada saat dipakai, bukan dari ingatan.

    Kontrak §12 dan aturan 'never select a model from memory': berkas itu yang jadi kontrak,
    dan operator menyuntingnya dari Station. Yang dikembalikan adalah (role_id, registry_key) —
    nama model, kolam kuota dan jendelanya tetap milik config/engines.json, satu definisi.
    """
    import yaml
    slots = REPO / "config" / "slots.yaml"
    doc = yaml.safe_load(slots.read_text(encoding="utf-8")) or {}
    want = {r.get("role_id"): r.get("registry_key")
            for t in doc.get("teams", []) if t.get("id") == doc.get("active_team")
            for r in t.get("roles", [])}
    return [(role, want[role]) for role in RESOLVER_ROLES if want.get(role)]


def decision_prompt(question, options):
    """Pertanyaan yang berdiri sendiri: headless tidak bisa mencari berkasnya sendiri."""
    lines = [
        "Kamu adalah RESOLVER keputusan di workstation ini (kontrak §4). Sebuah pilihan level "
        "preferensi tidak boleh dilempar ke manusia, jadi kamu yang memilih.",
        "",
        f"PERTANYAAN: {question}",
        "",
        "PILIHAN (bukti dan biaya ikut ditulis; yang kosong berarti memang tidak diukur):",
    ]
    for i, o in enumerate(options, 1):
        lines.append(f"{i}. {o.get('label', '(tanpa label)')}")
        lines.append(f"   bukti: {o.get('evidence') or '(tidak ada bukti terukur yang dilampirkan)'}")
        lines.append(f"   biaya: {o.get('cost') or '(tidak diukur)'}")
    lines += [
        "",
        "Aturan yang mengikat kamu:",
        "- Pilih salah satu nomor. Kalau semua pilihan buruk, pilih yang paling sedikit "
        "merusak dan tulis alasannya; jangan menolak memilih.",
        "- Jangan menambah pilihan ketiga, jangan mengubah pertanyaan.",
        "- Keputusan yang tidak bisa dibatalkan (hapus data, push paksa, kirim uang, pasang "
        "perangkat) tidak pernah jadi tugasmu: jawab `PILIHAN: TIDAK` dan sebutkan kenapa.",
        "",
        "Balas PERSIS dengan tiga baris ini, tanpa apa pun sebelumnya:",
        "PILIHAN: <nomor>",
        "ALASAN: <satu atau dua kalimat>",
        "BUKTI: <baris atau angka yang kamu pakai>",
    ]
    return "\n".join(lines)


def answer_tail(text, prompt):
    """Buang gema prompt sebelum jawabannya dibaca.

    Pane menampilkan kembali apa yang diketik ke dalamnya, jadi baris contoh format
    (`PILIHAN: <nomor>`, `ALASAN: <satu atau dua kalimat>`) dan kalimat aturan
    `jawab PILIHAN: TIDAK` ikut berada di layar SEBELUM jawaban yang sebenarnya. Parser yang
    mencari dari awal akan membaca instruksinya sendiri sebagai jawaban — dan yang paling
    berbahaya bukan nomor yang salah, melainkan sebuah penolakan yang tidak pernah diucapkan
    siapa pun. Yang dipakai di sini adalah teks setelah kemunculan TERAKHIR baris terakhir
    prompt: batas antara apa yang kita kirim dan apa yang mereka balas.
    """
    lines = [l.strip() for l in (prompt or "").splitlines() if l.strip()]
    if not lines or not text:
        return text or ""
    marker = lines[-1]
    i = text.rfind(marker)
    return text[i + len(marker):] if i >= 0 else text


def parse_choice(text, count):
    """Ambil nomor pilihan dari jawaban. Tidak ada baris PILIHAN = tidak ada keputusan."""
    if not text:
        return None, "jawaban kosong"
    m = re.search(r"PILIHAN\s*[:=]\s*(?:nomor\s*)?(\d+)", text, re.I)
    letter = None
    if not m:
        # Huruf diterima juga. Pertanyaan ditulis orang dalam huruf (A/B/C) sementara daftar yang
        # dilihat resolver bernomor; menjawab "PILIHAN: B" adalah jawaban yang benar tentang
        # pilihan nomor dua, dan membuangnya karena bentuknya huruf adalah kerusakan alat, bukan
        # kerusakan jawaban — persis yang terjadi pada dua tingkat resolver malam ini.
        lm = re.search(r"PILIHAN\s*[:=]\s*([A-Ea-e])\b", text)
        if lm:
            letter = ord(lm.group(1).upper()) - 64
    if not m and letter is None:
        if re.search(r"PILIHAN\s*[:=]\s*TIDAK", text, re.I):
            return None, "resolver menolak memilih (PILIHAN: TIDAK)"
        return None, "tidak ada baris 'PILIHAN: <nomor>' — jawaban tidak bisa dibaca sebagai keputusan"
    idx = int(m.group(1)) if m else letter
    if not 1 <= idx <= count:
        return None, f"nomor {idx} di luar 1..{count}"
    reason = re.search(r"ALASAN\s*[:=]\s*(.+)", text, re.I)
    return idx, (reason.group(1).strip() if reason else "(tanpa alasan)")


def broker(conn, engine, decision_id, timeout=240):
    """Kiratkan satu pertanyaan OPEN ke tangga resolver, catat yang menjawab.

    Tiga hal yang membuat fungsi ini ada, semuanya hasil pengukuran, bukan selera:

    1. Model dipilih dari config pada saat dipakai, dan setiap anak panah dispatch adalah
       `cross_verify.run_engine` — registry, pagar kuota, dan pembacaan pane sudah milik di
       sana. Menyalinnya ke sini menghasilkan dua pagar yang boleh berbeda.
    2. Pane diperiksa sebelum diketik. `run_via_pane` mengirim lewat /api/cli/run, jadi
       mengetik ke pane yang sedang bekerja atau penuh teks belum terkirim akan menumpuk
       prompt ke dalam pekerjaan orang lain. Tingkatnya dilewati, bukan dipaksakan.
    3. Jawaban dibaca walau statusnya TIMEOUT. Satu dispatch gemini terukur TIMEOUT oleh
       harness padahal pane sudah menjawab; broker yang hanya percaya pipa akan membuang
       keputusan yang nyata-nyata sudah ada.
    4. Gema prompt dibuang sebelum parsing (`answer_tail`). Yang diketemukan saat uji pertama
       malam ini: ALASAN terbaca dari baris contoh di instruksi sendiri, dan teks aturan
       memuat `PILIHAN: TIDAK` — pane yang hanya menggemakan prompt akan tercatat sebagai
       resolver yang menolak memilih. Kesimpulan palsu dari kalimat kita sendiri.
    """
    import cross_verify as cv
    ph = "%s" if engine == "POSTGRESQL" else "?"
    rows = run(conn, engine, f"SELECT * FROM decisions WHERE id={ph}", (decision_id,))
    if not rows:
        return False, f"decision {decision_id} tidak ada"
    d = rows[0]
    if d["state"] != "OPEN":
        return False, f"decision {decision_id} sudah {d['state']} — broker tidak menulis ulang kesimpulan"
    opts = normalise_options(json.loads(d["options"] or "[]"))
    if not opts:
        return False, "pertanyaan tanpa pilihan — broker bukan tempat menebak apa opsinya"
    if len(opts) < 2:
        # Satu pilihan bukan sebuah pilihan. Ini pagar yang seharusnya menangkap empat keputusan
        # malam ini lebih awal: `--options 'A,B,C'` disimpan sebagai SATU butir, dan yang dikirim
        # ke resolver adalah daftar bernomor satu baris sementara pertanyaannya berbicara huruf.
        # Dua tingkat menjawab, keduanya dibuang parser, dan pertanyaan mati sebagai ESCALATED —
        # dibayar dengan waktu operator yang sedang menunggu, bukan dengan kuota.
        return False, (f"hanya {len(opts)} pilihan — sebuah keputusan butuh paling sedikit dua; "
                       "pisahkan dengan '|' atau koma (`--options 'A,B,C'`)")
    prompt = decision_prompt(d["question"], opts)

    rungs = resolver_rungs()
    if not rungs:
        return False, ("tidak satu pun role resolver ada di config/slots.yaml — pertanyaan "
                       "dibiarkan OPEN, bukan dijawab oleh mesin yang dipilih di tempat")
    registry = cv.load_registry()
    attempts = []
    for role_id, key in rungs:
        spec = registry.get(key)
        if not spec:
            attempts.append(f"{role_id}->{key}: tidak ada di config/engines.json")
            continue
        present, why = cv.engine_present(spec)
        if not present:
            attempts.append(f"{role_id}->{key}: UNAVAILABLE — {why or 'tidak hadir di mesin ini'}")
            continue
        if spec.get("kind") == "pane":
            tab = spec.get("tab", "antigravity")
            screen = cv.capture_pane(tab)
            if screen is None or not cv.pane_idle(screen):
                attempts.append(f"{role_id}->{key}: pane '{tab}' sedang bekerja atau ada teks "
                                "belum terkirim — prompt tidak diketik ke dalamnya")
                continue
        status, out = cv.run_engine(spec, prompt, timeout)
        idx, detail = parse_choice(answer_tail(out, prompt), len(opts))
        if idx:
            chosen = opts[idx - 1].get("label", f"pilihan {idx}")
            answer = (f"PILIHAN {idx}: {chosen}\nALASAN: {detail}\n\n"
                      f"--- jawaban mentah ({status}) ---\n{(out or '')[:2500]}")
            ok, msg = resolve_decision(conn, engine, decision_id, role_id, key,
                                       spec.get("model", ""), answer, "RESOLVED",
                                       note=f"{status}; {len(attempts)} tingkat dicoba sebelum ini")
            return ok, msg
        attempts.append(f"{role_id}->{key}: {status} — {detail}")

    note = "semua tingkat habis: " + " | ".join(attempts)
    ok, msg = resolve_decision(conn, engine, decision_id, None, None, None, None,
                               "ESCALATED", note[:3000])
    return ok, f"{msg} — {note}"



def seed(conn, engine, items, refresh_gates=None):
    """Idempoten berdasarkan judul — memanggil ulang tidak menggandakan pekerjaan.

    Dependensi ditulis sebagai KEY, bukan id: id di DB live sudah mulai dari 27, jadi angka
    '1' akan menunjuk baris tua yang bukan apa-apa. Peta key->id dibangun setelah semua baris
    ada, lalu kolom depends_on diisi — dua tahap, karena item B bisa menyebut A yang belum
    pernah punya id di pemanggilan pertama.
    """
    keymap, made = {}, []
    for key, title, phase, deps, check in items:
            exists = run(conn, engine, "SELECT id FROM quest_tasks WHERE title=%s"
                         if engine == "POSTGRESQL" else "SELECT id FROM quest_tasks WHERE title=?",
                         (title,))
            if exists:
                keymap[key] = int(exists[0]["id"])
                # Gerbang boleh DIPERBAIKI selama barisnya belum divonis. Kebutuhan ini nyata:
                # gerbang f109 diperketat hari ini (menuntut sisi "push tanpa verdict ditahan"),
                # tapi seed idempoten berdasarkan judul dan tidak pernah menulis ulang
                # check_command — jadi kode dan baris produksi berbeda diam-diam, dan drainer
                # akan menutup F10.9 dengan gerbang lama yang sekarang lolos karena peristiwa
                # push_verified yang sah sudah ada. Menandai selesai karena gerbang yang salah
                # adalah laporan yang ditulis tangan, dan itu §12.
                # Hanya PENDING yang boleh disentuh: COMPLETED/FAILED adalah vonis, dan vonis
                # tidak pernah ditulis ulang.
                if refresh_gates and key in refresh_gates \
                        and norm(get_item(conn, engine, keymap[key])
                                          .get("status")) == "PENDING":
                    cur_gate = (get_item(conn, engine, keymap[key]).get("check_command") or "").strip()
                    if cur_gate != (check or "").strip():
                        run(conn, engine, "UPDATE quest_tasks SET check_command=%s WHERE id=%s"
                            if engine == "POSTGRESQL" else
                            "UPDATE quest_tasks SET check_command=? WHERE id=?",
                            (check, keymap[key]))
                        made.append((keymap[key], "gerbang diperbarui", title))
                        continue
                made.append((keymap[key], "ada", title))
                continue
            ph = "%s" if engine == "POSTGRESQL" else "?"
            run(conn, engine,
                f"INSERT INTO quest_tasks (title, status, phase, check_command, summary, origin) "
                f"VALUES ({ph}, 'PENDING', {ph}, {ph}, {ph}, {ph})",
                (title, phase, check, "dipetakan dari ROADMAP.md oleh work_order seed",
                 ORIGIN_ROADMAP))
            row = run(conn, engine, "SELECT id FROM quest_tasks WHERE title=%s"
                      if engine == "POSTGRESQL" else "SELECT id FROM quest_tasks WHERE title=?",
                      (title,))
            keymap[key] = int(row[0]["id"])
            made.append((keymap[key], "baru", title))
    for key, _title, _phase, deps, _check in items:
        if not deps:
            continue
        missing = [d for d in deps if d not in keymap]
        if missing:
            made.append((keymap.get(key), f"DEPS HILANG {missing}", key))
            continue
        ph = "%s" if engine == "POSTGRESQL" else "?"
        run(conn, engine, f"UPDATE quest_tasks SET depends_on={ph} WHERE id={ph}",
            (",".join(str(keymap[d]) for d in deps), keymap[key]))
    return made


ROADMAP_ITEMS = [
    ("f84", "F8.4+F10.2 work-order schema dan lease atomik: phase, check_command, depends_on, "
            "claimed_by, lease, evidence", "build", None, "python3 tools/work_order.py selftest"),
    ("f108", "F10.8 tombol Autopilot + tab Human Decide, tiga jalur mati tanpa izin AI "
             "(WAJIB sebelum drainer menyala)", "build", ["f84"],
             # Gerbangnya dulu `bash bin/deploy_engine.sh --check`, dan `--check` BUKAN argumen yang
             # dikenal skrip itu: parser-nya menutup dengan `*) exit 2`. Jadi gerbang ini selalu
             # GAGAL — kebalikan dari gerbang yang selalu benar, tapi sama-sama tidak mengukur
             # apa pun, dan itemnya tetap ditandai COMPLETED dengan bukti berupa prosa commit.
             # Bentuk sekarang menanyakan dua hal yang benar-benar merupakan F10.8: panel mode
             # adalah cerminan kosakata mesin (dijalankan dengan MEMANGGIL renderer sungguhan),
             # dan selftest yang memegang tiga jalur mati tanpa TTY.
             "bash bin/check_autopilot_modes.sh && python3 tools/work_order.py selftest"),
    ("f101", "F10.1 decision broker: baris keputusan di-resolve dari role resolver, model yang "
             "menjawab tercatat, jawabannya dibaca dari tabel/pane bukan hanya pipa", "build",
     ["f84"], "python3 tools/work_order.py selftest"),
    ("f107", "F10.7 heartbeat: dua sinyal independen sebelum CONTINUE dikirim, pane tak terbaca "
             "= UNKNOWN", "build", ["f108"],
             "python3 tools/work_order.py selftest && python3 tools/work_order.py heartbeat"),
    ("f103", "F10.3 drainer systemd timer dengan circuit breaker + batas kuota per malam", "build",
     ["f84", "f108", "f101"],
     # Judul item ini menyebut TIGA hal, dan gerbangnya dulu hanya membuktikan satu — itu bukan
     # keluhan kecil: `drainer.py` tidak pernah menyebut kata kuota sama sekali sementara gerbangnya
     # lolos `exit=0`, karena klausa keduanya (`grep '"action"'` di log hari) benar pada hari timer
     # pernah jalan apa pun isinya. Gerbang yang selalu benar adalah undangan bagi drainer untuk
     # menandai itemnya sendiri COMPLETED tanpa bukti — bentuk yang dilarang §12.
     #
     # Bentuk sekarang menuntut ketiganya, dan tiap bagiannya sudah diukur hari ini:
     #   - kedua unit terpasang;
     #   - timer TIDAK di-enable. BUKAN `is-enabled` yang dicari: operator menutup itu pagi ini
     #     ("SEMI tidak boleh auto start saat boot"), jadi unit yang menyala sendiri setelah restart
     #     adalah keadaan yang harus DITOLAK gerbang ini, bukan dirayakan. `!` di depan sengaja.
     #   - detaknya sungguh berjalan (aktif sekarang, atau satu tick tercatat hari ini);
     #   - `drainer selftest` hijau, yang adalah satu-satunya tempat pemutus kegagalan DAN
     #     langit-langit dispatch verifikasi per malam dibuktikan perilakunya, bukan keberadaannya.
     "test -f ~/.config/systemd/user/irsofka-autopilot.timer && "
     "test -f ~/.config/systemd/user/irsofka-autopilot.service && "
     "! systemctl --user is-enabled --quiet irsofka-autopilot.timer && "
     "(systemctl --user is-active --quiet irsofka-autopilot.timer || "
     "grep -q '\"action\"' ~/.ai-station/logs/drainer-$(date +%F).jsonl) && "
     "python3 tools/drainer.py selftest"),
    ("f91", "F9.1 deterministic environment state: exit code + process tree + delta pane "
            "disuapkan ke prompt (jalan paralel, tidak bergantung antrean)", "build", None,
     # Gerbang lamanya `bash bin/deploy_engine.sh --check`, dan `--check` bukan argumen yang
     # dikenal skrip itu: parser-nya menutup dengan `*) exit 2`. Jadi ia MUSTAHIL lolos, dan
     # item ini menunggu enam belas jam dalam keadaan itu. Bentuk sekarang menanyakan sifat yang
     # benar-benar dibuat F9.1: pairing exit code pada urutan DESC, ketidakmampuan membaca yang
     # dilaporkan sebagai UNKNOWN (bukan daftar kosong), ringkasan proses tanpa dump dict, dan
     # delta pane alih-alian salinan isi pane.
     "python3 tools/drainer.py selftest"),
    ("f92", "F9.2 action-outcome anti-pattern: bentuk perintah yang pernah gagal -> peringatan "
            "sebelum diketik", "build", ["f91"], "python3 tools/work_order.py selftest"),
    ("f93", "F9.3 reflex proof: ydotool bergerak lalu diff 64x64 membuktikan layar berubah",
     "build", None, "cargo test -q reflex_proof"),
    ("f105", "F10.5 dream phase: konsolidasi memori saat idle, berkas tracked hanya lewat digest",
     "build", ["f103"],
     # GERBANG INI PERNAH DIBUAT MUSTAHIL DENGAN SENGAJA, dan begitulah ketahuan bahwa fiturnya
     # belum ada: "dream"/"konsolidasi" NOL kemunculan di seluruh tools/, hooks/ dan bin/, sementara
     # `digest` yang ada adalah laporan pagi yang tidak mengukur apa pun tentang memori.
     # Sekarang fiturnya ada (tools/dream_phase.py) dan gerbangnya menuntut dua sifat di judulnya,
     # bukan keberadaan berkas: (a) konsolidasi sungguh disetir oleh idle yang terukur — dibuktikan
     # selftest modul itu, yang menolak ledger kosong sebagai UNKNOWN dan menolak mode OFF;
     # (b) Berkas terlacak hanya boleh berubah lewat digest — dibuktikan oleh tes bahwa selisih
     # `git status` muncul disebut-by-name di baris digest, dan bahwa git yang tidak terbaca
     # dilaporkan UNKNOWN, bukan "bersih". Ditambah: unit-nya terpasang, TIDAK di-enable (mimpi yang
     # menyala sendiri setelah restart adalah keadaan yang harus ditolak, seperti F10.3), dan satu
     # putaran sungguh pernah mengeluarkan baris digest.
     "test -f ~/.ai-station/tools/dream_phase.py && "
     "python3 tools/dream_phase.py selftest && "
     "test -f ~/.config/systemd/user/irsofka-dream.timer && "
     "test -f ~/.config/systemd/user/irsofka-dream.service && "
     "! systemctl --user is-enabled --quiet irsofka-dream.timer && "
     "(systemctl --user is-active --quiet irsofka-dream.timer || "
     "python3 tools/dream_phase.py has-digest)"),
    # Jawabannya sudah diberikan operator lewat tab Human Decide (decision 6, 2026-10-08 07:00): ON boleh
    # commit dan push asal ada verifier yang meloloskan lebih dulu. Yang belum ada adalah barisnya —
    # aturan itu sekarang cuma hidup di §6, tempat ia mengikat sejauh engine mengingatnya, padahal §12
    # melarang satu kata punya dua definisi.
    #
    # Versi pertama gerbang ini membaca action_log dengan `summary LIKE '%push%' AND '%verifier%'` dan
    # diukur keluar 0 — yang ditemukan adalah kalimat laporan milik engine sendiri, bukan peristiwa.
    # Gerbang yang cocok dengan prosa selalu benar, jadi bentuknya sekarang peristiwa terstruktur:
    # kind=push_verified dengan sha dan model yang meloloskan di baris yang sama. Tidak ada satu pun
    # kode yang memancarkan kind itu hari ini, dan itu memang sisanya.
    ("f109", "F10.9 push gate: push hanya setelah verdict verifier COMPLETED menyebut model yang "
             "meloloskan diff-nya; tanpa verdict, commit berhenti lokal dan masuk digest pagi",
     "build", ["f101", "f103"],
     # Dua sisi, bukan satu. Menerima push yang diloloskan verifier adalah separuh; separuh
     # lainnya adalah menahan push yang TIDAK ada verdict-nya. Gerbang yang hanya menuntut sisi
     # pertama akan menandai F10.9 COMPLETED begitu peristiwa pertama ditulis, padahal sisanya
     # persis yang membuat pagar ini berarti.
     # Bukan grep global. Dua `grep -q` atas berkas peristiwa akan keluar 0 selamanya begitu satu
     # baris dari tiap jenis pernah tertulis sekali — dua pemeriksa independen menemukannya dari diff
     # yang berbeda. Yang ditanya sekarang adalah keadaan yang masih bisa diperbaiki: apakah setiap
     # commit yang belum berangkat sudah DINYATAKAN, divonis lolos atau ditahan dengan alasan.
     "python3 tools/work_order.py push-gate"),
]


# --- proyek: satu kata untuk "di mana pekerjaan ini terjadi" ----------------
# Workstation punya TIGA pembacaan direktori yang sudah hidup sebelum Dashboard ada, dan itu
# persis cara mesin jadi bingung identitas: REPO (dipakai gerbang drainer dan aturan working-tree
# kotor), TabProfile.workspace (dipakai pane), dan symlink brain/workspaces/current_project
# (ditulis brain_bridge, tidak pernah dibaca Rust). Yang ditambahkan modul ini hanya SATU kata
# baru — `ws_path` per baris tugas — dan ia membiarkannya KOSONG berarti hari kemarin persis.
# REPO sengaja BUKAN sebuah proyek: ia keadaan git mesin ini, milik aturan §6, dan menyatukannya
# dengan "proyek" akan membuat dirty-tree rule memeriksa direktori yang salah besok pagi.
WORKSPACE_LINK = REPO / "brain" / "workspaces" / "current_project"
GUIDE_MAX_FILES = 8
GUIDE_MAX_DEPTH = 2
GUIDE_MAX_CHARS = 3200          # sama dengan plafon yang sudah dipakai auto_restore.py
GUIDE_SKIP = (".git", "node_modules", "target", "build", "dist", "__pycache__", ".venv", "runtime")
# Pedoman yang selalu boleh dipilih, di proyek apa pun, karena ia milik workstation: tanpanya
# sebuah proyek game di direktori lain tidak punya jalan untuk tunduk pada ROADMAP.
CANONICAL_GUIDES = ("ROADMAP.md", "AGENTS.md", "documents/AGENTS.md",
                    "brain/DESIGN_NOTES.md", "brain/memory/projects/ARCHITECTURE.md")


def focused_workspace() -> Path | None:
    """Proyek yang sedang difokuskan — dibaca dari symlink, satu-satunya tempat jawaban itu ada.

    None berarti tidak ada, dan bukan "berarti REPO". Sebuah tabel workspaces tidak pernah boleh
    mengklaim fokus: dua pernyataan yang boleh berbeda tentang identitas proyek adalah engine yang
    mengerjakan direktori sambil melaporkan direktori lain.
    """
    try:
        return WORKSPACE_LINK.resolve(strict=True)
    except OSError:
        return None


def resolve_workspace(item) -> Path:
    """Direktori tempat gerbang baris ini dijalankan. Baris lama (kolom kosong) = REPO, persis."""
    raw = (item or {}).get("ws_path")
    if not (raw or "").strip():
        return REPO
    return Path(raw).expanduser().resolve(strict=False)


def known_engines() -> list[str]:
    """Nama tab yang benar-benar terdaftar, dibaca saat ini dipakai — bukan dari ingatan (§12)."""
    try:
        data = json.loads((REPO / "config" / "cli_profiles.json").read_text())
        return [k for k, v in data.items() if isinstance(v, dict)]
    except Exception:  # noqa: BLE001
        return []


# Pedoman yang mengikat sebuah perintah adalah KONTRAK, dan kontrak tidak bisa dipilih-pilih oleh
# layar yang iaikatinya. Operator mengunci daftar ini pada 2026-10-08 11:05: "LOCK saja MD nya,
# jangan sampai bisa diganti — aturan ketat untuk Ai-Workstation, kontrak tidak boleh diganggu
# gugat." Sebuah kotak centang yang bisa mematikan AGENTS.md adalah tombol "jadilah tidak disiplin"
# yang kebetulan sudah tersedia, dan §12.3 melarang kontrol yang menghitung kebijakannya sendiri.
# Karena itu tidak ada lagi jalur tulis: daftar ini dibaca dari kode, bukan dari kolom database.
LOCKED_GUIDES = ("documents/AGENTS.md", "ROADMAP.md",
                 "brain/DESIGN_NOTES.md", "brain/memory/projects/ARCHITECTURE.md")


def locked_guides() -> list[dict]:
    """Berkas kontrak yang berlaku di SEMUA proyek.

    Berkas yang Hilang TIDAK disaring diam-diam. Menghilangkan satu nama dari daftar karena path-nya
    tidak ada adalah cara membuat kunci mengecil tanpa suara — yang harus terjadi adalah layar
    menunjukkannya merah, bukan daftar yang tiba-tiba lebih pendek.
    """
    out = []
    for rel in LOCKED_GUIDES:
        p = REPO / rel
        out.append({"path": rel, "abs": str(p), "canonical": True, "locked": True,
                    "exists": p.is_file(), "size": p.stat().st_size if p.is_file() else None})
    return out


def detect_guidelines(root: Path | None) -> list[dict]:
    """Hanya untuk MELAPORKAN apa yang ada di proyek — bukan untuk dipilih.

    Dipakai layar supaya Bro melihat berkas MD mana yang sebenarnya ada di proyeknya. Yang mengikat
    perintah adalah `locked_guides()`, dan keduanya sengaja fungsi berbeda: kalau satu daftar bisa
    ditulis, daftar itu bukan kontrak lagi.
    """
    found: list[dict] = []
    seen: set[str] = set()

    def add(path: Path, base: Path, canonical: bool):
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            return
        abs_key = str(path.resolve(strict=False))
        # Kunci yang dibaca adalah berkasnya, bukan nama relatifnya. Pernah diukur di mesin ini:
        # proyek Ai-Workstation punya AGENTS.md sendiri, dan karena dedupe memakai nama relatif,
        # AGENTS.md MILIK WORKSTATION hilang dari daftar — pedoman yang berlaku di semua proyek
        # lenyap hanya karena kebetulan memakai huruf yang sama.
        if abs_key in seen:
            return
        seen.add(abs_key)
        found.append({"path": rel, "abs": abs_key, "canonical": canonical,
                      "base": str(base), "size": path.stat().st_size})

    if root and root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= GUIDE_MAX_DEPTH:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if d not in GUIDE_SKIP and not d.startswith(".")]
            for name in sorted(filenames):
                # Berkas titik adalah bukan-dokumen: `.ai_brain_link.md` ditulis brain_bridge sebagai
                # penunjuk, dan membiarkannya ikut terpilih berarti pedoman proyek didaftarkan berisi
                # jarum, bukan isinya.
                if name.startswith("."):
                    continue
                if name.lower().endswith(".md") and len(found) < GUIDE_MAX_FILES:
                    add(Path(dirpath) / name, root, False)
    for rel in CANONICAL_GUIDES:
        p = REPO / rel
        if p.is_file():
            add(p, REPO, True)
    return found


def list_workspaces(conn, engine) -> dict:
    """Daftar proyek + fokus yang Diturunkan. Tidak ada kolom `active` untuk dibaca."""
    rows = run(conn, engine, "SELECT id, display_name, path, guidelines, created_epoch "
                             "FROM workspaces ORDER BY id")
    focus = focused_workspace()
    out = []
    for r in rows:
        try:
            resolved = Path(r["path"]).expanduser().resolve(strict=False)
        except OSError:
            resolved = None
        out.append({**r,
                    "is_focus": bool(focus) and resolved == focus,
                    "exists": bool(resolved and Path(r["path"]).is_dir())})
    return {"workspaces": out,
            "focus": str(focus) if focus else None,
            "focus_readable": focus is not None}


def create_workspace(conn, engine, display_name: str, path: str) -> tuple[bool, str, int | None]:
    """Daftarkan sebuah proyek. Mendaftarkan tidak memindahkan fokus — itu keputusan terpisah."""
    target = Path(path).expanduser()
    if not target.is_dir():
        return False, f"{path} bukan direktori yang bisa dibaca — tidak ada yang didaftarkan", None
    real = str(target.resolve(strict=False))
    name = (display_name or "").strip()[:120] or Path(real).name
    ph = "%s" if engine == "POSTGRESQL" else "?"
    before = run(conn, engine, "SELECT id, display_name FROM workspaces WHERE path=" + ph, (real,))
    if engine == "POSTGRESQL":
        run(conn, engine,
            "INSERT INTO workspaces (display_name, path, created_epoch) VALUES (%s, %s, %s) "
            "ON CONFLICT (path) DO NOTHING", (name, real, now()))
    else:
        run(conn, engine,
            "INSERT OR IGNORE INTO workspaces (display_name, path, created_epoch) VALUES (?, ?, ?)",
            (name, real, now()))
    rows = run(conn, engine, "SELECT id, display_name FROM workspaces WHERE path=" + ph, (real,))
    if not rows:
        return False, "baris tidak terbalik setelah ditulis — dilaporkan gagal, bukan dikira berhasil", None
    # Dibaca SEBELUM dan SESUDAH, bukan disimpulkan dari jumlah kolom yang berubah: menyebut
    # pendaftaran ulang "terdaftar" padahal path-nya sudah ada di bawah nama lain adalah laporan
    # yangarang, dan satu laporan karangan membuat seluruh layar tidak lagi dipercaya (§12.2).
    label = "sudah terdaftar" if before else "terdaftar"
    return True, f"proyek {rows[0]['display_name']} {label} di {real}", int(rows[0]["id"])


def guidelines_are_locked() -> tuple[bool, str]:
    """Satu-satunya jawaban atas permintaan mengubah pedoman: ditolak, dengan alasannya.

    Fungsinya sengaja tidak menerima parameter apa pun. Begitu ada jalur tulis yang bisa dipanggil,
    ia akan dipanggil — oleh layar, oleh skrip, atau oleh engine yang sedang buru-buru — dan kontrak
    yang bisa dimatikan dari UI bukan kontrak lagi.
    """
    return False, ("pedoman TERKUNCI oleh kontrak §12.3: daftar MD ditentukan di "
                   "tools/work_order.py (LOCKED_GUIDES), bukan dari layar dan bukan dari database. "
                   "Yang mengikat: " + ", ".join(LOCKED_GUIDES))


def focus_workspace(conn, engine, needle: str, who: str = "operator-gui") -> tuple[bool, str]:
    """Pindahkan fokus lewat PEMILIK yang sudah ada: brain_bridge.link_workspace().

    Fungsi ini tidak menulis symlink sendiri. Membuat penulis kedua atas satu keadaan adalah cara
    tercepat menghasilkan dua alat yang saling menyangkal, dan §9 sudah menunjuk satu tempat.
    """
    listed = list_workspaces(conn, engine)["workspaces"]
    needle = (needle or "").strip()
    hit = next((w for w in listed if str(w["id"]) == needle or w["display_name"] == needle
                or w["path"] == needle), None)
    if not hit:
        return False, (f"proyek {needle!r} tidak terdaftar — daftarkan lebih dulu lewat `ws create`"
                       f" (yang ada: {', '.join(w['display_name'] for w in listed[:5]) or 'kosong'})")
    try:
        import brain_bridge
    except Exception as exc:  # noqa: BLE001
        return False, f"brain_bridge tidak bisa dimuat ({type(exc).__name__}: {exc}) — fokus tidak diubah"
    ok = brain_bridge.link_workspace(Path(hit["path"]))
    if not ok:
        return False, f"link_workspace menolak {hit['path']} — fokus tidak diubah"
    return True, (f"fokus = {hit['display_name']} ({hit['path']}) oleh {who}; "
                  "perpane cli_profiles TIDAK diubah fungsi ini — itu jalur /api/cli/config")


def queue_command(conn, engine, title: str, cli_engine: str, gate: str = "",
                  ws_path: str = "", who: str = "operator") -> tuple[bool, str, int | None]:
    """Satu perintah operator masuk antrean yang sama dengan pekerjaan roadmap.

    Tabel kedua untuk kata "antrean" akan berarti dua definisi "berikutnya" (§12), jadi baris ini
    adalah quest_tasks biasa dengan `origin='operator'`. `cli_engine` WAJIB terisi dan wajib nama
    yang terdaftar: baris tanpa tujuan pernah terbukti direset ke PENDING oleh drainer sebagai
    NO_TARGET, dan menolak di pintu lebih murah daripada menyimpan baris yang tak bisa dikirim.
    """
    title = (title or "").strip()
    if not title:
        return False, "perintah kosong ditolak — tidak ada baris yang ditulis", None
    target = (cli_engine or "").strip()
    known = known_engines()
    if not target:
        listed = ", ".join(known) or "registri tidak terbaca"
        return False, f"cli_engine wajib diisi dan tidak boleh ditebak (terdaftar: {listed})", None
    if not known:
        # §4: "Unknown is never treated as empty". Registri yang tidak terbaca BUKAN daftar kosong
        # yang meloloskan nama apa pun — itu menerima "qoderr" hari ini dan menolaknya besok,
        # tergantung apakah sebuah berkas bisa dibaca.
        return False, ("cli_profiles.json tidak terbaca — mesin tujuan tidak bisa divalidasi, "
                       "jadi tidak ada yang diantrekan"), None
    if target not in known:
        return False, f"{target!r} bukan tab terdaftar (terdaftar: {', '.join(known)})", None
    where = (ws_path or "").strip()
    if not where:
        # Kosong berarti REPO, dan tab qoder TIDAK duduk di REPO — jadi perintah tanpa proyek adalah
        # baris yang tidak bisa dikirim oleh jalur mana pun: drainer menolaknya (PANE_WRONG_DIR) dan
        # hook menolaknya (SEMI_OTHER_PROJECT). Yang ditolak secara senyap akan dibaca operator
        # sebagai "perintahku hilang". Kalau fokus terbaca, ia jadi default; kalau tidak, kata
        # kosong tetap kata kosong dan alasannya ikut dilaporkan.
        # Sumber yang benar adalah workspace TAB milik mesin tujuan — karena itulah yang
        # diperiksa drainer (PANE_WRONG_DIR) dan tidak ada gunanya mengisi dari symlink fokus
        # global kalau fokus itu sendiri belum tentu sama dengan tab-nya.
        try:
            import cross_verify as cv
            tab_ws = cv.tab_project(target)
        except Exception:  # noqa: BLE001 — tidak terbaca bukan berarti kosong
            tab_ws = None
        where = tab_ws or str(focused_workspace() or "")
    if where:
        p = Path(where).expanduser()
        if not p.is_dir():
            return False, f"{where} bukan direktori — perintah ditolak, tidak ada yang antre", None
        where = str(p.resolve(strict=False))
    # `RETURNING id` / `lastrowid`, bukan `WHERE title=? ORDER BY id DESC`: judul bukan kunci, dan
    # membaca balik lewat judul bisa mengambil id baris LAIN yang judulnya kebetulan sama —
    # termasuk butir roadmap, atau antrean yang ditulis sesi lain pada detik yang sama.
    if engine == "POSTGRESQL":
        rows = run(conn, engine,
                   "INSERT INTO quest_tasks (title, status, phase, check_command, summary, "
                   "cli_engine, origin, ws_path) VALUES (%s, 'PENDING', 'operator', %s, %s, %s, "
                   "%s, %s) RETURNING id",
                   (title[:255], (gate or "").strip()[:2000],
                    f"perintah operator lewat Dashboard ({who})", target, ORIGIN_OPERATOR,
                    where or None))
        new_id = int(rows[0]["id"]) if rows else None
    else:
        cur = conn.cursor()
        cur.execute("INSERT INTO quest_tasks (title, status, phase, check_command, summary, "
                    "cli_engine, origin, ws_path) VALUES (?, 'PENDING', 'operator', ?, ?, ?, ?, ?)",
                    (title[:255], (gate or "").strip()[:2000],
                     f"perintah operator lewat Dashboard ({who})", target, ORIGIN_OPERATOR,
                     where or None))
        conn.commit()
        new_id = cur.lastrowid
        cur.close()
    if not new_id:
        return False, "id tidak kembali setelah INSERT — dilaporkan gagal, bukan dikira berhasil", None
    # "begitu perintah masuk, backend harus langsung mengirim ke CLI" — permintaan operator
    # 2026-10-08, dan dia menyebut sendiri bentuk cacatnya: jalur SQL→CLI hanya hidup di dalam tick,
    # jadi perintahnya menggantung sampai giliran berikutnya dan tidak ada yang tahu kenapa.
    # Yang dipanggil di sini adalah jalur tick yang sama, pada saat penulisan: tidak ada aturan
    # kedua, tidak ada pagar kedua. Tangga `level` tetap menahan — kalau tangan belum diangkat,
    # barisnya TETAP antre dan penolakannya dilaporkan sebagai kata, bukan sebagai "terkirim".
    handed = {}
    try:
        handed = deliver_now(conn, engine, new_id) or {}
    except Exception as exc:  # noqa: BLE001 — perintah yang sudah tertulis tidak boleh hilang karena kirim
        handed = {"sent": False, "queued": True, "refused": f"jalur kirim tidak terpanggil: "
                  f"{type(exc).__name__}: {exc}"}
    # Alasan penolakan harus TINGGAL, bukan hanya lewat. `emit()` di `deliver_now` mengumpulkan
    # peristiwanya ke akumulator lokal, dan pemanggil di jalur ini tidak menyuplai `log=`, jadi
    # daftar ["GATE_NO_COMMAND","PANE_NOT_QUIET"] hidup sepanjang satu HTTP response lalu dibuang.
    # Tujuh baris ditolak tanpa satu pun jejak yang bisa ditanya, dan kolom yang dipakai UI untuk
    # menjawab "kenapa belum" hanya pernah ditulis `finish()` — jadi PENDING yang ditolak terlihat
    # persis seperti PENDING yang belum dilihat siapa pun.
    _events = handed.get("detail") or []
    if _events:
        try:
            _record_send_events(_events)
        except Exception as _el:  # noqa: BLE001 — log yang gagal tidak boleh menghapus perintah orang
            banner("LOG_WRITE_FAILED", f"peristiwa kirim item {new_id} tidak tersimpan: "
                  f"{type(_el).__name__}: {_el}", urgent=False)
    if not handed.get("sent"):
        _last = _events[-1] if _events else {}
        _code = (handed.get("refused") or handed.get("why") or _last.get("action")
                 or (handed.get("events") or ["tanpa peristiwa"])[-1])
        _note = _last.get("why") or _last.get("note") or ""
        note_refusal(conn, engine, new_id,
                     " · ".join(str(x) for x in (_code, _note) if x)[:500])
    if handed.get("sent"):
        banner("WORK_DISPATCHED", f"perintah {new_id} langsung masuk ke {target}", urgent=False)
    else:
        banner("QUEUED_NOT_DELIVERED",
               f"perintah {new_id} untuk {target} sudah antre tapi belum terkirim · "
               f"{handed.get('refused') or handed.get('why') or (handed.get('events') or '?')}",
               urgent=True)
    return True, (f"perintah antre sebagai item {new_id} untuk {target}"
                  + (f" di {where}" if where else " (tanpa proyek → REPO)")
                  # "diserahkan", bukan "terkirim": `default_sender` mencatat balasan daemon apa
                  # adanya, dan `/api/cli/run` bisa menjawab "dispatched" untuk sesi yang tidak ada.
                  # Kata yang lebih kuat dari buktinya adalah awal dari laporan yang tidak dipercaya
                  # lagi saat laporan itu dibutuhkan.
                  + (" — diserahkan ke CLI" + (f" (balasan: {handed.get('reply')})"
                                               if handed.get("reply") else "")
                     if handed.get("sent") else
                     f" — belum diserahkan: {handed.get('refused') or handed.get('why') or handed.get('events')}")), int(new_id)


def semi_status(conn, engine) -> dict:
    """Yang SEMI butuhkan untuk melaporkan dirinya sendiri — semuanya turunan, tidak ada angka karangan."""
    listed = list_workspaces(conn, engine)
    # Satu sumber: apa yang benar-benar siap ditawarkan. Aturan pemilihannya identik dengan
    # `next_item`, karena dua definisi "berikutnya" pernah membuat Dashboard bilang "3 menunggu"
    # sementara mesin tidak menawarkan apa pun.
    _operator_ready = [r for r in ready_items(conn, engine, respect_due=False, origin=ORIGIN_OPERATOR)
                       if norm(r["status"]) == "PENDING"]
    _t = now()
    pend = [r for r in _operator_ready if int(r.get("due_epoch") or 0) <= _t]
    # Baris yang sedang cooldown TIDAK boleh hilang dari laporan: ia ditahan, dan menahan harus
    # terlihat. `due_epoch` ditulis oleh penolakan, jadi tanpa daftar ini `operator_pending`
    # turun ke nol tepat pada detik sesudah perintah orang ditolak — dan komentar di berkas ini
    # sendiri sudah menyatakan bahwa penolakan senyap dibaca orang sebagai "perintahku hilang".
    held = [{"id": r["id"], "title": r["title"], "cli_engine": r.get("cli_engine"),
             "due_epoch": int(r.get("due_epoch") or 0),
             "reason": r.get("escalation_reason")}
            for r in _operator_ready if int(r.get("due_epoch") or 0) > _t]
    working = run(conn, engine,
                  "SELECT id, title, cli_engine, claimed_by FROM quest_tasks "
                  "WHERE origin=%s AND status IN ('WORKING','CLAIMED') ORDER BY id"
                  if engine == "POSTGRESQL" else
                  "SELECT id, title, cli_engine, claimed_by FROM quest_tasks "
                  "WHERE origin=? AND status IN ('WORKING','CLAIMED') ORDER BY id",
                  (ORIGIN_OPERATOR,))
    return {"operator_pending": len(pend),
            "operator_queue": pend,
            "operator_held": held,
            "operator_working": working,
            "engines_with_a_claim": sorted({r["cli_engine"] for r in working if r.get("cli_engine")}),
            "focus": listed["focus"], "focus_readable": listed["focus_readable"],
            "workspaces": listed["workspaces"],
            "guidelines_locked": True,
            # `binding` adalah yang MENGIKAT sebuah perintah; `present` hanya laporan tentang apa
            # yang ada di proyek itu, dan layar tidak boleh menukar keduanya.
            "guidelines_binding": locked_guides(),
            "guidelines_present": detect_guidelines(focused_workspace()),
            "known_engines": known_engines()}


# --- selftest ---------------------------------------------------------------
def record_push_held(shas, reason, events_path=None):
    """Catat bahwa commit TIDAK berangkat, dan kenapa.

    F10.9 menuntut dua sisi: satu push yang diloloskan verifier, dan satu push yang ditahan karena
    vonisnya tidak ada. Sisi pertama punya pemancar; sisi kedua tidak, jadi gerbang bisa menuntut
    `push_held` sementara tidak ada satu jalur pun yang boleh menulisnya — dan separuh dari pagar
    itu hanya bisa "lulus" dengan cara ditulis tangan, yang adalah hal yang persis dilarang §12
    untuk laporan.

    `reason` adalah kata, bukan kalimat bebas: sebuah keadaan yang bisa ditulis apa saja tidak bisa
    dibandingkan antar-run.
    """
    import datetime as _dt
    if not (reason or "").strip():
        return []
    ev = Path(events_path or (REPO / "logs" / "station_events.jsonl"))
    ev.parent.mkdir(parents=True, exist_ok=True)
    out = []
    with ev.open("a", encoding="utf-8") as fh:
        for sha in shas:
            row = {"ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "kind": "push_held", "sha": sha, "reason": reason.strip()[:60]}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.append(row)
    return out


def _git(*args, repo=None):
    """Satu pembacaan git; None berarti TIDAK TERBACA, yang bukan berarti kosong."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo or REPO), *args], capture_output=True, text=True,
                           timeout=20)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip()


def push_events(events_path=None):
    """Baris peristiwa push_verified / push_held, apa adanya dari berkasnya."""
    path = Path(events_path or (REPO / "logs" / "station_events.jsonl"))
    out = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") in ("push_verified", "push_held"):
                out.append(row)
    return out


def push_gate(events_path=None, repo=None):
    """Pagar push: setiap commit yang belum berangkat harus DINYATAKAN, bukan didiamkan.

    `repo` adalah direktori git yang diadili; default-nya repo nyata mesin ini. Gerbangnya sendiri
    tidak berubah karena itu — yang berubah hanya bahwa selftest boleh membangun repo dengan angka
    yang DIKETAHUI. Mengadili per-sha terhadap repo nyata membuat tesnya merah justru saat repo
    itu sehat, dan keadaan yang merah karena alasan yang salah berhenti dipercaya orang yang
    membacanya.

    Gerbang f109 versi sebelumnya adalah dua `grep -q` global: begitu satu baris `push_verified`
    dan satu baris `push_held` pernah tertulis di masa lalu, ia keluar 0 selamanya — dua pemeriksa
    independen menemukannya dari diff yang berbeda. Yang diperiksa di sini bukan keberadaan kata,
    melainkan kecocokan per sha:

    - commit yang SUDAH sampai ke origin harus punya `push_verified` dengan verdict yang sungguh
      lolos, menyebut sha itu; kalau tidak, sejarah sudah berisi push yang tidak ada yang menanggung;
    - commit yang BELUM berangkat harus punya salah satu: vonis lolos (boleh dorong) atau catatan
      `push_held` beralasan (sengaja ditahan). Yang tidak punya keduanya adalah commit yang hilang
      di tengah jalan, dan itulah keadaan yang tidak boleh lulus;
    - git yang tidak terbaca = UNKNOWN = pagar tertutup. Tidak ada interpretasi "mungkin bersih".
    """
    ev = push_events(events_path)
    # KEDUA sisi dinormalisasi ke 12 karakter. Peristiwa boleh menyebut sha pendek (seseorang
    # menyalinnya dari `git log --oneline`) dan `git rev-list` selalu memberi 40 — membandingkan
    # keduanya apa adanya membuat setiap vonis tampak seperti vonis untuk commit lain, dan pagar
    # akan menahan pekerjaan yang sebenarnya sudah diloloskan. Dua belas adalah panjang baku git
    # sendiri; tabrakan prefiks sepanjang itu bukan keadaan yang perlu diperebutkan malam ini.
    def _norm(sha):
        sha = str(sha or "")
        return sha[:12] if re.fullmatch(r"[0-9a-f]{7,40}", sha) else ""
    cleared = {_norm(r.get("sha")) for r in ev
               if r.get("kind") == "push_verified"
               and str(r.get("verdict") or "").upper() in CLEARANCE_VERDICTS} - {""}
    held = {_norm(r.get("sha")) for r in ev
            if r.get("kind") == "push_held" and (r.get("reason") or "").strip()} - {""}
    unpushed_raw = _git("rev-list", "@{u}..HEAD", repo=repo)
    pushed_raw = _git("rev-list", "--max-count=200", "@{u}", repo=repo)
    if unpushed_raw is None or pushed_raw is None:
        return {"ok": False, "readable": False,
                "why": ["git tidak terbaca — UNKNOWN, dan UNKNOWN tidak pernah berarti boleh"]}
    unpushed = [x for x in unpushed_raw.splitlines() if x.strip()]
    pushed = [x for x in pushed_raw.splitlines() if x.strip()]
    undelivered = [sha for sha in unpushed if sha[:12] not in cleared and sha[:12] not in held]
    shipped_without = [sha for sha in pushed if sha[:12] not in cleared]
    assert "" not in cleared and "" not in held   # sebuah sha kosong tidak boleh membebaskan apa pun
    reasons = []
    # Di sinilah pemutus "working tree kotor" seharusnya berdiri, dan hari ini ia tidak berdiri di
    # mana pun: `push_gate` hanya mengadili sha terhadap peristiwa verdict, jadi mendorong di atas
    # pekerjaan yang belum di-commit selama ini lulus diam-diam. Itu bahaya yang sebenarnya dari
    # tree kotor — bukan "mesin membaca berkas", melainkan "sejarah publik ditulis sambil ada
    # pekerjaan orang lain yang belum selesai, dan satu `git add -A` nanti akan menyapu keduanya".
    # Pindah ke sini bukan pelonggaran: klaim operator yang selama ini tersumbat jadi jalan, dan
    # yang menahan sekarang adalah gerbang yang menghadap keluar.
    dirty_now = tree_dirty(repo)
    blocking = []
    if dirty_now is True:
        blocking.append("working tree kotor — dorong hanya setelah pekerjaan yang belum di-commit "
                        "di-commit atau dikembalikan, supaya riwayat publik tidak menyapu kerja orang")
    elif dirty_now is None:
        blocking.append("git tidak terbaca — UNKNOWN, dan UNKNOWN tidak pernah berarti boleh dorong")
    if undelivered:
        reasons.append(f"{len(undelivered)} commit belum berangkat TANPA vonis dan tanpa catatan "
                       f"ditahan (contoh {undelivered[0][:12]})")
    # `git push` tidak memilih per-sha: ia mendorong SELURUH branch. Jadi satu commit yang sengaja
    # DITAHAN tidak bisa "lulus" dengan cara tidak dihitung — selama ia masih di belakang HEAD,
    # setiap dorongan ikut membawanya. Versi sebelumnya mengecualikan `held` dari `undelivered`,
    # sehingga keadaan "satu diloloskan + satu ditahan" mengembalikan ok=True.
    # Tapi ia masuk ke `blocking`, BUKAN ke `reasons`: `declared` menjawab "apakah nasib setiap
    # commit tercatat", dan commit yang ditahan NASIBNYA tercatat — justru catatan `push_held`-nya
    # yang membuatnya ditahan. Mencampur keduanya membuat satu kata berarti dua hal, dan itu §12.
    held_pending = [x for x in unpushed if x[:12] in held]
    if held_pending:
        blocking.append(f"{len(held_pending)} commit belum berangkat DAN DITAHAN dengan sengaja "
                        f"(contoh {held_pending[0][:12]}) — dorong branch tetap membawanya, jadi "
                        "tidak ada dorongan yang aman sampai commit itu dihapus atau diloloskan")
    # Sejarah TIDAK diadili di sini. `shipped_without` hampir selalu besar — semua commit sebelum
    # F10.9 lahir berangkat tanpa peristiwa verdict — dan membuat pagar merah permanen untuk keadaan
    # yang tidak bisa lagi diubah lebih buruk daripada tidak punya pagar: orang belajar melewati
    # yang merah. Yang dilaporkan adalah keadaan yang masih bisa diperbaiki, yaitu antrean yang belum
    # berangkat; sisanya dicatat sebagai angka, bukan sebagai vonis.
    # `ok` dinamai ulang menjadi dua kata yang tidak bisa tertukar, dan itu bukan kosmetik: ronde
    # kesembilan membaca `ok: True` pada keadaan "semua commit DITAHAN" sebagai "semua commit boleh
    # didorong". Satu nama untuk dua keadaan berbeda adalah dua laporan yang boleh berbeda (§12),
    # dan yang salah baca di sini adalah pagar yang menahan kode keluar dari mesin orang lain.
    return {"declared": not reasons,
            "ok": not reasons and not blocking and bool(cleared),
            "tree_dirty": dirty_now,
            "held_pending": [x[:12] for x in held_pending][:5],
            "pushable": [x[:12] for x in unpushed if x[:12] in cleared][:5],
            "unpushed": len(unpushed),
            "cleared": len(cleared), "held": len(held),
            "undelivered": [x[:12] for x in undelivered][:5],
            "shipped_without": len(shipped_without),
            "shipped_without_example": shipped_without[0][:12] if shipped_without else None,
            "why": reasons + blocking, "blocking": blocking}


def subprocess_run_sha():
    """HEAD yang sungguh ada di repo ini, untuk menguji gerbang pada sha nyata."""
    import subprocess
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=15)
    return (r.stdout or "").strip()


def fixture_git_repo(base):
    """Repo git sungguhan dengan upstream yang terbaca dan angka yang DIKETAHUI lebih dulu.

    Dipakai selftest pagar push. Alasannya terukur, bukan selera: gerbang per-sha yang mengadili
    repo nyata akan merah justru pada saat repo itu sehat (tidak ada commit yang belum berangkat),
    dan biru saat ada pekerjaan menggantung — persis kebalikan dari yang ingin dilaporkan. Di sini
    HEAD sengaja satu commit di depan upstream, jadi `unpushed == 1` adalah fakta yang diciptakan,
    bukan yang ditebak.

    Mengembalikan (direktori, sha_commit_pertama_yang_sudah_berangkat, sha_kedua_yang_belum).
    """
    import subprocess

    def g(*args):
        r = subprocess.run(["git", "-C", str(base), *args], capture_output=True, text=True,
                           timeout=30, env={**os.environ,
                                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                            "GIT_COMMITTER_NAME": "t",
                                            "GIT_COMMITTER_EMAIL": "t@t"})
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "git gagal").strip()[:200])
        return (r.stdout or "").strip()

    Path(base).mkdir(parents=True, exist_ok=True)
    g("init", "-q", "-b", "main")
    (Path(base) / "a.txt").write_text("satu\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "satu")
    first = g("rev-parse", "HEAD")
    bare = Path(base).parent / (Path(base).name + "-up.git")
    if subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True,
                      text=True, timeout=30).returncode != 0:
        raise RuntimeError("bare repo tidak bisa dibuat")
    g("remote", "add", "origin", str(bare))
    g("push", "-q", "-u", "origin", "main")
    (Path(base) / "b.txt").write_text("dua\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "dua")
    second = g("rev-parse", "HEAD")
    return Path(base), first, second


def selftest():
    globals()["_push_src"] = __import__("inspect").getsource(push_state)
    """Jalankan di SQLite sementara. Tes tidak boleh menyentuh produksi: satu selftest yang
    membuat orang mematikan notifikasi yang sebenarnya dia butuhkan."""
    tmp = Path(f"/tmp/work_order_selftest_{now()}.db")
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row
    banners, orig_banner = _mute_banners()
    checks = []
    # Kedua sambatan dipasang SEBELUM try dan dilepas di finally. Kalau dipasang di dalam try,
    # satu ledakan di `ensure_schema` membuat finally membaca nama yang belum pernah ada — dan
    # NameError itu menutupi ledakan asli yang sedang dicarinya.
    # `night_state()` dibaca oleh `claim()` begitu fixture di bawah menyalakan ON, dan ia mengukur
    # working tree SUNGGUHAN — yang selama tes ini berjalan pasti kotor oleh tesnya sendiri.
    # Tanpa sambatan ini ratusan klaim fixture ditolak karena berkas yang sedang diedit modul ini.
    orig_tree_dirty = globals()["tree_dirty"]
    # Nama kedua ini TIDAK pernah ditimpa. Bloks lain di bawah mengambil `orig_tree_dirty` lagi
    # saat global-nya sudah disambat, jadi nama itu kemudian menunjuk sambatan, bukan git — dan
    # satu cek di pagar push yang memulihkannya akan mengira tree fixture bersih.
    tree_dirty_real = orig_tree_dirty
    globals()["tree_dirty"] = lambda *_a: False
    # `queue_command` sekarang MENGANTARKAN perintahnya, dan jalur kirim asli berbicara ke daemon
    # pane sungguhan. Selftest yang tidak menukar sambatan ini akan mengetik ke terminal operator
    # saat orang sedang tidur, jadi pagarnya dipasang sebelum satu baris tes pun berjalan dan
    # keberadaan pagar itu sendiri diperiksa di bawah.
    orig_deliver = globals()["deliver_now"]
    globals()["deliver_now"] = lambda conn, engine, item_id: {
        "sent": False, "queued": True, "refused": "SELFTEST"}
    # Penulis peristiwa kirim ikut disambat untuk SELURUH durasi tes, bukan per-blok. Versi
    # pertama menyambatnya hanya di dalam satu blok dan lupa memasang kembali stub tanpa
    # `detail` sesudahnya, sehingga satu tes BERIKUTNYA mengirim dua baris PANE_NOT_QUIET
    # berbunyi `ts: 1` ke logs/drainer-<hari>.jsonl — pemalsuan catatan yang persis sama dengan
    # yang baru ditutup di jalur hook, dan kali ini pelakunya adalah tes itu sendiri.
    send_events, orig_send_events = [], globals()["_record_send_events"]
    globals()["_record_send_events"] = lambda ev: send_events.append(list(ev))
    fails = []
    try:
        log = ensure_schema(conn, "SQLITE")

        def check(name, cond, detail=""):
            checks.append((name, bool(cond), detail))

        check("schema dibuat", all(not l.startswith("skip") for l in log), "; ".join(log))
        # Kosakata CLI dibaca dari konstanta yang sama, dan itu dibuktikan dengan menjalankan
        # parser sungguhan atas satu kata yang memang salah: argparse keluar SEBELUM menyentuh
        # database, jadi ini aman di tengah soak. Yang diperiksa adalah pesan kesalahan itu
        # menyebut SETIAP kata yang dikenal kode — kalau ada yang menyalin daftar secara manual,
        # salinan itu akan berhenti menyebut salah satunya di sini.
        import subprocess as _sp
        bad = _sp.run([sys.executable, str(Path(__file__).resolve()), "autopilot", "zzz-tidak-sah"],
                      capture_output=True, text=True, timeout=60)
        listed = (bad.stderr or "") + (bad.stdout or "")
        missing = [w for w in [m.lower() for m in (MODES + STOP_WORDS)] if w not in listed]
        check("parser CLI mengenal seluruh kosakata dari konstanta, bukan dari daftar salinan",
              bad.returncode == 2 and not missing, f"rc={bad.returncode} hilang={missing}")
        # --- kegagalan banner harus meninggalkan jejak, di KEDUA cabang ---------------------------
        # Cabang pertama (daemon menolak) punya jejak; cabang kedua (modul notify tidak bisa
        # diimpor sama sekali) tidak, dan itu justru yang paling mungkin terjadi di mesin ini —
        # `notify-send` pernah hilang berhari-hari tanpa satu jalur pun mengaku. Keduanya diuji.
        import tempfile as _tf0
        _bl_d = Path(_tf0.mkdtemp(prefix="banner_"))
        globals()["BANNER_LOG"] = _bl_d / "events.jsonl"
        import notify as _nv
        _real_ev = _nv.notify_event
        try:
            _nv.notify_event = lambda *a, **k: {"sent": False, "err": "daemon menolak (id 0)"}
            # Dipanggil lewat `_banner`, bukan `banner`: nama yang terakhir sudah ditukar dengan
            # perekam di awal selftest, dan menguji boneka akan lulus tanpa menyentuh satu baris pun
            # dari jalur yang sedang diuji.
            r_refused = _banner("WORK_DISPATCHED", "item 1 → qoder")
            _nv.notify_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gdbus hilang"))
            r_broken = _banner("ESCALATED", "pertanyaan")
        finally:
            _nv.notify_event = _real_ev
            globals()["BANNER_LOG"] = None
        _txt = (_bl_d / "events.jsonl").read_text(encoding="utf-8").strip().splitlines() \
            if (_bl_d / "events.jsonl").exists() else []
        check("banner yang DITOLAK daemon meninggalkan jejak banner_failed",
              r_refused["sent"] is False and any('"banner_failed"' in x for x in _txt), str(_txt[:1]))
        check("banner yang MELEDAK (modul/perintah tidak ada) juga meninggalkan jejak — bukan "
              "kembali diam-diam", r_broken["sent"] is False and len(_txt) >= 2
              and "gdbus hilang" in _txt[-1], str(_txt[-1:])[:160])
        (_bl_d / "events.jsonl").unlink(missing_ok=True)
        (_bl_d).rmdir()
        check("jalur kirim disuntik sebelum tes pertama: selftest TIDAK BISA mengetuk pane asli",
              globals()["deliver_now"] is not _deliver_now, str(globals()["deliver_now"]))
        # Stub yang selalu menolak membuat cabang `if handed.get("sent")` tidak pernah dijalankan
        # sama sekali, dan pesan "diserahkan ke CLI" tidak punya satu pun bukti. Jadi cabang itu
        # diuji sendiri, dengan stub yang BERHASIL, lalu stub penolak dipasang kembali: yang tidak
        # boleh terjadi adalah tes yang hijau karena jalur keberhasilannya tidak pernah ditempuh.
        globals()["deliver_now"] = lambda conn, engine, item_id: {
            "sent": True, "target": "qoder", "reply": "dispatched"}
        ok_sent, msg_sent, id_sent = queue_command(conn, "SQLITE", "uji cabang terkirim", "qoder")
        check("cabang penyerahan berhasil melaporkan 'diserahkan' dan membawa balasan daemon",
              ok_sent and "diserahkan ke CLI" in msg_sent and "dispatched" in msg_sent, msg_sent)
        check("penyerahan berhasil tidak menghapus barisnya dari ledger",
              id_sent and get_item(conn, "SQLITE", int(id_sent)) is not None, str(id_sent))
        run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (int(id_sent),))
        globals()["deliver_now"] = lambda conn, engine, item_id: {
            "sent": False, "queued": True, "refused": "SELFTEST"}
        # Cabang PENOLAKAN juga harus punya bukti. Stub yang menolak tanpa `detail` membuat
        # `note_refusal` dan penulis peristiwa tidak pernah dijalankan di satu pun tes — dan
        # itulah lubang yang dibiarkan terbuka sampai operator bertanya perintahnya hilang ke mana:
        # tujuh baris ditolak, `escalation_reason` tetap NULL, dan ledger tidak bisa ditanya.
        # Tidak ada swap persis-di-sini: perekam global di atas sudah dipasang untuk seluruh tes,
        # dan menyambatnya lagi di dalam blok membuat perekam itu tidak melihat apa pun.
        _ev_before = len(send_events)
        globals()["deliver_now"] = lambda conn, engine, item_id: {
            "sent": False, "queued": True, "events": ["PANE_NOT_QUIET"],
            "detail": [{"ts": 1, "action": "PANE_NOT_QUIET",
                        "why": "pane sedang menampilkan pekerjaan"}]}
        ok_ref, msg_ref, id_ref = queue_command(conn, "SQLITE", "uji penolakan tercatat", "qoder")
        _captured = send_events[_ev_before:]
        _row_ref = get_item(conn, "SQLITE", int(id_ref)) if id_ref else None
        check("penolakan kirim menulis escalation_reason, bukan meninggalkan NULL",
              bool(ok_ref and (_row_ref or {}).get("escalation_reason")), str(_row_ref)[:180])
        check("alasan yang ditulis menyebut kode penolakannya, supaya ledger bisa ditanya",
              "PANE_NOT_QUIET" in ((_row_ref or {}).get("escalation_reason") or ""),
              str((_row_ref or {}).get("escalation_reason"))[:180])
        check("status baris yang ditolak TETAP PENDING — penolakan bukan kesimpulan",
              (_row_ref or {}).get("status") == "PENDING", str((_row_ref or {}).get("status")))
        check("peristiwa kirim ditahan lewat penulis log, tidak dibuang bersama response",
              bool(_captured) and _captured[-1] and _captured[-1][0].get("action") == "PANE_NOT_QUIET",
              str(_captured)[:180])
        # Stub ber-`detail` dibuang lagi. Meninggalknya hidup membuat satu tes SESUDAHNYA memanggil
        # queue_command dan ikut menulis peristiwa, dan itu yang dulu memalsukan empat baris
        # PANE_NOT_QUIET `ts: 1` ke log produksi.
        globals()["deliver_now"] = lambda conn, engine, item_id: {
            "sent": False, "queued": True, "refused": "SELFTEST"}
        if id_ref:
            run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (int(id_ref),))
        # Keadaan awal fixture harus DINYATAKAN, bukan diwarisi dari default skema. Tanpa baris
        # ini, seluruh blok di bawah ikut bentuk baris pertama: begitu default berubah dari OFF
        # menjadi SEMI, keranjang SEMI menolak item roadmap yang dibuat `seed` dan setiap tes di
        # bawahnya runtuh karena alasan yang tidak sedang diuji. Urutannya penting: default dibaca
        # dulu, baru fixture mengambil keadaannya sendiri.
        fresh = autopilot(conn, "SQLITE")
        check("baris baru lahir sebagai SEMI — OFF bukan lagi sebuah keadaan mesin",
              fresh and fresh["mode"] == "SEMI", str(fresh))
        check("tangan lahir turun: default SEMI tidak bergerak sendiri",
              fresh and fresh["level"] == "observe", str(fresh and fresh["level"]))
        check("jendela belum dibuka di baris baru: boot tidak menemukan loop yang sudah jalan",
              fresh and not fresh.get("until_epoch"), str(fresh and fresh.get("until_epoch")))
        conn.execute("UPDATE autopilot_state SET mode='ON', on_epoch=?, until_epoch=?, "
                     "level='observe' WHERE id=1", (now(), now() + 3600))
        conn.commit()
        made = seed(conn, "SQLITE", [("kA", "A", "build", None, "true"),
                                     ("kB", "B", "build", ["kA"], "true")])
        ids = {t: i for i, _, t in made}
        check("seed memberi id", ids.get("A") and ids.get("B"))
        check("dependensi terselesaikan jadi id, bukan key",
              "kA" not in str(get_item(conn, "SQLITE", ids["B"]).get("depends_on")),
              str(get_item(conn, "SQLITE", ids["B"]).get("depends_on")))
        again = seed(conn, "SQLITE", [("kA", "A", "build", None, "true")])
        check("seed idempoten", again[0][1] == "ada")

        ok, msg = claim(conn, "SQLITE", ids["A"], "qoder")
        check("klaim pertama menang", ok, msg)
        ok2, msg2 = claim(conn, "SQLITE", ids["A"], "antigravity")
        check("klaim kedua kalah", not ok2, msg2)
        check("alasan menyebut pemegang", "qoder" in msg2, msg2)

        nxt = next_item(conn, "SQLITE")
        check("B tertahan dependensi belum COMPLETED", not nxt or nxt["title"] != "B",
              str(nxt and nxt["title"]))

        ok, msg = finish(conn, "SQLITE", ids["A"], "COMPLETED", evidence=None)
        check("COMPLETED tanpa bukti ditolak", not ok, msg)
        ok, msg = finish(conn, "SQLITE", ids["A"], "COMPLETED", evidence="selftest: exit 0")
        check("COMPLETED dengan bukti diterima", ok, msg)
        nxt = next_item(conn, "SQLITE")
        check("B terbuka setelah A selesai", nxt and nxt["title"] == "B", str(nxt))

        ok, msg = finish(conn, "SQLITE", ids["B"], "HUMAN", reason="ambang: disk < 10G")
        check("eskalasi menyimpan alasan", ok and get_item(conn, "SQLITE", ids["B"])["escalation_reason"])
        check("item HUMAN dilewati", next_item(conn, "SQLITE") is None)
        # Eskalasi harus menghasilkan sesuatu yang bisa DIJAWAB, bukan hanya judul untuk dibaca.
        esc_q = open_decision_for(conn, "SQLITE", ids["B"])
        check("naik ke antrean orang sekaligus mengajukan pertanyaan yang tertaut",
              esc_q is not None and int(esc_q["item_id"]) == ids["B"]
              and esc_q["state"] == "OPEN", str(esc_q))
        finish(conn, "SQLITE", ids["B"], "HUMAN", reason="ambang: disk < 10G")
        check("eskalasi kedua atas item yang sama tidak mengajukan pertanyaan kedua",
              len([d for d in decisions_for_item(conn, "SQLITE", ids["B"])
                   if d["state"] in ("OPEN", "ESCALATED")]) == 1,
              str(decisions_for_item(conn, "SQLITE", ids["B"])))

        conn.execute("INSERT INTO quest_tasks (title, status) VALUES ('unavail','UNAVAILABLE')")
        conn.commit()
        check("item UNAVAILABLE tidak pernah ditawarkan sebagai kerja",
              next_item(conn, "SQLITE") is None, str(next_item(conn, "SQLITE")))

        # --- F10.7 heartbeat: dua sinyal harus setuju sebelum ada yang boleh disebut diam ---
        # capture/busy disuntik: tes tidak pernah membaca pane operator yang sedang hidup.
        hb_id = seed(conn, "SQLITE", [("kHB", "HB", "build", None, "true")])[0][0]
        claim(conn, "SQLITE", hb_id, "qoder")

        GAP = 0.02   # jeda yang benar-benar diambil; produksi dilantai oleh MIN_SAMPLE_GAP

        def still(_tab):
            return "prompt kosong"

        def shifting(_tab):
            shifting.n += 1
            return f"frame {shifting.n}"

        shifting.n = 0

        def explode(_tab):
            raise RuntimeError("tmux hilang")

        calm = lambda _screen: False          # noqa: E731
        spinner = lambda _screen: True        # noqa: E731

        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=lambda _t: None, busy=calm)
        check("pane tidak terbaca = UNKNOWN, bukan AT_REST",
              rows and rows[0]["state"] == "UNKNOWN" and rows[0]["pane_readable"] is False,
              str(rows and rows[0]))
        check("sinyal database yang tidak terbaca ikut menjadi UNKNOWN",
              rows and rows[0]["action_log_readable"] is False, str(rows and rows[0]))
        check("satu sinyal hilang cukup untuk menolak kesimpulan",
              rows and rows[0]["state"] == "UNKNOWN" and "AT_REST" not in str(rows), str(rows))
        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=explode, busy=calm)
        check("capture yang melempar bukan crash dan bukan diam — UNKNOWN",
              rows[0]["state"] == "UNKNOWN" and rows[0]["pane_readable"] is False, str(rows[0]))
        check("stalled() tidak mengarang kandidat dari tabel yang tak terbaca",
              stalled(conn, "SQLITE", older_than=0) == [], str(stalled(conn, "SQLITE", 0)))

        # Kolomnya harus selengkap yang dibaca `push_clearance`: tabel (engine, ts) saja akan
        # membuat setiap pencarian izin gagal lebih awal karena "no such column", dan itu membuat
        # tes penolakan lulus tanpa membuktikan apa pun tentang vonis.
        conn.execute("CREATE TABLE action_log (engine TEXT, ts TEXT, kind TEXT, tab TEXT, "
                     "model TEXT, exit_code INTEGER, summary TEXT, raw_ref TEXT)")
        conn.commit()
        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=still, busy=calm)
        check("tabel terbaca tapi kosong atas nama pemegang = UNKNOWN, bukan AT_REST",
              rows[0]["state"] == "UNKNOWN" and rows[0]["last_action_epoch"] is None
              and rows[0]["action_log_readable"] is True, str(rows[0]))

        conn.execute("INSERT INTO action_log (engine, ts) VALUES ('qoder', 'bukan-tanggal')")
        conn.commit()
        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=still, busy=calm)
        check("tanggal yang tak bisa dibaca = sinyal mati, bukan sinyal kosong",
              rows[0]["state"] == "UNKNOWN" and rows[0]["action_log_readable"] is False,
              str(rows[0]))

        conn.execute("DELETE FROM action_log")
        conn.execute("INSERT INTO action_log (engine, ts) VALUES ('qoder', datetime('now','-1 hour'))")
        conn.commit()
        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=still, busy=calm)
        check("teks UTC dibaca sebagai UTC: umur aksi ≈ 3600s",
              3540 <= (rows[0]["quiet_seconds"] or 0) <= 3660, str(rows[0]["quiet_seconds"]))
        check("dua sinyal setuju = AT_REST", rows[0]["state"] == "AT_REST", str(rows[0]))
        check("lease dan sisa budget resume ikut dilaporkan",
              rows[0]["lease_alive"] is True and rows[0]["resume_budget_left"] == MAX_RESUME,
              str(rows[0]))
        rows = heartbeat(conn, "SQLITE", 180, 0, capture=still, busy=calm)
        check("dua pembacaan tanpa jeda tidak boleh menyimpulkan diam",
              rows[0]["state"] == "UNKNOWN" and "jeda" in rows[0]["why"], str(rows[0]))

        conn.execute("INSERT INTO action_log (engine, ts) VALUES ('qoder', datetime('now'))")
        conn.commit()
        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=still, busy=calm)
        check("satu aksi terukur membatalkan kesimpulan diam",
              rows[0]["state"] == "WORKING" and "aksi terukur" in rows[0]["why"], str(rows[0]))

        rows = heartbeat(conn, "SQLITE", 180, GAP, capture=shifting, busy=calm)
        check("teks pane yang berubah = WORKING walau database diam",
              rows[0]["state"] == "WORKING" and "berubah" in rows[0]["why"], str(rows[0]))
        rows = heartbeat(conn, "SQLITE", 180, 0, capture=shifting, busy=calm)
        check("perubahan tetap bukti walau tanpa jeda; yang butuh jeda hanya ketiadaan perubahan",
              rows[0]["state"] == "WORKING", str(rows[0]))

        rows = heartbeat(conn, "SQLITE", 0, GAP, capture=still, busy=spinner)
        check("spinner lebih dipercaya daripada dua sinyal diam",
              rows[0]["state"] == "WORKING" and "spinner" in rows[0]["why"], str(rows[0]))

        finish(conn, "SQLITE", hb_id, "COMPLETED", evidence="selftest: heartbeat dua sinyal")
        check("tanpa klaim terbuka heartbeat tidak mengarang baris",
              heartbeat(conn, "SQLITE", 180, 0, capture=still, busy=calm) == [], "ada baris")
        # Invarian yang tidak boleh lulus tanpa diperiksa: jalur baca ini hanya indera. Cara
        # tercepat menjebolnya adalah orang berikutnya menambah "sedikit" pengiriman di ujung yang
        # sama — jadi yang diperiksa bukan hanya heartbeat(), tapi setiap fungsi yang ia panggil.
        seen = ""
        for fn in (heartbeat, _pane_text, _last_action, stalled):
            seen += inspect.getsource(fn)
        check("jalur baca tidak punya tangan: nol jalur ketik/spawn di heartbeat dan setiap bagiannya",
              not any(w in seen for w in ("send-keys", "send_to_terminal", "ydotool",
                                          "subprocess", "run_grouped", "run_bounded")),
              "ada jalur pengiriman di jalur baca")
        # Klaimnya harus sebatas yang benar: membaca pane memang memakai subprocess, tapi di
        # cross_verify.capture_pane — satu-satunya nama yang boleh dipinjam heartbeat.
        borrowed = [n for n in ("capture_pane", "pane_is_busy", "run_engine", "engine_present",
                                "send_keys") if f"cv.{n}" in inspect.getsource(heartbeat)]
        check("yang dipinjam dari cross_verify hanya dua pembacaan, bukan pengiriman",
              borrowed == ["capture_pane", "pane_is_busy"], str(borrowed))

        ap = autopilot(conn, "SQLITE")
        check("fixture membaca jendela yang dibuka di atas, bukan default skema",
              ap["mode"] == "ON" and int(ap["until_epoch"] or 0) > now(), str(ap))
        ap = autopilot(conn, "SQLITE", "ON", minutes=30, budget=4)
        check("ON menyimpan budget dan expiry", ap["mode"] == "ON" and ap["item_budget"] == 4
              and ap["until_epoch"] > now(), str(ap))
        ap = autopilot(conn, "SQLITE", "off", reason="selftest selesai")
        check("kata berhenti menyimpan alasan di kolom yang sama",
              "selftest" in (ap["off_reason"] or ""), str(ap))
        # Ini keadaan yang operator batalkan pada 2026-10-09. Perilaku lama: `berhenti` menulis
        # jendela ke masa lalu dan MEMBIARKAN mode = ON/SEMI, sehingga mesin yang sudah mati
        # dilaporkan masih dalam mode kerja. Yang sekarang ditegakkan justru kebalikannya — dan
        # kalau seseorang mengembalikan perilaku lama, dua tes di bawah menolaknya.
        check("berhenti menulis state OFF yang nyata, bukan jendela basi dengan mode yang tersisa",
              ap["mode"] == OFF_MODE, str(ap["mode"]))
        check("kata berhenti menutup jendela dan menurunkan tangan (rem tanpa TTY)",
              int(ap["until_epoch"] or 0) <= now() and ap["level"] == "observe",
              str({k: ap[k] for k in ("until_epoch", "level")}))
        # Alias rem harus menulis keadaan yang SAMA dengan tombolnya, tidak menambah kata keempat.
        for word in STOP_WORDS:
            ap_alias = autopilot(conn, "SQLITE", "SEMI", minutes=10, who="selftest")
            check(f"menyalakan SEMI lagi menghapus status berhenti (mode={ap_alias['mode']})",
                  ap_alias["mode"] == "SEMI" and ap_alias["off_reason"] is None, str(ap_alias["mode"]))
            ap_alias = autopilot(conn, "SQLITE", word, reason=f"selftest alias {word}")
            check(f"kata '{word}' adalah alias menuju OFF, bukan keadaan keempat",
                  ap_alias["mode"] == OFF_MODE and "OFF" not in (ap_alias.get("refused") or ""),
                  str({k: ap_alias.get(k) for k in ("mode", "refused")}))
        ap = autopilot(conn, "SQLITE", "nanti-dulu", reason="typo")
        check("mode di luar kosakata DITOLAK dengan menyebut kosakata, tidak dipilihkan diam-diam",
              "bukan kosakata" in (ap.get("refused") or "") and ap["mode"] == OFF_MODE, str(ap))
        check("penolakan mode asing tidak mengubah baris: OFF yang sudah ada tetap tinggal",
              ap["mode"] == OFF_MODE and "SEMI/OFF/ON" in (ap.get("refused") or ""),
              str(ap.get("refused"))[:160])
        # OFF bukan label: tidak ada satu pun jalur yang boleh mengklaim di atasnya, bahkan ketika
        # pemanggil bersikeras mengirim mode lain. Klaim adalah satu-satunya tulisan yang menggerakkan
        # pekerjaan, jadi inilah pagar yang membuktikan keadaan itu nyata.
        off_made = seed(conn, "SQLITE", [("kOff", "off-claim-target", "build", None, "true")])
        off_item = off_made[0][0]
        ok_off, msg_off = claim(conn, "SQLITE", off_item, "selftest", dirty=False, mode="SEMI")
        check("OFF menolak klaim walaupun pemanggil mengirim mode='SEMI'",
              not ok_off and "OFF" in msg_off, msg_off)
        ok_off2, msg_off2 = claim(conn, "SQLITE", off_item, "selftest", dirty=False, mode="ON")
        check("penimpaan mode dari pemanggil tidak bisa menembus OFF", not ok_off2, msg_off2)
        check("penolakan OFF tidak meninggalkan klaim di barisnya",
              (get_item(conn, "SQLITE", off_item) or {}).get("claimed_by") is None,
              str((get_item(conn, "SQLITE", off_item) or {}).get("claimed_by")))
        run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (off_item,))

        # --- F10.3: tangga tangan, dan pagar yang membuatnya tak bisa dinaikkan sendiri ---
        lvl = drain_level(conn, "SQLITE")
        check("tangga lahir tertutup: observe, nol izin dispatch dan nol izin resume",
              lvl["level"] == "observe" and not lvl["allows_dispatch"]
              and not lvl["allows_resume"], str(lvl))
        check("turun-naik tangan ditulis oleh `arm`, tapi default tidak pernah menulis apa pun",
              lvl["readable"] is True and lvl["level"] == "observe", str(lvl))
        ok, msg = set_level(conn, "SQLITE", "resume", 30, "selftest", tty=False)
        check("naik tangga tanpa terminal pengendali ditolak", not ok and "/dev/tty" in msg, msg)
        check("penolakan tidak mengubah keadaan", drain_level(conn, "SQLITE")["level"] == "observe")
        ok, msg = set_level(conn, "SQLITE", "observe", 30, "selftest", tty=False)
        check("menurunkan tangan tidak butuh tty — jalan mati tidak minta izin", ok, msg)
        ok, msg = set_level(conn, "SQLITE", "shoot", 30, "selftest", tty=True)
        check("kata di luar kosakata ditolak", not ok and "kosakata" in msg, msg)
        ok, msg = set_level(conn, "SQLITE", "dispatch", 0, "selftest", tty=True)
        check("naik tangga tanpa batas waktu ditolak", not ok and "batas waktu" in msg, msg)
        ok, msg = set_level(conn, "SQLITE", "dispatch", ARM_MAX_MINUTES + 1, "selftest", tty=True)
        check("batas menit atas ditegakkan, bukan dipotong diam-diam",
              not ok and str(ARM_MAX_MINUTES) in msg, msg)
        ok, msg = set_level(conn, "SQLITE", "dispatch", 30, "selftest", tty=True)
        lvl = drain_level(conn, "SQLITE")
        check("dispatch boleh mulai engine tapi tidak boleh mengetik",
              ok and lvl["level"] == "dispatch" and lvl["allows_dispatch"]
              and not lvl["allows_resume"], str(lvl))
        check("sisa menit dilaporkan dalam detik, bukan hanya statusnya",
              1770 <= lvl["seconds_left"] <= 1800, str(lvl))
        ok, msg = set_level(conn, "SQLITE", "resume", 30, "selftest", tty=True)
        check("resume membuka tangan", ok and drain_level(conn, "SQLITE")["allows_resume"], msg)
        conn.execute("UPDATE autopilot_state SET level_until=? WHERE id=1", (now() - 5,))
        lvl = drain_level(conn, "SQLITE")
        check("kedaluwarsa menutup tangan tanpa ada yang mengetik disarm",
              lvl["level"] == "observe" and lvl["expired"] and not lvl["allows_resume"], str(lvl))
        check("alasan penutupan ikut dilaporkan, tidak hilang bersama tutupnya",
              "kedaluwarsa" in " ".join(lvl["why"]), str(lvl["why"]))
        conn.execute("UPDATE autopilot_state SET level='teleport', level_until=? WHERE id=1",
                     (now() + 600,))
        lvl = drain_level(conn, "SQLITE")
        check("level asing diturunkan ke tangga terkecil, bukan dipercaya",
              lvl["level"] == "observe" and lvl["requested"] == "teleport", str(lvl))
        conn.execute("UPDATE autopilot_state SET level='resume', level_until=NULL WHERE id=1")
        lvl = drain_level(conn, "SQLITE")
        check("resume tanpa batas waktu terbaca tertutup: lupa bukan izin",
              lvl["level"] == "observe" and not lvl["allows_resume"], str(lvl))
        # Kata berhenti ikut menutup tangan; kalau tidak, ON berikutnya menemukan level yang
        # diangkat orang lain pada malam yang berbeda.
        set_level(conn, "SQLITE", "resume", 30, "selftest", tty=True)
        ap = autopilot(conn, "SQLITE", "off", reason="selftest: kata berhenti menutup tangan")
        check("kata berhenti ikut menutup tangan",
              drain_level(conn, "SQLITE")["level"] == "observe" and ap["level"] == "observe",
              str(drain_level(conn, "SQLITE")))
        check("has_tty adalah ukuran, bukan dugaan", isinstance(has_tty(), bool), str(has_tty()))

        # --- F10.3: anggaran sentuhan ditegakkan di tempat tulisannya terjadi, bukan di timer.
        # Presedennya circuit breaker di `claim()`: manusia yang mengetik perintah yang sama
        # menabrak angka yang sama. Selama batasnya hidup di pemanggil, pemanggil berikutnya
        # boleh lupa membawa batasnya.
        conn.execute("INSERT INTO quest_tasks (title, status) VALUES ('nudge-target','PENDING')")
        conn.commit()
        nid = run(conn, "SQLITE", "SELECT id FROM quest_tasks WHERE title='nudge-target'")[0]["id"]
        for k in range(MAX_RESUME):
            ok, msg = nudge(conn, "SQLITE", nid)
            check(f"sentuhan {k + 1} dari {MAX_RESUME} diizinkan dan terbaca kembali",
                  ok and int(get_item(conn, "SQLITE", nid)["resume_count"] or 0) == k + 1, msg)
        ok, msg = nudge(conn, "SQLITE", nid)
        check("sentuhan berikutnya ditolak: item ini macet, bukan tertidur",
              not ok and "habis" in msg and str(MAX_RESUME) in msg, msg)
        check("penolakan tidak menaikkan angka diam-diam",
              int(get_item(conn, "SQLITE", nid)["resume_count"] or 0) == MAX_RESUME,
              str(get_item(conn, "SQLITE", nid)["resume_count"]))
        check("nudge hanya menghitung — status item tidak ikut ditulis",
              get_item(conn, "SQLITE", nid)["status"] == "PENDING",
              str(get_item(conn, "SQLITE", nid)["status"]))
        ok, msg = nudge(conn, "SQLITE", 999999)
        check("item yang tidak ada ditolak, tidak dibuatkan angka", not ok and "tidak ada" in msg, msg)

        # --- batas injeksi per baris. `resume_count` menjaga CONTINUE ke item yang sudah dipegang;
        # `inject_count` menjaga perintah BARU yang disuntikkan ke sesi yang berhenti. Dua kata,
        # dua kolom — dan tes ini memastikan memenuhkan yang satu tidak menghabiskan yang lain,
        # karena menumpang satu kolom adalah persis cara anggaran yang dibaca operator berbeda dari
        # anggaran yang mesin pakai (§12).
        conn.execute("INSERT INTO quest_tasks (title, status) VALUES ('inject-target','PENDING')")
        conn.commit()
        iid = run(conn, "SQLITE", "SELECT id FROM quest_tasks WHERE title='inject-target'")[0]["id"]
        for k in range(MAX_INJECT):
            ok, msg = nudge_inject(conn, "SQLITE", iid)
            check(f"injeksi {k + 1} dari {MAX_INJECT} diizinkan dan terbaca kembali",
                  ok and int(get_item(conn, "SQLITE", iid)["inject_count"] or 0) == k + 1, msg)
        ok, msg = nudge_inject(conn, "SQLITE", iid)
        check("injeksi berikutnya ditolak: baris ini sudah disuntik dan tidak ada yang menyelesaikan",
              not ok and "habis" in msg and str(MAX_INJECT) in msg, msg)
        check("penolakan injeksi tidak menaikkan angka diam-diam",
              int(get_item(conn, "SQLITE", iid)["inject_count"] or 0) == MAX_INJECT,
              str(get_item(conn, "SQLITE", iid)["inject_count"]))
        check("batas injeksi TIDAK membaca atau menulis resume_count — dua kata, dua kolom",
              int(get_item(conn, "SQLITE", iid)["resume_count"] or 0) == 0
              and int(get_item(conn, "SQLITE", nid)["inject_count"] or 0) == 0,
              f"inject={get_item(conn, 'SQLITE', iid)['inject_count']} "
              f"resume={get_item(conn, 'SQLITE', iid)['resume_count']} "
              f"target.inject_count={get_item(conn, 'SQLITE', nid)['inject_count']}")
        check("nudge_inject hanya menghitung — status item tidak ikut ditulis",
              get_item(conn, "SQLITE", iid)["status"] == "PENDING",
              str(get_item(conn, "SQLITE", iid)["status"]))
        ok, msg = nudge_inject(conn, "SQLITE", 999999)
        check("injeksi ke item yang tidak ada ditolak, tidak dibuatkan angka",
              not ok and "tidak ada" in msg, msg)

        # --- `retry_now`: IDLE harus berarti terima perintah, tanpa menghapus rem yang lain.
        # Operator menamai sendiri sifat ini: yang penting adalah status IDLE otomatis menerima,
        # supaya mesin bisa berputar sendiri. Jeda 300 dtk yang ditulis penolakan "pane sibuk"
        # menahan baris itu sampai lima menit justru pada detik mesin terbukti sudah diam.
        _t = 1_700_000_100
        check("jeda yang sudah lewat selalu boleh diambil",
              retry_now({"due_epoch": _t - 1, "escalation_reason": None}, at=_t) is True, "past due")
        check("penolakan 'pane sibuk' dibatalkan oleh bukti mesin baru saja diam",
              retry_now({"due_epoch": _t + 300,
                         "escalation_reason": "PANE_NOT_QUIET · pane sedang menampilkan pekerjaan"},
                        at=_t) is True, "busy reason must retry at a boundary")
        check("penolakan 'engine masih memegang satu perintah' juga keluarga yang sama",
              retry_now({"due_epoch": _t + 300, "escalation_reason": "CLAIM_REFUSED · ENGINE_BUSY: qoder"},
                        at=_t) is True, "ENGINE_BUSY reason must retry at a boundary")
        # Arah sebaliknya, dan ini yang membuat perubahan di atas bukan pintu belakang: alasan yang
        # tidak akan berubah oleh sendirinya tetap menunggu jeda-nya.
        check("direktori pane yang salah TETAP menunggu jeda, bukan dicoba di setiap batas",
              retry_now({"due_epoch": _t + 300, "escalation_reason": "PANE_WRONG_DIR · tab proyek lain"},
                        at=_t) is False, "wrong dir must keep waiting")
        check("injeksi habis TETAP menunggu jeda — inilah rem anti-loop",
              retry_now({"due_epoch": _t + 300, "escalation_reason": "INJECT_CAPPED · budget habis"},
                        at=_t) is False, "capped must keep waiting")
        check("jendela yang ditutup operator TETAP berarti tertutup di jalur ini",
              retry_now({"due_epoch": _t + 300, "escalation_reason": "WINDOW_CLOSED"}, at=_t) is False,
              "operator brake must not be bypassed")
        check("baris tanpa alasan tersimpan tidak pernah diloloskan lebih awal",
              retry_now({"due_epoch": _t + 300, "escalation_reason": None}, at=_t) is False,
              "no reason recorded must keep waiting")

        # --- ON harus benar-benar berarti ON. Ini regresi yang terukur hari ini, bukan teori:
        # jalur timer menyaring keranjang menurut mode, Stop hook menulis ORIGIN_OPERATOR tanpa
        # syarat, dan karena level=observe mengunci timer, hook adalah satu-satunya yang hidup.
        # Hasilnya: sakelar ON, roadmap penuh, tidak ada yang bergerak, operator disuruh "lanjut".
        conn.execute("INSERT INTO quest_tasks (title, status, origin, cli_engine) "
                     "VALUES ('selftest-roadmap-basket','PENDING','roadmap','qoder')")
        conn.commit()
        rb_id = int(run(conn, "SQLITE", "SELECT id FROM quest_tasks "
                          "WHERE title='selftest-roadmap-basket'")[0]["id"])
        check("keranjang SEMI = hanya perintah operator",
              queue_origin_for("SEMI") == ORIGIN_OPERATOR
              and rb_id not in [int(x["id"]) for x in ready_items(conn, "SQLITE",
                                                                  origin=queue_origin_for("SEMI"),
                                                                  for_engine="qoder")],
              str(queue_origin_for("SEMI")))
        _on_ids = [int(x["id"]) for x in ready_items(conn, "SQLITE",
                                                     origin=queue_origin_for("ON"),
                                                     for_engine="qoder")]
        check("keranjang ON = antrean roadmap TERLIHAT, bukan hanya dideklarasikan",
              queue_origin_for("ON") is None and rb_id in _on_ids, str(_on_ids[:6]))
        check("huruf kecil tidak mengubah keranjang (on == ON)",
              queue_origin_for("on") == queue_origin_for("ON"), str(queue_origin_for("on")))
        # Pagar proyek: repo workstation boleh dipelihara oleh tab workstation, tapi proyek KETIGA
        # tetap tidak boleh masuk. Kalau pengecualian ini ditulis longgar, ia jadi lubang.
        check("~/.ai-station layak dikerjakan dari tab yang diluncurkan di ai-workstation",
              row_workable_here(REPO, REPO.parent / "Documents" / "ai-workstation") is True,
              "workstation must maintain itself")
        check("proyek KETIGA tetap diveto — pengecualian bukan pelonggaran umum",
              row_workable_here("/tmp/proyek-orang-lain", REPO) is False, "third project vetoed")
        check("baris tanpa proyek tidak diveto oleh pagar yang tidak punya bahan",
              row_workable_here(None, REPO) is True, "no ws = allowed")
        check("path yang tidak bisa di-resolve diveto, bukan dianggap cocok",
              row_workable_here("", REPO) is True and row_workable_here(123, REPO) is False,
              "junk input must not pass")
        run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (rb_id,))

        # --- F9.2: bentuk perintah yang pernah gagal, diperingatkan sebelum diketik.
        for cmd, want in (
                ("cd /home/x && bash bin/check_dash_order.sh", "bash check_dash_order.sh"),
                ("cd ~/.ai-station && timeout 400 python3 tools/work_order.py selftest",
                 "python3 work_order.py"),
                ("grep -rn \"def x\" tools/ | head -20", ""),
                ("git status --short", "git status"),
                ("", "")):
            got = command_shape(cmd)
            check(f"F9.2 bentuk {want!r:28} <- {cmd[:44]!r}", got == want, f"dapat {got!r}")
        conn.execute("INSERT INTO action_log (engine, ts, kind, summary, exit_code) VALUES "
                     "('qoder','2026-02-01 00:00:01','command','python3 tools/bad.py',NULL),"
                     "('qoder','2026-02-01 00:00:02','command_exit','aborted=False',2),"
                     "('qoder','2026-02-01 00:00:03','command','python3 tools/bad.py',NULL),"
                     "('qoder','2026-02-01 00:00:04','command_exit','aborted=False',2),"
                     "('qoder','2026-02-01 00:00:05','command','grep nothing_here tools/',NULL),"
                     "('qoder','2026-02-01 00:00:06','command_exit','aborted=False',1),"
                     "('qoder','2026-02-01 00:00:07','command','grep a tools/ | head -3',NULL),"
                     "('qoder','2026-02-01 00:00:08','command_exit','aborted=False',1)")
        conn.commit()
        _fs = failing_shapes(conn, "SQLITE")
        _shapes = {x["shape"] for x in _fs}
        check("F9.2 bentuk yang berulang dan benar-benar gagal masuk laporan",
              "python3 bad.py" in _shapes, str(_fs))
        check("F9.2 grep exit=1 BUKAN anti-pola — itu jawaban 'tidak ada yang cocok'",
              "grep nothing_here" not in _shapes and "grep tools/" not in _shapes, str(_shapes))
        check("F9.2 pipeline tidak dituduhkan ke tahap terakhirnya",
              "head" not in " ".join(_shapes), str(_shapes))
        check("F9.2 laporan menyebut berapa kali dan angka keluar terakhir",
              all({"fails", "last_exit"} <= set(x) for x in _fs), str(_fs[:1]))
        check("F9.2 peringatan menyebut bentuknya secara eksplisit",
              "python3 bad.py" in (shape_warning("python3 bad.py") or ""), str(shape_warning("x")))
        check("F9.2 bentuk kosong tidak menghasilkan peringatan kosong",
              shape_warning("") is None, str(shape_warning("")))
        run(conn, "SQLITE", "DELETE FROM action_log WHERE ts LIKE '2026-02-01%'")
        conn.commit()

        # --- F10.3: batas kuota per malam. Diuji pada pembacanya sendiri, bukan pada tiruan yang
        # disuntik drainer: kalau fungsi ini salah, semua tes drainer di atas tetap hijau karena
        # mereka tidak pernah memanggilnya.
        # Tes ini dulu menuliskan `"gemini-pro" in verification_keys()` — sebuah nama model yang
        # dihafal, tepat di bawah judul yang melarangnya. Suntingan slots.yaml yang sah (memindah
        # decider ke Claude, karena `agy models` hari ini tidak memuat gemini-3.1-pro sama sekali)
        # lalu membuatnya merah, dan itu bukan tes yang menangkap regresi: itu tes yang menangkap
        # perubahan konfigurasi yang benar. Yang diuji sekarang adalah SIFATNYA: himpunan kunci
        # harus sama dengan yang dinyatakan slots.yaml untuk peran verifier/decider, dan setiap
        # kunci harus benar-benar ada di registri. Tidak ada nama model yang dihafal di mana pun.
        _vk = verification_keys()
        try:
            import yaml as _yaml
            _slots_doc = _yaml.safe_load((REPO / "config" / "slots.yaml").read_text())
            _want = set()
            for _team in _slots_doc.get("teams") or []:
                if _team.get("id") != _slots_doc.get("active_team"):
                    continue
                for _r in _team.get("roles") or []:
                    if str(_r.get("role_id") or "").startswith(("verifier", "decider", "resolver")) \
                            and _r.get("registry_key"):
                        _want.add(str(_r["registry_key"]))
            _doc = {"slots": _slots_doc}
            _unknown = sorted(set(_vk) - set(_want))
        except Exception as _exc:  # noqa: BLE001 — tidak terbaca BUKAN berarti cocok
            _want, _unknown = set(_vk), [f"slots.yaml tidak terbaca ({str(_exc)[:40]})"]
        check("kosakata verifikasi dibaca dari registri, bukan dari ingatan",
              len(_vk) >= 3 and not _unknown and set(_vk) == _want,
              f"registri={sorted(_vk)} slots={sorted(_want)} asing={_unknown}")
        # Setiap kunci harus menunjuk mesin yang benar-benar terdaftar. Peran yang menunjuk kunci
        # hantu akan membuat dispatch gagal jauh setelah pemutus antrean diloloskan.
        try:
            _reg = json.loads((REPO / "config" / "engines.json").read_text())
            _eng = _reg.get("engines") or _reg
            _ghost = sorted(k for k in _vk if k not in _eng)
        except Exception as _exc:  # noqa: BLE001
            _ghost = [f"engines.json tidak terbaca ({str(_exc)[:40]})"]
        check("setiap kunci verifikasi menunjuk mesin yang terdaftar, bukan nama hantu",
              not _ghost, str(_ghost)[:160])
        # `action_log` sudah dibuat lebih awal di suite ini (kolom: engine, ts, kind, tab, model,
        # exit_code, summary, raw_ref) dan dipakai tes heartbeat. Jadi blok ini TIDAK membuat atau
        # menjatuhkan tabel — hanya menyisipkan barisnya sendiri, dengan penanda yang bisa dihapus
        # lagi. Versi pertama melakukan `CREATE` lalu `DROP` dan menghancurkan fixture tes lain.
        conn.execute("INSERT INTO action_log (engine, ts, kind) VALUES "
                     "('selftest-quota', '2026-01-01 00:00:00', 'cross_verify')")
        conn.execute("INSERT INTO action_log (engine, ts, kind) VALUES "
                     "('selftest-quota', '2026-01-01 00:00:05', 'cross_verify')")
        conn.execute("INSERT INTO action_log (engine, ts, kind) VALUES "
                     "('selftest-quota', '2026-01-01 00:00:06', 'command')")
        conn.execute("INSERT INTO action_log (engine, ts, kind) VALUES "
                     "('selftest-quota', '2026-01-01 00:00:07', 'cross_verify')")
        conn.commit()
        _mine = "SELECT COUNT(*) AS n FROM action_log WHERE kind='cross_verify' " \
                "AND engine='selftest-quota'"
        _total_mine = int(run(conn, "SQLITE", _mine)[0]["n"])
        _n = verifications_in_window(conn, "SQLITE", 1767225605)
        # Jendela harus MEMOTONG. Bug yang diikat di sini adalah `strftime()` yang mengembalikan
        # TEKS: tanpa CAST, teks melawan integer selalu benar dan penghitung ini melaporkan SEMUA
        # baris lintas waktu, sehingga batas per malam tidak pernah bisa tersentuh.
        check("penghitung verifikasi memotong pada jendela, bukan menghitung semuanya",
              _n is not None and _n < _total_mine, f"dalam jendela={_n} totalpunya saya={_total_mine}")
        check("hanya baris verifikasi yang dihitung, bukan setiap aksi",
              _total_mine == 3, str(_total_mine))
        # Jalur "tidak terukur" diuji pada koneksi terpisah: menjatuhkan tabelnya di sini akan
        # merampas fixture dari tes heartbeat yang sudah berjalan lebih dulu.
        _bare = sqlite3.connect(":memory:")
        _bare.row_factory = sqlite3.Row
        check("tanpa action_log penghitung mengembalikan None, BUKAN nol",
              verifications_in_window(_bare, "SQLITE", 1) is None, "none expected")
        _bare.close()
        # `night_quota_state` dibaca lewat seam: meter SUNGGUHAN tidak boleh menentukan hasil tes,
        # sama seperti `tree_dirty` dan `deliver_now` di atas.
        import cross_verify as _cvq
        _real_gate = _cvq.quota_gate_reason
        _real_ap = globals()["autopilot"]
        try:
            globals()["autopilot"] = lambda conn, engine, *a, **k: {"mode": "ON", "on_epoch": 1}
            _cvq.quota_gate_reason = lambda spec: None
            s_ok = night_quota_state(conn, "SQLITE")
            check("meter masih ada + belum melewati batas = run boleh memulai item baru",
                  s_ok["ok"] and s_ok["measurable"] and not s_ok["notes"], str(s_ok)[:200])
            _cvq.quota_gate_reason = lambda spec: "semua jendela habis"
            s_none = night_quota_state(conn, "SQLITE")
            check("SEMUA jalur verifikasi ditolak meter = item baru tidak dimulai",
                  not s_none["ok"] and any("jalur verifikasi" in w for w in s_none["why"]),
                  str(s_none["why"])[:200])
            # Langit-langit diuji dengan baris bertanda sendiri, di atas action_log yang SUDAH ada.
            # `used >= cap`, bukan `== cap`: tabel ini juga dipakai tes heartbeat, dan menuntut
            # angka persis berarti membuat tes bergantung pada apa yang ditulis tes lain.
            run(conn, "SQLITE", "DELETE FROM action_log WHERE engine='selftest-quota'")
            for i in range(NIGHT_MAX_VERIFICATIONS):
                conn.execute("INSERT INTO action_log (engine, ts, kind) VALUES "
                             "('selftest-quota', ?, 'cross_verify')",
                             ("2026-01-01 00:00:%02d" % i,))
            conn.commit()
            _cvq.quota_gate_reason = lambda spec: None
            s_full = night_quota_state(conn, "SQLITE")
            check("langit-langit dispatch terlampaui = run berhenti memulai item baru, dengan angka",
                  not s_full["ok"] and (s_full["used"] or 0) >= NIGHT_MAX_VERIFICATIONS
                  and any(str(NIGHT_MAX_VERIFICATIONS) in w for w in s_full["why"]),
                  str(s_full)[:220])
            run(conn, "SQLITE", "DELETE FROM action_log WHERE engine='selftest-quota'")
        finally:
            _cvq.quota_gate_reason = _real_gate
            globals()["autopilot"] = _real_ap

        d = decide(conn, "SQLITE", "A atau B?", ["A", "B"], weight="heavy")
        check("keputusan tercatat OPEN", d and d[0]["state"] == "OPEN")
        ok, msg = resolve_decision(conn, "SQLITE", d[0]["id"], "resolver", "gemini", "", "ya")
        check("RESOLVED tanpa model_id ditolak", not ok, msg)
        ok, msg = resolve_decision(conn, "SQLITE", d[0]["id"], "resolver", "gemini",
                                   "gemini-3.8-flash-high", "B", "RESOLVED")
        row = run(conn, "SQLITE", "SELECT * FROM decisions WHERE id=%s"
                  .replace("%s", "?"), (d[0]["id"],))[0]
        check("jawabannya mencatat model yang menjawab", ok and row["model_id"].startswith("gemini")
              and row["state"] == "RESOLVED", str(row["model_id"]))

        # --- F10.1 decision broker: yang diuji adalah gerbangnya, BUKAN dispatch-nya.
        # Satu panggilan run_engine di dalam selftest akan membakar kuota asli dan mengetik
        # ke pane orang, jadi yang diverifikasi di sini adalah jalur yang berhenti sendiri.
        irr = decide(conn, "SQLITE", "Hapus seluruh tabel?", ["ya", "tidak"], weight="irreversible")
        check("pilihan tak bisa dibatalkan tidak pernah dikirim ke resolver",
              irr[0]["state"] == "ESCALATED" and irr[0]["rung_used"] == "human-only",
              f"{irr[0]['state']}/{irr[0]['rung_used']}")
        ok, msg = broker(conn, "SQLITE", irr[0]["id"])
        check("broker menolak menulis ulang baris yang sudah selesai",
              not ok and "kesimpulan" in msg, msg)
        empty = decide(conn, "SQLITE", "A atau B?", [], weight="light")
        ok, msg = broker(conn, "SQLITE", empty[0]["id"])
        check("pertanyaan tanpa pilihan ditolak, tidak ditebak", not ok and "pilihan" in msg, msg)

        # --- empat keputusan yang mati karena bentuk opsi, bukan karena isinya ------------------
        # Terukur 2026-10-15 03:1x: keputusan 41/42/43/44 semua tersimpan sebagai SATU opsi
        # berlabel "A,B,C", karena `--options` dulu hanya memecah pada '|'. Resolver melihat daftar
        # bernomor satu butir sementara pertanyaannya berbicara huruf, jadi dua tingkat menjawab
        # dan keduanya dibuang parser. Yang diuji di bawah adalah tiga lapis tambalnya: pemisah,
        # pembacaan huruf, dan penolakan lebih awal.
        check("--options 'A,B,C' menjadi TIGA pilihan, dan label panjang yang mengandung koma "
              "TIDAK dipecah (label orang utuh, bukan bahan split)",
              [o if isinstance(o, str) else o.get("label") for o in parse_options_arg("A,B,C")]
              == ["A", "B", "C"]
              and len(parse_options_arg("park 14 baris, tanpa menghapus isi")) == 1,
              str(parse_options_arg("A,B,C")))
        idx_b, why_b = parse_choice("PILIHAN: B\nALASAN: paling kecil kerusakan", 3)
        idx_2, _w2 = parse_choice("PILIHAN: 2\nALASAN: sama", 3)
        check("jawaban huruf dibaca sebagai nomor pilihan yang dimaksud — 'PILIHAN: B' == 2, dan "
              "bentuk digit tidak berubah", idx_b == 2 and idx_2 == 2 and "kecil" in why_b,
              f"B->{idx_b} 2->{idx_2}")
        idx_oob, why_oob = parse_choice("PILIHAN: E", 3)
        check("huruf di luar daftar tetap ditolak dengan nama, tidak diam-diam jadi pilihan",
              idx_oob is None and "di luar" in (why_oob or ""), str(why_oob))

        # --- keputusan 45: kepala antrean legacy tidak boleh ditawarkan --------------------------
        # Versi pertama filter ini hanya menyebut "WORKING" dan karena itu TIDAK melakukan apa
        # pun: `norm()` memetakan IN_PROGRESS warisan ke CLAIMED, jadi `next_ready()` tetap
        # mengembalikan baris yang sama dan perbaikannya hanya tampak di diff. Yang diuji di
        # bawah adalah perbedaan itu, plus sisi yang tidak boleh ikut terbuang.
        conn.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin,lease_epoch) "
                     "VALUES ('legacy-stale','IN_PROGRESS','qoder','legacy',0)")
        conn.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin,lease_epoch,claimed_by)"
                     "VALUES ('legacy-dikerjakan','IN_PROGRESS','qoder','legacy',?, 'qoder')",
                     (now() + 600,))
        conn.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin) "
                     "VALUES ('legacy-pending','PENDING','qoder','legacy')")
        conn.commit()
        keep = {r["title"] for r in ready_items(conn, "SQLITE", drop_stale_legacy=True)}
        offer = {r["title"] for r in ready_items(conn, "SQLITE")}
        check("legacy IN_PROGRESS tanpa lease hidup DIBUANG dari penawaran, tapi legacy PENDING "
              "dan legacy yang sedang dikerjakan orang TETAP ada — dan laporan tanpa flag masih "
              "melihat yang dibuang",
              "legacy-stale" not in keep and "legacy-pending" in keep and "legacy-dikerjakan" in keep
              and "legacy-stale" in offer, f"keep={sorted(keep)}")
        one_opt = decide(conn, "SQLITE", "Satu pilihan?", ["cuma ini"], weight="heavy")
        one_id = one_opt[2] if len(one_opt) > 2 else None
        if one_id:
            b_ok, b_msg = broker(conn, "SQLITE", one_id)
            check("broker menolak pertanyaan satu pilihan SEBELUM menghabiskan dispatch — "
                  "empat keputusan malam ini seharusnya mati di sini, bukan di resolver",
                  b_ok is False and "paling sedikit dua" in (b_msg or ""), str(b_msg)[:120])

        # Anak tangga yang tidak bisa dibaca harus dilewati, dan kalau semuanya habis
        # pertanyaannya mendarat ESCALATED — BUKAN dijawab oleh mesin lain yang kebetulan ada.
        # Kunci palsu dipilih sengaja: ia gagal sebelum dispatch, jadi tes ini tidak bisa
        # membakar kuota maupun mengetik ke pane siapa pun.
        orig_rungs = globals()["resolver_rungs"]
        globals()["resolver_rungs"] = lambda: [("resolver", "mesin-yang-tidak-ada")]
        try:
            un = decide(conn, "SQLITE", "A atau B?", ["A", "B"], weight="light")
            ok, msg = broker(conn, "SQLITE", un[0]["id"])
            ur = run(conn, "SQLITE", "SELECT state, model_id, note FROM decisions WHERE id=?",
                     (un[0]["id"],))[0]
            check("resolver tak terbaca -> ESCALATED tanpa model penjawab",
                  ur["state"] == "ESCALATED" and not ur["model_id"]
                  and "engines.json" in (ur["note"] or ""), str(ur))
        finally:
            globals()["resolver_rungs"] = orig_rungs

        rungs = resolver_rungs()
        check("tangga resolver dibaca dari config, urut seperti di slots.yaml",
              [r for r, _k in rungs] == list(RESOLVER_ROLES) and all(k for _r, k in rungs),
              str(rungs))
        import cross_verify as _cv
        reg = _cv.load_registry()
        check("setiap anak tangga menunjuk registry key yang benar-benar ada",
              all(k in reg for _r, k in rungs), str([k for _r, k in rungs if k not in reg]))

        p = decision_prompt("A atau B?", [{"label": "A", "evidence": "ukur 3ms", "cost": "1 GB"},
                                          {"label": "B"}])
        check("prompt membawa bukti dan biaya per pilihan",
              "ukur 3ms" in p and "1 GB" in p and "tidak diukur" in p)
        check("prompt memuat aturan menolak yang tak bisa dibatalkan", "PILIHAN: TIDAK" in p)
        check("prosa tanpa baris PILIHAN bukan keputusan",
              parse_choice("menurut saya dua-duanya bagus", 2)[0] is None)
        check("nomor di luar jangkauan ditolak", parse_choice("PILIHAN: 7", 2)[0] is None)
        check("penolakan eksplisit terbaca sebagai penolakan",
              "menolak" in (parse_choice("PILIHAN: TIDAK", 2)[1] or ""))

        # Regresi nyata dari uji pertama broker (decision 3, pane antigravity): teks yang
        # kembali adalah prompt + jawaban, dan parser membaca baris contoh di prompt sebagai
        # jawaban. Yang berbahaya bukan nomornya — yang berbahaya adalah penolakan palsu.
        pr = decision_prompt("A atau B?", [{"label": "A"}, {"label": "B"}])
        echoed = pr + "\n▸ Thought for 10s, 714 tokens\nPILIHAN: 2\nALASAN: lebih hemat\nBUKTI: 8x4=32\n"
        i2, d2 = parse_choice(answer_tail(echoed, pr), 2)
        check("gema prompt dibuang: nomor dan alasan datang dari jawaban, bukan instruksi",
              i2 == 2 and d2.startswith("lebih hemat"), f"{i2}/{d2}")
        i3, d3 = parse_choice(answer_tail(pr + "\n", pr), 2)
        check("pane yang hanya menggemakan prompt bukan keputusan, dan bukan penolakan",
              i3 is None and "TIDAK" not in (d3 or ""), str(d3))
        check("tanpa pemotongan, kalimat aturan kita sendiri terbaca sebagai penolakan",
              "menolak" in (parse_choice(pr, 2)[1] or ""), str(parse_choice(pr, 2)))
        stt_irr = status(conn, "SQLITE")
        check("baris ESCALATED muncul di antrean yang dibaca GUI",
              any(x["id"] == irr[0]["id"] for x in stt_irr["decisions_open"]),
              str([x["id"] for x in stt_irr["decisions_open"]]))
        # Tab Human Decide membaca kolom ini satu per satu. Kalau query di status() berhenti
        # menyertainya, halaman tidak error — ia cuma menampilkan baris kosong, dan yang
        # dilihat operator adalah pertanyaan tanpa alasan. Jadi kolomnya diassert.
        need = {"id", "question", "options", "weight", "state", "rung_used", "model_id", "note",
                "item_id"}
        sample = next((x for x in stt_irr["decisions_open"] if x["state"] != "OPEN"), None)
        check("setiap kolom yang dibaca tab Human Decide ikut terkirim",
              sample is not None and need <= set(sample), str(sorted(set(sample or {}))))

        # Jalur TULIS operator. Antrean yang hanya bisa dibaca adalah pajangan: kontrak menuntut
        # manusia menjawab, jadi penjawab manusia harus punya kolomnya sendiri di baris yang sama.
        # Item khusus dipakai supaya pertanyaan tes tidak menumpang pada B, yang sudah punya
        # pertanyaannya sendiri dari eskalasi di atas.
        conn.execute("INSERT INTO quest_tasks (title, status) VALUES ('answer-host','HUMAN')")
        conn.commit()
        host = int(run(conn, "SQLITE",
                        "SELECT id FROM quest_tasks WHERE title='answer-host'")[0]["id"])
        q_b = decide(conn, "SQLITE", "Timer dinyalakan malam ini?",
                     [{"label": "ya", "evidence": "log satu malam", "cost": "kuota"},
                      {"label": "nanti", "evidence": "belum ada yang mengawasi", "cost": "—"}],
                     "heavy", host)[0]["id"]
        check("pertanyaan bisa ditautkan ke item yang menunggunya",
              _decision_row(conn, "SQLITE", q_b).get("item_id") == host,
              str(_decision_row(conn, "SQLITE", q_b).get("item_id")))
        check("item dengan pertanyaan terbuka terdeteksi sebelum ada yang mengetik",
              (open_decision_for(conn, "SQLITE", host) or {}).get("id") == q_b,
              str((open_decision_for(conn, "SQLITE", host) or {}).get("id")))
        ok_a, msg_a, row_a = answer_decision(conn, "SQLITE", q_b, "ya, nyalakan", "operator-selftest")
        check("jawaban orang tercatat sebagai jawaban orang",
              ok_a and row_a["state"] == "ANSWERED" and row_a["answered_by"] == "operator-selftest"
              and row_a["rung_used"] == "human", msg_a)
        check("setelah dijawab, itemnya tidak lagi menunggu",
              open_decision_for(conn, "SQLITE", host) is None,
              str(open_decision_for(conn, "SQLITE", host)))
        ok_a2, msg_a2, _ = answer_decision(conn, "SQLITE", q_b, "nanti saja")
        check("jawaban kedua ditolak tanpa menimpa yang pertama",
              not ok_a2 and "sudah" in msg_a2
              and _decision_row(conn, "SQLITE", q_b)["answer"] == "ya, nyalakan", msg_a2)
        check("jawaban kosong tidak menulis apa pun",
              not answer_decision(conn, "SQLITE", q_b, "   ")[0])
        ok_a3, msg_a3, _ = answer_decision(conn, "SQLITE", 9_999_999, "x")
        check("id asing ditolak sebelum ada UPDATE", not ok_a3 and "tidak ada" in msg_a3, msg_a3)
        check("jawaban dapat dibaca balik dari itemnya",
              any(d["id"] == q_b and d["state"] == "ANSWERED" for d in
                  decisions_for_item(conn, "SQLITE", host)),
              str(len(decisions_for_item(conn, "SQLITE", host))))
        stt_ans = status(conn, "SQLITE")
        check("yang sudah dijawab dilaporkan sebagai riwayat, bukan hilang",
              any(d["id"] == q_b for d in stt_ans["decisions_answered"])
              and not any(d["id"] == q_b for d in stt_ans["decisions_open"]),
              str([d["id"] for d in stt_ans["decisions_answered"]]))

        item = get_item(conn, "SQLITE", ids["A"])
        check("resume_count ada", "resume_count" in item)
        # alias: baris lama tidak ditulis ulang, hanya dibaca sebagai CLAIMED
        conn.execute("INSERT INTO quest_tasks (title, status) VALUES ('legacy','IN_PROGRESS')")
        conn.commit()
        lg = run(conn, "SQLITE", "SELECT status FROM quest_tasks WHERE title='legacy'")[0]["status"]
        check("IN_PROGRESS terbaca utuh, tidak ditulis ulang", lg == "IN_PROGRESS")
        check("alias memetakannya ke CLAIMED", norm(lg) == "CLAIMED")

        lease = int(get_item(conn, "SQLITE", ids["A"])["lease_epoch"] or 0)
        check("lease disimpan sebagai epoch", lease > now(), str(lease))

        stt = status(conn, "SQLITE")
        check("status punya semua bagian yang dibaca GUI",
              all(k in stt for k in ("autopilot", "drain", "night", "limits", "per_status",
                                     "human_queue", "decisions_open", "decisions_answered",
                                     "stalled", "next")),
              str(sorted(stt)))
        check("laporan GUI turunan, bukan karangan: tree kotor berarti night.ok false",
              tree_dirty() is not True or not stt["night"]["ok"], str(stt["night"]))

        # --- circuit breaker: loop yang gagal cepat tidak boleh menguras kuota malam ---
        st = night_state(conn, "SQLITE", dirty=True)
        check("tree kotor menahan klaim", not st["ok"] and "kotor" in " ".join(st["why"]), str(st))
        # None sekarang berarti "diukur, tapi git tidak bisa dibaca" — bukan "belum diukur".
        st = night_state(conn, "SQLITE", dirty=None)
        check("UNKNOWN bukan berarti bersih", not st["ok"] and "UNKNOWN" in " ".join(st["why"]), str(st["why"]))
        dirty_now = tree_dirty()
        check("tree_dirty memberi jawaban terbaca", dirty_now in (True, False, None), str(dirty_now))

        conn.execute("UPDATE autopilot_state SET mode='ON', on_epoch=?, item_budget=12, "
                     "until_epoch=? WHERE id=1", (now(), now() + 3600))
        conn.commit()
        orig_tree_dirty = globals()["tree_dirty"]
        globals()["tree_dirty"] = lambda *_a: False
        try:
            for k in range(NIGHT_MAX_FAILURES):
                conn.execute("INSERT INTO quest_tasks (title, status, completed_at) "
                             "VALUES (?, 'FAILED', datetime('now'))", (f"gagal{k}",))
            conn.commit()
            conn.execute("INSERT INTO quest_tasks (title, status) VALUES ('setelah gagal','PENDING')")
            conn.commit()
            nid = run(conn, "SQLITE", "SELECT id FROM quest_tasks WHERE title='setelah gagal'")[0]["id"]
            ok, msg = claim(conn, "SQLITE", nid, "qoder")
            check("3 kegagalan mematikan klaim berikutnya", not ok and "circuit breaker" in msg, msg)
            st = night_state(conn, "SQLITE", dirty=False)
            check("batas kegagalan terlihat di night_state", not st["ok"], str(st["why"]))
            # --- sifat yang diminta operator: satu malam panjang tidak boleh mati ---
            # Ini bukan variasi dari tes di atas; ini sisi lain dari pagar yang sama. Tiga
            # kegagalan yang SELING-BERSELING dengan keberhasilan adalah mesin yang sedang
            # bekerja, bukan mesin yang mengunyah satu baris rusak. Di bawah perhitungan lama
            # ("semua kegagalan dalam 12 jam") keadaan ini menutup loop dan komputer diam
            # sampai pagi — persis waktu yang dia bilang tidak boleh terbuang.
            conn.execute("UPDATE quest_tasks SET completed_at=datetime('now','-3 days') "
                         "WHERE status='COMPLETED'")
            conn.execute("DELETE FROM quest_tasks WHERE title LIKE 'gagal%'")
            conn.commit()
            for k in range(4):
                conn.execute("INSERT INTO quest_tasks (title,status,completed_at) "
                             "VALUES (?,?,datetime('now'))", (f"selang-gagal{k}", "FAILED"))
                conn.execute("INSERT INTO quest_tasks (title,status,completed_at) "
                             "VALUES (?,?,datetime('now'))", (f"selang-sukses{k}", "COMPLETED"))
            conn.commit()
            st_mix = night_state(conn, "SQLITE", dirty=False)
            check("4 gagal + 4 sukses berselang-seling TIDAK menutup loop — yang menutup hanya "
                  "rentetan tanpa satu pun keberhasilan",
                  st_mix["ok"] and consecutive_failures(conn, "SQLITE") == 0,
                  f"{st_mix['why']} beruntun={consecutive_failures(conn, 'SQLITE')}")
            # Sisi lain dari perataan sedetik, dan ini yang membuat `>` bukan jawaban. Loop di atas
            # berakhir pada keberhasilan, jadi kegagalan SESUDahnya pada detik yang sama harus tetap
            # dihitung — kalau tidak, mesin yang mengunyah satu baris rusak dalam satu detik yang
            # sama dengan keberhasilan terakhir tidak akan pernah ditutup, dan pagar itu hanya
            # berbunyi pada malam yang lambat. Di bawah `>` angkanya 0; di bawah `>=` keempat
            # kegagalan di atas ikut terseret jadi 4. Hanya (detik, id) yang memberi 1.
            conn.execute("INSERT INTO quest_tasks (title,status,completed_at) "
                         "VALUES ('selang-gagal-tail','FAILED',datetime('now'))")
            conn.commit()
            check("kegagalan pada DETIK yang sama dengan keberhasilan terakhir, tapi ditulis "
                  "sesudahnya, tetap dihitung — dan hanya itu yang dihitung",
                  consecutive_failures(conn, "SQLITE") == 1,
                  f"beruntun={consecutive_failures(conn, 'SQLITE')}")
            # Baris COMPLETED tanpa completed_at itu nyata di database lama, dan tanpa filter
            # `IS NOT NULL` ia MENANG di ORDER BY DESC. Dengan id terbesar di tabel saat ini,
            # tanpa filter itu rentetan terbaca sebagai "sejak nol" — pemutus menutup loop yang
            # sedang bekerja dengan baik. Baris ini diberi id tertinggi secara sengaja.
            conn.execute("INSERT INTO quest_tasks (title,status,completed_at) "
                         "VALUES ('selang-sukses-null','COMPLETED',NULL)")
            conn.commit()
            check("COMPLETED tanpa completed_at tidak menyamar jadi keberhasilan terakhir",
                  consecutive_failures(conn, "SQLITE") == 1,
                  f"beruntun={consecutive_failures(conn, 'SQLITE')}")
            conn.execute("DELETE FROM quest_tasks WHERE title LIKE 'selang-%'")
            conn.commit()
            for k in range(NIGHT_MAX_FAILURES):
                conn.execute("INSERT INTO quest_tasks (title,status,completed_at) "
                             "VALUES (?,?,datetime('now'))", (f"rentetan{k}", "FAILED"))
            conn.commit()
            check("rentetan SUNGGUHAN tanpa keberhasilan tetap menutup loop",
                  consecutive_failures(conn, "SQLITE") >= NIGHT_MAX_FAILURES
                  and not night_state(conn, "SQLITE", dirty=False)["ok"],
                  str(night_state(conn, "SQLITE", dirty=False)["why"]))
            conn.execute("DELETE FROM quest_tasks WHERE title LIKE 'rentetan%'")
            conn.commit()
        finally:
            globals()["tree_dirty"] = orig_tree_dirty

        # --- anggaran item per jendela: diturunkan, dan jendelanya benar-benar dipakai ---
        # Jebakan yang diikat di sini terukur, bukan dicurigai: `strftime('%%s', ...)` menghasilkan
        # TEKS '%s' di SQLite (parameternya tidak diinterpolasi di sana), dan SQLite membandingkan
        # teks melawan integer dengan aturan "teks selalu lebih besar" — filter jendela yang begitu
        # menjadi benar untuk semua baris, selamanya, tanpa pernah menampilkan kesalahan apa pun.
        check("strftime('%%s') menghasilkan teks, bukan angka",
              run(conn, "SQLITE", "SELECT strftime('%%s', '2026-01-01 00:00:00') AS v")[0]["v"] == "%s",
              str(run(conn, "SQLITE", "SELECT strftime('%%s', '2026-01-01 00:00:00') AS v")))
        check("strftime yang BENAR pun mengembalikan teks — tanpa CAST jendela mana pun selalu benar",
              run(conn, "SQLITE",
                  "SELECT typeof(strftime('%s', '2026-01-01 00:00:00')) AS t")[0]["t"] == "text")
        conn.execute("INSERT INTO quest_tasks (title, status, completed_at) "
                     "VALUES ('selesai-lama', 'COMPLETED', datetime('now','-3 days'))")
        conn.commit()
        total = run(conn, "SQLITE",
                    "SELECT COUNT(*) AS n FROM quest_tasks WHERE status='COMPLETED'")[0]["n"]
        check("jendela di masa depan tidak menampung apa pun — pembuktian filternya hidup",
              items_done_in_window(conn, "SQLITE", now() + 3600) == 0,
              str(items_done_in_window(conn, "SQLITE", now() + 3600)))
        check("item yang selesai tiga hari lalu tidak dihitung untuk jendela yang baru dibuka",
              total > 0 and items_done_in_window(conn, "SQLITE", now()) < total,
              f"total={total} in_window={items_done_in_window(conn, 'SQLITE', now())}")
        check("tanpa jendela ON, tidak ada angka yang dikarang",
              items_done_in_window(conn, "SQLITE", 0) == 0)
        before = items_done_in_window(conn, "SQLITE", now() - 30)
        conn.execute("INSERT INTO quest_tasks (title, status, completed_at) "
                     "VALUES ('selesai-baru', 'COMPLETED', datetime('now'))")
        conn.commit()
        check("item yang baru selesai menambah hitungan satu, tidak lebih",
              items_done_in_window(conn, "SQLITE", now() - 30) == before + 1,
              f"before={before} after={items_done_in_window(conn, 'SQLITE', now() - 30)}")
        before_ap = autopilot(conn, "SQLITE")["items_done"]
        conn.execute("INSERT INTO quest_tasks (title, status, completed_at) "
                     "VALUES ('selesai-lagi', 'COMPLETED', datetime('now'))")
        conn.commit()
        ap = autopilot(conn, "SQLITE")
        check("angka yang dibaca kedua permukaan bergerak bersama ledger, bukan kolom mati",
              ap["items_done"] == before_ap + 1 and ap["items_done_stored"] == 0,
              f"before={before_ap} {ap}")

        # --- bentuk gerbang yang terbukti tidak membuktikan apa pun ---
        # Diukur 2026-10-08 02:58 di mesin ini: `systemctl --user list-timers irsofka-tidak-ada.timer`
        # keluar 0 dengan tabel kosong. Karena drainer menutup item atas angka keluar, gerbang model
        # ini akan menandai pekerjaannya sendiri COMPLETED tanpa melakukan apa pun — laporan yang
        # ditulis tangan tanpa mengaku menulis tangan (§12). Jadi bentuknya yang dilarang, dicek.
        vacuous = [title for _k, title, _p, _d, gate in ROADMAP_ITEMS
                   if "list-timers" in (gate or "")]
        check("tidak ada gerbang ROADMAP yang berupa list-timers telanjang", not vacuous, str(vacuous))
        # Satu kelas lagi, ditemukan dengan mengukurnya: gerbang f109 versi pertama membaca
        # action_log.summary dengan LIKE, dan lolos karena kalimat laporan engine sendiri memuat
        # kata-kata itu. Prosa bukan peristiwa — gerbang tidak boleh membuktikan dirinya dengan teks.
        prose = [title for _k, title, _p, _d, gate in ROADMAP_ITEMS
                 if "action_log" in (gate or "") and "LIKE" in (gate or "").upper()]
        check("tidak ada gerbang ROADMAP yang dibuktikan oleh prosa action_log", not prose, str(prose))
        no_gate = [title for _k, title, _p, _d, gate in ROADMAP_ITEMS if not (gate or "").strip()]
        check("setiap butir ROADMAP yang di-seed membawa gerbang", not no_gate, str(no_gate))

        # --- Dashboard: SEMI, asal baris, dan identitas proyek ----------------
        # Kegagalan yang diincar blok ini bukan crash, melainkan DUA laporan yang berbeda: tombol
        # yang menulis satu nilai tapi layar membacakan nilai lain, antrean roadmap yang ikut
        # dikerjakan "karena toh sudah antri", dan dua tempat yang boleh menyatakan proyek mana
        # yang aktif.
        # Ketiganya tidak kelihatan dari layar — karena itu ketiganya diuji di sini.
        conn.execute("UPDATE quest_tasks SET completed_at=datetime('now','-2 days') "
                     "WHERE status IN ('FAILED','PARKED')")
        conn.commit()

        ap = autopilot(conn, "SQLITE", "SEMI", minutes=30, who="selftest")
        check("SEMI tersimpan sebagai SEMI dengan tangan turun",
              ap["mode"] == "SEMI" and ap["level"] == "observe", str(ap))
        ap = autopilot(conn, "SQLITE", "semii", who="selftest")
        check("nilai mode yang tidak dikenal tidak mengubah apa pun (invarian 6: typo bukan izin)",
              ap.get("refused") and ap["mode"] == "SEMI" and ap["level"] == "observe", str(ap))
        autopilot(conn, "SQLITE", "SEMI", minutes=30, who="selftest")

        seed(conn, "SQLITE", [("kRM", "RM-only", "build", None, "true")])
        rm_id = int(run(conn, "SQLITE", "SELECT id FROM quest_tasks WHERE title='RM-only'")[0]["id"])
        ok, msg, op_id = queue_command(conn, "SQLITE", "perintah bro", "qoder", "", str(REPO))
        check("queue menulis perintah operator ke antrean yang sama", ok and op_id, msg)
        pick = next_item(conn, "SQLITE", origin=ORIGIN_OPERATOR)
        check("SEMI hanya melihat perintah operator di antrean",
              pick and pick["origin"] == ORIGIN_OPERATOR and int(pick["id"]) == op_id, str(pick))
        check("tanpa origin antrean tetap apa pun — tidak ada pemanggil lama yang berubah",
              next_item(conn, "SQLITE") is not None)
        ok, msg = claim(conn, "SQLITE", rm_id, "drainer", dirty=False)
        check("SEMI menolak mengklaim item roadmap di pintu, bukan hanya di penyaring",
              not ok and "SEMI" in msg, msg)
        ok, msg = claim(conn, "SQLITE", op_id, "drainer", dirty=False)
        check("SEMI boleh mengklaim perintah operator", ok, msg)
        ok, msg = claim(conn, "SQLITE", op_id, "drainer", dirty=True)
        check("working tree kotor menutup SEMI juga — §6 tidak punya mode pengecualian",
              not ok and "working tree kotor" in msg, msg)
        for i in range(NIGHT_MAX_FAILURES):
            conn.execute("INSERT INTO quest_tasks (title,status,completed_at) "
                         "VALUES (?,?,datetime('now'))", (f"semi-fail-{i}", "FAILED"))
        conn.commit()
        ok, msg, op2 = queue_command(conn, "SQLITE", "perintah setelah pemutus", "qoder")
        # `dirty=False` di sini bukan kenyamanan: tanpa itu pesan penolakan bisa berasal dari tree
        # yang sedang disunting dan ceknya tetap hijau karena kata "circuit breaker" muncul.
        # Yang sedang dibuktikan adalah bahwa TIGA BARIS GAGAL menutup SEMI, jadi hanya itu yang
        # boleh menutupnya.
        blocked = claim(conn, "SQLITE", op2, "drainer", dirty=False)
        # Kata yang diuji adalah kata yang ditulis kode, dan angkanya ikut: kontrak pemutus bilang
        # `claim` menolak DENGAN angka yang menjatuhkannya, jadi penolakan tanpa angka itu adalah
        # pagar yang berbunyi tanpa mengatakan apa yang dimakannya.
        check("pemutus malam menutup SEMI persis seperti menutup ON",
              not blocked[0] and "kegagalan beruntun" in blocked[1]
              and str(NIGHT_MAX_FAILURES) in blocked[1], str(blocked))

        ok, msg, _ = queue_command(conn, "SQLITE", "tanpa engine", "")
        check("queue menolak baris tanpa cli_engine alih-alih memilih favorit",
              not ok and "cli_engine" in msg, msg)
        ok, msg, _ = queue_command(conn, "SQLITE", "engine hantu", "engine-yang-tidak-ada")
        check("queue menolak engine yang tidak terdaftar dan menyebut yang ada",
              not ok and "qoder" in msg, msg)
        ok, msg, _ = queue_command(conn, "SQLITE", "proyek hantu", "qoder", "", "/tmp/tidak-ada-x")
        check("queue menolak direktori proyek yang tidak ada", not ok, msg)

        check("baris tanpa ws_path mengerjakan REPO persis seperti dulu",
              resolve_workspace({"ws_path": None}) == REPO and resolve_workspace({}) == REPO)
        check("baris dengan ws_path mengerjakan direktorinya sendiri — gerbang dan engine di tree "
              "yang sama, bukan laporan tentang orang lain",
              resolve_workspace({"ws_path": "/tmp"}) == Path("/tmp").resolve(),
              str(resolve_workspace({"ws_path": "/tmp"})))

        ok, msg, ws_id = create_workspace(conn, "SQLITE", "Proyek Uji", "/tmp")
        check("proyek didaftarkan", ok and ws_id, msg)
        listed = list_workspaces(conn, "SQLITE")
        planted = next((w for w in listed["workspaces"] if w["id"] == ws_id), None)
        check("baris registry tidak menciptakan fakta fokus: is_focus mengikuti symlink, apa pun "
              "isyarat di tabel",
              planted is not None
              and planted["is_focus"] == (Path("/tmp").resolve() == focused_workspace()),
              str(planted))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(workspaces)")]
        check("workspaces tidak punya kolom active — satu definisi fokus, bukan dua",
              "active" not in cols, str(cols))
        ok, msg, _ = create_workspace(conn, "SQLITE", "Nama Lain", "/tmp")
        same = run(conn, "SQLITE", "SELECT id FROM workspaces WHERE path=?",
                   (str(Path("/tmp").resolve()),))
        check("path UNIQUE: proyek yang sama tidak terdaftar dua kali di bawah dua nama",
              ok and len(same) == 1, f"{msg} rows={len(same)}")
        ok, msg = focus_workspace(conn, "SQLITE", "proyek-yang-tidak-ada")
        check("focus menolak nama tak terdaftar sebelum menyentuh symlink", not ok, msg)
        # Regresi yang ditemukan lewat data hidup, bukan lewat tes: dedupe pedoman semula memakai
        # NAMA RELATIF, dan karena proyek Ai-Workstation punya AGENTS.md sendiri, AGENTS.md milik
        # workstation lenyap dari daftar. Pedoman yang berlaku di semua proyek hilang hanya karena
        # memakai huruf yang sama.
        clash = Path("/tmp/ws_clash")
        clash.mkdir(exist_ok=True)
        (clash / "ROADMAP.md").write_text("proyek", encoding="utf-8")
        det = detect_guidelines(clash)
        pairs = {(g["path"], g["canonical"]) for g in det}
        check("proyek dan workstation boleh punya berkas bernama sama — keduanya tetap terdaftar",
              ("ROADMAP.md", False) in pairs and ("ROADMAP.md", True) in pairs, str(sorted(pairs)))
        check("pedoman tidak menawarkan berkas penunjuk yang dimulai dengan titik",
              not [g for g in det if Path(g["abs"]).name.startswith(".")], str(det))
        abs_rm = str((clash / "ROADMAP.md").resolve())
        ok, msg, ws2 = create_workspace(conn, "SQLITE", "Proyek Clash", str(clash))
        check("proyek kedua terdaftar di samping yang pertama", ok and ws2 and ws2 != ws_id, msg)
        # --- pedoman TERKUNCI (perintah operator 2026-10-08 11:05) ---
        # Yang diuji bukan isinya, melainkan ketiadaan jalan: kalau masih ada satu pun fungsi yang
        # bisa menulis daftar ini, "terkunci" cuma label di layar.
        bound = locked_guides()
        check("kontrak yang mengikat selalu ada dan tidak bisa kosong karena pilihan layar",
              {"documents/AGENTS.md", "ROADMAP.md"} <= {g["path"] for g in bound}
              and all(g["locked"] and g["canonical"] for g in bound), str(bound))
        # Kunci yang menyebut berkas yang tidak ada BUKAN kunci. Yang menemukannya duluan adalah
        # daftar ini menyebut "AGENTS.md" di root REPO, tempat berkas itu memang tidak pernah ada.
        gone = [g["path"] for g in bound if not g["exists"]]
        check("setiap nama yang dikunci benar-benar ada di disk", not gone, str(gone))
        check("nama yang hilang dilaporkan, tidak disaring diam-diam dari daftar",
              len(bound) == len(LOCKED_GUIDES), f"{len(bound)} dari {len(LOCKED_GUIDES)}")
        check("tidak ada lagi jalur tulis untuk pedoman", "set_guidelines" not in globals(),
              "set_guidelines masih ada — kunci bisa dibuka dari kode")
        ok, msg = guidelines_are_locked()
        check("permintaan mengubah pedoman ditolak dengan alasan, bukan diam",
              not ok and "TERKUNCI" in msg and "LOCKED_GUIDES" in msg, msg)
        # Ditemukan audit Gemini: pelepasan klaim mengosongkan claimed_by tapi meninggalkan
        # model_assigned, jadi baris PENDING masih menyebut mesin yang tidak lagi memegangnya —
        # laporan yang salah tentang siapa yang bekerja, yang justru bentuk kegagalan §12.
        conn.execute("INSERT INTO quest_tasks (title,status,cli_engine,claimed_by,lease_epoch,"
                     "model_assigned) VALUES ('uji-lepas-nama','WORKING','qoder','qoder',?,'qoder')",
                     (now() + 600,))
        conn.commit()
        rel_id = int(run(conn, "SQLITE",
                         "SELECT id FROM quest_tasks WHERE title='uji-lepas-nama'")[0]["id"])
        release_claim(conn, "SQLITE", rel_id, "qoder")
        after_release = get_item(conn, "SQLITE", rel_id)
        check("melepas klaim juga melepas nama mesin yang tercatat di barisnya",
              not after_release.get("model_assigned"), str(after_release.get("model_assigned")))

        # Jalur PostgreSQL (kunci advisory + finally) tidak bisa dijalankan oleh fixture SQLite.
        # Yang ditegakkan di sini adalah STRUKTURNYA: klaim berada di dalam try yang finally-nya
        # melepas kunci, sehingga tidak ada jalur return yang bisa keluar sambil memegang lock.
        import inspect as _ins
        _cs = _ins.getsource(claim)
        _code = "\n".join(l for l in _cs.splitlines() if not l.strip().startswith("#"))
        _try_at = _code.index("    try:")
        _claim_at = _code.index("run(conn, engine, q,")
        _unlock_at = _code.index("pg_advisory_unlock")
        check("kunci advisory dilepas di finally, bukan di jalur normal yang bisa dilewati return",
              _try_at < _claim_at < _unlock_at and "finally:" in _code[_try_at:_unlock_at],
              f"try={_try_at} claim={_claim_at} unlock={_unlock_at}")
        check("mode dari pemanggil MENAMBAL baris, bukan menggantikannya",
              "ap = autopilot(conn, engine) or {}" in _code
              and 'ap["mode"] = wanted_mode' in _code, "penggantian buta masih ada")

        # Round-trip. Audit Gemini menemukan bahwa gerbang mencari `sha=` sementara JSON menulis
        # `"sha":` — keduanya tidak akan pernah bertemu, dan aku sebelumnya menyimpulkan "rc=1
        # berarti benar" padahal kedua sisi gagal. Jadi yang diuji di sini adalah peristiwa SUNGGUHAN
        # yang ditulis emitter SUNGGUHAN, dibaca gerbang SUNGGUHAN.
        import tempfile as _tf
        _tmpdir = Path(_tf.mkdtemp(prefix="f109_roundtrip_"))
        _tmpdir.mkdir(parents=True, exist_ok=True)
        _ev_file = _tmpdir / "station_events.jsonl"
        _row = {"kind": "push_verified", "sha": "abc1234def", "model": "gemini-3.8-flash-high",
                "verdict": "PASS"}
        _ev_file.write_text(json.dumps(_row) + "\n", encoding="utf-8")
        _pat = _row["kind"], _row["sha"], _row["model"]
        _txt = _ev_file.read_text(encoding="utf-8")
        check("peristiwa push_verified terbaca sebagai teks yang dicari gerbang (round-trip)",
              ('\"kind\": "push_verified"'.replace("\\", "") in _txt)
              and '"sha": "abc1234def"' in _txt and '"model": "gemini-3.8-flash-high"' in _txt
              and _pat[0] == "push_verified",
              _txt[:120])
        _ev_file.unlink(missing_ok=True); _tmpdir.rmdir()
        gate109 = [i[4] for i in ROADMAP_ITEMS if i[0] == "f109"][0]
        # Yang dulu diperiksa adalah regex di dalam string gerbang. Sekarang gerbang mendelegasi ke
        # `push-gate`, jadi yang benar-benar harus dipastikan: ia mendelegasi (bukan lagi menggrep
        # berkas secara global), dan pembacanya menguraikan JSON — bukan mencocokkan teks.
        check("gerbang f109 mendelegasi ke pemilik katanya, bukan menggrep berkas peristiwa lagi",
              "push-gate" in gate109 and "grep -q" not in gate109, gate109[:120])
        import inspect as _ins
        _pg = _ins.getsource(push_events) + _ins.getsource(push_gate)
        check("pembaca peristiwa menguraikan JSON dan menolak bentuk `sha=` polos",
              "json.loads" in _pg and '\"sha=\"' not in _pg and "sha=" not in _pg,
              "pembacaan peristiwa bukan JSON")

        # Gerbang F10.3 pernah menuntut `is-enabled`, yaitu menuntut unit yang menyala sendiri
        # setelah boot — persis yang operator larang. Sifat yang ingin dijaga: gerbang tidak boleh
        # MEMINTA keadaan "menyala sendiri". Versi pertama cek ini mencari substring `is-enabled`
        # dan menyimpulkan dari keberadaannya, dan itu salah tuduh: gerbang yang sekarang menulis
        # `! systemctl --user is-enabled …` justru MENOLAK keadaan itu, yang merupakan bentuk
        # terkuat dari aturan yang sama. Jadi yang ditanya adalah tanda `!` di depannya, bukan
        # kemunculan katanya.
        def _demands_timer_enabled(gate):
            # Negasi shell menempel di AWAL KLAUSA, bukan di depan kata yang dinegasinya:
            # `! systemctl --user is-enabled x` menegasi seluruh perintah. Versi pertama cek ini
            # mencari `!` yang berakhir tepat sebelum `is-enabled` dan karena itu GAGAL pada
            # gerbang yang benar — ketahuan oleh cek dua arah di bawah, yang memang untuk itu ada.
            for clause in (gate or "").split("&&"):
                c = clause.strip()
                if "is-enabled" in c and not c.startswith("!"):
                    return True
            return False
        gate103 = [i[4] for i in ROADMAP_ITEMS if i[0] == "f103"][0]
        check("gerbang F10.3 tidak menuntut timer yang menyala sendiri setelah boot",
              not _demands_timer_enabled(gate103) and "! systemctl --user is-enabled" in gate103
              and "irsofka-autopilot.timer" in gate103
              and ("is-active" in gate103 and "drainer-" in gate103), gate103[:140])
        check("cek itu sendiri bisa salah: bentuk positif `is-enabled` tanpa `!` tetap tertangkap",
              _demands_timer_enabled("systemctl --user is-enabled --quiet x.timer") is True
              and _demands_timer_enabled("! systemctl --user is-enabled --quiet x.timer") is False,
              "negation-aware check must go both ways")
        # Dan keadaan yang sama harus benar di LEDGER, bukan hanya di sumber Python: gerbang yang
        # dibaca mesin adalah kolom check_command di barisnya. Kalau sumber diperbaiki tapi barisnya
        # tidak disegarkan, yang berjalan tetap grep lama — dan itu sudah terjadi malam ini.
        seed(conn, "SQLITE", ROADMAP_ITEMS)   # baris roadmap nyata, supaya tes di bawah punya objek
        _g103 = run(conn, "SQLITE",
                    "SELECT check_command AS c FROM quest_tasks WHERE title LIKE 'F10.3%'")
        # `not _g103 or ...` akan lulus tanpa satu baris pun — fixture SQLite memang tidak selalu
        # punya baris F10.3, dan tes yang hijau karena tidak menemukan apa yang dicari bukan tes.
        # Jadi kehadirannya dituntut lebih dulu, lewat seed yang sama yang dipakai produksi.
        check("satu baris F10.3 benar-benar ada di ledger untuk diperiksa", bool(_g103),
              "tidak ada baris F10.3 di fixture")
        check("baris ledger tidak menyimpan gerbang lama yang menuntut is-enabled",
              bool(_g103) and not _demands_timer_enabled(_g103[0]["c"])
              and "! systemctl --user is-enabled" in (_g103[0]["c"] or "")
              and "irsofka-autopilot.timer" in (_g103[0]["c"] or ""),
              str((_g103[0]["c"] if _g103 else "")[:120]))

        # --- gerbang F10.5: harus mengukur mimpi, bukan keberadaan berkas ------------------------
        gate105 = [i[4] for i in ROADMAP_ITEMS if i[0] == "f105"][0]
        check("gerbang F10.5 menjalankan selftest modul mimpi, bukan hanya test -f",
              "dream_phase.py selftest" in gate105 and "dream_phase.py" in gate105, gate105[:160])
        check("gerbang F10.5 menuntut kedua unit mimpi terpasang",
              "irsofka-dream.timer" in gate105 and "irsofka-dream.service" in gate105, gate105[:160])
        check("gerbang F10.5 tidak menuntut timer mimpi yang menyala sendiri setelah boot",
              not _demands_timer_enabled(gate105) and "! systemctl --user is-enabled" in gate105,
              gate105[:160])
        check("gerbang F10.5 menuntut bukti satu putaran sungguh mengeluarkan baris, bukan janji",
              "has-digest" in gate105 and "is-active" in gate105, gate105[:200])
        _g105 = run(conn, "SQLITE",
                    "SELECT check_command AS c FROM quest_tasks WHERE title LIKE 'F10.5%'")
        check("baris ledger F10.5 menyimpan gerbang baru, bukan gerbang mustahil lama",
              bool(_g105) and "dream_phase.py selftest" in (_g105[0]["c"] or "")
              and "mustahil" not in (_g105[0]["c"] or ""), str(_g105)[:160])

        # --- pagar per-sha: dua grep global lulus selamanya, ini tidak ---------------------------
        # Dijalankan di atas repo fixture, bukan repo nyata. Yang diuji adalah aturan pembanding
        # sha, dan aturan itu tidak boleh bergantung pada berapa commit yang kebetulan sedang
        # menunggu di mesin ini.
        _d3 = Path(_tf.mkdtemp(prefix="pushgate_"))
        _e3 = _d3 / "events.jsonl"
        _repo3, _sha_shipped, _sha_pending = fixture_git_repo(_d3 / "src")
        _e3.write_text("", encoding="utf-8")
        g_empty = push_gate(events_path=_e3, repo=_repo3)
        check("tanpa satu peristiwa pun, commit yang belum berangkat dianggap BELUM dinyatakan "
              "(diukur pada repo fixture: unpushed=1, undelivered=sha itu persis)",
              g_empty["ok"] is False and g_empty["declared"] is False
              and g_empty["unpushed"] == 1 and g_empty["undelivered"] == [_sha_pending[:12]],
              str(g_empty["why"])[:120])
        held_rows = record_push_held([_sha_pending], "no-verdict", events_path=_e3)
        g_held = push_gate(events_path=_e3, repo=_repo3)
        # Dua kata yang berbeda, dan tes ini yang menegakkan perbedaannya: dinyatakan ≠ boleh
        # dorong. Ronde kesembilan membaca yang pertama sebagai yang kedua.
        check("satu catatan `ditahan dengan alasan` menutup celah DITOLAK-TANPA-SEBAB, tapi tidak "
              "membuatnya boleh didorong",
              g_held["declared"] is True and g_held["ok"] is False
              and g_held["pushable"] == [] and len(held_rows) == 1
              and g_held["held_pending"] == [_sha_pending[:12]], str(g_held)[:160])
        check("penahan tanpa alasan tidak dihitung sebagai pernyataan",
              record_push_held(["abc1234def"], "  ", events_path=_e3) == [], "alasan kosong")
        _e3.write_text("", encoding="utf-8")
        _e3.write_text(json.dumps({"kind": "push_verified", "sha": _sha_pending[:12],
                                   "model": "gemini-3.8-flash-high", "verdict": "FAIL"}) + "\n",
                       encoding="utf-8")
        g_fail = push_gate(events_path=_e3, repo=_repo3)
        check("peristiwa push_verified dengan verdict FAIL BUKAN izin — nilai verdict dibaca, "
              "bukan hanya kata kuncinya",
              g_fail["ok"] is False and g_fail["cleared"] == 0
              and g_fail["undelivered"] == [_sha_pending[:12]], str(g_fail)[:160])
        _e3.write_text("", encoding="utf-8")
        _e3.write_text(json.dumps({"kind": "push_verified", "sha": "0" * 40,
                                   "model": "m", "verdict": "SETUJU"}) + "\n", encoding="utf-8")
        g_other = push_gate(events_path=_e3, repo=_repo3)
        check("vonis untuk sha lain tidak membebaskan commit yang menganggur (pengikatan per-sha)",
              g_other["ok"] is False and g_other["unpushed"] == 1
              and g_other["undelivered"] == [_sha_pending[:12]], str(g_other["why"])[:120])
        # dan sisi positifnya, karena pagar yang hanya pernah dites merah tidak terbukti pagar:
        # vonis SETUJU yang menyebut sha yang benar membuka gerbang, dan commit yang SUDAH
        # berangkat tidak ikut dihitung sebagai utang.
        _e3.write_text("", encoding="utf-8")
        _e3.write_text(json.dumps({"kind": "push_verified", "sha": _sha_pending,
                                   "model": "claude-opus", "verdict": "SETUJU"}) + "\n",
                       encoding="utf-8")
        g_go = push_gate(events_path=_e3, repo=_repo3)
        check("vonis SETUJU pada sha yang benar MELULUSKAN gerbang — termasuk saat peristiwa "
              "menyebut sha panjang dan git menyebut sha pendek (dinormalisasi ke 12)",
              g_go["ok"] is True and g_go["declared"] is True and g_go["blocking"] == []
              and g_go["pushable"] == [_sha_pending[:12]], str(g_go)[:200])
        # satu berkas kerja yang belum di-commit menutupnya lagi, di atas vonis yang sama.
        # Selftest memasang `tree_dirty -> False` di seluruh jalurnya (kalau tidak, ratusan klaim
        # fixture ditolak karena berkas yang sedang diedit), jadi cek ini MEMASANG ULANG fungsi
        # aslinya khusus untuk repo fixture — dan melepaskannya lagi. Tanpa itu yang terukur adalah
        # sambungannya, bukan gerbangnya. Nama `orig_tree_dirty` TIDAK dipakai di sini: blok
        # pemutus malam di atas mengambil nama itu saat global-nya sudah disambat, jadi ia sekarang
        # menunjuk sambatan. Yang dipasang adalah salinan fungsi asli yang diambil sebelum apa pun
        # menyambatnya.
        globals()["tree_dirty"] = tree_dirty_real
        try:
            (Path(_repo3) / "kerja-orang-lain.txt").write_text("jangan disentuh\n", encoding="utf-8")
            g_dirty_fixture = push_gate(events_path=_e3, repo=_repo3)
        finally:
            globals()["tree_dirty"] = lambda *_a: False
        check("tree kotor menahan dorongan sekalipun vonisnya sudah lolos — dan ia menyebut "
              "tree, bukan sha", g_dirty_fixture["ok"] is False
              and g_dirty_fixture["tree_dirty"] is True
              and any("kotor" in w for w in g_dirty_fixture["blocking"]),
              str(g_dirty_fixture["blocking"])[:150])
        _e3.unlink(missing_ok=True)
        import shutil as _sh
        _sh.rmtree(_d3, ignore_errors=True)

        # --- push tertunda harus menjadi keadaan yang tercatat ---
        # §6 menjanjikan "commit lokal + push masuk digest pagi" bila verifier tidak tercapai.
        # F10.9 dibuka justru karena janji seperti itu mengikat sejauh engine mengingatnya, jadi
        # yang diuji di sini adalah bahwa laporan itu ADA dan ANGKANYA TURUNAN.
        ps = push_state()
        check("digest melaporkan push tertunda sebagai angka dari git, bukan dari ingatan",
              ps["readable"] is True and isinstance(ps["unpushed"], int)
              and isinstance(ps["verifier_verdicts"], int), str(ps))
        check("daftar commit ikut dilaporkan, supaya 'belum ter-push' bisa diperiksa satu per satu",
              ps["unpushed"] == 0 or len(ps["commits"]) == min(ps["unpushed"], 20), str(ps["commits"][:3]))
        # Hanya baris KODE yang boleh diperiksa. Versi pertama memindai seluruh sumber dan
        # gagal karena komentarnya sendiri menyebut "action_log" untuk menolaknya — kelas
        # false positive yang sama seperti `claim(` di kalimat penasihat, untuk ketiga kalinya
        # hari ini. Sebuah pagar yang bisa dipicu oleh prosa bukan pagar, itu kebisingan.
        code_lines = [ln for ln in globals()["_push_src"].splitlines()
                      if not ln.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        check("verdict hanya diterima dari peristiwa push_verified, bukan dari prosa laporan",
              '"kind": "push_verified"' in code_only and "action_log" not in code_only,
              "push_state membaca sumber yang salah")

        # --- vonis tidak bisa ditulis dari sini; hanya bisa dibaca dari catatan verifier ---------
        # Ini jawaban untuk temuan audit ronde keempat: `record_push_verified` versi pertama
        # menerima `verdict="PASS"` sebagai PARAMETER dengan nilai dasar. Engine mana pun bisa
        # menulis clears-nya sendiri tanpa bertanya kepada siapa pun, dan gerbang akan lolos karena
        # baris peristiwa itu ada — persis keputusan 6 yang F10.9 dibuat untuk menegakkannya.
        import tempfile as _tf2
        _d2 = Path(_tf2.mkdtemp(prefix="f109_clear_"))
        _ev2 = _d2 / "events.jsonl"
        head = subprocess_run_sha()
        conn.execute("DELETE FROM action_log WHERE kind='cross_verify'")
        conn.commit()
        ok0, why0, w0 = record_push_verified(conn, "SQLITE", "gemini-verify", [head],
                                             events_path=_ev2)
        check("tanpa catatan verifikasi sama sekali, izin DITOLAK dan nol peristiwa ditulis",
              ok0 is False and w0 == [] and not _ev2.exists(), str(why0)[:120])
        conn.execute("INSERT INTO action_log (engine, ts, kind, tab, model, exit_code, summary) "
                     "VALUES ('station', datetime('now'), 'cross_verify', 'gemini-verify', "
                     "'gemini-3.8-flash-high', 0, 'verifikasi: COMPLETED verdict=PERLU_KOREKSI')")
        conn.commit()
        ok1, why1, w1 = record_push_verified(conn, "SQLITE", "gemini-3.8-flash-high", [head],
                                             events_path=_ev2)
        check("vonis PERLU_KOREKSI BUKAN izin — walau tercatat COMPLETED oleh model yang benar",
              ok1 is False and not _ev2.exists(), str(why1)[:120])
        conn.execute("INSERT INTO action_log (engine, ts, kind, tab, model, exit_code, summary) "
                     "VALUES ('station', datetime('now'), 'cross_verify', 'gemini-verify', "
                     "'gemini-3.8-flash-high', 1, 'verifikasi: FAILED verdict=SETUJU')")
        conn.commit()
        ok1b, _w, _e = record_push_verified(conn, "SQLITE", "gemini-3.8-flash-high", [head],
                                            events_path=_ev2)
        check("baris exit_code!=0 tidak dihitung sebagai izin walau teksnya menyebut SETUJU",
              ok1b is False, str(ok1b))
        conn.execute("DELETE FROM action_log WHERE kind='cross_verify'")
        # Vonis harus menunjuk BERKAS BUKTI yang memuat sha-nya. Tanpa ikatan itu, satu pemeriksaan
        # apa pun yang lolos — bahkan soal aritmatika — memberi izin mendorong commit mana pun,
        # karena yang dicari hanyalah "ada baris SETUJU dari model ini dalam 3 jam".
        _evd = _d2 / "evidence.diff"
        # Satu sha per baris manifest — bentuk yang sama dengan yang ditulis generator bukti
        # sungguhan. Substring bebas tidak cukup: sha yang muncul di tengah komentar diff bukan
        # pernyataan cakupan.
        _evd.write_text("# MANIFEST\n# " + head + "\n\ndiff --git a/x b/x\n", encoding="utf-8")
        conn.execute("INSERT INTO action_log (engine, ts, kind, tab, model, exit_code, summary, "
                     "raw_ref) VALUES ('station', datetime('now'), 'cross_verify', 'gemini-verify', "
                     "'gemini-3.8-flash-high', 0, 'verifikasi: COMPLETED verdict=SETUJU', ?)",
                     (str(_evd),))
        conn.commit()
        okx, whyx, _ex = record_push_verified(conn, "SQLITE", "gemini-3.8-flash-high",
                                              ["0123456789abcdef0123456789abcdef01234567"],
                                              events_path=_ev2)
        check("vonis yang berkas buktinya tidak menyebut sha ini TIDAK membebaskan push",
              okx is False and "bukan untuk commit ini" in whyx and not _ev2.exists(),
              str(whyx)[:150])
        ok2, why2, w2 = record_push_verified(conn, "SQLITE", "gemini-3.8-flash-high",
                                             [head[:8], "zzzzzzz"], events_path=_ev2)
        check("sha yang bukan commit di repo ini membatalkan SEMUA penulisan, bukan hanya miliknya",
              ok2 is False and w2 == [] and not _ev2.exists(), str(why2)[:140])
        ok3, why3, w3 = record_push_verified(conn, "SQLITE", "gemini-3.8-flash-high", [head],
                                             events_path=_ev2)
        line = _ev2.read_text(encoding="utf-8").strip() if _ev2.exists() else ""
        check("dengan vonis SETUJU yang nyata dan sha yang nyata, satu peristiwa ditulis "
              "dan bentuknya dibaca gerbang f109",
              ok3 is True and len(w3) == 1 and '"kind": "push_verified"' in line
              and f'"sha": "{head}"' in line, line[:160])
        check("peristiwa mencatat kapan vonisnya diberikan, supaya izin basi bisa dilihat",
              w3 and w3[0].get("verified_at"), str(w3[:1]))
        # Pemalsuan lewat kutipan: ringkasan verification record menyimpan potongan jawaban model
        # setelah pemisah " :: ", dan potongan itu bisa memuat teks apa pun — termasuk baris vonis
        # yang dipetik dari prompt. Izin hanya boleh dibaca dari BIDANG vonis, bukan dari kutipan.
        conn.execute("DELETE FROM action_log WHERE kind='cross_verify'")
        conn.execute("INSERT INTO action_log (engine, ts, kind, tab, model, exit_code, summary) "
                     "VALUES ('station', datetime('now'), 'cross_verify', 'gemini-verify', "
                     "'forger', 0, "
                     "'verifikasi: COMPLETED verdict=PERLU_KOREKSI :: kutipan: verdict=SETUJU "
                     "bila bersih')")
        conn.commit()
        okf, _wf, _ef = record_push_verified(conn, "SQLITE", "forger", [head], events_path=_ev2)
        check("jawaban yang MEMUAT 'verdict=SETUJU' sebagai kutipan tidak memberi izin",
              okf is False, str(okf))
        conn.execute("DELETE FROM action_log WHERE kind='cross_verify'")
        conn.commit()
        _evd.unlink(missing_ok=True)
        _ev2.unlink(missing_ok=True); _d2.rmdir()
        conn.execute("DELETE FROM action_log WHERE kind='cross_verify'")
        conn.commit()

        # --- jebakan yang membuat baris tak bisa diklaim selamanya ---
        # Premisnya harus dinyatakan: kegagalan dari blok sebelumnya masih di jendela 12 jam dan
        # akan membuka pemutus, jadi klaim ini akan ditolak karena alasan yang bukan diuji di sini.
        conn.execute("UPDATE quest_tasks SET completed_at=datetime('now','-2 days') "
                     "WHERE status IN ('FAILED','PARKED')")
        conn.commit()
        conn.execute("INSERT INTO quest_tasks (title,status,cli_engine,claimed_by,lease_epoch) "
                     "VALUES ('jebakan-null-lease','PENDING','qoder','hantu',NULL)")
        conn.commit()
        nul_id = int(run(conn, "SQLITE",
                         "SELECT id FROM quest_tasks WHERE title='jebakan-null-lease'")[0]["id"])
        ok_n, msg_n = claim(conn, "SQLITE", nul_id, "qoder", dirty=False, mode="ON")
        check("baris dengan claimed_by terisi tapi lease_epoch NULL TETAP BISA diklaim "
              "— tanpa COALESCE ia terkunci selamanya", ok_n, msg_n)
        conn.execute("INSERT INTO quest_tasks (title,status,cli_engine,claimed_by,lease_epoch) "
                     "VALUES ('sudah-selesai','COMPLETED','qoder','pihak-x',0)")
        conn.commit()
        fin_id = int(run(conn, "SQLITE",
                         "SELECT id FROM quest_tasks WHERE title='sudah-selesai'")[0]["id"])
        release_claim(conn, "SQLITE", fin_id, "pihak-x")
        stayed = get_item(conn, "SQLITE", fin_id)
        check("pelepasan klaim yang terlambat tidak bisa menimpa baris yang sudah divonis",
              norm(stayed["status"]) == "COMPLETED", str(stayed["status"]))

        semi = semi_status(conn, "SQLITE")
        check("layar melaporkan mana yang MENGIKAT, dan ia sama dengan yang diikat kode",
              semi["guidelines_locked"] is True
              and [g["path"] for g in semi["guidelines_binding"]] == [g["path"] for g in bound],
              str(semi["guidelines_binding"]))
        check("MD yang hanya ADA di proyek dilaporkan terpisah, tidak menyamar jadi pedoman",
              "guidelines_present" in semi
              and {g["path"] for g in semi["guidelines_present"]} != {g["path"] for g in bound},
              str([g["path"] for g in semi["guidelines_present"]][:4]))
        semi = semi_status(conn, "SQLITE")
        check("semi_status menurunkan antrean operator, daftar proyek, dan tab terdaftar",
              semi["operator_pending"] >= 1 and isinstance(semi["workspaces"], list)
              and semi["known_engines"],
              str({k: semi[k] for k in ("operator_pending", "known_engines")}))
    finally:
        globals()["tree_dirty"] = orig_tree_dirty
        globals()["banner"] = orig_banner
        globals()["deliver_now"] = orig_deliver
        globals()["_record_send_events"] = orig_send_events
        conn.close()
        tmp.unlink(missing_ok=True)

    check("peristiwa kirim tes tertangkap penampung, dan penulis log produksi tidak pernah dipanggil",
          len(send_events) >= 1 and globals()["_record_send_events"] is orig_send_events,
          f"tertangkap={len(send_events)}")

    check("pertanyaan yang butuh manusia benar-benar mengirim satu banner darurat — jalur ini bukan "
          "kode yang lulus karena tidak ada yang memantau",
          any(a == "ESCALATED" and u for a, _d, u in banners), str(banners[:3]))
    check("selftest tidak pernah menyentuh desktop: semua banner tercatat, none dikirim",
          all(isinstance(b, str) for _a, b, _u in banners) and banners, str(len(banners)))

    idle_selftest(check)
    dirty_tree_selftest(check)

    bad = [c for c in checks if not c[1]]
    for name, okval, detail in checks:
        print(f"[{'ok    ' if okval else 'GAGAL '}] {name}" + (f" — {detail}" if detail and not okval else ""))
    print(f"\nwork_order selftest: {len(checks) - len(bad)}/{len(checks)} sesuai")
    return 1 if bad else 0


def dirty_tree_selftest(check):
    """Pemutus tree kotor: apa yang boleh dan tidak boleh disentuh, diuji dua sisi.

    Percobaan 2026-10-15 untuk mengecualikan baris `operator` dari klausul ini DIBATALKAN setelah
    menabrak tes lama yang menulis alasannya sendiri — §6 tidak punya mode pengecualian. Yang
    tersisa dari percobaan itu adalah bagian yang tidak menabrak apa pun dan menutup celah nyata:
    `push_gate`, satu-satunya pintu keluar, sebelumnya buta terhadap tree. Jadi yang diuji di
    bawah: klausul tree masih bisa dipisah datanya, SEMUA klaim (operator termasuk) tetap tertutup,
    dan mendorong sekarang menolak di atas tree kotor — tempat bahaya itu memang berada.
    """
    import sqlite3 as _sq
    tmp = Path(f"/tmp/dirty_tree_{now()}.db")
    c = _sq.connect(str(tmp)); c.row_factory = sqlite3.Row
    try:
        ensure_schema(c, "SQLITE")
        c.execute("UPDATE autopilot_state SET mode='ON', on_epoch=?, until_epoch=?, level='dispatch' "
                  "WHERE id=1", (now() - 60, now() + 7200))
        c.commit()
        st = night_state(c, "SQLITE", dirty=True)
        check("tree kotor masih MENUTUP loop secara umum (ok=False, `why` tidak berubah bentuk)",
              st["ok"] is False and any("kotor" in w for w in st["why"]), str(st["why"])[:120])
        check("klausul tree bisa dipisah: ia ada di `dirty_why` dan TIDAK di `blocking`",
              any("kotor" in w for w in st["dirty_why"])
              and not any("kotor" in w for w in st["blocking"]), str(st)[:160])

        def _mk(origin):
            c.execute("INSERT INTO quest_tasks (title, status, cli_engine, origin) "
                      "VALUES (?, 'PENDING', 'qoder', ?)", (f"baris {origin}", origin))
            c.commit()
            return int(c.execute("SELECT id FROM quest_tasks WHERE title=?",
                                 (f"baris {origin}",)).fetchone()[0])

        ok_op, why_op = claim(c, "SQLITE", _mk(ORIGIN_OPERATOR), who="qoder", dirty=True, mode="ON")
        ok_rd, why_rd = claim(c, "SQLITE", _mk(ORIGIN_ROADMAP), who="qoder", dirty=True, mode="ON")
        ok_id, why_id = claim(c, "SQLITE", _mk(ORIGIN_IDLE), who="qoder", dirty=True, mode="ON")
        # Tiga-tiganya DITOLAK, dan itu bukan kebetulan yang belum sempat dilonggarkan. Percobaan
        # 2026-10-15 untuk mengecualikan baris operator dari klausul ini ditabrakkan pada tes lama
        # `working tree kotor menutup SEMI juga — §6 tidak punya mode pengecualian` dan dibatalkan:
        # §6 tidak mengenal mode pengecualian. Yang diubah di sini hanya menambah penjaga di
        # `push_gate`, tempat bahaya tree kotor sebenarnya hidup.
        check("tree kotor menutup klaim operator JUGA — §6 tidak punya pengecualian, dan itu "
              "sudah diuji; yang menahan perintah orang adalah tree itu sendiri, bukan kodenya",
              ok_op is False and "kotor" in (why_op or ""), str(why_op)[:120])
        check("pekerjaan yang dimulai mesin juga tertutup",
              ok_rd is False and "kotor" in (why_rd or ""), str(why_rd)[:120])
        check("kerja idle juga tertutup", ok_id is False and "kotor" in (why_id or ""),
              str(why_id)[:120])

        orig_td = globals()["tree_dirty"]
        try:
            globals()["tree_dirty"] = lambda *_a: True
            g_dirty = push_gate()
            globals()["tree_dirty"] = lambda *_a: False
            g_clean = push_gate()
        finally:
            globals()["tree_dirty"] = orig_td
        check("PUSH sekarang menolak di atas tree kotor — gerbang keluar yang sebelumnya buta",
              g_dirty["ok"] is False and g_dirty["tree_dirty"] is True
              and any("kotor" in w for w in g_dirty.get("why", [])), str(g_dirty.get("why"))[:150])
        check("dan ia tidak lagi menyebut tree kotor kalau tree bersih — kelaian bukan tameng",
              g_clean["tree_dirty"] is False
              and not any("kotor" in w for w in g_clean.get("why", [])), str(g_clean.get("why"))[:150])
    finally:
        c.close()
        tmp.unlink(missing_ok=True)


def idle_selftest(check):
    """Tes dua sisi untuk kerja idle, di fixture SQLite sendiri.

    Urutannya sengaja: sisi POSITIF dijalankan lebih dulu. Tanpa itu, setiap tes negatif di
    bawah akan hijau untuk alasan yang salah — fungsi yang tidak pernah bisa membuat baris
    juga tidak pernah bisa ditolak oleh keempat pagarnya.
    """
    import sqlite3 as _sq
    tmp = Path(f"/tmp/idle_selftest_{now()}.db")
    c = _sq.connect(str(tmp))
    c.row_factory = sqlite3.Row
    ev: list = []
    emit = lambda a, **kw: ev.append((a, kw))
    import tempfile as _tf
    _broot = Path(_tf.mkdtemp(prefix="idle_brain_")) / "brain"
    _broot.mkdir(parents=True, exist_ok=True)
    try:
        ensure_schema(c, "SQLITE")
        # `ensure_schema` sudah memasang baris id=1, jadi jendela ON-nya ditulis lewat UPDATE —
        # INSERT ke sini akan menabrak UNIQUE(id) dan tesnya mati karena fixture, bukan karena kode.
        c.execute("UPDATE autopilot_state SET mode='ON', on_epoch=?, until_epoch=?, level='dispatch' "
                  "WHERE id=1", (now() - 60, now() + 7200))
        # `skills_inventory` TIDAK dibuat `ensure_schema` — ia milik daemon, sama seperti
        # `action_log`. Di produksi konsekuensinya sudah benar: tabel yang tidak ada membuat
        # sumbernya tidak terbaca, dan kalau tidak ada satu pun sumber yang terbaca jalurnya
        # melaporkan IDLE_DEBT_UNKNOWN (urgent), bukan mengarang pekerjaan. Di tes, tabelnya
        # dibuat di sini supaya sisi positifnya menguji perilaku, bukan ketiadaan skema.
        c.execute("CREATE TABLE IF NOT EXISTS skills_inventory (id INTEGER PRIMARY KEY, name TEXT, "
                  "category TEXT, level INTEGER, description TEXT, file_path TEXT, "
                  "auto_learned INTEGER DEFAULT 0, use_count INTEGER DEFAULT 0, "
                  "created_at TIMESTAMP, updated_at TIMESTAMP)")
        # `incident_log` juga dibuat: tanpa tabelnya salah satu sumber SELALU tidak terbaca, dan
        # itu membuat kesimpulan "tidak ada utang" mustahil diambil — jalurnya akan melaporkan
        # UNKNOWN, yang benar tapi tidak bisa diuji. Jadi kedua sumber dibuat terbaca lebih dulu,
        # lalu baru dikosongkan untuk menguji sisi diam.
        c.execute("CREATE TABLE IF NOT EXISTS incident_log (error_signature VARCHAR(255) PRIMARY KEY, "
                  "first_seen TIMESTAMP, last_seen TIMESTAMP, repeat_count INTEGER DEFAULT 1, "
                  "last_error_text TEXT, resolved BOOLEAN DEFAULT 0, auto_skill_id VARCHAR(50))")
        c.execute("INSERT INTO skills_inventory (name, category, level, description, file_path, "
                  "auto_learned, use_count) VALUES ('idle_probe_skill', 'coding', 1, 'x', '', 0, 1)")
        c.commit()

        def _idle_count(where=""):
            r = run(c, "SQLITE", f"SELECT COUNT(*) AS n FROM quest_tasks WHERE origin='idle' {where}")
            return int((r[0]["n"] if r else 0) or 0)

        pos = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        check("kerja idle NYATA membuat satu baris ketika ada utang terukur "
              "(kalau ini gagal, semua tes negatif di bawah tidak berarti apa pun)",
              pos is not None and _idle_count() == 1
              and pos.get("origin") == ORIGIN_IDLE and pos.get("phase") == "idle"
              and pos.get("cli_engine") == "qoder", str(pos)[:120])
        check("baris idle membawa bukti mengapa ia ada, bukan judul kosong",
              any(a == "IDLE_QUEUED" and k.get("kind") == "skill_no_file" for a, k in ev),
              str([a for a, _ in ev]))

        ev.clear()
        again = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        check("satu baris idle terbuka sudah cukup — panggilan kedua tidak menumpuk yang kedua",
              again is None and _idle_count("AND status='PENDING'") == 1
              and any(a == "IDLE_ALREADY_OPEN" for a, _ in ev), str([a for a, _ in ev]))

        # tutup barisnya, lalu biarkan mesin memegang pekerjaan hidup
        # `completed_at` diisi dari Python: `now()` bukan fungsi SQLite, dan menulisnya di dalam
        # SQL akan membuat tes gagal karena sintaks, bukan karena perilakunya.
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run(c, "SQLITE", "UPDATE quest_tasks SET status='COMPLETED', completed_at=?, "
            "evidence='tes' WHERE origin='idle'", (_ts,))
        c.commit()
        c.execute("INSERT INTO quest_tasks (title, status, cli_engine, origin, lease_epoch, claimed_by) "
                  "VALUES ('pekerjaan nyata', 'WORKING', 'qoder', 'roadmap', ?, 'qoder')", (now() + 600,))
        c.commit()
        ev.clear()
        busy = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        check("mesin yang sedang memegang item hidup tidak ditumpuki kerja idle",
              busy is None and _idle_count("AND status='PENDING'") == 0
              and any(a == "IDLE_ENGINE_BUSY" for a, _ in ev), str([a for a, _ in ev]))
        run(c, "SQLITE", "DELETE FROM quest_tasks WHERE title='pekerjaan nyata'")
        c.commit()

        # Setengah dari anggaran diisi oleh baris yang GAGAL, bukan selesai. Ini inti koreksinya:
        # menghitung hanya COMPLETED membuat pola buat→gagal→buat-ulang tidak pernah menghabiskan
        # anggaran, dan satu-satunya rem tersisa adalah pemutus malam — terlalu jauh untuk sebuah
        # loop yang seharusnya berhenti sendiri.
        for i in range(IDLE_MAX_ITEMS):
            _st = 'FAILED' if i % 2 else 'COMPLETED'
            c.execute("INSERT INTO quest_tasks (title, status, cli_engine, origin, completed_at, "
                      "evidence) VALUES (?, ?, 'qoder', 'idle', ?, 'tes')",
                      (f"idle lama {i} {_st}", _st, _ts))
        c.commit()
        ev.clear()
        spent = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        # `>=`, bukan `==`: baris idle dari tes positif di atas ikut selesai sebelum dihapus, jadi
        # penghitungnya bisa lewat dari angka pas. Yang diuji adalah "anggaran habis dan itu
        # dilaporkan", bukan kebetulan angkanya persis sama dengan konstantanya.
        check(f"anggaran idle ({IDLE_MAX_ITEMS}/jendela) menahan pengayaan-diri memakan malam, "
              "DAN kegagalan ikut dihitung bukan hanya keberhasilan",
              spent is None and any(a == "IDLE_BUDGET_SPENT" and k.get("used", 0) >= IDLE_MAX_ITEMS
                                    for a, k in ev), str([a for a, _ in ev]))
        run(c, "SQLITE", "DELETE FROM quest_tasks WHERE origin='idle'")
        c.execute("DELETE FROM skills_inventory")
        c.commit()
        ev.clear()
        none = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        check("tanpa utang yang terukur mesin DIAM — ia tidak membuat pekerjaan supaya terlihat sibuk",
              none is None and _idle_count() == 0 and any(a == "IDLE_NO_DEBT" for a, _ in ev),
              str([a for a, _ in ev]))

        # dan baris idle yang dibuat harus melewati keranjang yang sama, bukan memotong jalan
        c.execute("INSERT INTO skills_inventory (name, category, level, description, file_path, "
                  "auto_learned, use_count) VALUES ('idle_probe_skill', 'coding', 1, 'x', '', 0, 1)")
        c.commit()
        row = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        rid = int(row["id"])
        c.execute("UPDATE autopilot_state SET mode='SEMI' WHERE id=1")
        c.commit()
        ok_semi, why_semi = claim(c, "SQLITE", rid, who="drainer", dirty=False, mode="SEMI")
        check("SEMI menolak baris idle dengan alasan keranjang — pengayaan-diri hanya milik ON",
              ok_semi is False and "perintah operator" in (why_semi or ""), str(why_semi)[:120])
        c.execute("UPDATE autopilot_state SET mode='ON' WHERE id=1")
        c.commit()
        ok_on, why_on = claim(c, "SQLITE", rid, who="drainer", dirty=False, mode="ON")
        check("ON mengklaim baris idle lewat `claim()` yang sama — tidak ada jalur kedua "
              "dengan pagar sendiri", ok_on is True, str(why_on)[:120])

        # --- regresi blocker: `backend` adalah jenis DB, `cli_engine` adalah mesin tujuan --------
        # Versi pertama fungsi ini memakai SATU parameter untuk dua arti, jadi di produksi baris
        # idle akan ditulis dengan cli_engine='POSTGRESQL' — mesin yang tidak ada. Tes yang
        # menambalnya dulu juga salah, dan dengan cara yang lebih sunyi lagi: ia memanggil
        # backend="POSTGRESQL" di atas koneksi SQLite, jadi yang menolak panggilan itu adalah
        # placeholder `%s` yang tidak dikenali, bukan pagarnya — hijau tanpa membuktikan apa pun.
        # Yang diuji sekarang dijalankan sampai ke gerbang registri: tujuannya disebut dengan nama
        # backend, dan yang diharapkan adalah penolakan yang MENYEBUT REGISTRI, plus sisi
        # positifnya — utang yang sama dengan tujuan sah harus benar-benar menulis baris.
        run(c, "SQLITE", "DELETE FROM quest_tasks WHERE origin='idle'")
        c.commit()
        ev.clear()
        ghost = ensure_idle_item(c, "SQLITE", "SQLITE", emit=emit, brain_root=_broot)
        leaked = run(c, "SQLITE", "SELECT COUNT(*) AS n FROM quest_tasks WHERE cli_engine IN "
                                 "('SQLITE','POSTGRESQL')")
        by_registry = any(a == "IDLE_DEBT_UNKNOWN" and "registri" in str(k.get("why") or "")
                          for a, k in ev)
        check("backend yang dipakai sebagai mesin tujuan ditolak OLEH GERBANG REGISTRI — alasannya "
              "harus menyebut registri, bukan kegagalan SQL",
              ghost is None and by_registry
              and int((leaked[0]["n"] if leaked else 0) or 0) == 0,
              str([a for a, _ in ev])[:160])

        # Sisi yang membuat penolakan di atas berarti: fungsi yang sama, utang yang sama, tujuan
        # yang terdaftar — dan satu baris tertulis. Tanpa ini, "ditolak" bisa berarti apa saja.
        ev.clear()
        legit = ensure_idle_item(c, "SQLITE", "qoder", emit=emit, brain_root=_broot)
        check("utang yang sama dengan tujuan TERDAFTAR menulis satu baris — penolakan di atas "
              "bukti gerbang, bukan bukti jalur yang mati",
              legit is not None and legit.get("cli_engine") == "qoder", str(legit)[:120])

        # --- DONE -> lanjut, bukan DONE -> diam -------------------------------------------------
        run(c, "SQLITE", "DELETE FROM quest_tasks WHERE origin='idle'")
        c.commit()
        c.execute("INSERT INTO skills_inventory (name,category,level,description,file_path,"
                  "auto_learned,use_count) VALUES ('probe2','coding',1,'x','',0,1)")
        c.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin) "
                  "VALUES ('pekerjaan nyata selesai','PENDING','qoder','roadmap')")
        c.commit()
        real_id = int(c.execute("SELECT id FROM quest_tasks WHERE title='pekerjaan nyata selesai'"
                                ).fetchone()[0])
        ev.clear()
        okf, _msg = finish(c, "SQLITE", real_id, "COMPLETED", evidence="gerbang hijau 82/82",
                           followup=True, emit=emit)
        fu = run(c, "SQLITE", "SELECT id,phase,origin FROM quest_tasks WHERE phase='followup'") or []
        check("DONE mengantrikan lanjutan: satu item selesai di ON langsung melahirkan putaran "
              "berikutnya, bukan diam", okf and len(fu) == 1
              and fu[0]["origin"] == ORIGIN_IDLE and any(a == "FOLLOWUP_QUEUED" for a, _ in ev),
              str([a for a, _ in ev])[:150])

        # baris IDLE yang selesai tidak boleh melahirkan penerusnya
        run(c, "SQLITE", "DELETE FROM quest_tasks WHERE phase='followup'")
        c.commit()
        c.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin) "
                  "VALUES ('idle selesai','PENDING','qoder','idle')")
        c.commit()
        idle_id = int(c.execute("SELECT id FROM quest_tasks WHERE title='idle selesai'"
                                ).fetchone()[0])
        ev.clear()
        finish(c, "SQLITE", idle_id, "COMPLETED", evidence="selesai", followup=True, emit=emit)
        still = run(c, "SQLITE", "SELECT COUNT(*) AS n FROM quest_tasks WHERE phase='followup'")
        check("baris idle yang selesai TIDAK melahirkan penerusnya — kalau iya, setiap lanjutan "
              "akan mengantrikan lanjutan berikutnya selamanya",
              int((still[0]["n"] if still else 0) or 0) == 0, str([a for a, _ in ev])[:150])

        # dan anggaran tetap langit-langitnya — diisi CAMPURAN, bukan hanya keberhasilan.
        # Ini pengunci `idle_budget_used`: `queue_followup` dulu menghitung HANYA COMPLETED dan
        # TANPA batas jendela, jadi tiga kegagalan tidak memakan apa pun dan baris yang selesai
        # di jendela lain yang sudah lewat ikut terhitung selamanya.
        for i in range(IDLE_MAX_ITEMS):
            _st = 'FAILED' if i % 2 else 'COMPLETED'
            c.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin,completed_at,"
                      "evidence) VALUES (?, ?, 'qoder', 'idle', ?, 'x')",
                      (f"idle penuh {i} {_st}", _st, _ts))
        c.commit()
        # dan satu baris idle yang selesai SEBELUM jendela ini tidak boleh ikut memakan sisa
        c.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin,completed_at,evidence) "
                  "VALUES ('idle jendela lama','COMPLETED','qoder','idle',datetime('now','-2 days'),'x')")
        c.commit()
        c.execute("INSERT INTO quest_tasks (title,status,cli_engine,origin) "
                  "VALUES ('satu lagi','PENDING','qoder','roadmap')")
        c.commit()
        another = int(c.execute("SELECT id FROM quest_tasks WHERE title='satu lagi'").fetchone()[0])
        ev.clear()
        finish(c, "SQLITE", another, "COMPLETED", evidence="x", followup=True, emit=emit)
        over = run(c, "SQLITE", "SELECT COUNT(*) AS n FROM quest_tasks WHERE phase='followup'")
        spent_ev = [k for a, k in ev if a == "IDLE_BUDGET_SPENT"]
        # Angka yang diharapkan dihitung ulang dengan ekspresi SQL yang ditulis tangan di sini —
        # kalau dibandingkan dengan konstanta saja, tes ini akan merah pada fixture yang punya baris
        # idle sah lain (persis yang terjadi), dan kalau dibandingkan dengan hasil fungsi itu
        # sendiri, ia hanya menggemakan dirinya.
        _oe = int((autopilot(c, "SQLITE") or {}).get("on_epoch") or 0)
        _exp = run(c, "SQLITE",
                   "SELECT COUNT(*) AS n FROM quest_tasks WHERE origin='idle' AND status IN "
                   "('COMPLETED','FAILED','PARKED') AND "
                   "CAST(strftime('%s', completed_at) AS INTEGER) >= ?", (_oe,))
        _expected = int((_exp[0]["n"] if _exp else 0) or 0)
        check("lanjutan kena langit-langit yang SAMA seperti kerja idle: kegagalan ikut dihitung, "
              "hanya jendela ON ini yang dihitung, dan baris jendela lama tidak ikut",
              int((over[0]["n"] if over else 0) or 0) == 0 and spent_ev
              and spent_ev[0].get("used") == _expected
              and _expected >= IDLE_MAX_ITEMS
              and "idle jendela lama" not in str(run(c, "SQLITE",
                    "SELECT title FROM quest_tasks WHERE origin='idle' AND status IN "
                    "('COMPLETED','FAILED','PARKED') AND CAST(strftime('%s', completed_at) AS "
                    "INTEGER) >= ?", (_oe,))),
              f"used={spent_ev[0].get('used') if spent_ev else None} expected={_expected}")
    finally:
        c.close()
        tmp.unlink(missing_ok=True)


def selftest_live():
    """Jalankan asersi yang sama di backend yang benar-benar dipakai malam ini.

    Bug pertama modul ini justru tidak terlihat di SQLite: psycopg2 mengembalikan tuple, dan
    selftest yang hanya menyentuh SQLite akan tetap hijau sementara produksi jatuh pada SELECT
    pertama. Satu-satunya cara mencegahnya adalah menjalankan tes pada backend yang sama dengan
    yang dibaca engine lain. Baris uji diberi penanda dan dihapus lagi; yang dihapus hanya
    buatan tes ini sendiri.
    """
    conn, engine = connect()
    mark = f"selftest-live-{now()}"
    checks = []
    # Jalur live menjalankan `decide()` sungguhan, dan `decide()` sekarang berbanner saat sebuah
    # pertanyaan mendarat di antrean manusia. Tes yang membangunkan orang lewat desktop-nya sendiri
    # bukan tes yang baik: banner direkam, tidak dikirim.
    banners_live, orig_banner_live = _mute_banners()

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    try:
        ensure_schema(conn, engine)
        rows = run(conn, engine, "SELECT 1 AS one")
        check(f"SELECT mengembalikan dict di {engine}", rows and rows[0].get("one") == 1, str(rows))
        ph = "%s" if engine == "POSTGRESQL" else "?"
        # Baris uji ini diberi `origin='operator'` karena itu memang kenyataannya: selftest ini
        # berjalan atas nama orang yang menyuruhnya. Tanpa label itu, sejak OFF dibuang dari
        # kosakata dan default menjadi SEMI, klaim di bawah ditolak keranjang SEMI ("hanya perintah
        # operator") dan tes yang merah itu tidak sedang menguji apa pun selain labelnya sendiri.
        run(conn, engine,
            f"INSERT INTO quest_tasks (title, status, phase, check_command, origin) "
            f"VALUES ({ph}, 'PENDING', 'test', {ph}, {ph})",
            (f"{mark}-A", "true", ORIGIN_OPERATOR))
        got = run(conn, engine, f"SELECT id FROM quest_tasks WHERE title={ph}", (f"{mark}-A",))
        check("baris uji bisa dibaca kembali", got, str(got))
        try:
            lit = run(conn, engine,
                      f"SELECT COUNT(*) AS n FROM quest_tasks WHERE title LIKE '{mark}%'")
            check("LIKE dengan % literal dan tanpa parameter tidak meledak",
                  lit and int(lit[0]["n"]) >= 1, str(lit))
        except Exception as exc:  # noqa: BLE001
            check("LIKE dengan % literal dan tanpa parameter tidak meledak", False, repr(exc))
        if got:
            iid = int(got[0]["id"])
            ok, msg = claim(conn, engine, iid, "qoder")
            check("klaim berhasil di backend produksi", ok, msg)
            ok2, msg2 = claim(conn, engine, iid, "antigravity")
            check("klaim ganda ditolak", not ok2, msg2)
            ok, msg = finish(conn, engine, iid, "COMPLETED")
            check("COMPLETED tanpa bukti ditolak", not ok, msg)
            ok, msg = finish(conn, engine, iid, "COMPLETED", evidence="selftest_live")
            check("COMPLETED dengan bukti diterima", ok, msg)
        ap = autopilot(conn, engine)
        check("autopilot_state terbaca", ap and ap.get("mode") in MODES, str(ap))
        # Yang diuji di sini adalah keadaan PRODUKSI sesudah migrasi yang berjalan di
        # `ensure_schema`: baris 'OFF' warisan menjadi SEMI dengan tangan tetap turun. Kalau
        # migrasi itu salah — misalnya ia membuka jendela atau mengangkat tangan — cek kedua di
        # bawah ini yang merah, bukan cek mode.
        check("produksi tidak menyimpan mode di luar kosakata",
              (ap or {}).get("mode") in MODES, str(ap))
        check("rem produksi terbaca dari kolom tangan, bukan dari mode",
              (ap or {}).get("level") in LEVELS, str((ap or {}).get("level")))
        st = night_state(conn, engine)
        check("night_state terbaca tanpa crash", isinstance(st.get("ok"), bool), str(st))
        # `consecutive_failures` punya dua cabang SQL dan yang satu hanya jalan di sini. Menulis
        # `EXTRACT(EPOCH FROM ...)` yang salah ketik akan tetap hijau di SQLite dan menolak setiap
        # klaim di produksi pada tick pertama — jadi yang diuji adalah ANGKANYA, bukan hanya
        # keberadaannya. Baris yang ditulis paling baru selalu paling akhir urut (detik, id),
        # jadi selisihnya pasti, berapa pun isi tabel yang sedang dibaca engine lain.
        base = consecutive_failures(conn, engine)
        run(conn, engine,
            f"INSERT INTO quest_tasks (title, status, completed_at) VALUES "
            f"({ph}, 'FAILED', CURRENT_TIMESTAMP)" if engine == "POSTGRESQL" else
            f"INSERT INTO quest_tasks (title, status, completed_at) VALUES "
            f"({ph}, 'FAILED', datetime('now'))", (f"{mark}-breaker-fail",))
        check("satu kegagalan baru menaikkan rentetan persis satu di backend produksi",
              consecutive_failures(conn, engine) == base + 1,
              f"base={base} now={consecutive_failures(conn, engine)}")
        run(conn, engine,
            f"INSERT INTO quest_tasks (title, status, completed_at) VALUES "
            f"({ph}, 'COMPLETED', CURRENT_TIMESTAMP)" if engine == "POSTGRESQL" else
            f"INSERT INTO quest_tasks (title, status, completed_at) VALUES "
            f"({ph}, 'COMPLETED', datetime('now'))", (f"{mark}-breaker-win",))
        check("satu keberhasilan memotong rentetan sampai nol — itu yang membuat malam panjang "
              "tidak mati",
              consecutive_failures(conn, engine) == 0,
              f"now={consecutive_failures(conn, engine)}")
        # Dua baris pemutus DIHAPUS di sini, bukan di akhir tes. Sebuah baris COMPLETED yang
        # tertinggal walau beberapa detik berarti `consecutive_failures` produksi membaca nol di
        # tengah operasi sungguhan — tepat angka yang dipakai pemutus untuk memutuskan mesin boleh
        # lanjut atau berhenti. Yang bersih adalah yang tidak pernah terlihat pembaca lain.
        run(conn, engine, "DELETE FROM quest_tasks WHERE title IN "
            + ("(%s, %s)" if engine == "POSTGRESQL" else "(?, ?)"),
            (f"{mark}-breaker-fail", f"{mark}-breaker-win"))
        nxt = next_item(conn, engine)
        check("next_item tidak menawarkan baris UNAVAILABLE di produksi",
              not nxt or norm(nxt["status"]) not in ("UNAVAILABLE", "FAILED", "COMPLETED"),
              str(nxt))
        # decide() yang baru menyebut kolom rung_used/note, dan status() yang baru menyebut
        # rung_used di SELECT. Keduanya sudah ada di SQLite fixture, jadi fixture tidak bisa
        # membuktikan PostgreSQL punya kolom yang sama — dan itu persis kelas bug yang sudah
        # dua kali lolos ke produksi dari modul ini.
        irr = decide(conn, engine, f"{mark} hapus tabel?", [{"label": "ya"}, {"label": "tidak"}],
                     weight="irreversible")
        check("decide() bentuk baru tertulis di backend produksi",
              irr and irr[0]["state"] == "ESCALATED" and irr[0]["rung_used"] == "human-only",
              str(irr[0] if irr else None))
        stt = status(conn, engine)
        check("status() membaca kolom baru decisions di produksi",
              any(x["id"] == irr[0]["id"] for x in stt["decisions_open"])
              and "rung_used" in (stt["decisions_open"][0] if stt["decisions_open"] else {}),
              str([x.get("id") for x in stt["decisions_open"]][:5]))
        # Kelas bug yang sama untuk F10.7: pertanyaan lama memakai `created_at` di jalur SQLite,
        # padahal kolomnya bernama `ts` di KEDUA backend (DDL milik session_ingestor.py). Nama yang
        # salah hanya meledak saat tabelnya benar-benar dibaca, jadi ia diperiksa di produksi.
        epoch, readable = _last_action(conn, engine, "qoder")
        check("aksi engine terbaca di backend produksi", readable, str(epoch))
        check("umur aksi berupa epoch bilangan, bukan teks",
              epoch is None or isinstance(epoch, int), f"{type(epoch).__name__}: {epoch}")
        # heartbeat produksi: hanya membaca (capture-pane + SELECT). Tidak ada satu byte pun yang
        # dikirim ke pane mana pun, jadi aman dijalankan sementara operator memegang terminalnya.
        beats = heartbeat(conn, engine, sample_gap=1)
        check("heartbeat atas klaim produksi mengembalikan keadaan tiga nilai",
              all(b["state"] in ("WORKING", "AT_REST", "UNKNOWN") for b in beats),
              str([(b["claimed_by"], b["state"]) for b in beats]))
        check("heartbeat menyebut pane yang memang terbaca",
              all(isinstance(b["pane_readable"], bool) for b in beats), str(beats[:1]))
        check("UNKNOWN tidak pernah dibuat dari pane yang terbaca dan log yang terbaca",
              all(b["state"] != "UNKNOWN" or not (b["pane_readable"] and b["action_log_readable"])
                  for b in beats),
              str([b for b in beats if b["state"] == "UNKNOWN"]))
        # F10.3 di backend produksi, dengan pengukuran NYATA: proses ini tidak punya terminal
        # pengendali, jadi naik tangga harus ditolak di sini. Semua cek di atas menyuntik tty=False
        # supaya bisa diuji; yang satu ini membuktikan suntikannya cocok dengan dunia.
        lvl = drain_level(conn, engine)
        check("drain_level terbaca dan kosakatanya tertutup rapi di produksi",
              lvl["readable"] is True and lvl["level"] in LEVELS
              and (not lvl["allows_resume"] or lvl["level"] == "resume"), str(lvl))
        ok, msg = set_level(conn, engine, "dispatch", 30, "selftest-live")
        check("proses tanpa tty ditolak NAIK tangga di produksi, tanpa menulis apa pun",
              not ok and "/dev/tty" in msg, msg)
        check("penolakan di produksi tidak mengubah level yang terbaca",
              drain_level(conn, engine)["level"] == lvl["level"], str(drain_level(conn, engine)))
        # `nudge()` adalah satu-satunya tempat anggaran sentuhan ditulis. Placeholder-nya berbeda
        # per backend (%s vs ?), dan kesalahan placeholder tidak bersuara di SQLite — ia cuma
        # menghasilkan tulisan ke baris yang salah. Jadi dihitung di backend nyata.
        run(conn, engine, f"INSERT INTO quest_tasks (title, status) VALUES ({ph}, 'PENDING')",
            (f"{mark}-nudge",))
        nud = run(conn, engine, f"SELECT id FROM quest_tasks WHERE title={ph}",
                  (f"{mark}-nudge",))
        nudge_id = int(nud[0]["id"])
        for k in range(MAX_RESUME):
            ok, msg = nudge(conn, engine, nudge_id)
            check(f"produksi: sentuhan {k + 1}/{MAX_RESUME} diterima dan terbaca kembali",
                  ok and int((get_item(conn, engine, nudge_id) or {}).get("resume_count") or 0) == k + 1,
                  msg)
        ok, msg = nudge(conn, engine, nudge_id)
        check("produksi: sentuhan ke-4 ditolak, dan tidak ada yang mengetik karenanya",
              not ok and "habis" in msg, msg)
        # Anggaran item per jendela di backend NYATA. Bentuk SQLite-nya salah dua kali dan kedua
        # salah itu tidak bersuara, jadi asersi yang sama dijalankan di sini dengan dua baris
        # bertanda yang dihapus lagi: satu selesai sekarang, satu selesai tiga hari lalu.
        old_stamp = (f"{mark}-lama", )
        run(conn, engine,
            f"INSERT INTO quest_tasks (title, status, completed_at) VALUES ({ph}, 'COMPLETED', "
            f"CURRENT_TIMESTAMP - INTERVAL '3 days')", old_stamp)
        run(conn, engine,
            f"INSERT INTO quest_tasks (title, status, completed_at) VALUES ({ph}, 'COMPLETED', "
            "CURRENT_TIMESTAMP)", (f"{mark}-baru",))
        try:
            future = items_done_in_window(conn, engine, now() + 3600)
            wide = items_done_in_window(conn, engine, now() - 86400 * 7)
            tight = items_done_in_window(conn, engine, now() - 30)
            check("produksi: jendela masa depan kosong — filter tipe benar di backend ini",
                  future == 0, str(future))
            check("produksi: baris tiga hari lalu hanya masuk jendela yang lebar",
                  wide - tight >= 1, f"wide={wide} tight={tight} selisih={wide - tight}")
            check("produksi: baris yang baru selesai masuk jendela 30 detik",
                  tight >= 1, str(tight))
            if engine == "POSTGRESQL":
                # Asumsi zona waktu diuji, bukan diasumsikan. `completed_at` tanpa zona dibaca
                # EXTRACT sebagai UTC, dan selisihnya persis offset Asia/Jakarta: +25 200 detik.
                # Baris yang baru ditulis ini epoch-nya diketahui dalam sedetik, jadi bentuk
                # `::timestamptz` harus cocok — kalau tidak, seluruh jendela malam ini salah.
                seen = run(conn, engine,
                           "SELECT EXTRACT(EPOCH FROM completed_at::timestamptz)::bigint AS e "
                           f"FROM quest_tasks WHERE title={ph}", (f"{mark}-baru",))
                check("produksi: epoch yang dibaca PG sama dengan jam Python (selisih < 5s)",
                      seen and abs(int(seen[0]["e"]) - now()) < 5,
                      f"{seen and int(seen[0]['e']) - now()}s")
            ap = autopilot(conn, engine)
            check("produksi: items_done yang dibaca GUI adalah turunan, dan nilai tersimpan "
                  "dipisahkan", isinstance(ap.get("items_done"), int)
                  and "items_done_stored" in ap, str({k: ap.get(k) for k in ("items_done", "items_done_stored")}))
        finally:
            for t in (f"{mark}-lama", f"{mark}-baru"):
                run(conn, engine, f"DELETE FROM quest_tasks WHERE title={ph}", (t,))
    finally:
        like = f"{mark}%"
        run(conn, engine, "DELETE FROM quest_tasks WHERE title LIKE "
            + ("%s" if engine == "POSTGRESQL" else "?"), (like,))
        run(conn, engine, "DELETE FROM decisions WHERE question LIKE "
            + ("%s" if engine == "POSTGRESQL" else "?"), (like,))
        left = run(conn, engine, "SELECT COUNT(*) AS n FROM quest_tasks WHERE title LIKE "
                    + ("%s" if engine == "POSTGRESQL" else "?"), (like,))
        check("baris uji dibersihkan", left and int(left[0]["n"]) == 0, str(left))
        conn.close()

    bad = [c for c in checks if not c[1]]
    for name, okval, detail in checks:
        print(f"[{'ok    ' if okval else 'GAGAL '}] (live/{engine}) {name}"
              + (f" — {detail}" if detail and not okval else ""))
    print(f"\nwork_order selftest live: {len(checks) - len(bad)}/{len(checks)} sesuai")
    return 1 if bad else 0


CLEARANCE_VERDICTS = ("SETUJU",)
CLEARANCE_WINDOW_MINUTES = 180


def _registry_roles(model_id):
    """Entri registry untuk sebuah kunci mesin, atau {} kalau nama itu bukan pemeriksa terdaftar.

    Docstring fungsi ini dulu MENYEBUT validasi registry padahal tidak melakukannya — dan itu
    bentuk kegagalan terburuk dari sebuah komentar: pembaca berhenti curiga karena teksnya sudah
    menjanjikan pagar yang ada di bawahnya.
    """
    try:
        import json as _j
        reg = _j.loads((REPO / "config" / "engines.json").read_text(encoding="utf-8")).get("engines", {})
    except Exception:  # noqa: BLE001 — registry tidak terbaca = tidak ada yang terverifikasi
        return {}
    # Dicari sebagai KUNCI registry ATAU sebagai id model di dalam sebuah entri. Catatan verifikasi
    # menyimpan id model (`gemini-3.8-flash-high`), bukan kunci registry (`gemini-verify`); membanding
    # -kan yang satu dengan daftar yang lain akan menolak setiap izin yang sah, dan pagar yang selalu
    # menolak terasa aman sampai seseorang butuh mendorong pekerjaannya.
    roles = ("reviewer", "decider", "validator", "deep-reviewer")
    for key, spec in reg.items():
        if not isinstance(spec, dict):
            continue
        if str(spec.get("role") or "") not in roles:
            continue
        if key == model_id or str(spec.get("model") or "") == model_id:
            return {**spec, "_key": key}
    return {}


def push_clearance(conn, engine, model_id, within_minutes=CLEARANCE_WINDOW_MINUTES):
    """Satu catatan verifikasi COMPLETED yang sungguh ada, milik model ini, masih segar.

    Ini satu-satunya sumber klaim "boleh push". F10.9 dibuka justru karena keputusan 6 hidup di
    prosa; mencatat vonis sebagai PARAMETER dengan nilai dasar PASS akan mengulang kesalahan yang
    sama dengan satu lapis tambahan — engine mana pun bisa menulis clears-nya sendiri tanpa pernah
    bertanya kepada siapa pun. Jadi vonis tidak diterima di sini, vonis DIBACA dari `action_log`:
    baris `cross_verify` yang exit_code-nya 0 dan verdict-nya SETUJU.

    Jendela waktu ikut disaring karena verifikasi yang berumur seminggu bukan izin untuk commit
    yang baru saja berubah.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    # Pembacaan yang gagal adalah TIDAK ADA IZIN, bukan "entah, lanjut saja". Arah gagalnya sama
    # dengan sisa kontrak ini: ketidaktahuan menutup jalur, tidak pernah membukanya.
    try:
        rows = _push_clearance_rows(conn, engine, model_id, within_minutes, ph)
    except Exception:  # noqa: BLE001
        return None
    # Hanya bagian SEBELUM pemisah " :: " yang boleh dibaca sebagai vonis. Setelah pemisah itu
    # ada potongan jawaban model, dan potongan itu bisa memuat teks siapa pun — termasuk kalimat
    # `verdict=SETUJU` yang dipetiknya dari prompt. Pencarian substring di seluruh baris akan
    # memberi izin kepada kutipan, bukan kepada pemeriksa.
    def _verdict_of(summary):
        head = str(summary or "").split(" :: ", 1)[0]
        # POLA-nya ikut dikapitalkan: mencari `verdict=` di teks yang sudah diupper tidak akan
        # pernah cocok, dan kegagalan seperti ini tidak berisik — ia hanya membuat setiap vonis
        # terbaca sebagai "tidak ada izin", yang dari luar terlihat sangat hati-hati.
        m = re.search(r"VERDICT=([A-Z_]+)", head.upper())
        return m.group(1) if m else ""
    ok = [r for r in rows or [] if _verdict_of(r.get("summary")) in CLEARANCE_VERDICTS]
    return ok[0] if ok else None


def clearance_covers(clearance, shas):
    """Apakah bukti yang sama benar-benar memuat commit yang akan didorong.

    Tanpa ini, satu pemeriksaan apa pun yang kebetulan lolos — sebuah klaim aritmatika, satu baris
    kode yang tidak berhubungan — memberi izin untuk mendorong SEMUA commit, karena yang dicari
    hanyalah "ada baris SETUJU dari model ini". Itu izin tanpa cakupan, dan pendorong yang jujur
    harus bisa menunjukkan bukti mana yang ia rujuk.

    Cakupan dibaca dari berkas bukti yang dicatat bersama vonisnya (`raw_ref`): berkas itu harus ada
    dan harus menyebut setiap sha yang akan berangkat. Bukti untuk pekerjaan lain tidak bisa dipakai
    silang tanpa memalsukan berkasnya sendiri, dan pemalsuan itu meninggalkan jejak di disk.
    """
    if not clearance:
        return False, "tidak ada catatan vonis"
    ref = str(clearance.get("raw_ref") or "")
    if not ref:
        return False, "catatan vonis tidak menyebut berkas bukti"
    path = Path(ref)
    if not path.exists():
        return False, f"berkas bukti tidak ada lagi: {ref}"
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"berkas bukti tidak terbaca: {exc}"
    # Dicocokkan dengan BARIS MANIFEST, bukan dengan keberadaan substring. Sebuah sha yang
    # kebetulan muncul di tengah komentar diff, atau yang ditempel di mana pun dalam berkas, tidak
    # boleh berarti "commit ini ikut diperiksa" — yang dimaksud adalah daftar yang sengaja dibuat
    # untuk menyatakan cakupan, dan bentuknya harus bisa dibedakan dari teks biasa.
    listed = set(re.findall(r"^#\s*([0-9a-f]{7,40})\s*$", body, re.M))
    missing = [x for x in shas
               if not any(l == x or l.startswith(x[:12]) or x.startswith(l[:12]) for l in listed)]
    if missing:
        return False, (f"berkas bukti tidak menyebut {len(missing)} sha yang akan berangkat "
                       f"(contoh {missing[0][:12]}) — vonis ini bukan untuk commit ini")
    return True, ref


def _push_clearance_rows(conn, engine, model_id, within_minutes, ph):
    return run(conn, engine,
               f"SELECT ts, tab, model, summary, raw_ref FROM action_log "
               f"WHERE kind = 'cross_verify' "
               f"AND model = {ph} AND exit_code = 0 AND ts >= " +
               ("NOW() - ({ph} || ' minutes')::interval" if engine == "POSTGRESQL"
                else "datetime('now', '-' || {ph} || ' minutes')").format(ph=ph) +
               " ORDER BY ts DESC LIMIT 12",
               (model_id, str(within_minutes)))


def record_push_verified(conn, engine, model_id: str, shas: list[str], events_path=None) -> tuple:
    """Pancarkan peristiwa bahwa satu commit berangkat karena seorang verifier meloloskannya.

    Tiga hal harus benar sebelum satu baris pun ditulis, dan ketiganya diukur, bukan dipercaya:

    - ada catatan `cross_verify` COMPLETED dengan verdict SETUJU dari model yang disebut, masih di
      dalam jendela (lihat `push_clearance`);
    - setiap sha yang disebut benar-benar commit di repo ini (`git cat-file`), supaya sebuah vonis
      tidak bisa ditempelkan pada string yang kebetulan berbentuk hex;
    - model yang menyebutnya adalah model yang terdaftar sebagai verifier/decider, bukan nama karangan.

    Yang dicatat adalah peristiwa: sha, model, vonis. Bukan kalimat laporan — gerbang f109 versi
    pertama lolos justru karena engine menulis sendiri kata "push" dan "verifier" di prosanya.
    """
    import subprocess
    reg = _registry_roles(model_id)
    if not reg:
        return False, (f"{model_id!r} tidak terdaftar sebagai pemeriksa di config/engines.json — "
                       "izin hanya bisa datang dari mesin yang memang dipasang sebagai verifier/"
                       "reviewer/decider, bukan dari nama yang cocok"), []
    clearance = push_clearance(conn, engine, model_id)
    if not clearance:
        return False, (f"tidak ada catatan verifikasi COMPLETED bervonis "
                       f"{'/'.join(CLEARANCE_VERDICTS)} dari {model_id!r} dalam "
                       f"{CLEARANCE_WINDOW_MINUTES} menit terakhir — vonis tidak bisa ditulis dari "
                       "sini, hanya bisa dibaca dari action_log"), []
    covers, why_cover = clearance_covers(clearance, shas)
    if not covers:
        return False, f"vonis tidak menutup commit ini: {why_cover}", []
    good, bad = [], []
    for sha in shas:
        s = (sha or "").strip()
        if not re.fullmatch(r"[0-9a-f]{7,40}", s):
            bad.append(sha)
            continue
        try:
            r = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{s}^{{commit}}"],
                               capture_output=True, text=True, timeout=15)
            (good if r.returncode == 0 else bad).append(s)
        except Exception:  # noqa: BLE001 — git tidak terbaca = tidak ada bukti
            bad.append(s)
    if bad:
        return False, (f"sha bukan commit yang terbaca di repo ini: {', '.join(map(str, bad))} — "
                       "tidak ada peristiwa yang ditulis"), []
    if not good:
        return False, "tidak ada satu sha pun yang disebut", []
    import datetime as _dt
    # Jalur peristiwa bisa dipindah. Bukan untuk kemudahan: tes yang menulis ke berkas sungguhan di
    # sini akan meninggalkan `push_verified` untuk HEAD yang TIDAK pernah diverifikasi manusia mana
    # pun, dan gerbang f109 akan membacanya sebagai izin — tes yang menciptakan bukti yang ia
    # klaim sedang dicari.
    ev = Path(events_path or (REPO / "logs" / "station_events.jsonl"))
    ev.parent.mkdir(parents=True, exist_ok=True)
    # SATU peristiwa per sha, tiap field satu nilai. `",".join(shas)` menghasilkan
    # "sha": "abc,def" dan gerbang mencari [0-9a-f]{7,40}: koma di tengah membuatnya tidak akan
    # pernah cocok untuk push berapa pun kecuali satu commit.
    written = []
    with ev.open("a", encoding="utf-8") as fh:
        for sha in good:
            row = {"ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "kind": "push_verified", "sha": sha, "model": model_id,
                   "verdict": "SETUJU", "verified_at": str(clearance.get("ts"))}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            written.append(row)
    return True, f"{len(written)} peristiwa ditulis atas vonis {model_id}", written


def push_state() -> dict:
    """Push yang tertunda adalah keadaan, bukan ingatan salah satu engine.

    §6 berjanji bahwa run yang tidak bisa mencapai verifier "meng-commit lokal dan meninggalkan
    push di digest pagi". Janji itu tidak cukup — F10.9 justru dibuka karena aturan yang hanya
    hidup di prosa mengikat sejauh engine mengingatnya. Kalau tidak ada baris di digest yang
    menyebut commit yang belum berangkat, janji itu hilang bersama sesi yang menulisnya, dan
    seseorang bangun tanpa tahu ada pekerjaan yang belum sampai ke origin.

    Semua angkanya dari git dan dari berkas peristiwa. Tidak ada yang ditulis tangan, dan git
    yang tidak terbaca dilaporkan UNKNOWN — bukan nol.
    """
    import subprocess
    out = {"unpushed": None, "commits": [], "verifier_verdicts": 0, "readable": False}
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-list", "--count", "@{u}..HEAD"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            out["why"] = (r.stderr or r.stdout or "git menolak").strip()[:160]
            return out
        out["unpushed"] = int(r.stdout.strip() or 0)
        out["readable"] = True
    except Exception as exc:  # noqa: BLE001
        out["why"] = f"{type(exc).__name__}: {exc}"
        return out
    if not out["unpushed"]:
        return out
    shas: list[str] = []
    try:
        r = subprocess.run(["git", "-C", str(REPO), "log", "--format=%h", "@{u}..HEAD"],
                           capture_output=True, text=True, timeout=15)
        shas = [x for x in r.stdout.split() if x]
        out["commits"] = shas[:20]
    except Exception:  # noqa: BLE001
        pass
    # Verdict yang sah adalah peristiwa `push_verified` yang menyebut sha commit ini — bukan
    # kalimat laporan di action_log. Versi pertama gerbang f109 membaca prosa dan lolos karena
    # engine menulis sendiri kata "push" dan "verifier".
    try:
        ev = REPO / "logs" / "station_events.jsonl"
        if ev.is_file():
            for line in ev.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"kind": "push_verified"' in line and any(sha in line for sha in shas):
                    out["verifier_verdicts"] += 1
    except Exception:  # noqa: BLE001 — berkas tidak terbaca bukan berarti nol verdict
        out["verdicts_readable"] = False
    return out


# --- muka untuk GUI ---------------------------------------------------------


def status(conn, engine, older_than=180):
    """Satu amplop JSON untuk sakelar Autopilot dan tab Human Decide.

    Semua isinya turunan: baris database, angka pemutus, dan satu pembacaan git. Tidak ada
    satu pun field yang boleh ditulis tangan — §12 melarang laporan yang dikarang, dan muka
    GUI yang mengarang adalah cara tercepat membuat operator percaya angka yang salah.

    `decisions_open` adalah "belum selesai", bukan "berstatus OPEN": baris ESCALATED dan
    UNAVAILABLE juga muncul di sini, karena keduanya adalah pertanyaan yang mesin tidak bisa
    tutup sendiri dan artinya menunggu orang.
    """
    ap = autopilot(conn, engine) or {}
    per_status = run(conn, engine,
                     "SELECT status, COUNT(*) AS n FROM quest_tasks GROUP BY status ORDER BY 2 DESC")
    human = run(conn, engine,
                "SELECT id, title, status, escalation_reason, phase FROM quest_tasks "
                "WHERE status='HUMAN' ORDER BY id")
    # `ANSWERED` tidak ikut antrean yang menunggu: baris yang sudah dijawab bukan lagi sesuatu yang
    # menahan mesin. Ia dilaporkan terpisah sebagai riwayat — "sudah dijawab" tidak boleh berarti
    # "hilang dari laporan", karena operator yang tidak melihat jawabannya sendiri akan menjawab dua kali.
    dec = run(conn, engine,
              "SELECT id, question, options, weight, state, rung_used, engine_key, model_id, "
              "note, created_epoch, item_id FROM decisions "
              "WHERE state IN ('OPEN','ESCALATED') ORDER BY id")
    dec_done = run(conn, engine,
                   "SELECT id, question, state, answer, rung_used, model_id, answered_by, "
                   "resolved_epoch, item_id FROM decisions "
                   "WHERE state IN ('ANSWERED','RESOLVED') ORDER BY id DESC LIMIT 8")
    nxt = next_item(conn, engine)
    return {
        "engine": engine,
        "autopilot": ap,
        "drain": drain_level(conn, engine),
        "night": night_state(conn, engine),
        "limits": {"max_failures": NIGHT_MAX_FAILURES, "max_items": NIGHT_MAX_ITEMS,
                   "failure_lookback": FAILURE_LOOKBACK,
                   "lease_seconds": LEASE_SECONDS, "max_resume": MAX_RESUME,
                   "arm_default_minutes": ARM_DEFAULT_MINUTES, "arm_max_minutes": ARM_MAX_MINUTES,
                   "levels": list(LEVELS)},
        "per_status": per_status,
        "human_queue": human,
        "decisions_open": dec,
        "decisions_answered": dec_done,
        "stalled": stalled(conn, engine, older_than),
        "next": nxt,
        # Dashboard butuh wajah proyek, bukan hanya wajah antrean. Isinya diturunkan oleh satu
        # fungsi di atas supaya `/api/autopilot` tetap jalan tipis: menambah angka di handler Rust
        # adalah cara membuat dua laporan yang boleh berbeda (§12.2).
        "workspace": semi_status(conn, engine),
    }


# --- CLI --------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Work order autopilot — klaim, lease, bukti, eskalasi")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ensure")
    p = sub.add_parser("seed"); p.add_argument("--roadmap", action="store_true")
    p.add_argument("--refresh-gates", nargs="*", default=None, metavar="KEY",
                   help="perbarui check_command HANYA butir yang disebut (mis. f109). Tanpa batas "
                        "ini, satu flag akan menimpa gerbang SETIAP baris PENDING lain yang boleh "
                        "saja sedang disetel orang dari layar")
    p = sub.add_parser("claim"); p.add_argument("id", type=int); p.add_argument("--by", required=True)
    p = sub.add_parser("lease"); p.add_argument("id", type=int)
    p = sub.add_parser("heartbeat")
    p.add_argument("--quiet", type=int, default=180,
                   help="berapa detik tanpa aksi terukur baru dianggap diam")
    p.add_argument("--gap", type=float, default=float(MIN_SAMPLE_GAP),
                   help=f"detik antara dua pembacaan pane (lantai {MIN_SAMPLE_GAP}s di produksi)")
    p = sub.add_parser("complete"); p.add_argument("id", type=int); p.add_argument("--evidence", default="")
    p = sub.add_parser("block"); p.add_argument("id", type=int); p.add_argument("--reason", required=True)
    p = sub.add_parser("human"); p.add_argument("id", type=int); p.add_argument("--reason", required=True)
    p = sub.add_parser("next"); p.add_argument("--origin", default=None,
                                               choices=[ORIGIN_OPERATOR, ORIGIN_ROADMAP,
                                                        ORIGIN_LEGACY],
                                               help="batasi pada satu asal baris; kosong = apa pun")
    sub.add_parser("list")
    sub.add_parser("stalled")
    p = sub.add_parser("autopilot")
    # Daftarnya DIPINDAI dari kosakata, tidak ditulis ulang. Selama ini argparse menyimpan
    # ["on","semi","off"] sebagai salinan ketiga — setelah Rust berhenti menyalinnya dan setelah
    # MODES berubah, parser inilah yang akan menolak kata lebih dulu dengan rc=2, sehingga
    # `autopilot()` tidak pernah sempat menjelaskan kenapa sebuah kata ditolak dan cabang
    # STOP_WORDS tidak bisa dicapai sama sekali. Satu daftar, satu pemilik.
    p.add_argument("mode", nargs="?",
                   choices=[w.lower() for w in (MODES + STOP_WORDS)] + ["show"])
    p.add_argument("--minutes", type=int); p.add_argument("--budget", type=int)
    p.add_argument("--reason", default=None); p.add_argument("--by", default="operator")
    p = sub.add_parser("queue", help="satu perintah operator masuk antrean quest_tasks "
                                     "(origin='operator' — hanya SEMI/ON yang mengambilnya)")
    p.add_argument("title"); p.add_argument("--engine", required=True,
                                            help="nama tab yang akan mengerjakannya; tidak pernah ditebak")
    p.add_argument("--gate", default="", help="perintah gerbang penerimaan, boleh kosong")
    p.add_argument("--ws", default="",
                   help="direktori proyek; kosong = workspace tab mesin tujuan "
                        "(BUKAN REPO — REPO hanya untuk baris lama yang kolomnya NULL)")
    p.add_argument("--by", default="operator")
    p = sub.add_parser("ws", help="daftar / buat / fokus proyek, dan pilih pedoman MD-nya")
    p.add_argument("action", choices=["list", "create", "focus", "guidelines", "detect"])
    p.add_argument("first", nargs="?", default="", help="nama untuk create, id/nama/path untuk focus")
    p.add_argument("--path", default="", help="direktori proyek (create)")
    p.add_argument("--name", default="", help="nama tampilan (create)")
    p.add_argument("--files", default="", help="daftar pedoman terpisah koma (guidelines)")
    p.add_argument("--by", default="operator-gui")
    p = sub.add_parser("arm"); p.add_argument("level", choices=list(LEVELS))
    p.add_argument("--minutes", type=int, default=ARM_DEFAULT_MINUTES)
    p.add_argument("--by", default="operator")
    p = sub.add_parser("disarm"); p.add_argument("--by", default="operator")
    sub.add_parser("drain")
    p = sub.add_parser("decide"); p.add_argument("question"); p.add_argument("--options", default="")
    p.add_argument("--weight", default="heavy", choices=["heavy", "light", "irreversible"])
    p.add_argument("--item", type=int, default=None,
                   help="quest id yang menunggu jawaban ini; tanpa ini pertanyaan tidak membangunkan siapa-siapa")
    p.add_argument("--route", action="store_true", help="langsung kiratkan ke tangga resolver")
    p.add_argument("--timeout", type=int, default=240)
    p = sub.add_parser("answer"); p.add_argument("id", type=int)
    p.add_argument("--choice", required=True, help="isi jawaban; label pilihan, kalimat bebas, apa pun yang operator tulis")
    p.add_argument("--by", default="operator")
    p.add_argument("--note", default=None)
    p = sub.add_parser("route"); p.add_argument("id", type=int)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--dry-run", action="store_true",
                   help="tunjukkan tangga + prompt tanpa mengirim apa pun")
    p = sub.add_parser("decisions"); p.add_argument("--limit", type=int, default=15)
    p = sub.add_parser("resolve"); p.add_argument("id", type=int)
    p.add_argument("--rung", required=True); p.add_argument("--engine-key", required=True)
    p.add_argument("--model", required=True); p.add_argument("--answer", required=True)
    p.add_argument("--state", default="RESOLVED",
                   choices=["RESOLVED", "ESCALATED", "UNAVAILABLE"])
    p.add_argument("--note", default=None)
    p = sub.add_parser("push-verified")
    p.add_argument("--model", required=True,
                   help="kunci registry yang memberi vonis (harus punya catatan cross_verify)")
    p.add_argument("--sha", action="append", required=True,
                   help="commit yang akan diberangkatkan; boleh berulang, satu peristiwa per sha")
    sub.add_parser("push-gate")
    p = sub.add_parser("hold", help="catat bahwa commit yang belum berangkat DITAHAN, dan kenapa")
    p.add_argument("--reason", required=True)
    sub.add_parser("digest")
    sub.add_parser("status")
    p = sub.add_parser("selftest"); p.add_argument("--live", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        rc = selftest()
        if a.live and not rc:
            rc = selftest_live()
        return rc
    conn, engine = connect()
    if engine == "POSTGRESQL":
        ensure_schema(conn, engine)

    if a.cmd == "ensure":
        for line in ensure_schema(conn, engine):
            print(line)
    elif a.cmd == "seed":
        # `--refresh-gates f105` tanpa `--roadmap` adalah perintah yang keluar 0 tanpa mengubah apa
        # pun: daftar butirnya kosong, jadi tidak ada yang bisa disentuh. Terukur malam ini. Satu
        # flag yang tampak bekerja tapi tidak melakukan apa-apa lebih berbahaya daripada
        # flag yang tidak ada, jadi sekarang ia ditolak dengan namanya sendiri.
        if a.refresh_gates and not a.roadmap:
            print("seed: --refresh-gates butuh --roadmap (daftar butirnya kosong tanpa itu, "
                  "jadi tidak ada yang bisa diperbarui)"); return 2
        for i, state, title in seed(conn, engine, ROADMAP_ITEMS if a.roadmap else [],
                                    refresh_gates=set(a.refresh_gates or [])):
            print(f"{i}\t{state}\t{title}")
    elif a.cmd == "claim":
        ok, msg = claim(conn, engine, a.id, a.by)
        print(msg)
        return 0 if ok else 1
    elif a.cmd == "lease":
        it = renew_lease(conn, engine, a.id)
        print(json.dumps({"id": a.id, "lease_epoch": it and it.get("lease_epoch")}))
    elif a.cmd == "heartbeat":
        # Hanya indera. Tidak ada satu baris pun di bawah yang mengetik ke pane mana pun.
        print(json.dumps(heartbeat(conn, engine, a.quiet, a.gap), default=str, indent=2))
    elif a.cmd == "complete":
        ok, msg = finish(conn, engine, a.id, "COMPLETED", evidence=a.evidence)
        print(msg)
        return 0 if ok else 1
    elif a.cmd in ("block", "human"):
        ok, msg = finish(conn, engine, a.id, "BLOCKED" if a.cmd == "block" else "HUMAN",
                         reason=a.reason)
        print(msg)
        return 0 if ok else 1
    elif a.cmd == "next":
        nxt = next_item(conn, engine, origin=a.origin)
        print(json.dumps(nxt, default=str) if nxt else '{"item": null, "why": "antrean kosong"}')
    elif a.cmd == "list":
        for r in run(conn, engine, "SELECT id, title, status, claimed_by, phase, origin, "
                                   "ws_path, cli_engine FROM quest_tasks ORDER BY id"):
            print(f"{r['id']:>4}  {norm(r['status']):<10}  {(r['claimed_by'] or '-'):<12}  "
                  f"{(r['phase'] or '-'):<7}  {(r['origin'] or ORIGIN_LEGACY):<8}  "
                  f"{(r['cli_engine'] or '-'):<11}  {r['title'][:60]}")
    elif a.cmd == "queue":
        ok, msg, item_id = queue_command(conn, engine, a.title, a.engine, a.gate, a.ws, a.by)
        print(json.dumps({"ok": ok, "item": item_id, "why": msg}))
        return 0 if ok else 1
    elif a.cmd == "ws":
        if a.action == "list":
            print(json.dumps(list_workspaces(conn, engine), default=str, indent=2))
        elif a.action == "detect":
            root = Path(a.first).expanduser() if a.first else focused_workspace()
            print(json.dumps({"focus": str(root) if root else None,
                              "guidelines": detect_guidelines(root)}, default=str, indent=2))
        elif a.action == "create":
            ok, msg, ws_id = create_workspace(conn, engine, a.name, a.path)
            print(json.dumps({"ok": ok, "workspace": ws_id, "why": msg}))
            return 0 if ok else 1
        elif a.action == "focus":
            ok, msg = focus_workspace(conn, engine, a.first or a.path, a.by)
            print(json.dumps({"ok": ok, "why": msg, "focus": str(focused_workspace() or "")}))
            return 0 if ok else 1
        else:  # guidelines — permintaan mengubah, bukan membaca; selalu ditolak
            ok, msg = guidelines_are_locked()
            print(json.dumps({"ok": ok, "why": msg, "locked": True}))
            return 1
    elif a.cmd == "stalled":
        print(json.dumps(stalled(conn, engine), default=str, indent=2))
    elif a.cmd == "autopilot":
        mode = None if a.mode in (None, "show") else a.mode.upper()
        state = autopilot(conn, engine, mode, a.minutes, a.budget, a.by, a.reason)
        print(json.dumps(state, default=str, indent=2))
        # Keluar non-zero saat ditolak. `arm`/`disarm` sudah begitu, dan pemanggil yang sama
        # (Rust, shell, engine) tidak boleh punya dua kontrak: dengan exit 0 di sini, sebuah
        # penulisan mode yang DITOLAK terbaca sebagai keberhasilan oleh siapa pun yang hanya
        # melihat kode keluar — persis kelas "UNAVAILABLE bukan COMPLETED" yang dilarang invarian 5.
        return 1 if (state or {}).get("refused") else 0
    elif a.cmd == "arm":
        ok, msg = set_level(conn, engine, a.level, a.minutes, a.by)
        print(msg)
        return 0 if ok else 1
    elif a.cmd == "disarm":
        # Jalan mati tidak pernah minta izin — invarian 6, dan karena itu disarm sengaja tidak
        # memeriksa tty: kalau pagar yang sama menutup jalur kembali, ia bukan jalan mati.
        ok, msg = set_level(conn, engine, "observe", ARM_DEFAULT_MINUTES, a.by)
        print(msg)
        return 0 if ok else 1
    elif a.cmd == "drain":
        print(json.dumps(drain_level(conn, engine), default=str, indent=2))
    elif a.cmd == "decide":
        rows = decide(conn, engine, a.question, parse_options_arg(a.options), a.weight, a.item)
        row = rows[0]
        print(json.dumps(row, default=str, indent=2))
        if a.weight == "irreversible":
            print("-> ESCALATED: invarian 3, tidak dikirim ke resolver mana pun")
        elif a.route:
            ok, msg = broker(conn, engine, row["id"], a.timeout)
            print(msg)
            return 0 if ok else 1
    elif a.cmd == "answer":
        # Jawaban orang dicatat lebih dulu, baru disampaikan: kalau pengiriman ke pane gagal,
        # keputusannya tetap utuh di ledger. Urutan ini bukan gaya — kebalikannya akan membuat
        # satu pane yang sedang penuh menghapus keputusan operator.
        ok, msg, row = answer_decision(conn, engine, a.id, a.choice, a.by, a.note)
        target = None
        if ok and row and row.get("item_id"):
            item = get_item(conn, engine, int(row["item_id"])) or {}
            target = {"item": int(row["item_id"]), "cli_engine": item.get("cli_engine"),
                      "claimed_by": item.get("claimed_by"), "status": item.get("status")}
        print(json.dumps({"ok": ok, "message": msg, "decision": row, "target": target},
                         default=str, indent=2))
        return 0 if ok else 1
    elif a.cmd == "route":
        if a.dry_run:
            import cross_verify as cv
            rows = run(conn, engine, "SELECT * FROM decisions WHERE id=%s"
                       if engine == "POSTGRESQL" else "SELECT * FROM decisions WHERE id=?",
                       (a.id,))
            if not rows:
                print(f"decision {a.id} tidak ada")
                return 1
            d = rows[0]
            registry = cv.load_registry()
            opts = normalise_options(json.loads(d["options"] or "[]"))
            print(json.dumps({
                "state": d["state"], "weight": d["weight"],
                "rungs": [{"role": r, "registry_key": k,
                           "model": registry.get(k, {}).get("model"),
                           "kind": registry.get(k, {}).get("kind")}
                          for r, k in resolver_rungs()],
                "prompt": decision_prompt(d["question"], opts),
            }, indent=2, ensure_ascii=False))
            return 0
        ok, msg = broker(conn, engine, a.id, a.timeout)
        print(msg)
        return 0 if ok else 1
    elif a.cmd == "decisions":
        rows = run(conn, engine, "SELECT id, state, weight, rung_used, model_id, question, answer, "
                                 "note FROM decisions ORDER BY id DESC LIMIT "
                   + ("%s" if engine == "POSTGRESQL" else "?"), (a.limit,))
        if not rows:
            print("belum ada keputusan — antrean ini kosong artinya normal")
        for r in rows:
            who = r["model_id"] or ("MENUNGGU ORANG" if r["state"] != "RESOLVED" else "?")
            pick = ""
            if r["answer"]:
                first = r["answer"].splitlines()[0].strip()
                pick = first[:70]
            print(f"#{r['id']:<4} {r['state']:<10} {r['weight']:<12} "
                  f"{(r['rung_used'] or '-'):<14} {who:<28} {r['question'][:44]}")
            if pick:
                print(f"      ↳ {pick}")
            if r["state"] != "RESOLVED" and r["note"]:
                print(f"      ↳ {r['note'][:110]}")
        return 0
    elif a.cmd == "resolve":
        ok, msg = resolve_decision(conn, engine, a.id, a.rung, a.engine_key, a.model,
                                   a.answer, a.state, a.note)
        print(msg)
        return 0 if ok else 1
    elif a.cmd == "hold":
        # "Run yang tidak bisa mencapai verifier meng-commit lokal dan meninggalkan push di digest"
        # adalah janji §6. Janji itu butuh pemancar, bukan hanya paragraf.
        un = [x for x in (_git("rev-list", "@{u}..HEAD") or "").splitlines() if x.strip()]
        if un is None:
            print(json.dumps({"ok": False, "why": "git tidak terbaca"}, indent=2))
            return 1
        written = record_push_held(un, a.reason)
        print(json.dumps({"ok": bool(written), "held": len(written), "reason": a.reason}, indent=2))
        return 0 if written else 1
    elif a.cmd == "push-gate":
        g = push_gate()
        print(json.dumps(g, default=str, indent=2))
        return 0 if g.get("ok") else 1
    elif a.cmd == "push-verified":
        ok, msg, written = record_push_verified(conn, engine, a.model, a.sha)
        print(json.dumps({"ok": ok, "why": msg, "events": written}, default=str, indent=2))
        return 0 if ok else 1
    elif a.cmd == "digest":
        rows = run(conn, engine, "SELECT status, COUNT(*) AS n FROM quest_tasks GROUP BY status ORDER BY 2 DESC")
        open_dec = run(conn, engine, "SELECT COUNT(*) AS n FROM decisions WHERE state='OPEN'")
        push = push_state()
        if push["readable"]:
            note = (f"{push['unpushed']} commit belum ke origin; "
                    + (f"{push['verifier_verdicts']} di antaranya punya verdict push_verified"
                       if push["unpushed"] else "tidak ada yang tertunda"))
        else:
            note = "git tidak terbaca — UNKNOWN, bukan 'bersih'"
        print(json.dumps({"engine": engine, "per_status": rows,
                          "decisions_open": open_dec[0]["n"] if open_dec else 0,
                          "autopilot": autopilot(conn, engine),
                          "push_pending": push, "note": note}, default=str, indent=2))
    elif a.cmd == "status":
        print(json.dumps(status(conn, engine), default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
