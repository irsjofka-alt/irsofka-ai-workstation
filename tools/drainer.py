"""Drainer F10.3 — satu tick deterministik tanpa LLM yang memilih pekerjaan berikutnya dari ledger.

Roadmap F10.3 menuntut sebuah systemd timer yang menjalankan skrip tetap: ambil item PENDING
berikutnya, jalankan `check_command`-nya sebagai gerbang, lalu baru memanggil engine. Yang
menahan modul ini sampai sekarang bukan kemalasan — tangannya. Timer yang bisa mengetik ke pane
milik orang lain adalah timer yang bisa menggunting sesi kerja operator, jadi F10.8 (sakelar) dan
F10.7 (heartbeat) harus selesai lebih dulu supaya timer tahu kapan wajib diam.

Tangga tangan (`work_order.drain_level`) adalah satu-satunya penentu seberapa jauh tick ini boleh
bergerak, dan modul ini tidak pernah menaikkannya: menaiki tangga butuh terminal pengendali milik
manusia (`/dev/tty`), jadi seorang AI yang ditinggal sendirian secara struktural tidak bisa
mempersenjatai dirinya sendiri. Menurunkan tangan tetap bisa dari mana saja — invarian 6 bilang
jalan mati tidak boleh bergantung pada kerja sama AI.

    observe   membaca dan mencatat; tidak menjalankan apa pun, tidak menulis ledger
    dispatch  + menjalankan gerbang shell (grup proses sendiri, timeout) dan menulis hasil Angka
                keluar yang terukur; + menyerahkan item baru ke engine yang benar-benar menganggur
    resume    + mengetik CONTINUE ke engine yang memegang klaim dan terbukti AT_REST oleh F10.7

Pembagian itu bukan kenyamanan. `AT_REST` adalah kesimpulan dua sinyal independent; mendefinisikan
ulang "istirahat" di dalam timer adalah cara mesin berakhir mengetik ke sesi yang sedang di tengah
kalimat (§12). Karena itu modul ini memakai `wo.heartbeat()` apa adanya dan dilarang keras
menyentuh pembacaan pane mana pun — cek sumber di dalam selftest menegaskannya.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402
import work_order as wo  # noqa: E402

REPO = Path.home() / ".ai-station"
LOG_DIR = REPO / "logs"

CHECK_TIMEOUT = 240      # detik; gerbang yang lebih lambat dari ini bukan "sedang bekerja", tapi macet
GATE_COOLDOWN = 1800     # detik; kegagalan barusan tidak ditanya ulang setiap tick
CLAIM_COOLDOWN = 300     # detik; klaim yang ditolak dicoba lagi setelah jeda pendek
HOLDER = "drainer"       # pemegang klaim tanpa pane — alasannya di `hand_new_work`
# Jeda minimum antara dua pembacaan pane di jalur dispatch. Di bawah angka ini "teksnya tidak
# berubah" adalah tautologi, bukan pengamatan. Diwarisi dari konstanta yang sama yang dipakai
# `heartbeat`, supaya workstation hanya punya satu ukuran untuk "cukup lama untuk disebut diam".
MIN_PANE_GAP = wo.MIN_SAMPLE_GAP

# Invarian 3: tindakan yang tidak bisa dibatalkan berada di luar jangkauan tick mana pun. Daftar
# ini dipakai untuk MENOLAK, bukan untuk menjalankan dengan hati-hati, jadi kelebihan tangkap hanya
# membuat sebuah item naik ke antrean manusia — kegagalan yang masih bisa diterima. Celah di sini
# adalah kehilangan pekerjaan seseorang, jadi daftar yang longgar adalah harga yang benar.
UNDOABLE = (
    "rm -rf", "rm -fr", "sudo rm", "drop table", "drop database", "drop schema", "truncate ",
    "delete from", "mkfs", "dd if=", "> /dev/sd", "push --force", "push -f", "reset --hard",
    "clean -fd", "clean -df", "checkout --", "rebase", "kill-server", "pkill", "shutdown",
    "reboot", "stop postgresql", "uninstall", "destroy", "format",
)

# Yang dikirim ke engine yang memegang klaim dan terbukti istirahat. Arahan di dalamnya adalah
# kontrak F10.1 yang ditulis ulang di setiap sentuhan: pilihan level preferensi tidak boleh
# dilempar ke operator — mesin yang menjawab atas namanya, dan menyebut model yang menjawab.
CONTINUE_PROMPT = (
    "Lanjut: item {id} masih WORKING atas nama {who}, dan dua sinyal independent menyimpulkan "
    "kamu beristirahat ({why}). Kerjakan sampai gerbangnya lolos: "
    "`python3 tools/work_order.py complete {id} --evidence \"<perintah yang benar-benar lolos>\"`. "
    "Selama Autopilot ON jangan meminta saya memilih: untuk keputusan level preferensi jalankan "
    "`python3 tools/work_order.py decide ...` lalu `route`, biar resolver terdaftar yang menjawab. "
    "Hanya keadaan genting (data hilang, tidak bisa dibatalkan, kuota habis) yang boleh menunggu "
    "saya; kalau terhalang, lewati dan ambil item berikutnya lewat `next`."
)

# Yang dikirim ke engine pengangguran untuk item yang baru saja diklaim drainer. Item ini belum
# pernah disentuh siapa pun, jadi isinya adalah perintah mulai, bukan teguran.
START_PROMPT = (
    "Tugas baru dari drainer (Autopilot ON): item {id} — {title}. "
    "Klaim dulu supaya engine lain tidak ganda mengerjakannya, tapi drainer memegang lease item "
    "ini sampai kamu mulai: perpanjang dengan `python3 tools/work_order.py lease {id}`. "
    "Gerbang penerimaannya: `{gate}`. Kerja, lalu tutup dengan bukti angka keluar, bukan dengan "
    "prosa. Kalau tersangkut keputusan preferensi, jangan tanya saya — `decide` + `route`."
)

# Yang dikirim ke engine ketika yang memegang mesin adalah perintah orang, bukan antrean roadmap.
# Bedanya dengan START_PROMPT bukan hiasan: SEMI berarti ada manusia yang sedang mengawasi, jadi
# yang dikerjakan SATU perintah yang dia tulis sendiri, dan mengambil item roadmap adalah perluasan
# mandat yang tidak pernah dia berikan.
SEMI_PROMPT = (
    "Perintah operator dari Dashboard (Semi Autopilot): item {id} — {title}. "
    "Proyek baris ini: `{ws}` — kerjakan di direktori itu, bukan di ~/.ai-station. Drainer memegang "
    "lease sampai kamu mulai: perpanjang dengan `python3 tools/work_order.py lease {id}`. "
    "Gerbang penerimaannya: `{gate}`. Ada manusia di meja yang sedang melihat, jadi selesaikan SATU "
    "perintah ini lalu tutup dengan `complete {id} --evidence \"<perintah yang benar-benar lolos>\"` "
    "— bukti menyebut angka keluar dan direktori, bukan prosa. Jangan ambil item roadmap: SEMI hanya "
    "mengerjakan apa yang operator tulis sendiri ke SQL. Keputusan level preferensi tetap jangan "
    "ditanyakan ke dia: `decide` lalu `route`."
)


# --- pembacaan ----------------------------------------------------------------
def read_gate(conn, engine):
    """Tiga pembacaan yang menentukan apa yang boleh dilakukan tick ini — semuanya turunan."""
    return {"level": wo.drain_level(conn, engine),
            "autopilot": wo.autopilot(conn, engine),
            "night": wo.night_state(conn, engine)}


def undoable_in(*texts):
    """Kata kerja mana yang tak bisa dibatalkan, atau None. Mengembalikan bukti, bukan bool."""
    blob = " ".join((t or "").lower() for t in texts)
    return next((verb for verb in UNDOABLE if verb in blob), None)


def open_claims(conn, engine, holder=None):
    """Klaim yang masih berstatus kerja, dibaca sebagai baris — bukan dari ingatan tick sebelumnya."""
    if holder is None:
        return wo.run(conn, engine,
                      "SELECT id, title, status, claimed_by, check_command, resume_count, "
                      "lease_epoch, cli_engine, due_epoch, origin FROM quest_tasks "
                      "WHERE status IN ('WORKING','CLAIMED') ORDER BY id")
    return wo.run(conn, engine,
                  "SELECT id, title, status, claimed_by, check_command, resume_count, "
                  "lease_epoch, cli_engine, due_epoch, origin FROM quest_tasks "
                  f"WHERE status IN ('WORKING','CLAIMED') AND claimed_by={wo_p(engine)} ORDER BY id",
                  (holder,))


def wo_p(engine):
    """Placeholder per backend. Dua backend adalah dua dialek; yang menulis SQL dua kali pasti
    akan salah satu di antaranya, jadi bentuknya disatukan di sini."""
    return "%s" if engine == "POSTGRESQL" else "?"


def next_ready(conn, engine, origin=None, drop_stale_legacy=True):
    """Item berikutnya yang siap, dengan batas asal yang diminta pemanggil.

    `origin` diteruskan, tidak disaring di sini: "apa berikutnya" punya satu pemilik (`next_item`)
    dan drainer hanya meminjamnya. Menyaring di sisi drainer membuat dua definisi antrean.

    `drop_stale_legacy` default True karena fungsi ini adalah jalur PENAWARAN, dan kepala antrean
    legacy tanpa lease tidak bisa diselesaikan oleh jalur mana pun (keputusan 45, dijawab
    `qwen3.5:9b`). Laporan tidak memanggil fungsi ini untuk daftar — ia memakai `ready_items`
    tanpa flag itu, jadi baris yang ditahan tetap terlihat orang.
    """
    return wo.next_item(conn, engine, respect_due=True, origin=origin,
                        drop_stale_legacy=drop_stale_legacy)


# --- tulisan ------------------------------------------------------------------
def cooldown(conn, engine, item_id, seconds):
    """Tandai `due_epoch`; antrean patuh pada kolom ini lewat `next_item(respect_due=True)`."""
    wo.run(conn, engine,
           f"UPDATE quest_tasks SET due_epoch={wo_p(engine)} WHERE id={wo_p(engine)}",
           (wo.now() + int(seconds), item_id))


def refuse(conn, engine, item_id, emit, action, seconds=None, **rest):
    """Satu penolakan = tiga tulisan, di satu tempat.

    Peristiwa log, jeda, dan `escalation_reason` selama ini terpisah-pisah: enam cabang penolakan
    di `hand_new_work` memanggil `cooldown()` + `emit()`, dan tidak ada yang menyentuh alasan.
    Akibatnya baris yang ditolak gerbang punya `escalation_reason` NULL — identik dengan baris yang
    belum dilihat siapa pun — sementara satu-satunya jawaban yang dicari operator adalah "kenapa
    belum". Menyatukannya di satu fungsi membuat lubang itu tidak bisa muncul lagi hanya karena
    seseorang lupa satu baris di cabang baru.
    """
    cooldown(conn, engine, item_id, CLAIM_COOLDOWN if seconds is None else seconds)
    wo.note_refusal(conn, engine, item_id,
                    " · ".join(str(x) for x in (action,
                                               rest.get("why") or rest.get("note") or "") if x)[:500])
    emit(action, item=int(item_id), **rest)
    return None


def default_runner(cmd, timeout=CHECK_TIMEOUT):
    """Jalankan gerbang lewat call-worker yang berjanji mati bersama pemanggilnya (kontrak §4).

    Jalur subprocess biasa dengan timeout hanya membunuh anak langsung; satu panggilan yang
    menggantung pernah terbukti meninggalkan cucu yang tetap memegang RSS. `run_grouped` memulai
    perintah di sesi proses sendiri dan menyapu seluruh grupnya — termasuk pada jalur sukses.

    Direktori TIDAK lagi diputuskan di sini. `cd {REPO}` yang lama membuat setiap gerbang di dunia
    mengukur workstation, termasuk gerbang milik proyek lain — dan engine yang mengerjakan proyek A
    sementara vonisnya dibaca dari tree B adalah laporan tentang orang lain, bukan tentang dirinya.
    Yang memilih direktori sekarang hanya `gate_command`, dan satu baris hanya punya satu jawaban.
    """
    import call_workers as cw
    return cw.run_grouped(["bash", "-lc", cmd], timeout=timeout, text=True, merge_stderr=True)


def gate_command(item, command):
    """Bungkus gerbang dengan direktori milik baris ini — di-kuip, bukan disambungkan mentah.

    `ws_path` kosong berarti `REPO`, persis seperti sebelum kolom itu ada, sehingga baris lama yang ada sekarang tetap
    lama tidak perlu dipaksa punya proyek sebelum mereka memang punya satu.
    """
    cwd = shlex.quote(str(wo.resolve_workspace(item)))
    return f"cd {cwd} && {command}"


def default_sender(target, prompt):
    """Satu-satunya jalur tik ke pane: API daemon yang sudah ada.

    Modul ini tidak punya jalur kirim kedua — tidak ada tombol yang dikirim lewat mekanisme di luar
    API ini, karena tiga jalur kirim adalah tiga perilaku berbeda dan hanya satu yang bisa dihentikan
    sakelar. Balasan daemon adalah klaim daemon, BUKAN bukti: `/api/cli/run` mengembalikan
    "dispatched" walaupun sesi targetnya tidak ada, jadi yang dicatat di log adalah balasannya apa
    adanya, bukan "pasti sampai".
    """
    import cross_verify as cv
    return cv.daemon_post("/api/cli/run", {"prompt": prompt, "target_cli": target}, timeout=10)


# --- gerbang ------------------------------------------------------------------
def gate_item(conn, engine, item, runner=None, stage="after", emit=None):
    """Jalankan `check_command` satu item dan catat angkanya. `stage` menentukan artinya.

    `before` adalah baseline: item yang belum dikerjakan memang HARUS gagal di sini, jadi kegagalan
    tidak ditulis sebagai FAILED — itu akan meracuni ledger dengan angka yang tidak berarti dan,
    lebih parah, memicu circuit breaker atas pekerjaan yang belum dimulai. Kelulusan di sini berarti
    item sudah terpenuhi: ditutup dengan bukti terukur, tanpa memanggil engine mana pun.

    `after` adalah vonis: kelulusan menutup item, kegagalan menutupnya sebagai FAILED dan memberi
    jeda, karena mengulang gerbang yang sama setiap lima menit adalah cara kuota habis dalam sedetik.
    """
    runner = runner or default_runner
    emit = emit or (lambda *a, **kw: None)   # bentuk panggilan: (action, **isi)
    command = (item.get("check_command") or "").strip()
    item_id = int(item["id"])
    if not command:
        emit("GATE_NO_COMMAND", item=item_id, title=item.get("title"))
        return {"item": item_id, "exit": None, "why": "tanpa check_command"}

    verb = undoable_in(command, item.get("title"))
    if verb:
        # Invarian 3. Ditolak SEBELUM satu proses pun dibuat, dan item dinaikkan ke antrean manusia
        # supaya kepalanya tidak memacetkan antrean selamanya. Bukan PARKED: PARKED dihitung sebagai
        # kegagalan malam ini, padahal ini bukan kegagalan — ini penolakan yang benar.
        ok, msg = wo.finish(conn, engine, item_id, "HUMAN",
                            reason=f"invarian 3: gerbang memuat '{verb}' — tidak bisa dibatalkan")
        emit("REFUSED_UNDOABLE", item=item_id, verb=verb, escalated=bool(ok), why=msg)
        return {"item": item_id, "exit": None, "refused": verb}

    try:
        done = runner(gate_command(item, command), CHECK_TIMEOUT)
        code = int(getattr(done, "returncode", -1))
        tail = (getattr(done, "stdout", "") or "")[-200:].strip()
    except Exception as exc:  # noqa: BLE001 — gerbang yang meledak adalah gerbang yang gagal
        code, tail = -1, f"{type(exc).__name__}: {exc}"

    if code == 0:
        # Bukti menyebut direktori yang sebenarnya diukur. "exit 0: cargo test" tanpa tempat adalah
        # laporan yang bisa dibaca sebagai kelulusan tree mana pun — dan setelah kolom ws_path ada,
        # itu justru tree yang salah.
        wo.finish(conn, engine, item_id, "COMPLETED",
                  evidence=f"drainer gate ({stage}) exit 0: {gate_command(item, command)[:180]}")
        emit("GATE_PASSED", item=item_id, exit=code, stage=stage, title=item.get("title"))
    elif stage == "before":
        emit("GATE_BASELINE", item=item_id, exit=code, stage=stage, out=tail)
    else:
        wo.finish(conn, engine, item_id, "FAILED",
                  reason=f"drainer gate exit {code}: {command[:180]}")
        cooldown(conn, engine, item_id, GATE_COOLDOWN)
        emit("GATE_FAILED", item=item_id, exit=code, stage=stage, out=tail)
    return {"item": item_id, "exit": code}


def reconcile(conn, engine, mode, runner=None, emit=None):
    """Vonis atas klaim drainer yang masih hidup: gerbang diulang, angka keluar yang bicara."""
    emit = emit or (lambda *a, **kw: None)   # bentuk panggilan: (action, **isi)
    wanted = (mode or "").upper()
    if wanted not in ("ON", "SEMI"):
        # Sama seperti hand_new_work: mode yang tidak dikenal bukan berarti "kerjakan semuanya".
        # Di sini yang dipertaruhkan adalah MENJALANKAN check_command milik baris roadmap.
        emit("MODE_UNKNOWN", where="reconcile", mode=str(mode),
             why="tidak ada gerbang yang dijalankan")
        return
    for row in open_claims(conn, engine, holder=HOLDER):
        if wanted == "SEMI" and (row.get("origin") or "legacy") != wo.ORIGIN_OPERATOR:
            # `reconcile` MENJALANKAN check_command baris itu. Di bawah SEMI menjalankan shell
            # milik baris roadmap adalah pekerjaan yang tidak pernah diminta operator, walau
            # klaimnya tertinggal dari ON sebelumnya. Barisnya tidak dihukum: hanya ditahan.
            emit("HELD_BY_MODE", item=int(row["id"]), origin=row.get("origin") or "legacy",
                 why="klaim tertinggal dari mode lain — SEMI tidak menjalankan gerbang roadmap")
            continue
        if int(row.get("lease_epoch") or 0) > wo.now():
            # Lease masih dipegang: pekerjaan sedang berjalan atau engine belum mulai. Vonis
            # dijatuhkan setelah lease habis — lebih awal dari itu artinya menyebut pekerjaan orang
            # gagal hanya karena ia belum selesai.
            wo.renew_lease(conn, engine, int(row["id"]))
            emit("HELD", item=int(row["id"]), lease_left=int(row["lease_epoch"]) - wo.now())
            continue
        gate_item(conn, engine, row, runner=runner, stage="after", emit=emit)


def _release_claim(conn, engine, item_id):
    """Tarik kembali klaim drainer lewat pemilik kosakatanya, dan hanya kalau masih miliknya.

    `WHERE id=?` saja pernah cukup di sini. Audit putaran lima menunjuk yang sebaliknya: pelepasan
    tanpa syarat `claimed_by` bisa menghapus klaim pihak lain yang baru saja menang, dan itu
    membuat dua engine mengerjakan satu baris sambil keduanya punya bukti bahwa mereka yang pegang.
    `resume_count` sengaja TIDAK direset — itu anggaran seumur hidup baris, bukan anggaran tick.
    """
    wo.release_claim(conn, engine, int(item_id), HOLDER)


def effective_gap(gap):
    """Berapa lama benar-benar menunggu antara dua pembacaan pane. Tidak bisa dinegosiasi.

    Lantai ini pernah ditulis sebagai `gap if capture is not None else max(...)` dan jadi kode
    mati: `capture` sudah diisi default beberapa baris di atasnya, sehingga selalu bukan-None.
    Ceknya lolos karena hanya mencari string-nya di sumber. Versi kedua memakai "pembacanya
    disuntik?" sebagai syarat bebas tidur — dan audit putaran tiga menunjuk itu sebagai pencampuran
    dua hal: pemanggil produksi yang menyuntik pembaca sendiri bisa meminta gap=0 lagi.

    Jadi lantainya unconditional. Selftest tidak melambat karena yang disuntik adalah SLEEPER-nya,
    bukan aturan jeda-nya.
    """
    return max(gap, MIN_PANE_GAP)


def pane_is_quiet(target, capture=None, busy=None, gap=MIN_PANE_GAP, sleeper=None, pane_state=None):
    """Bukti bahwa pane target tidak sedang dipakai.

    Bukti pertama adalah yang paling murah dan paling benar: apa yang MESIN ITU SENDIRI nyatakan
    lewat judul pane. Qodercli menulis Working selama satu giliran berjalan dan Ready di prompt yang
    diam; kalau itu terbaca, tidak ada ruang untuk menebak. Yang di bawahnya — glyph spinner dan
    perbandingan dua pembacaan — adalah bukti tidak langsung untuk mesin yang tidak menyatakan
    apa pun (antigravity menulis "pop-os" sebagai judulnya), dan bukti tidak langsung itulah yang
    membuat qoder tampak diperlakukan berbeda: chrome miliknya berdetak, jadi layar utuh tidak pernah
    sama dua kali, jadi pane yang diam pun disebut sibuk.

    None dari pembacaan mana pun = UNKNOWN = jangan mengetik (invarian 5). Jeda tidak bisa
    dinolkan: `effective_gap` memaksa lantai, dan tes tidak tidur karena SLEEPER-nya yang disuntik
    lewat `hand_new_work`/`tick` — bukan karena aturannya dilonggarkan.
    """
    import cross_verify as cv
    capture = capture or cv.capture_pane
    busy = busy or cv.pane_is_busy
    sleeper = sleeper or time.sleep
    declared = (pane_state or cv.pane_state)(target)
    if declared == "working":
        return {"quiet": False, "why": "CLI menyatakan Working lewat judul pane — ia sedang "
                                       "mengerjakan sesuatu, tidak ada yang boleh diketik"}
    if declared == "ready":
        return {"quiet": True, "why": "CLI menyatakan Ready lewat judul pane"}
    first = capture(target)
    if first is None:
        return {"quiet": False, "why": "pane tidak terbaca — UNKNOWN, bukan diam"}
    if busy(first):
        return {"quiet": False, "why": "pane sedang menampilkan pekerjaan (spinner / esc to cancel)"}
    # `gap=0` hanya dihormati kalau pembacanya disuntik (selftest tidak boleh tidur). Dengan
    # pembacaan nyata, dua frame yang diambil berbarengan SELALU sama, dan menyebut itu "diam"
    # adalah mengarang pengamatan — persis yang dilarang invarian 5.
    wait = effective_gap(gap)
    if wait > 0:
        sleeper(wait)
    second = capture(target)
    if second is None:
        return {"quiet": False, "why": "pembacaan kedua gagal — UNKNOWN, bukan diam"}
    if busy(second):
        return {"quiet": False, "why": "pane menjadi sibuk di antara dua pembacaan"}
    if cv.last_frame(second) != cv.last_frame(first):
        return {"quiet": False, "why": "teks pane berubah antar pembacaan — masih bekerja"}
    return {"quiet": True, "why": f"dua pembacaan dengan jeda {wait}s: tidak berubah, tanpa spinner"}


def _as_path(text):
    """Path yang siap dibandingkan dengan `resolve_workspace` — `~` diperluas lebih dulu."""
    return Path(text).expanduser().resolve(strict=False)


def hand_new_work(conn, engine, mode, runner=None, sender=None, emit=None,
                  capture=None, busy=None, tab_project=None, pane_cwd=None, gap=MIN_PANE_GAP,
                  sleeper=None, dirty=wo._MEASURE, for_item=None):
    """Serahkan satu item baru ke engine yang ditunjuk barisnya. Tidak pernah menebak tujuan.

    `for_item` adalah jalur perintah instan (operator baru saja menulis perintahnya dan jawabannya
    tidak boleh menunggu tick berikutnya): barisnya sudah diketahui, jadi tidak perlu diambil dari
    kepala antrean. Semuanya tetap lewat pagar yang sama persis — gerbang, pane tenang, direktori
    tab, klaim bersyarat, pelepasan saat kirim gagal. Dua jalur dengan dua set pagar adalah dua
    aturan, dan yang lebih longgar akan menjadi yang benar-benar berjalan.

    Mesin mana yang mengerjakan adalah milik kolom `cli_engine` pada baris itu — operator menyuntingnya
    dari Station, dan kontrak melarang memilih engine dari ingatan (§12). Baris tanpa tujuan ditolak,
    bukan diisi dengan favorit tick ini.

    Drainer mengklaim atas namanya sendiri supaya tidak ada dua klaim atas item yang sama malam itu;
    HOLDER tidak punya pane, jadi `heartbeat` selalu menjawab UNKNOWN untuknya dan jalur resume tidak
    akan pernah bisa mengetuknya — aman secara konstruksi, bukan karena ada yang ingat menjaga.

    `mode`/`origin` adalah seluruh beda SEMI dengan FULL AUTO di sisi pengiriman: SEMI hanya mengambil
    baris `origin='operator'`, dan ia menunggu satu engine menyelesaikan perintahnya sebelum memberi
    perintah kedua. Yang terakhir itu jawaban untuk "kalau melihat kamu Working, nunggu sampai
    selesai": mesin yang sedang mengerjakan sesuatu tidak ditumpuki, karena dua perintah dalam satu
    pane bukan dua pekerjaan — yang satu akan tertelan yang lain dan keduanya tercatat jalan.
    ON tidak mendapat aturan ini: tidak ada pengawas yang menunggui giliran, dan memperlambat
    semalam untuk sebuah kesamaan yang tidak diminta adalah penurunan, bukan penguatan.
    """
    emit = emit or (lambda *a, **kw: None)   # bentuk panggilan: (action, **isi)
    # Keranjang Diturunkan dari mode, tidak pernah diterima sebagai argumen terpisah. Selama
    # `origin` masih bisa dikirim sendiri, `hand_new_work(conn, engine, "SEMI")` tanpa origin
    # mengambil baris roadmap dan mengirimnya dengan SEMI_PROMPT — satu-satunya penahan adalah
    # `claim()` yang membaca mode dari database, dan itu pagar yang bisa dilewati pemanggil yang
    # tidak tahu. Satu sumber kebenaran, tidak ada pasangan yang bisa tidak cocok.
    # Dinormalisasi dan gagal-TERTUTUP. `mode` dari pemanggil bisa "semi", "SEMI", "OFF", atau
    # salah tulis; selama branch-nya "else = keranjang penuh", mode yang tidak dikenal memberi
    # akses ke antrean roadmap. Yang tidak dikenal bukan "ON", melainkan "tidak mengirim apa pun".
    wanted = (mode or "").upper()
    if wanted not in wo.MODES:
        emit("MODE_UNKNOWN", mode=str(mode), why=f"bukan {'/'.join(wo.MODES)} — tidak ada yang "
             "diambil, tidak ada yang diketik")
        return None
    if wanted == wo.OFF_MODE:
        # OFF adalah state yang SAH, bukan MODE_UNKNOWN — dan ia tetap tidak boleh mengirim apa
        # pun. Keduanya harus dibedakan: orang yang menekan berhenti berhak membaca "OFF" di log,
        # bukan "mode tidak dikenal", sementara mesin tetap tidak mengetik. Tidak ada baris yang
        # terpilih di titik ini, jadi tidak ada yang boleh didenda cooldown atau ditulis alasan.
        emit("MODE_OFF", mode=str(mode),
             why="mesin sedang OFF — tidak ada yang diambil, tidak ada yang diketik")
        return None
    origin = wo.ORIGIN_OPERATOR if wanted == "SEMI" else None
    if for_item is not None:
        # Jalur instan: barisnya sudah ditunjuk oleh orang yang menulis perintahnya. Keranjang SEMI
        # TETAP ditegakkan — oleh `claim()`, pada saat penulisan — jadi memanggil jalur ini dengan
        # mode SEMI atas satu baris roadmap berakhir CLAIM_REFUSED, bukan pengiriman.
        item = for_item
    else:
        item = next_ready(conn, engine, origin=origin)
        if not item and wanted == "ON":
            # ON tanpa antrean tidak berarti mesinnya boleh diam sampai pagi. Satu baris 'idle'
            # dibuat HANYA kalau ledger menunjukkan utang yang terukur, dan baris itu lalu masuk
            # ke jalur yang sama persis di bawah sini — gerbang, pane tenang, direktori tab, klaim
            # bersyarat, pelepasan saat kirim gagal. Tidak ada jalur kedua dengan pagar sendiri.
            # SEMI tidak pernah sampai ke sini: `wanted == "ON"` adalah satu-satunya gerbangnya,
            # dan `claim()` tetap menolak keranjangnya sebagai lapis kedua.
            # Ditulis sebagai kata kunci, bukan posisi: argumen kedua `ensure_idle_item` adalah
            # JENIS DATABASE, dan argumen ketiganya — mesin tujuan — sengaja tidak diisi supaya
            # diturunkan dari ledger dan divalidasi ke registri di dalam fungsi itu. `engine,` tanpa
            # nama mudah dibaca sebagai "mesin ini", dan membacanya begitu akan menulis baris yang
            # menunjuk mesin bernama 'SQLITE'. Tesnya ada di selftest: panggilan ini direkam dan
            # backend-nya harus masuk sebagai backend, bukan sebagai tujuan.
            item = wo.ensure_idle_item(conn, backend=engine, emit=emit)
        if not item:
            emit("QUEUE_EMPTY", origin=origin or "apa pun")
            return None
    full = wo.get_item(conn, engine, int(item["id"])) or item
    # Baseline dulu, sebelum klaim: item yang gerbangnya sudah lolos tidak pernah perlu dikerjakan.
    verdict = gate_item(conn, engine, full, runner=runner, stage="before", emit=emit)
    if verdict.get("refused") or verdict.get("exit") == 0:
        return verdict
    target = (full.get("cli_engine") or "").strip()
    if not target:
        # Ditolak SEBELUM ada klaim, bukan sesudahnya: baris WORKING tanpa pemegang adalah laporan
        # bahwa ada yang mengerjakan padahal tidak ada, dan mengarang laporan itu sebentar pun sudah
        # cukup untuk dibaca engine lain pada saat yang salah.
        return refuse(conn, engine, int(item["id"]), emit, "NO_TARGET",
                      why="kolom cli_engine kosong — memilih engine dari ingatan dilarang, jadi "
                          "item ini menunggu orang mengisinya")

    # --- dua pagar yang sebelumnya tidak ada di jalur ini (temuan audit peer, 2026-10-08) ------
    # (1) PANE BUTA. Jalur ini membaca SQL lalu langsung mengetik; SQL tidak tahu ada orang yang
    #     sedang menulis di pane itu.
    quiet = pane_is_quiet(target, capture=capture, busy=busy, gap=gap, sleeper=sleeper)
    if not quiet["quiet"]:
        return refuse(conn, engine, int(item["id"]), emit, "PANE_NOT_QUIET", target=target,
                      why=quiet["why"],
                      note="tidak ada yang diklaim dan tidak ada yang diketik — dicoba lagi "
                           "setelah jeda")

    # (2) DIREKTORI BUTA. `semi_wake.py` sudah menjaga ini; jalur timer belum. Tugas proyek A yang
    #     masuk ke pane yang sedang berdiri di proyek B membuat engine mengerjakan satu tree sambil
    #     melaporkan tree lain — kebingungan identitas yang persisnya dilarang operator.
    if tab_project is None:
        import cross_verify as cv
        read_conf = cv.tab_project
    else:
        read_conf = tab_project
    if pane_cwd is None:
        import cross_verify as cv
        read_live = cv.pane_cwd
    else:
        read_live = pane_cwd
    where = read_conf(target)      # proyek yang dikonfigurasi untuk tab ini
    live = read_live(target)       # dicatat untuk diagnostik; BUKAN pagar — alasannya di bawah
    want = wo.resolve_workspace(full)
    if where is None:
        return refuse(conn, engine, int(item["id"]), emit, "PANE_DIR_UNKNOWN", target=target,
                      why="proyek tab tidak terbaca dari cli_profiles — UNKNOWN, dan UNKNOWN tidak "
                          "pernah boleh mengetik")
    if not wo.row_workable_here(want, where):
        return refuse(conn, engine, int(item["id"]), emit, "PANE_WRONG_DIR", target=target,
                      tab_project=str(where), pane_now=str(live) if live else None,
                      wants=str(want),
                      why="tab target adalah proyek lain — perintah ini tidak akan dikerjakan di "
                          "tree yang bukan miliknya")

    # Prompt disusun SEBELUM klaim, sama seperti di `semi_wake.py`. Alasannya bukan gaya:
    # `.format()` di bawah klaim berarti satu KeyError meninggalkan baris WORKING tanpa ada yang
    # mengirim, dan itu kelas bug yang baru dihapus di jalur hook tapi masih hidup di sini.
    if wanted == "SEMI":
        prompt = SEMI_PROMPT.format(id=item["id"], title=item["title"], ws=want,
                                    gate=(full.get("check_command") or "tanpa gerbang"))
    else:
        prompt = START_PROMPT.format(id=item["id"], title=item["title"],
                                     gate=(full.get("check_command") or "tanpa gerbang"))

    ok, msg = wo.claim(conn, engine, int(item["id"]), HOLDER, mode=wanted, dirty=dirty)
    if not ok:
        # `claim()` yang memutuskan; di sini hasilnya hanya diterjemahkan menjadi nama kejadian,
        # supaya "engine masih mengerjakan satu perintah" terbaca berbeda dari "pemutus terbuka".
        _act = "ENGINE_BUSY" if msg.startswith("ENGINE_BUSY") else "CLAIM_REFUSED"
        return refuse(conn, engine, int(item["id"]), emit, _act, target=target, why=msg)
    delivered = None
    try:
        reply = (sender or default_sender)(target, prompt)
        delivered = {"item": int(item["id"]), "target": target, "mode": mode,
                     "tab_project": str(where), "pane_now": str(live) if live else None,
                     "reply": str(reply)[:200]}
    except Exception as exc:  # noqa: BLE001 — kegagalan kirim dicatat, tidak dikarang ulang
        # Klaim DILEPAS. Versi lama meninggalkan baris WORKING tanpa ada yang mengerjakan dan
        # tetap mengembalikan bentuk seperti sukses — dari luar terlihat terdistribusi, dan engine
        # itu terkunci ENGINE_BUSY sampai lease habis. Persis kelas bug yang diklaim sudah dihapus.
        _release_claim(conn, engine, item["id"])
        cooldown(conn, engine, int(item["id"]), CLAIM_COOLDOWN)
        emit("SEND_FAILED", item=int(item["id"]), target=target, released=True,
             why=f"{type(exc).__name__}: {exc}")
        return {"item": int(item["id"]), "target": target, "sent": False,
                "why": f"{type(exc).__name__}: {exc}"}
    # Pencatahan terjadi DI LUAR try, dan ini bukan kerapian. Pada titik ini `sender` sudah
    # mengetik ke pane. Kalau baris log-nya melempar dan pengecualian itu jatuh ke except di
    # atas, klaim akan dilepas padahal pekerjaannya sedang berjalan — ledger lalu mengatakan
    # tidak ada yang mengerjakan sesuatu yang sedang dikerjakan, dan tick berikutnya boleh
    # mengirim ulang perintah yang sama ke mesin yang sama. Satu kiriman yang hilang masih bisa
    # diulang; satu ketikan ganda di tengah pekerjaan orang lain tidak bisa ditarik kembali.
    emit("WORK_DISPATCHED", **delivered)
    return {"item": int(item["id"]), "target": target, "sent": True}


def deliver_now(conn, engine, item_id, mode=None, sender=None, capture=None, busy=None,
                tab_project=None, pane_cwd=None, gap=MIN_PANE_GAP, sleeper=None, runner=None,
                log=None, dirty=wo._MEASURE):
    """Antarkan satu baris yang BARUSAN ditulis orang — jawaban untuk "jangan tunggu tick".

    Operator mengatakannya langsung: jalur SQL→CLI selama ini hanya hidup di dalam timer, jadi
    perintah yang dia tulis bisa menggantung sampai lima menit dan dia tidak pernah melihat kenapa.
    Fungsi ini memanggil jalur yang sama pada saat penulisan, dengan pagar yang sama, dan TIDAK
    menambahkan aturan baru: keranjang, klaim bersyarat, pane tenang, direktori tab semuanya
    milik `hand_new_work` dan `claim()`.

    Yang tetap tidak boleh dilakukannya: bergerak tanpa tangan. Tangga `level` diperiksa di sini,
    dan kalau masih `observe` barisnya TIDAP dikirim dan TIDAK dilepas — tetap PENDING, dan
    penolakannya dilaporkan sebagai kata, bukan sebagai keberhasilan semu. Sebuah API yang membalas
    "terkirim" padahal tidak ada yang diketik adalah cara tercepat membuat orang percaya pada
    antrean yang sebenarnya tidak berjalan.
    """
    row = wo.get_item(conn, engine, int(item_id))
    if not row:
        return {"sent": False, "refused": f"item {item_id} tidak ditemukan di ledger"}
    ap = wo.autopilot(conn, engine) or {}
    wanted = (mode or ap.get("mode") or "").upper()
    if wanted not in wo.MODES:
        return {"sent": False, "queued": True, "refused": "MODE_UNKNOWN",
                "why": f"mode {ap.get('mode')!r} bukan {'/'.join(wo.MODES)}"}
    if wanted == wo.OFF_MODE:
        # OFF adalah keadaan, bukan jendela yang kebetulan lewat. Dinamai sendiri supaya yang
        # dibaca operator dan yang tersimpan di ledger menyebut alasan yang sebenarnya: mesin ini
        # disuruh berhenti, dan perintah orang TETAP.antre sampai salah satu mode kerja dipilih lagi.
        return {"sent": False, "queued": True, "refused": "MODE_OFF",
                "why": f"mesin sedang OFF — {ap.get('off_reason') or 'tidak ada alasan tercatat'}; "
                       "perintah tetap antre, pilih SEMI atau FULL AUTO dari Dashboard untuk "
                       "membuka kembali"}
    # Tangga `level` TIDAK menahan jalur ini, dan itu keputusan yang diminta operator langsung:
    # "kalau saya mode semi, saya tidak perlu arm dispatch, saat saya kirim command AI harus jawab".
    # `level` menjaga gerakan yang DIMULAI MESIN — timer yang mengambil antrean, CONTINUE yang
    # diketik ke pane orang. Sebuah perintah yang barusan ditulis orang adalah niat manusia yang
    # sudah ada di ledger sebelum fungsi ini dipanggil, jadi menyimpan satu pagar lagi di depannya
    # hanya membuat SEMI terasa mati. Yang tetap menahan: keranjang (`claim()` menolak baris roadmap
    # di bawah SEMI), satu perintah aktif per engine, pane yang harus terbukti tenang dan benar
    # proyeknya, dan JENDELA di bawah ini — rem `berhenti` menulis jendela ke masa lalu, dan selama
    # jendela tertutup perintah pun tidak diantar. `disarm` masih menurunkan tangan: mesin berhenti
    # berinisiatif, perintah orang tetap sampai.
    lvl = wo.drain_level(conn, engine)
    window = int(ap.get("until_epoch") or 0)
    if window and window <= wo.now():
        return {"sent": False, "queued": True, "refused": "WINDOW_CLOSED",
                "why": f"jendela {wanted} lewat pada {window} — perintah tetap antre; "
                       "buka lagi dari Dashboard atau `autopilot semi`"}
    events = []

    def emit(action, **rest):
        # Selalu isi akumulator LEBIH DULU, baru serahkan ke logger pemanggil. Versi pertama
        # memakai `log or lambda...`, jadi pemanggil yang menyuplai logger sendiri membuat
        # akumulator ini tetap kosong — dan `sent` di bawah dihitung dari akumulator itu. Hasilnya:
        # pengiriman yang sungguh-sungguh berhasil dilaporkan GAGAL, diam-diam, tepat pada jalur
        # yang dipakai orang menulis perintah. Kegagalan palsu lebih murah daripada keberhasilan
        # palsu, tapi tetap saja satu cara membuat orang berhenti mempercayai antreannya.
        row = {"ts": wo.now(), "action": action, "level": lvl.get("level"), "mode": wanted, **rest}
        events.append(row)
        if log:
            log(action, **rest)
    hand_new_work(conn, engine, wanted, runner=runner, sender=sender, emit=emit,
                  capture=capture, busy=busy, tab_project=tab_project, pane_cwd=pane_cwd,
                  gap=gap, sleeper=sleeper, for_item=row, dirty=dirty)
    acts = [e.get("action") for e in events]
    dispatched = [e for e in events if e.get("action") == "WORK_DISPATCHED"]
    # `sent` bukan "ada baris WORK_DISPATCHED di suatu tempat". Yang diakui di sini adalah: ada
    # penyerahan yang tercatat, DAN tidak ada kegagalan kirim pada putaran yang sama. Dua-duanya
    # diperiksa karena `log=` dari pemanggil membuat akumulator lokal tetap kosong — dan kalau
    # `sent` hanya dibaca dari akumulator itu, jalur yang disuntik pelapor akan SELALU menjawab
    # False, sementara jalur tanpa pelapor menjawab True walau SEND_FAILED ikut tercatat.
    sent = bool(dispatched) and "SEND_FAILED" not in acts
    out = {"sent": sent, "item": int(item_id), "mode": wanted, "level": lvl.get("level"),
           "events": acts, "detail": events}
    if dispatched:
        # Balasan daemon ikut dibawa pulang. `default_sender` sendiri sudah didokumentasikan sebagai
        # klaim daemon, bukan bukti ketikan; pemanggil yang menampilkan "diserahkan" harus bisa
        # menunjukkan apa yang daemon jawab, bukan hanya bahwa fungsinya kembali.
        out["reply"] = str(dispatched[-1].get("reply") or "")[:200] or None
        out["target"] = dispatched[-1].get("target")
    return out


def nudge_resting(conn, engine, mode, beats=None, sender=None, emit=None):
    """CONTINUE untuk klaim yang terbukti istirahat — anggaran dihitung oleh `wo.nudge`.

    Di bawah SEMI hanya klaim atas perintah operator yang boleh disentuh: menaikkan `CONTINUE` ke
    pane yang sedang memegang item roadmap adalah cara memperluas mandat SEMI tanpa ada yang
    mengangkatnya, dan pagar yang bisa dilebarkan sendiri bukan pagar.
    """
    emit = emit or (lambda *a, **kw: None)   # bentuk panggilan: (action, **isi)
    sender = sender or default_sender
    wanted = (mode or "").upper()
    if wanted not in ("ON", "SEMI"):
        emit("MODE_UNKNOWN", where="nudge_resting", mode=str(mode),
             why="tidak ada CONTINUE yang dikirim")
        return
    if beats is None:
        beats = wo.heartbeat(conn, engine)
    for b in beats:
        if wanted == "SEMI":
            row = wo.get_item(conn, engine, int(b["id"])) or {}
            if (row.get("origin") or "legacy") != wo.ORIGIN_OPERATOR:
                emit("SKIP_NOT_OPERATOR", item=b["id"], who=b["claimed_by"],
                     origin=row.get("origin") or "legacy",
                     why="SEMI tidak menegur pekerjaan roadmap — naikkan ke ON untuk itu")
                continue
        if b["state"] != "AT_REST":
            emit("SKIP_NOT_RESTING", item=b["id"], who=b["claimed_by"], state=b["state"],
                 why=b["why"])
            continue
        ok, msg = wo.nudge(conn, engine, int(b["id"]))
        if not ok:
            wo.finish(conn, engine, int(b["id"]), "PARKED", reason=f"budget resume habis: {msg}")
            emit("STUCK_PARKED", item=int(b["id"]), who=b["claimed_by"], why=msg)
            continue
        prompt = CONTINUE_PROMPT.format(id=b["id"], who=b["claimed_by"], why=b["why"])
        try:
            reply = sender(b["claimed_by"], prompt)
            emit("CONTINUE_SENT", item=int(b["id"]), who=b["claimed_by"], budget=msg,
                 reply=str(reply)[:200])
        except Exception as exc:  # noqa: BLE001
            emit("SEND_FAILED", item=int(b["id"]), who=b["claimed_by"],
                 why=f"{type(exc).__name__}: {exc}")


ANSWER_PROMPT = (
    "JAWABAN OPERATOR untuk keputusan #{decision}, item #{item}:\n"
    "  {answer}\n"
    "Jawaban ini tercatat di tabel decisions sebagai state=ANSWERED, answered_by={by} — datang dari "
    "manusia di meja, bukan dari resolver. Kerjakan sekarang. Jangan ajukan pertanyaan yang sama "
    "untuk kedua kalinya; kalau pilihannya berubah, itu pertanyaan baru, bukan suntingan sejarah."
)


def deliver_answer(conn, engine, decision_id, sender=None, beats=None, capture=None, busy=None):
    """Antar satu jawaban yang barusan ditulis ke mesin yang menunggunya — atau laporkan mengapa tidak.

    Ini tangan, dan pembedanya dari `nudge_resting` bukan kecil: `nudge_resting` dipanggil timer dan
    mengetik `CONTINUE` atas nama mesin, sementara fungsi ini hanya dipanggil dari satu klik manusia
    (`POST /api/decide`) dan mengetik jawaban manusia atas nama manusia. Sakelar OFF melindungi orang
    yang tidur dari mesin yang mempersenjatai dirinya sendiri — bukan dari jawabannya sendiri.
    `tick()` tidak pernah menyebut fungsi ini, dan selftest menegakkan dua-duanya: token kirim tidak
    ada di jalur timer, dan nama fungsi ini tidak ada di badan `tick`.

    `AT_REST` tetap diminta walau yang menekan tombol adalah operator. Alasannya bukan izin, tapi
    keselamatan pekerjaan: teks yang masuk ke pane yang sedang di tengah kalimat mengacaukan giliran
    mesin itu, dan yang hilang adalah pekerjaannya, bukan cuma lognya. Untuk item tanpa klaim terbuka
    — kasus normal, karena item berhenti di `HUMAN` justru sambil menunggu ini — buktinya satu
    pembacaan pane: tidak sedang menampilkan spinner, dan terbaca.

    Yang tidak dilakukan: memilih engine. Tujuannya `cli_engine` pada baris item, atau tidak ada
    tujuan sama sekali (§12).
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    rows = wo.run(conn, engine, f"SELECT * FROM decisions WHERE id={ph}", (decision_id,))
    decision = rows[0] if rows else None
    if not decision:
        return {"ok": False, "sent": False, "why": f"decision {decision_id} tidak ada"}
    if decision.get("state") != "ANSWERED":
        return {"ok": False, "sent": False,
                "why": f"decision {decision_id} berstatus {decision.get('state')}: "
                       "yang diantar hanya jawaban yang sudah tercatat"}
    item_id = decision.get("item_id")
    if not item_id:
        return {"ok": False, "sent": False,
                "why": f"decision {decision_id} tidak menautkan item: jawabannya ada di ledger, "
                       "tapi tidak ada pekerjaan yang bisa dibangunkan"}
    item = wo.get_item(conn, engine, int(item_id)) or {}
    target = (item.get("cli_engine") or "").strip()
    if not target:
        return {"ok": False, "sent": False, "why": f"item {item_id} tidak punya cli_engine; "
                                                  "tujuan tidak dipilih dari kebiasaan"}
    if beats is None:
        beats = wo.heartbeat(conn, engine)
    held = next((b for b in beats if int(b["id"]) == int(item_id)), None)
    if held is not None:
        if held["state"] != "AT_REST":
            return {"ok": False, "sent": False, "target": target, "item": int(item_id),
                    "why": f"klaim masih {held['state']} ({held['why']}): jawaban sudah tercatat, "
                           "pengiriman ditunda — bukan digagalkan"}
        proof = f"heartbeat AT_REST ({held['why']})"
    else:
        import cross_verify as cv
        capture = capture or cv.capture_pane
        busy = busy or cv.pane_is_busy
        text = capture(target)
        if not text:
            return {"ok": False, "sent": False, "target": target, "item": int(item_id),
                    "why": f"pane {target} tidak terbaca: UNKNOWN bukan berarti diam"}
        if busy(text):
            return {"ok": False, "sent": False, "target": target, "item": int(item_id),
                    "why": f"pane {target} sedang bekerja: jawaban tercatat, diantar nanti"}
        proof = f"tidak ada klaim terbuka; pane {target} terbaca tanpa pekerjaan berjalan"
    prompt = ANSWER_PROMPT.format(
        decision=decision_id, item=int(item_id),
        answer=(decision.get("answer") or "").strip()[:1500],
        by=decision.get("answered_by") or decision.get("model_id") or "operator")
    sender = sender or default_sender
    try:
        reply = (sender(target, prompt) or "")
    except Exception as exc:  # noqa: BLE001 — pengiriman gagal bukan berarti jawaban hilang
        return {"ok": True, "sent": False, "target": target, "item": int(item_id),
                "why": f"tersimpan, pengiriman gagal: {type(exc).__name__}: {exc}"}
    return {"ok": True, "sent": True, "target": target, "item": int(item_id),
            "proof": proof, "reply": str(reply)[:300]}


# --- satu tick ----------------------------------------------------------------
def tick(conn, engine, gate=None, beats=None, runner=None, sender=None, log=None,
         capture=None, busy=None, tab_project=None, pane_cwd=None, gap=MIN_PANE_GAP,
         sleeper=None, dirty=wo._MEASURE, quota=None):
    """Satu putaran timer. Mengembalikan kejadian yang juga dituliskan ke log malam.

    Semua yang bisa mengetik atau mengeksekusi (`beats`, `runner`, `sender`) bisa disuntik, jadi
    selftest tidak menyentuh satu pane pun dan tidak membuat satu proses pun — karena itu file ini
    bisa dibaca sebagai bukti, bukan sebagai maksud.
    """
    events = []
    emit = log or events.append
    gate = gate or read_gate(conn, engine)
    lvl, ap, night = gate["level"], gate["autopilot"], gate["night"]
    # Satu pembacaan git per tick, diwarisi ke claim. `tree_dirty` ada di dalam
    # pembacaan gerbang itu sendiri, jadi tidak ada pengukuran kedua yang boleh
    # berbeda dari yang pertama.
    dirty = night.get("tree_dirty", wo._MEASURE) if night else wo._MEASURE

    def ev(action, **rest):
        row = {"ts": wo.now(), "action": action, "level": lvl.get("level"),
               "mode": ap.get("mode"), **rest}
        emit(row)
        return row

    # Pintu pertama ditutup oleh ketidaktahuan, bukan oleh keadaan "aman". Empat keadaan tak terbaca
    # adalah UNKNOWN (invarian 5) dan UNKNOWN tidak pernah berarti boleh bergerak.
    if lvl.get("readable") is not True:
        ev("CLOSED_UNKNOWN", why=lvl.get("why"))
        return events
    if ap.get("mode") not in wo.MODES:
        # OFF KEMBALI menjadi bagian kosakata pada 2026-10-09, jadi cabang ini murni berarti satu hal:
        # baris mode berisi nilai di luar SEMI/OFF/ON — kolom rusak, basis data belum dimigrasi, atau
        # seseorang menulis SQL mentah. Tick yang diam di sini adalah persis "auto mati, komputer
        # IDLE" yang operator takutkan, jadi namanya MODE_UNKNOWN dan ia masuk keranjang darurat.
        # Keadaan "seseorang menyuruh berhenti" bukan cabang sini: itu OFF, dan ia ditolak dengan
        # namanya sendiri di `hand_new_work`.
        ev("MODE_UNKNOWN", why=f"mode {ap.get('mode')!r} bukan {'/'.join(wo.MODES)} — tidak ada "
                               "satu pun tangan yang bergerak")
        return events
    # SEMI membuka pintu yang sama dengan ON dan mengambil keranjang yang lebih kecil. Yang membedakan
    # keduanya di sini bukan seberapa jauh tangan boleh pergi — itu tangga `level`, dan ia tidak punya
    # versi SEMI — melainkan APA yang boleh diambil dari antrean.
    mode = (ap.get("mode") or "").upper()
    origin = wo.ORIGIN_OPERATOR if mode == "SEMI" else None
    if not night.get("ok"):
        ev("CLOSED_NIGHT", why=night.get("why"))
        return events
    if lvl["level"] == "observe":
        ev("OBSERVED", claims=len(open_claims(conn, engine)),
           next=(next_ready(conn, engine, origin=origin) or {}).get("id"), rest=lvl.get("why"),
           mode=mode)
        if mode == "SEMI":
            # Di SEMI, `observe` adalah kegagalan yang terlihat: operator sedang di meja, dan
            # diamnya tangan berarti perintah yang dia tulis tidak akan pernah sampai. Dijawab
            # dengan jujur, bukan dengan menaikkan level diam-diam.
            ev("SEMI_HAND_FLAT", why="level masih observe — naikkan dari terminal: arm dispatch",
               operator_queue=(next_ready(conn, engine, origin=origin) or {}).get("id"))
        return events

    if lvl["allows_dispatch"]:
        # Meredam yang sudah berjalan terjadi DULU dan tidak pernah ditahan pagar kuota:
        # reconcile menjalankan gerbang shell, bukan memanggil model, jadi ia gratis — dan
        # menghentikannya justru meninggalkan baris WORKING menggantung karena satu-satunya yang
        # bisa menutupnya sedang dikunci oleh angka yang tidak bersangkutan dengannya.
        reconcile(conn, engine, mode, runner=runner, emit=ev)
        # Batas kuota per malam (F10.3) berdiri tepat di sini. Yang ditahan adalah MULAINYA kerja
        # baru, yaitu satu-satunya gerakan yang membeli kuota mesin lain. Jalur resume di bawah
        # sengaja TIDAK diduplikasi ke dalam cabang ini: satu aturan, satu tempat, kalau tidak ia
        # akan punya dua kesempatan untuk berbeda (§12).
        q = quota if quota is not None else wo.night_quota_state(conn, engine)
        if q.get("ok", True):
            hand_new_work(conn, engine, mode, runner=runner, sender=sender, emit=ev,
                          capture=capture, busy=busy,
                          tab_project=tab_project, pane_cwd=pane_cwd, gap=gap, sleeper=sleeper,
                          dirty=dirty)
        else:
            ev("QUOTA_CLOSED", used=q.get("used"), cap=q.get("cap"), why=q.get("why"),
               note="yang sudah berjalan sudah diredam di atas; hanya item BARU yang tidak "
                    "dimulai, karena itulah yang memakai kuota mesin lain")
    rest = [b for b in (beats if beats is not None else wo.heartbeat(conn, engine))
            if b["state"] == "AT_REST"]
    if lvl["allows_resume"]:
        nudge_resting(conn, engine, mode, beats=beats, sender=sender, emit=ev)
    elif rest:
        ev("HELD_BY_LEVEL", items=[b["id"] for b in rest],
           why="AT_REST terbukti, tangan tidak diangkat — naikkan dari terminal: arm resume")
    return events


# --- log malam ----------------------------------------------------------------
def night_log_path(day=None):
    return LOG_DIR / f"drainer-{day or datetime.now().strftime('%Y-%m-%d')}.jsonl"


def write_events(events, path=None):
    path = Path(path or night_log_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for e in events:
            fh.write(json.dumps(e, default=str) + "\n")
    return path


# --- selftest -----------------------------------------------------------------
def selftest():
    """Tick diuji dengan indera dan tangan suntikan di SQLite sementara: nol proses, nol ketikan,
    nol koneksi daemon — dan nol tulisan ke basis data produksi."""
    import sqlite3
    import drainer as _keep
    _orig_prompt = _keep.SEMI_PROMPT
    # Pembaca state pane juga disambat, sama seperti capture/busy/sleeper. Tanpa ini selftest akan
    # membaca tmux operator SUNGGUHAN: hasilnya bergantung pada apakah mesinnya sedang bekerja saat
    # suite dijalankan, dan suite yang hijau-hitam mengikuti jam orang bukan bukti apa pun. None
    # berarti "tidak ada yang dinyatakan", yaitu keadaan yang membuat jalur bukti tidak langsung
    # tetap diuji.
    import cross_verify as _cvx
    _orig_pane_state = _cvx.pane_state
    _cvx.pane_state = lambda _t: None
    # Pembaca kuota per malam juga disambat, untuk seluruh suite. Tick memanggilnya lewat
    # `wo.night_quota_state`, dan tanpa sambatan ini sebelas tes dispatch akan membaca meter
    # SUNGGUHAN: hasilnya berubah mengikuti berapa banyak verifikasi yang kebetulan sudah saya
    # kirim hari ini, dan suite yang hijau-hitam mengikuti jam orang bukan bukti apa pun.
    _orig_quota_state = wo.night_quota_state
    wo.night_quota_state = lambda conn, engine: {"used": 0, "cap": wo.NIGHT_MAX_VERIFICATIONS,
                                                 "paths": ["selftest-path"], "ok": True, "why": []}
    results = []

    def check(name, cond, detail=""):
        results.append((bool(cond), name, detail))
        print(f"[{'ok    ' if cond else 'FAIL  '}] {name}" + ("" if cond else f"  <- {detail}"))

    # `wo.queue_command` mengantar perintah lewat `wo.deliver_now`. Di dalam suite ini yang boleh
    # mengantar hanya `deliver_now` MILIK drainer yang dipanggil tes dengan `sender` suntikan;
    # sambatan di work_order ditutup supaya tidak ada satu jalur pun dari tes ini yang bisa sampai
    # ke daemon pane sungguhan.
    orig_wo_deliver = wo.deliver_now
    wo.deliver_now = lambda conn, engine, item_id: {"sent": False, "queued": True,
                                                    "refused": "SELFTEST"}

    def _restore_wo_deliver():
        wo.deliver_now = orig_wo_deliver
    import atexit as _ax
    _ax.register(_restore_wo_deliver)   # dilepas walau tes meledak di tengah jalan
    check("jalur kirim work_order disuntik: tes ini tidak bisa mengetuk pane asli",
          wo.deliver_now is not orig_wo_deliver, str(wo.deliver_now))

    src = Path(__file__).read_text()
    code = src.partition("def selftest(")[0]
    # Sumber modul adalah bagian dari klaim. Ini tangan: ia tidak boleh bisa menyimpulkan
    # "istirahat" sendiri (dua definisi = dua laporan yang boleh berbeda) dan tidak boleh punya
    # jalur kirim kedua di luar API daemon.
    #
    # Yang dipindai hanya kode, bukan selftest-nya sendiri: daftar token terlarang di bawah ini
    # menyebut token-token itu secara literal, jadi memindai seluruh berkas akan menemukan daftarnya
    # sendiri dan gagal.
    #
    # Klaimnya juga sudah bukan "modul ini tidak pernah menyentuh pane". Yang benar, dan yang bisa
    # ditegakkan, adalah SIAPA yang boleh mengetik: jalur timer (`tick` dan seluruh yang dipanggilnya)
    # tidak punya tangan, sementara `deliver_answer` adalah tangan manusia yang hanya dipanggil satu
    # klik. Karena itu pemindaian per fungsi, dan badan fungsi manusia dibuang dari pemindaian jalur
    # timer secara terbuka — token-nya tidak diizinkan diam-diam, ia memang bukan bagian timer.
    import ast as _ast
    segments = {n.name: (_ast.get_source_segment(code, n) or "")
                for n in _ast.parse(code).body if isinstance(n, _ast.FunctionDef)}
    hand = segments.get("deliver_answer") or ""
    timer_code = code.replace(hand, "") if hand else code
    check("tangan manusia ada satu fungsi, bukan tersebar", bool(hand) and hand != code,
          str(sorted(segments)))
    for banned in ("ydotool", "send-keys", "action_log", "set_level(", "subprocess.run",
                   "check_output", "Popen", "tmux"):
        check(f"jalur timer tidak memakai {banned!r}", banned not in timer_code)
    # Kosakata peristiwa adalah kontrak antara tick dan banner. Yang ditegakkan di sini bukan
    # daftarnya (itu akan membusuk) melainkan fakta bahwa SETIAP nama yang bisa di-emit modul ini
    # sudah diklasifikasi `notify`. Peristiwa baru yang lupa dikelompokkan dibaca notify sebagai
    # `unknown` — dan `unknown` selalu berbanner, jadi ia tidak bisa lulus diam; yang merah di sini
    # adalah tanda bahwa seseorang harus memutuskan apakah ia gerakan, catatan, atau kegagalan.
    emitted = set()
    for node in _ast.walk(_ast.parse(code)):
        if isinstance(node, _ast.Call) and getattr(node.func, "id", None) in ("ev", "emit", "log"):
            first = node.args[0] if node.args else None
            if isinstance(first, _ast.Constant) and isinstance(first.value, str) \
                    and first.value.isupper():
                emitted.add(first.value)
    # Kata refusal juga ikut dipindai. `deliver_now` tidak meng-emit "WINDOW_CLOSED" sebagai
    # peristiwa — ia mengembalikannya sebagai `refused=`, dan dari sana ia naik ke banner
    # `QUEUED_NOT_DELIVERED`. Kalau hanya nama peristiwa yang diperiksa, sebuah refusal baru bisa
    # lolos tanpa keranjang dan orang yang membacanya tidak akan pernah tahu itu nama apa.
    refused = set()
    for node in _ast.walk(_ast.parse(code)):
        # Dua bentuk yang benar-benar dipakai kode. kwargs saja tidak cukup: `deliver_now` menjawab
        # dengan dict (`{"refused": "WINDOW_CLOSED"}`), dan scan yang hanya mencari kwargs akan
        # mengumpulkan nol nama lalu lulus tanpa memeriksa apa pun — kelas bug yang paling sering
        # ditemukan di repo ini, jadi keduanya dipindai dan JUMLAH yang ditemukan ikut dinyatakan.
        if isinstance(node, _ast.keyword) and node.arg == "refused" \
                and isinstance(node.value, _ast.Constant) \
                and isinstance(node.value.value, str) and node.value.value.isupper():
            refused.add(node.value.value)
        elif isinstance(node, _ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, _ast.Constant) and k.value == "refused" \
                        and isinstance(v, _ast.Constant) and isinstance(v.value, str) \
                        and v.value.isupper():
                    refused.add(v.value)
    # SATU perhitungan, atas kedua kumpulan. Baris kedua dari pasangan yang lama menimpa hasil
    # pertama dengan daftar peristiwa saja, jadi cek "setiap kata refusal punya kelas" membaca
    # angka yang sudah dibersihkan dari refusal — hijau, dan buta. Bentuk kegagalan seperti ini
    # tidak meninggalkan jejak di output mana pun selain ketiadaan kegagalan.
    unclassified = sorted(a for a in (emitted | refused) if notify.classify(a) == "unknown")
    check("setiap peristiwa yang bisa di-emit tick punya kelas di notify",
          len(emitted) >= 12 and not unclassified,
          f"emitted={len(emitted)} belum diklasifikasi={unclassified}")
    check("pemindaian menemukan kata refusal untuk diklasifikasi (bukan lulus karena nol temuan)",
          len(refused) >= 2 and {"WINDOW_CLOSED", "MODE_UNKNOWN"} <= refused, str(sorted(refused)))
    check("setiap kata refusal punya kelas di notify",
          not [u for u in unclassified if u in refused],
          f"refused={sorted(refused)} unknown={unclassified}")
    check("nama peristiwa dibaca dari kode, bukan dari ingatan",
          "WORK_DISPATCHED" in emitted and "CLOSED_NIGHT" in emitted, str(sorted(emitted))[:200])
    # `capture_pane` / `pane_is_busy` AWAL-nya ada dalam daftar terlarang di atas, dan itu sekarang
    # salah arah: yang berbahaya bukan membaca pane, tapi MENULIS ke pane tanpa membacanya dulu.
    # Jadi klaimnya diganti dari "tidak pernah menyentuh pane" menjadi "selalu membaca sebelum
    # mengetik" — dan ditegakkan secara struktural, bukan dengan komentar.
    hn = segments.get("hand_new_work", "")
    read_at, claim_at = hn.find("pane_is_quiet("), hn.find("wo.claim(")
    check("jalur dispatch MEMBACA pane sebelum mengetik", read_at != -1)
    check("pembacaan pane terjadi SEBELUM klaim, bukan sesudahnya",
          read_at != -1 and claim_at != -1 and read_at < claim_at, f"read={read_at} claim={claim_at}")
    check("jalur dispatch memverifikasi direktori pane sebelum mengetik", "PANE_WRONG_DIR" in hn)
    check("penolakan di jalur dispatch tidak meninggalkan klaim menggantung",
          "_release_claim(" in hn)
    for name in ("tick", "reconcile", "hand_new_work", "nudge_resting", "gate_item", "read_gate"):
        check(f"{name}() tidak pernah memanggil tangan manusia",
              "deliver_answer" not in segments.get(name, ""), name)
    #bekas: cek urutan berbasis teks diganti tes yang MENJALANKAN, lihat "persiapan gagal"
    for needed in ("wo.heartbeat", "wo.drain_level", "wo.nudge", "wo.next_item", "cw.run_grouped",
                   "cv.daemon_post"):
        check(f"memakai {needed}, bukan menduakalinya", needed.split(".", 1)[1] in timer_code)

    # Pemicu SEMI (F10.10) adalah berkas TERPISAH di luar modul ini, dan klaim bahwa ia tidak
    # menyentuh pane harus ditegakkan dengan pemindaian yang sama — bukan dengan peringatan di
    # komentar. Satu suntingan yang menambahkan `default_sender(...)` ke hook akan mengubah
    # "menyuntik prompt ke sesi sendiri" menjadi "mengetik ke pane siapa pun", dan itu perbedaan
    # yang tidak bisa dilihat dari hasilnya sampai seseorang kehilangan pekerjaannya.
    hook_file = wo.REPO / "hooks" / "semi_wake.py"
    hsrc = hook_file.read_text() if hook_file.is_file() else ""
    check("pemicu SEMI ada di tempat yang didaftarkan", bool(hsrc.strip()), str(hook_file))
    if hsrc:
        hsegs = {n.name: (_ast.get_source_segment(hsrc, n) or "")
                 for n in _ast.parse(hsrc).body
                 if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
        for banned in ("deliver_answer", "default_sender", "daemon_post", "/api/cli/run",
                       "send-keys", "ydotool", "set_level(", "arm(", "tmux", "subprocess"):
            hit = [f"{banned} di {fn}()" for fn, body in hsegs.items() if banned in body]
            check(f"hook SEMI tidak memakai {banned!r}", not hit, "; ".join(hit))
        check("hook SEMI hanya bisa bicara sesudah satu klaim berhasil — antrean adalah pagarnya",
              "wo.claim(" in hsegs.get("main", ""), str(sorted(hsegs)))
        # Urutan klaim-vs-cetak dulu "dibuktikan" dengan mencari posisinya di teks. Yang dibuktikan
        # di sini adalah sifat yang membuat urutan itu penting: `prepare_prompt` TIDAK PERNAH
        # melempar — ia mengembalikan None — sehingga tidak ada jalur di mana kegagalan terjadi
        # setelah ada klaim. Klaim adalah satu-satunya tulisan, dan ia hanya dilalui kalau
        # persiapan sudah selesai.
        try:
            sys.path.insert(0, str(wo.REPO / "hooks"))
            import semi_wake as _sw2
            import drainer as _self_mod
            # Selftest INI yang mengarang 53 baris "SEMI_PREPARE_FAILED item=999" di
            # logs/semi_wake.jsonl, dan operator membacanya sebagai mesin yang mengulang sendiri
            # tanpa bisa dihentikan. Yang diuji adalah sifat prepare_prompt; yang ditulis adalah
            # log produksi. Jadi log hook dipindah ke sandbox untuk durasi ujian ini saja dan
            # dikembalikan di finally — kebalikannya menyisakan hook yang menunjuk ke direktori
            # sementara untuk seluruh sisa proses. Kegagalan persiapan yang ASLI tetap harus
            # tercatat di tempat aslinya; hanya kegagalan yang dikarang yang tidak.
            _prod_log = _sw2.NIGHT_LOG
            _before = _prod_log.stat().st_size if _prod_log.exists() else 0
            _sw2.NIGHT_LOG = Path(tempfile.mkdtemp(prefix="drainer_selftest_log_")) / "semi_wake.jsonl"
            try:
                _broken = dict(id=999, title="x")
                _self_mod.SEMI_PROMPT = object()  # .format tidak ada -> AttributeError di dalam
                _res = _sw2.prepare_prompt(_broken, Path("/tmp"))
            finally:
                _sw2.NIGHT_LOG = _prod_log
            check("persiapan yang rusak mengembalikan None, bukan melempar — jadi tidak ada "
                  "kemungkinan ia gagal SETELAH klaim", _res == (None, 0), str(_res)[:80])
            _after = _prod_log.stat().st_size if _prod_log.exists() else 0
            check("selftest tidak mengarang baris di log hook produksi", _after == _before,
                  f"log produksi tumbuh {_after - _before} byte karena satu ujian yang dikarang")
        except Exception as _e3:  # noqa: BLE001
            check("persiapan bisa diuji", False, f"{type(_e3).__name__}: {_e3}")
        finally:
            import drainer as _self_mod2
            _self_mod2.SEMI_PROMPT = _orig_prompt
        # REM MASUK-KEMBALI. `stop_hook_active` selama ini hanya dijadikan KOLOM LOG dan tidak
        # pernah dijadikan keputusan, jadi tidak ada satu pun tes yang bisa menangkap hilangnya
        # pengereman. Yang diuji di sini: dengan flag itu True, hook harus diam — tidak menulis
        # keputusan block, dan tidak mengklaim apa pun. Jalur ini sengaja diuji in-process dengan
        # NIGHT_LOG dialihkan, supaya kegagalan remnya tidak mengarang baris di log produksi.
        try:
            import io as _io
            import contextlib as _cl
            import semi_wake as _sw3
            _sandbox = Path(tempfile.mkdtemp(prefix="semiwake_reentry_")) / "semi_wake.jsonl"
            _real_log = _sw3.NIGHT_LOG
            _prod_before = _real_log.stat().st_size if _real_log.exists() else 0
            _sw3.NIGHT_LOG = _sandbox
            # Mode dibaca dari baris autopilot SUNGGUHAN oleh main(), dan rem masuk-kembali berdiri
            # SESUDAH pemeriksaan mode. Jadi tanpa sambatan ini tesnya bergantung pada posisi
            # sakelar operator: begitu ia memindah ke ON, hook keluar di baris pertama, log-nya
            # kosong, dan "rem tercatat dengan namanya sendiri" gagal tanpa ada rem yang rusak.
            # Yang diuji di sini adalah rem, jadi hanya mode yang boleh dipinjam.
            _real_ap = wo.autopilot
            wo.autopilot = lambda conn, engine, *a, **k: {"mode": "SEMI", "level": "observe",
                                                          "until_epoch": wo.now() + 600,
                                                          "off_reason": None}
            _out_buf = _io.StringIO()
            _real_stdin = _sw3.sys.stdin
            _sw3.sys.stdin = _io.StringIO(json.dumps(
                {"stop_hook_active": True, "cwd": "/tmp", "hook_event_name": "Stop"}))
            try:
                with _cl.redirect_stdout(_out_buf):
                    _rc_reentry = _sw3.main()
            finally:
                _sw3.sys.stdin = _real_stdin
                _sw3.NIGHT_LOG = _real_log
                wo.autopilot = _real_ap
            _reentry_lines = _sandbox.read_text(encoding="utf-8").splitlines() if _sandbox.exists() else []
            check("rem masuk-kembali: stop_hook_active=True membuat hook diam, tidak memblokir",
                  _rc_reentry == 0 and "decision" not in _out_buf.getvalue(),
                  f"rc={_rc_reentry} stdout={_out_buf.getvalue()[:120]!r}")
            check("rem masuk-kembali tercatat dengan namanya sendiri, bukan sebagai kegagalan",
                  any('"SEMI_REENTRY_HELD"' in x for x in _reentry_lines), str(_reentry_lines)[:160])
            _prod_after = _real_log.stat().st_size if _real_log.exists() else 0
            check("rem masuk-kembali tidak menambah satu byte pun ke log hook produksi",
                  _prod_after == _prod_before,
                  f"produksi tumbuh {_prod_after - _prod_before} byte")
            # Urutan, bukan hanya keberadaan. Rem yang berdiri sesudah `ready_items` masih bisa
            # menulis cooldown ke baris orang lain sebelum sempat diam, jadi yang ditegakkan di sini
            # adalah posisinya: sebelum satu bacaan antrean dan sebelum satu klaim pun. Kontrol
            # negatif dijalankan dengan memindah bloknya, bukan dengan mematikannya, karena
            # mematikan rem di tes akan menyuntik ke produksi.
            _msrc = hsegs.get("main", "")
            _i_re = _msrc.find('data.get("stop_hook_active")')
            _i_rdy = _msrc.find("wo.ready_items(")
            _i_clm = _msrc.find("wo.claim(")
            check("rem masuk-kembali berdiri SEBELUM antrean dibaca dan sebelum apa pun diklaim",
                  -1 < _i_re < _i_rdy and -1 < _i_re < _i_clm,
                  f"reentry={_i_re} ready_items={_i_rdy} claim={_i_clm}")
            # Lima jalur keluar tanpa suntikan di `main()`: bukan-proyek, persiapan gagal, klaim
            # ditolak, batas injeksi habis, stdout gagal. Semuanya harus memasang jeda; satu saja
            # yang lupa berarti baris itu dicoba lagi di setiap batas giliran selamanya.
            check("setiap jalur penolakan hook memasang jeda, tidak ada yang zero-backoff",
                  _msrc.count("drainer.cooldown(") >= 5,
                  f"cooldown() dipanggil {_msrc.count('drainer.cooldown(')}x di main()")
        except Exception as _e4:  # noqa: BLE001
            check("rem masuk-kembali bisa diuji", False, f"{type(_e4).__name__}: {_e4}")
        check("hook SEMI membaca mode dari database pada setiap tembakan, bukan dari harapan",
              "wo.autopilot(" in hsegs.get("main", ""))
        # Cek ini dulu berbunyi `"origin=wo.ORIGIN_OPERATOR" in main()` dan LOLOS — bukan karena
        # hook menyaring dengan benar, tapi karena KOMENTAR saya sendiri di dalam main() menyebut
        # string itu. Guard yang mencari teks bisa dibohongi oleh prosa, dan ia dibohongi pada
        # percobaan pertama. Bentuk sekarang menanyakan hal yang benar-benar ingin ditegakkan:
        # keranjang DITURUNKAN dari mode, dan tidak ada lagi jalan keluar awal untuk ON.
        _main_src = hsegs.get("main", "")
        check("hook menurunkan keranjang dari mode, bukan menulisnya sebagai konstanta",
              "wo.queue_origin_for(" in _main_src, _main_src[:120])
        check("hook TIDAK lagi pulang saat mode ON — bug yang membuat FULL AUTO mati struktural",
              '!= "SEMI"' not in _main_src, "masih ada guard mode != SEMI di main()")
        check("hanya OFF dan nilai di luar kosakata yang menghentikan jalur batas giliran",
              "wo.OFF_MODE" in _main_src and "wo.MODES" in _main_src, "pulang awal tidak terstruktur")
        # Perilaku keranjang itu sendiri ditegakkan di pemilik katanya, dengan baris nyata:
        # `work_order selftest: keranjang ON = antrean roadmap TERLIHAT, bukan hanya dideklarasikan`.
        # Regresi yang terjadi sungguhan, 2026-10-08 11:28, terdeteksi karena injeksi ini masuk ke
        # sesi penulisnya sendiri: `documents/AGENTS.md` (27 KB) menghabiskan seluruh anggaran
        # karakter, jadi tiga berkas kontrak lainnya TIDAK terkirim sama sekali. Pesan menyebut
        # "pedoman TERKUNCI" padahal yang sampai seperempat dari satu berkas.
        try:
            sys.path.insert(0, str(wo.REPO / "hooks"))
            import semi_wake as _sw
            _body, _notes, _n = _sw.guides_for(None, None)
            _missing = [g["path"] for g in wo.locked_guides() if ("### " + g["path"]) not in _body]
            check("setiap berkas yang dikunci benar-benar terkirim, bukan hanya yang pertama",
                  not _missing, str(_missing))
            check("pedoman yang dipotong mengaku dipotong dan menyebut path penuhnya",
                  all("karakter" in n or "penuh" in n for n in _notes)
                  and "(kutipan" in _body, str(_notes[:2]))
        except Exception as exc:  # noqa: BLE001 — kegagalan impor dilaporkan, tidak lulus diam-diam
            check("pedoman terkunci bisa diperiksa", False, f"{type(exc).__name__}: {exc}")

    # Penasehat antrean (F10.10) sengaja HANYA bicara. Ia menempelkan isi antrean ke prompt lewat
    # `additionalContext`, dan batas antara nasihat dan tangan adalah apakah ia boleh mengubah baris.
    adv_file = wo.REPO / "hooks" / "queue_advisor.py"
    asrc = adv_file.read_text() if adv_file.is_file() else ""
    check("penasehat antrean ada", bool(asrc.strip()), str(adv_file))
    if asrc:
        asegs = {n.name: (_ast.get_source_segment(asrc, n) or "")
                 for n in _ast.parse(asrc).body
                 if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
        for banned in ("claim(", "finish(", "deliver", "default_sender", "daemon_post",
                       "/api/cli/run", "UPDATE ", "INSERT ", "decision", "ydotool", "subprocess"):
            hit = [f"{banned} di {fn}()" for fn, body in asegs.items() if banned in body]
            check(f"penasehat antrean tidak memakai {banned!r}", not hit, "; ".join(hit))
        # Perilaku, bukan keberadaan string. Cek lama mencari "ws_path" di badan fungsi dan lulus
        # meski fungsinya tidak menyaring apa pun.
        try:
            sys.path.insert(0, str(wo.REPO / "hooks"))
            import queue_advisor as _adv
            _adv.THIS_ENGINE = "qoder"
            _rows = [{"id": 1, "cli_engine": "qoder", "ws_path": "/tmp/proj-a", "title": "a"},
                     {"id": 2, "cli_engine": "qoder", "ws_path": "/tmp/proj-b", "title": "b"},
                     {"id": 3, "cli_engine": "qoder", "ws_path": None, "title": "tanpa proyek"},
                     {"id": 4, "cli_engine": "antigravity", "ws_path": "/tmp/proj-a", "title": "c"}]
            _in_a = [r["id"] for r in _adv.select_rows(_rows, "/tmp/proj-a")]
            check("penasehat menyaring MESIN dan PROYEK, bukan hanya menandai",
                  _in_a == [1], str(_in_a))
            check("baris tanpa proyek tidak dilaporkan ke sesi proyek lain "
                  "— nasihat yang ditolak mesin terasa seperti perintah yang hilang",
                  3 not in _in_a, str(_in_a))
            _empty = _adv.select_rows(_rows, "")
            check("tanpa laporan cwd dari CLI, penasehat DIAM — tidak mengarang proyek "
                  "lalu menyuruh engine mengerjakan yang akan ditolaknya sendiri",
                  _empty == [], str(_empty))
        except Exception as _exc:  # noqa: BLE001
            check("penasehat bisa diuji", False, f"{type(_exc).__name__}: {_exc}")
        check("tanpa proyek yang diketahui, penasehat tidak mengklaim 'tidak ada yang untukmu'",
              'if not cwd:' in open(str(wo.REPO / "hooks" / "queue_advisor.py"),
                                    encoding="utf-8").read().split("def build_note")[1][:400],
              "build_note masih bicara tanpa tahu proyeknya")
        try:
            import semi_wake as _sw
            _orig = _sw.guides_for
            import work_order as _wo
            _saved = _wo.locked_guides
            _wo.locked_guides = lambda: (_ for _ in ()).throw(RuntimeError("meter rusak"))
            try:
                _res = _sw.guides_for(None, None)
            finally:
                _wo.locked_guides = _saved
            check("jalur gagal pedoman mengembalikan TIGA nilai, bukan dua — kalau dua, hook "
                  "meledak setelah klaim dan barisnya menggantung WORKING",
                  isinstance(_res, tuple) and len(_res) == 3, str(type(_res)))
        except Exception as _exc2:  # noqa: BLE001
            check("jalur gagal pedoman bisa diuji", False, f"{type(_exc2).__name__}: {_exc2}")

    tmp = Path(f"/tmp/drainer_selftest_{wo.now()}.db")
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row
    wo.ensure_schema(conn, "SQLITE")
    sent, ran = [], []

    def sender(target, prompt):
        sent.append((target, prompt))
        return json.dumps({"status": "dispatched", "target_cli": target})

    def runner(cmd, timeout=CHECK_TIMEOUT):
        ran.append(cmd)
        return type("R", (), {"returncode": 1, "stdout": "sengaja gagal dalam selftest"})()

    def passing_runner(cmd, timeout=CHECK_TIMEOUT):
        ran.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "lolos"})()

    # Pembaca pane untuk selftest. Yang diuji adalah KEPUTUSANNYA, jadi yang disuntik adalah
    # pengamatannya — selftest tidak pernah membaca pane operator yang sedang hidup, dan tidak
    # pernah tidur beneran (gap=0 di semua pemanggilan).
    cwd_box = {"v": str(REPO)}

    def quiet_pane(_tab):
        return "prompt siap"

    def not_busy(_screen):
        return False

    def cwd_of(_tab):
        return cwd_box["v"]

    def sink(out):
        """Penampung kejadian berbentuk seperti `ev` di dalam tick: (action, **isi) -> satu dict.

        Dipakai oleh pemanggilan langsung ke `gate_item`/`reconcile` tanpa lewat `tick`, supaya tes
        menguji fungsi yang sama dengan bentuk pemanggilan yang sama — bukan varian lemahnya.
        """
        def _emit(action, **rest):
            row = {"action": action, **rest}
            out.append(row)
            return row
        return _emit

    def make(title, status="PENDING", gate="true", engine_key=None, holder=None, lease=None,
             origin=None, ws=None):
        wo.run(conn, "SQLITE",
               "INSERT INTO quest_tasks (title, status, check_command, cli_engine, claimed_by, "
               "lease_epoch, origin, ws_path) VALUES (?,?,?,?,?,?,?,?)",
               (title, status, gate, engine_key, holder, lease, origin, ws))
        return int(wo.run(conn, "SQLITE",
                          "SELECT id FROM quest_tasks WHERE title=?", (title,))[0]["id"])

    try:
        lvl0 = wo.drain_level(conn, "SQLITE")
        assert lvl0["level"] == "observe", "fixture harus lahir dengan tangan tertutup"

        def gate_for(level, mode="ON", ok=True, why=None, dispatch=None, resume=None):
            allows_d = dispatch if dispatch is not None else level in ("dispatch", "resume")
            allows_r = resume if resume is not None else level == "resume"
            return {"level": {**lvl0, "level": level, "readable": True,
                              "allows_dispatch": allows_d, "allows_resume": allows_r, "why": why or []},
                    "autopilot": {"mode": mode, "off_reason": "selftest"},
                    # `tree_dirty` dinyatakan di sini sebagai PREMIS fixture, bukan sebagai
                    # pelarian: tanpa kunci ini tick mewarisinya sebagai "belum diukur" dan claim
                    # akan membaca git NYATA — yang kotor justru karena selftest ini berjalan di
                    # tengah suntingan. Yang diuji blok ini adalah keputusan drainer, bukan
                    # keadaan working tree, dan itu sudah dibuktikan di `work_order selftest`.
                    "night": {"ok": ok, "why": why or [], "tree_dirty": False}}

        out = tick(conn, "SQLITE", gate=gate_for("observe", mode="NGAWUR"), beats=[],
                   sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        # Yang diuji di sini adalah kolom mode yang berisi kata yang TIDAK PERNAH ditulis oleh
        # siapa pun — kolom rusak, migrasi belum jalan. Dulu "OFF" dipakai sebagai contohnya karena
        # OFF memang bukan kosakata. Sejak 2026-10-09 OFF adalah state yang sah (dan punya jalannya
        # sendiri di bawah), jadi contohnya diganti dengan kata yang benar-benar asing. Sifat yang
        # ditagih tidak berubah: TIDAK ADA yang bergerak, dan ia berteriak.
        check("mode di luar kosakata = tidak ada yang bergerak sama sekali, dan ia berteriak",
              [e["action"] for e in out] == ["MODE_UNKNOWN"] and not sent and not ran, str(out))

        unreadable = {"level": {"readable": False, "level": None, "allows_dispatch": False,
                                "allows_resume": False, "why": ["tidak terbaca"]},
                      "autopilot": {"mode": "ON"}, "night": {"ok": True, "why": []}}
        out = tick(conn, "SQLITE", gate=unreadable, beats=[], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        check("gerbang tak terbaca menutup sebagai UNKNOWN, bukan sebagai observe",
              [e["action"] for e in out] == ["CLOSED_UNKNOWN"], str(out))

        out = tick(conn, "SQLITE", gate=gate_for("observe", ok=False, why=["circuit breaker"]),
                   beats=[], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        check("circuit breaker menutup tick sebelum satu klaim pun",
              [e["action"] for e in out] == ["CLOSED_NIGHT"], str(out))

        # --- F10.3: batas kuota per malam. Tiga sifat, dan ketiganya bisa salah bentuk tanpa
        # terdengar: pagar yang menahan segalanya (termasuk meredam yang sudah jalan), pagar yang
        # hanya menulis catatan tanpa benar-benar berhenti, dan pagar yang dibaca dari meter yang
        # tidak ada sehingga selalu terlihat aman.
        q_ok = {"used": 0, "cap": 8, "paths": ["gemini-pro"], "ok": True, "why": []}
        q_no = {"used": 8, "cap": 8, "paths": ["gemini-pro"], "ok": False,
                "why": ["8 dispatch verifikasi sudah dibuat jendela ini (batas 8)"]}
        qz1 = make("selftest-quota-dispatch", gate="false", engine_key="qoder", origin="operator",
                   ws=str(REPO))
        sent.clear(); ran.clear()
        out = tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[], sender=sender, runner=runner,
                   capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                   dirty=False, quota=q_ok)
        check("kuota tersedia + dispatch = kerja baru benar-benar dimulai",
              bool(sent) and "QUOTA_CLOSED" not in [e["action"] for e in out],
              f"sent={len(sent)} acts={[e['action'] for e in out]}")
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (qz1,))

        qz2 = make("selftest-quota-blocked", gate="false", engine_key="qoder", origin="operator",
                   ws=str(REPO))
        sent.clear(); ran.clear()
        out = tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[], sender=sender, runner=runner,
                   capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                   dirty=False, quota=q_no)
        acts = [e["action"] for e in out]
        check("kuota habis menutup MULAINYA item baru dan menamainya sendiri",
              "QUOTA_CLOSED" in acts and not sent, f"{acts} sent={len(sent)}")
        check("penolakan kuota dilaporkan dengan angka, bukan hanya kata",
              any(e.get("action") == "QUOTA_CLOSED" and e.get("used") == 8 and e.get("cap") == 8
                  for e in out), str([e for e in out if e.get("action") == "QUOTA_CLOSED"]))
        check("item yang ditolak kuota TIDAK ditinggalkan WORKING tanpa pemegang",
              wo.get_item(conn, "SQLITE", qz2)["status"] == "PENDING"
              and not wo.get_item(conn, "SQLITE", qz2)["claimed_by"],
              str(wo.get_item(conn, "SQLITE", qz2)["status"]))
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (qz2,))

        # Sifat ketiga, dan inilah yang dulu salah di kode saya sendiri: reconcile harus tetap jalan
        # di bawah kuota habis. Menahannya berarti membiarkan baris WORKING menggantung. Lease
        # dibuat SUDAH HABIS karena `reconcile` memang menahan vonis selama lease masih hidup
        # (baris 282: memperpanjang dan melaporkan HELD) — memvonis lebih awal artinya menyebut
        # pekerjaan orang gagal hanya karena belum selesai, dan tes dengan lease hidup hanya akan
        # membuktikan aturan itu, bukan aturan yang sedang diuji di sini.
        qz3 = make("selftest-quota-reconcile", status="WORKING", gate="true", holder=HOLDER,
                   lease=wo.now() - 10)
        ran.clear()
        out = tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[], sender=sender, runner=runner,
                   capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                   dirty=False, quota=q_no)
        _acts3 = [e["action"] for e in out]
        _row3 = wo.get_item(conn, "SQLITE", qz3)
        # Yang ditagih di sini adalah "reconcile DIPANGGIL", bukan "barisnya COMPLETED": `runner`
        # fixture sengaja mengembalikan exit 1 (keluarannya bahkan berbunyi "sengaja gagal dalam
        # selftest"), jadi vonis yang benar untuk baris ini memang FAILED. Assertion pada COMPLETED
        # akan menguji kemurahan stub, bukan perilaku drainer.
        check("kuota habis TIDAK menghentikan peredaman baris yang sudah berjalan",
              "QUOTA_CLOSED" in _acts3 and _row3["status"] != "WORKING"
              and any(e.get("item") == qz3 for e in out),
              f"status={_row3['status']} acts={_acts3}")
        check("baris yang diredam di bawah kuota habis divonis oleh angka keluar gerbangnya, "
              "bukan digantung",
              _row3["status"] in ("COMPLETED", "FAILED"), str(_row3["status"]))
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (qz3,))

        ran.clear()
        out = tick(conn, "SQLITE", gate=gate_for("observe"), beats=[], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        check("observe = indera saja: nol gerbang, nol kiriman, nol tulisan",
              [e["action"] for e in out] == ["OBSERVED"] and not sent and not ran, str(out))

        # --- invarian 3 diuji pada baris sungguhan, dengan canary yang membuktikan penolakan ---
        canary = "/tmp/drainer_canary_selftest"
        Path(canary).unlink(missing_ok=True)
        poison = make("selftest-poison", gate=f"touch {canary} && rm -rf /tmp/drainer_target")
        ran.clear()
        verdict = gate_item(conn, "SQLITE", wo.get_item(conn, "SQLITE", poison), runner=runner,
                            stage="before", emit=sink([]))
        check("item mengandung 'rm -rf' ditolak sebelum satu proses pun dibuat",
              verdict.get("refused") and not ran, str((verdict, ran)))
        check("canary-nya benar-benar tidak pernah dieksekusi",
              not Path(canary).exists(), "berkas canary ada = gerbang tetap dijalankan")
        check("yang ditolak naik ke antrean manusia, tidak hilang dan tidak PARKED",
              wo.norm((wo.get_item(conn, "SQLITE", poison) or {}).get("status")) == "HUMAN",
              str((wo.get_item(conn, "SQLITE", poison) or {}).get("status")))

        # --- kegagalan baseline TIDAK boleh ditulis sebagai FAILED ---
        base = make("selftest-baseline", gate="false", engine_key="qoder")
        ran.clear()
        out = []
        tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False,
             log=out.append)
        check("dispatch menjalankan gerbang dan mencatat angka keluar",
              ran and any(e["action"] == "GATE_BASELINE" and e["exit"] == 1 for e in out), str(out))
        after_base = wo.get_item(conn, "SQLITE", base) or {}
        check("kegagalan baseline tidak ditulis sebagai FAILED: itu belum dimulai",
              wo.norm(after_base.get("status")) in ("WORKING", "CLAIMED"), str(after_base.get("status")))
        check("item yang dikerjakan drainer dipegang atas nama drainer, bukan atas nama pane",
              after_base.get("claimed_by") == HOLDER, str(after_base.get("claimed_by")))
        check("kirim pekerjaan baru memakai API daemon ke tujuan dari baris, bukan tebakan",
              [e["action"] for e in out if e["action"] == "WORK_DISPATCHED"] and
              any(e.get("target") == "qoder" for e in out), str(out))

        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET cli_engine=NULL WHERE id=?", (base,))
        fresh = make("selftest-tanpa-tujuan", gate="false")
        sent.clear()
        out = []
        tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False,
             log=out.append)
        check("baris tanpa cli_engine ditolak NO_TARGET, tidak dikirim ke engine favorit tick ini",
              any(e["action"] == "NO_TARGET" and e["item"] == fresh for e in out)
              and not any(e["action"] == "WORK_DISPATCHED" and e.get("item") == fresh for e in out),
              str(out))
        freed = wo.get_item(conn, "SQLITE", fresh) or {}
        check("yang ditolak NO_TARGET melepas lease drainer, tidak menahan item untuk dirinya",
              not freed.get("claimed_by"), str(freed.get("claimed_by")))

        # --- vonis: gerbang lulus = COMPLETED dengan bukti terukur, tanpa prosa ---
        ready = make("selftest-sudah-selesai", gate="true")
        ran.clear()
        verdict = gate_item(conn, "SQLITE", wo.get_item(conn, "SQLITE", ready),
                            runner=passing_runner, stage="before", emit=sink([]))
        row = wo.get_item(conn, "SQLITE", ready) or {}
        check("gerbang yang lolos saat baseline menutup item dengan bukti angka, bukan panggilan engine",
              verdict["exit"] == 0 and wo.norm(row.get("status")) == "COMPLETED"
              and "exit 0" in (row.get("evidence") or ""), str(row))

        # --- after-gate + cooldown ---
        held = make("selftest-dipegang", status="WORKING", gate="false", holder=HOLDER,
                    lease=wo.now() + 600)
        ran.clear()
        out = []
        reconcile(conn, "SQLITE", "ON", runner=runner, emit=sink(out))
        check("klaim drainer yang lease-nya masih hidup divonis nanti, bukan sekarang",
              any(e["action"] == "HELD" and e["item"] == held for e in out)
              and not any(e["item"] == held and e["action"] == "GATE_FAILED" for e in out)
              and wo.norm((wo.get_item(conn, "SQLITE", held) or {}).get("status")) == "WORKING",
              str(out))
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET lease_epoch=? WHERE id=?",
               (wo.now() - 1, held))
        ran.clear()
        out = []
        reconcile(conn, "SQLITE", "ON", runner=runner, emit=sink(out))
        row = wo.get_item(conn, "SQLITE", held) or {}
        check("lease habis + gerbang gagal = FAILED dengan angka keluar yang terukur",
              any(e["action"] == "GATE_FAILED" and e["item"] == held and e["exit"] == 1
                  for e in out) and wo.norm(row.get("status")) == "FAILED", str(out))
        check("yang gagal diberi jeda, supaya tick berikutnya tidak mengunyah baris yang sama",
              int(row.get("due_epoch") or 0) > wo.now(), str(row.get("due_epoch")))

        # --- tangan: hanya pada resume, hanya pada AT_REST, dan selalu dihitung ---
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET status='WORKING', claimed_by='qoder', "
               "resume_count=0, due_epoch=0, lease_epoch=? WHERE id=?", (wo.now() + 600, base))
        beat = {"id": base, "state": "AT_REST", "claimed_by": "qoder", "why": "dua sinyal setuju"}
        sent.clear()
        disp_out = []
        tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[beat], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False,
             log=disp_out.append)
        check("di tingkat dispatch, AT_REST ditahan dan tidak ada satu pun ketikan",
              not sent and any(e["action"] == "HELD_BY_LEVEL" and e["items"] == [base]
                               for e in disp_out), str((sent, disp_out)))

        resume_out = []
        tick(conn, "SQLITE", gate=gate_for("resume"), beats=[beat], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False,
             log=resume_out.append)
        check("resume + AT_REST = satu kiriman CONTINUE, dan anggaran ikut naik",
              len(sent) == 1 and sent[0][0] == "qoder"
              and int((wo.get_item(conn, "SQLITE", base) or {}).get("resume_count") or 0) == 1,
              str(sent))
        check("prompt yang dikirim membawa arahan broker, bukan pertanyaan kepada operator",
              bool(sent) and "jangan meminta saya memilih" in sent[0][1], str(sent[:1]))
        check("kiriman dicatat dengan balasan daemon apa adanya, bukan sebagai 'pasti sampai'",
              any(e["action"] == "CONTINUE_SENT" and "dispatched" in (e.get("reply") or "")
                  for e in resume_out), str(resume_out))

        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET resume_count=? WHERE id=?",
               (wo.MAX_RESUME, base))
        sent.clear()
        stuck = []
        tick(conn, "SQLITE", gate=gate_for("resume"), beats=[beat], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False,
             log=stuck.append)
        check("anggaran habis memarkir item dan tidak mengirim apa pun",
              not sent and any(e["action"] == "STUCK_PARKED" and e["item"] == base
                               for e in stuck), str(stuck))

        sent.clear()
        out = []
        tick(conn, "SQLITE", gate=gate_for("resume"),
             beats=[{"id": base, "state": "WORKING", "claimed_by": "qoder", "why": "spinner"},
                    {"id": base, "state": "UNKNOWN", "claimed_by": "qoder", "why": "pane mati"}],
             sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False, log=out.append)
        skipped = [e for e in out if e["action"] == "SKIP_NOT_RESTING"]
        check("WORKING dan UNKNOWN tidak pernah disentuh — UNKNOWN bukan berarti diam",
              not sent and len(skipped) == 2
              and {e["state"] for e in skipped} == {"WORKING", "UNKNOWN"}, str(out))

        # Jendela ON dibaca ulang pada saat klaim, bukan diwarisi dari awal tick. Timer yang mulai
        # pukul 01:55 untuk jendela yang mati pukul 02:00 tidak boleh masih mengklaim pada 02:05
        # hanya karena ia sudah terlanjur bangun (F10.8: expiry ditegakkan di `claim`, bukan di timer).
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET cli_engine='qoder' WHERE cli_engine IS NULL")
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET due_epoch=0")
        wo.run(conn, "SQLITE",
               "UPDATE autopilot_state SET mode='ON', on_epoch=?, until_epoch=?, item_budget=12",
               (wo.now() - 7200, wo.now() - 60))
        sent.clear()
        out = []
        tick(conn, "SQLITE", gate=gate_for("dispatch"), beats=[], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False,
             log=out.append)
        refused = [e for e in out if e["action"] == "CLAIM_REFUSED"]
        check("jendela yang lewat ketahuan saat klaim, bukan dipercaya dari awal tick",
              bool(refused) and "jendela ON lewat" in " ".join(str(e.get("why")) for e in refused),
              str(out))
        check("klaim yang ditolak ikut diberi jeda pendek, tidak langsung ditanya ulang",
              all(int((wo.get_item(conn, "SQLITE", e["item"]) or {}).get("due_epoch") or 0) > wo.now()
                  for e in refused), str(refused))
        # OFF ditulis ke baris fixture HANYA untuk blok tangan-manusia di bawah. Alasannya sekarang
        # harus disebut, karena dulu tidak: selama OFF bukan bagian kosakata, "OFF" adalah cara
        # praktis membuat `claim()` melompatkan pemutus malam dan tidak membaca git SUNGGUHAN —
        # tree ini kotor justru karena selftest ini sedang berjalan di tengah suntingan.
        # Sejak 2026-10-09 OFF berarti "tidak ada yang bergerak", dan itu justru yang mau diuji di
        # sini: mesin yang berhenti total harus tetap bisa MENGANTAR JAWABAN ORANG. Jawaban manusia
        # bukan mesin yang berinisiatif, jadi rem tidak boleh memaknainya sebagai gerakan.
        wo.run(conn, "SQLITE", "UPDATE autopilot_state SET mode='OFF', until_epoch=NULL")

        # Klaim atas nama HOLDER tidak boleh bisa diketuki: tanpa pane, heartbeat menjawab UNKNOWN.
        dr = make("selftest-drainer-held", status="WORKING", gate="true", holder=HOLDER,
                  lease=wo.now() + 600)
        beats = wo.heartbeat(conn, "SQLITE", capture=lambda t: None, busy=lambda t: False,
                             sample_gap=0)
        mine = [b for b in beats if b["claimed_by"] == HOLDER]
        check("klaim drainer selalu UNKNOWN: jalur resume secara struktur tidak menjangkaunya",
              any(b["id"] == dr for b in mine) and all(b["state"] == "UNKNOWN" for b in mine),
              str([(b["id"], b["state"]) for b in mine]))

        # --- tangan manusia: satu klik yang mengantar jawaban ----------------------
        # Beda dari `nudge_resting` di atas dan bedanya bukan kosakata: yang itu diketik timer atas
        # nama mesin, yang ini diketik karena operator menekan Save. Sakelar OFF melindungi orang yang
        # tidur dari mesin yang mempersenjatai dirinya sendiri — bukan dari jawabannya sendiri.
        tgt = make("selftest-answer-target", status="HUMAN", gate="true", engine_key="qoder")
        lonely = make("selftest-answer-notarget", status="HUMAN", gate="true", engine_key=None)
        q_open = wo.decide(conn, "SQLITE", "A atau B?", ["A", "B"], "heavy", tgt)[0]["id"]
        q_alone = wo.decide(conn, "SQLITE", "pertanyaan tanpa pekerjaan", ["A"], "heavy")[0]["id"]
        wo.answer_decision(conn, "SQLITE", q_alone, "A", "selftest-operator")
        d_nolink = deliver_answer(conn, "SQLITE", q_alone, sender=sender, beats=[])
        check("jawaban yang tidak menautkan item tidak membangunkan apa pun",
              not d_nolink["sent"] and "tidak menautkan" in d_nolink["why"], str(d_nolink))
        check("yang belum dijawab tidak diantar",
              not deliver_answer(conn, "SQLITE", q_open, sender=sender, beats=[])["sent"],
              str(deliver_answer(conn, "SQLITE", q_open, sender=sender, beats=[])))
        wo.answer_decision(conn, "SQLITE", q_open, "A, lebih hemat", "selftest-operator")
        sent.clear()
        d_work = deliver_answer(conn, "SQLITE", q_open, sender=sender,
                                beats=[{"id": tgt, "state": "WORKING", "claimed_by": "qoder",
                                        "why": "spinner"}])
        check("pane yang sedang bekerja tidak diketuki walau jawabannya sudah ada",
              not sent and not d_work["sent"] and "ditunda" in d_work["why"], str(d_work))
        d_rest = deliver_answer(conn, "SQLITE", q_open, sender=sender,
                                beats=[{"id": tgt, "state": "AT_REST", "claimed_by": "qoder",
                                        "why": "dua sinyal setuju"}])
        check("jawaban diantar ke engine yang tertulis di baris itemnya, bukan yang sedang disukai",
              d_rest["sent"] and [t for t, _ in sent] == ["qoder"], str(sent))
        check("isi kiriman membawa jawaban orang apa adanya",
              bool(sent) and "lebih hemat" in sent[0][1] and str(q_open) in sent[0][1], str(sent[:1]))
        # Item yang berhenti justru karena menunggu tidak memegang klaim: di sana buktinya satu
        # pembacaan pane, dan pane yang tidak terbaca tetap UNKNOWN.
        sent.clear()
        d_blind = deliver_answer(conn, "SQLITE", q_open, sender=sender, beats=[],
                                 capture=lambda t: "", busy=lambda t: False)
        check("pane yang tidak terbaca bukan berarti diam",
              not d_blind["sent"] and "tidak terbaca" in d_blind["why"], str(d_blind))
        check("pane yang sedang menampilkan pekerjaan tidak diketuki",
              not deliver_answer(conn, "SQLITE", q_open, sender=sender, beats=[],
                                 capture=lambda t: "esc to cancel",
                                 busy=lambda t: True)["sent"])
        d_clear = deliver_answer(conn, "SQLITE", q_open, sender=sender, beats=[],
                                 capture=lambda t: "prompt siap", busy=lambda t: False)
        check("tanpa klaim terbuka, satu pembacaan pane yang bersih cukup untuk menjawab",
              d_clear["sent"], str(d_clear))
        wo.run(conn, "SQLITE", "UPDATE decisions SET item_id=? WHERE id=?", (lonely, q_open))
        check("tanpa cli_engine pengiriman ditolak, bukan ditebak",
              not deliver_answer(conn, "SQLITE", q_open, sender=sender,
                                 beats=[{"id": lonely, "state": "AT_REST", "claimed_by": "qoder",
                                         "why": "x"}])["sent"])

        # Blok jawaban selesai, dan OFF-nya harus dilepas. Yang di bawah menguji pengiriman
        # perintah, dan mengirim apa pun di atas OFF adalah kegagalan yang BENAR — kalau mode itu
        # dibiarkan, tes-tes berikut hanya mengukur rem dan mengira dirinya mengukur pengiriman.
        # Jendela dibuka lagi karena `deliver_now` menolaknya sebagai WINDOW_CLOSED; `dirty=False`
        # yang dibawa tiap call-site di bawah-lah yang menjaga git nyata tidak dibaca, bukan mode.
        wo.run(conn, "SQLITE",
               "UPDATE autopilot_state SET mode='SEMI', on_epoch=?, until_epoch=?, off_reason=NULL",
               (wo.now(), wo.now() + 3600))

        # --- F10.10 SEMI: otomatis dengan pengawas -------------------------------------
        # Yang diuji di blok ini bukan "apakah drainer masih hidup", melainkan BEDA yang membuatnya
        # layak jadi mode ketiga: keranjang mana yang diambil, kapan ia menunggu, dan di direktori
        # mana gerbangnya diukur.
        #
        # Baris autopilot fixture DIBIARKAN OFF sementara gerbangnya disuntik sebagai SEMI, dan itu
        # bukan kecurangan yang diam: `claim()` membaca pemutus dari baris database, dan baris itu
        # akan membaca git NYATA — yang kotor justru karena selftest ini sedang berjalan di tengah
        # suntingan. Arah penyampangan ini aman (fixture lebih longgar dari produksi), dan aturan
        # "SEMI juga ditutup pemutus" dibuktikan di modul yang punya kata itu,
        # `work_order selftest: pemutus malam menutup SEMI persis seperti menutup ON`. Menambahkan
        # knob `dirty` ke jalur dispatch akan jauh lebih mudah, dan itu berarti setiap pemanggil
        # masa depan punya tombol untuk melewati §6 — pagar yang bisa dilewati pemakainya bukan pagar.
        sent.clear()
        out = tick(conn, "SQLITE", gate=gate_for("observe", mode="SEMI"), beats=[],
                   sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        acts = [e["action"] for e in out]
        check("SEMI tidak disalahtafsir sebagai keadaan diam — pintu dibuka, tangan belum diangkat",
              "MODE_UNKNOWN" not in acts and "SEMI_HAND_FLAT" in acts and not sent, str(out))

        sent.clear()
        out = tick(conn, "SQLITE", gate=gate_for("dispatch", mode="SEMI"), beats=[],
                   sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        acts = [e["action"] for e in out]
        check("SEMI dengan antrean tanpa perintah operator tidak mengambil apa pun — itu isi kata 'semi'",
              "QUEUE_EMPTY" in acts and not sent, str(out))

        # Jalur idle diuji pada BATASNYA, bukan di dalam: yang harus dibuktikan di sini adalah apa
        # yang drainer serahkan ke `ensure_idle_item`, karena satu-satunya cara fungsi itu menulis
        # mesin hantu adalah kalau pemanggilnya menukar arti dua argumen. Versi pertama jalur ini
        # mengirim `engine` secara posisi — benar kebetulan, dan tetap benar kalau suatu hari
        # dipanggil dengan target di tempat lain. Yang direkam di bawah adalah kata kuncinya.
        _seen_call = {}
        _orig_idle = wo.ensure_idle_item

        def _spy_idle(_conn, *a, **kw):
            _seen_call["positional"] = list(a)
            _seen_call["kw"] = dict(kw)
            return None          # sengaja: yang diukur adalah SERAHAN, bukan isi fungsinya

        wo.ensure_idle_item = _spy_idle
        try:
            sent.clear(); out = []
            hand_new_work(conn, "SQLITE", "ON", runner=runner, sender=sender, emit=sink(out),
                          capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of,
                          gap=0, dirty=False)
        finally:
            wo.ensure_idle_item = _orig_idle
        _backend_pos = _seen_call.get("positional", [])
        _backend = (_backend_pos[0] if _backend_pos else _seen_call.get("kw", {}).get("backend"))
        check("ON + antrean kosong memanggil jalur idle, dan menyerahkan JENIS DATABASE sebagai "
              "backend — bukan sebagai mesin tujuan (mesin tujuan datang dari ledger)",
              _seen_call and _backend == "SQLITE"
              and "cli_engine" not in _seen_call.get("kw", {})
              and len(_backend_pos) == 0
              and any(e["action"] == "QUEUE_EMPTY" for e in out) and not sent,
              str(_seen_call)[:160])

        # Sisi negatifnya TIDAK diuji di sini: `ensure_idle_item` punya lima penolakan sebelum
        # INSERT, dan mode jendela dibacakan dari ledger tes yang isinya berubah-ubah antar cek --
        # jadi menolak di sini bisa berarti menolak karena registri, atau karena mode bukan ON, dan
        # tesnya akan hijau tanpa membuktikan apa pun. Itu diuji di selftest work_order, di mana
        # mode-nya ditetapkan sendiri dan alasan penolakannya bisa dibandingkan dengan kata
        # "registri". Yang menjadi tanggung jawab drainer hanya bentuk serahannya, dan itu di atas.

        op = make("selftest-semi-cmd", gate="false", engine_key="qoder",
                  origin="operator", ws="/tmp")
        cwd_box["v"] = "/tmp"     # pane target memang berdiri di direktori baris itu
        sent.clear()
        tick(conn, "SQLITE", gate=gate_for("dispatch", mode="SEMI"), beats=[],
             sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        check("perintah operator diantar ke mesin yang tertulis di barisnya",
              sent and sent[0][0] == "qoder" and str(op) in sent[0][1], str(sent)[:220])
        check("yang dikirim adalah prompt perintah-orang, bukan tugas roadmap",
              sent and "Perintah operator dari Dashboard" in sent[0][1], str(sent)[:160])
        check("prompt SEMI menyebut direktori baris, jadi engine tidak dikerjakan di tree lain",
              sent and "/tmp" in sent[0][1], str(sent)[:220])

        op2 = make("selftest-semi-cmd-2", gate="false", engine_key="qoder", origin="operator")
        cwd_box["v"] = str(REPO)     # op2 tidak punya ws_path → tujuannya memang REPO
        sent.clear()
        out = tick(conn, "SQLITE", gate=gate_for("dispatch", mode="SEMI"), beats=[],
                   sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        acts = [e["action"] for e in out]
        check("engine yang sedang mengerjakan satu perintah tidak ditumpuki perintah kedua — "
              "'kalau Working, tunggu sampai selesai'",
              "ENGINE_BUSY" in acts and not sent, str(out))

        # --- dua pagar baru hasil audit peer: pane buta dan direktori buta ----------------
        # Yang diuji bukan hanya "ia menolak", tapi bahwa penolakannya terjadi SEBELUM ada klaim
        # dan SEBELUM ada ketikan: baris yang tertahan harus tetap PENDING, karena satu-satunya
        # kegagalan di jalur ini yang tidak bisa dibatalkan adalah teks yang sudah tertulis ke
        # dalam ketikan seseorang.
        def shifting_pane(_t):
            shifting_pane.n += 1
            return f"frame {shifting_pane.n}"
        shifting_pane.n = 0

        g1 = make("selftest-guard-busy", gate="false", engine_key="qoder", origin="operator")
        sent.clear(); out = []
        hand_new_work(conn, "SQLITE", runner=runner, sender=sender, emit=sink(out),
                      mode="SEMI",
                      capture=shifting_pane, busy=not_busy, tab_project=cwd_of, gap=0)
        check("pane yang teksnya berubah antar pembacaan tidak diketiki, dan barisnya tetap PENDING",
              out and out[-1]["action"] == "PANE_NOT_QUIET" and not sent
              and wo.get_item(conn, "SQLITE", g1)["status"] == "PENDING", str(out))
        # `refuse()` adalah satu-satunya tempat penolakan menulis tiga hal sekaligus. Tanpa tes ini,
        # menghapus `note_refusal`/`cooldown` dari dalamnya tetap membuat semua tes di atas hijau —
        # dan itulah lubang yang membuat operator bertanya perintahnya hilang ke mana.
        _row_g1 = wo.get_item(conn, "SQLITE", g1)
        check("penolakan gerbang menulis escalation_reason, bukan hanya peristiwa log",
              (_row_g1.get("escalation_reason") or "").startswith("PANE_NOT_QUIET"),
              str(_row_g1.get("escalation_reason"))[:160])
        check("penolakan gerbang juga memasang jeda, jadi baris yang sama tidak dicoba tiap tick",
              int(_row_g1.get("due_epoch") or 0) > wo.now(), str(_row_g1.get("due_epoch")))

        g2 = make("selftest-guard-blind", gate="false", engine_key="qoder", origin="operator")
        sent.clear(); out = []
        hand_new_work(conn, "SQLITE", runner=runner, sender=sender, emit=sink(out),
                      mode="SEMI",
                      capture=quiet_pane, busy=not_busy,
                      tab_project=lambda _t: None, gap=0)
        check("direktori pane yang tidak terbaca adalah UNKNOWN, dan UNKNOWN tidak pernah mengetik",
              out and out[-1]["action"] == "PANE_DIR_UNKNOWN" and not sent, str(out))

        g3 = make("selftest-guard-wrongdir", gate="false", engine_key="qoder",
                  origin="operator", ws="/tmp")
        cwd_box["v"] = str(Path.home())   # pane berdiri di $HOME, bukan di proyek baris ini
        sent.clear(); out = []
        hand_new_work(conn, "SQLITE", runner=runner, sender=sender, emit=sink(out),
                      mode="SEMI",
                      capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        check("perintah proyek A tidak pernah diketik ke pane yang sedang berdiri di proyek B",
              out and out[-1]["action"] == "PANE_WRONG_DIR" and not sent
              and wo.get_item(conn, "SQLITE", g3)["status"] == "PENDING", str(out))
        cwd_box["v"] = str(REPO)

        # --- empat temuan audit lainnya, masing-masing dikunci satu cek ------------------
        try:
            hand_new_work(conn, "SQLITE")      # sengaja tanpa mode
            _mode_required = False
        except TypeError:
            _mode_required = True
        check("mode wajib disebut — pemanggil yang lupa tidak otomatis dapat keranjang FULL AUTO",
              _mode_required)

        rm_held = make("selftest-reconcile-roadmap", status="WORKING", gate="false",
                       holder=HOLDER, lease=0, origin="roadmap")
        ran.clear(); out = []
        reconcile(conn, "SQLITE", "SEMI", runner=runner, emit=sink(out))
        check("reconcile di SEMI tidak menjalankan gerbang baris roadmap — menjalankan shell "
              "adalah mengerjakan yang tidak diminta",
              any(e["action"] == "HELD_BY_MODE" and e["item"] == rm_held for e in out) and not ran,
              str(out))

        # Lepaskan klaim yang masih dipegang engine ini lebih dulu, supaya yang diukur di bawah
        # adalah pagar direktori — bukan aturan satu-perintah-aktif yang sudah punya tes sendiri.
        wo.finish(conn, "SQLITE", op, "COMPLETED",
                  evidence="selftest: melepas klaim agar uji berikutnya mengukur satu hal")
        op3 = make("selftest-semi-cmd-3", gate="false", engine_key="qoder",
                   origin="operator", ws=str(REPO))
        out = []
        hand_new_work(conn, "SQLITE", "SEMI", runner=runner, sender=sender, emit=sink(out),
                      capture=quiet_pane, busy=not_busy,
                      tab_project=cwd_of, pane_cwd=lambda _t: str(Path.home()), gap=0,
                      dirty=False)
        # Terukur 2026-10-08 11:4x: pane qoder memang duduk di ~/.ai-station (cwd proses
        # foreground-nya) sambil proyek tab-nya ~/Documents/ai-workstation. Jadi cwd pane BUKAN
        # sinyal proyek; menuntut keduanya sama akan menolak hampir setiap pengiriman — aman, tapi
        # diam, dan yang diukur ini justru mengirim.
        check("cwd pane yang berbeda tidak menahan pengiriman, tapi tetap dicatat",
              out and out[-1]["action"] == "WORK_DISPATCHED"
              and out[-1].get("pane_now") == str(Path.home()), str(out[-1:]))

        # Cek perilaku, bukan cek teks. Versi sebelumnya mencari string "max(gap, MIN_PANE_GAP)"
        # di sumber dan LULUS pada kode mati — persis kelas bug yang kuburu sejak kemarin.
        # (B) Kemacetan yang ditemukan audit putaran tiga: klaim roadmap sisa ON, yang di SEMI
        #     justru ditahan HELD_BY_MODE dan tidak pernah diselesaikan siapa pun, dulu memblokir
        #     SETIAP perintah operator untuk engine itu selamanya.
        # Engine harus benar-benar bebas dulu, kalau tidak yang terukur adalah aturan
        # satu-perintah-aktif (yang sudah punya tesnya sendiri), bukan umur lease.
        wo.finish(conn, "SQLITE", op3, "COMPLETED",
                  evidence="selftest: membebaskan engine sebelum uji lease mati")
        stale = make("selftest-stale-roadmap", status="WORKING", gate="false", engine_key="qoder",
                     holder=HOLDER, lease=wo.now() - 10, origin="roadmap")
        op4 = make("selftest-semi-after-stale", gate="false", engine_key="qoder", origin="operator",
                   ws=str(REPO))
        sent.clear(); out = []
        hand_new_work(conn, "SQLITE", "SEMI", runner=runner, sender=sender, emit=sink(out),
                      capture=quiet_pane, busy=not_busy, tab_project=cwd_of,
                      pane_cwd=cwd_of, gap=0, dirty=False)
        check("klaim roadmap yang lease-nya sudah mati tidak membekukan antrean operator selamanya",
              sent and out[-1]["action"] == "WORK_DISPATCHED", str(out[-1:]))

        # --- jalur instan: perintah yang barusan ditulis harus sampai TANPA satu tick pun --------
        # Ini permintaan operator, dan bentuk buktinya harus bentuk yang dia pakai: tulis perintah,
        # CLI menjawab. Klaim serupa sebelumnya bisa lulus karena tick kebetulan berjalan di antara
        # dua perintah; di sini `tick()` tidak dipanggil sama sekali, jadi yang diketik hanya bisa
        # datang dari `deliver_now`.
        sent.clear()
        ran.clear()
        # Mesin harus benar-benar bebas: blok (C) di atas meninggalkan satu klaim qoder yang
        # lease-nya masih hidup, dan aturan satu-perintah-aktif menolak yang baru — penolakan yang
        # benar, tapi bukan yang sedang diukur di sini.
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET status='PENDING', claimed_by=NULL, "
                               "lease_epoch=0 WHERE status IN ('WORKING','CLAIMED')")
        # Fixture drainer lahir dengan mode 'OFF' warisan; jalur instan membaca mode dari ledger
        # persis seperti tick, jadi keadaan yang diuji harus dinyatakan di sini, bukan diwarisi.
        wo.run(conn, "SQLITE", "UPDATE autopilot_state SET mode='SEMI', level='dispatch', "
                               "level_until=strftime('%s','now')+3600, "
                               "until_epoch=strftime('%s','now')+3600")
        wo.run(conn, "SQLITE", "INSERT INTO quest_tasks (title,status,origin,cli_engine,"
                               "check_command,ws_path) VALUES ('instan-pertama','PENDING',"
                               "'operator','qoder','true',?)", (str(cwd_of("qoder")),))
        fast_row = wo.run(conn, "SQLITE",
                          "SELECT id FROM quest_tasks WHERE title='instan-pertama'")
        fast_id = int(fast_row[0]["id"])
        res = deliver_now(conn, "SQLITE", fast_id, sender=sender, capture=quiet_pane,
                          busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                          runner=runner, dirty=False)
        check("perintah baru terkirim ke CLI TANPA satu tick pun",
              res["sent"] and len(sent) == 1 and sent[0][0] == "qoder", str(res))
        check("yang diketik adalah judul perintahnya, bukan pesan kosong",
              sent and "instan-pertama" in sent[0][1], str(sent)[:160])
        wo.release_claim(conn, "SQLITE", fast_id, wo.HOLDER if hasattr(wo, "HOLDER") else "drainer")
        # Sisi yang operator minta dengan kalimat langsung: "kalau saya mode semi, saya tidak perlu
        # arm dispatch — saat saya kirim command, AI harus jawab". Tangan TURUN, jendela TERBUKA,
        # perintah operator: harus tetap sampai. Sebelum perubahan ini jalur ini menolak keadaan
        # tersebut, jadi SEMI terasa mati bagi orang yang justru sedang mengawasi.
        wo.run(conn, "SQLITE", "UPDATE autopilot_state SET level='observe', level_until=NULL, "
                               "until_epoch=strftime('%s','now')+3600")
        wo.release_claim(conn, "SQLITE", fast_id, "drainer")
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET status='PENDING', claimed_by=NULL, "
                               "lease_epoch=0 WHERE id=?", (fast_id,))
        sent.clear()
        res_flat = deliver_now(conn, "SQLITE", fast_id, sender=sender, capture=quiet_pane,
                               busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                               runner=runner, dirty=False)
        # Logger dari pemanggil tidak boleh mengubah jawaban. Ini bukan hiasan antarmuka:
        # `deliver_now` menghitung `sent` dari akumulatornya sendiri, jadi selama emit didelegasikan
        # ke logger pemanggil, akumulator itu tetap kosong dan SETIAP pengiriman sungguhan dilaporkan
        # gagal. Kegagalan palsu lebih murah daripada keberhasilan palsu — tapi tetap satu cara
        # membuat orang berhenti mempercayai antreannya sendiri.
        sink_rows = []
        sent.clear()          # blok di bawah menghitung panjang `sent`; jangan warisi kiriman ini
        res_logged = deliver_now(conn, "SQLITE", fast_id, sender=sender, capture=quiet_pane,
                                 busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                                 runner=runner, dirty=False,
                                 log=lambda a, **k: sink_rows.append((a, k)))
        check("dengan logger pemanggil, keberhasilan masih terbaca berhasil dan peristiwanya sampai "
              "ke dua-duanya",
              res_logged["sent"] is True and bool(sink_rows)
              and any(a == "WORK_DISPATCHED" for a, _k in sink_rows), str(res_logged)[:180])
        check("balasan daemon dan tujuannya ikut dibawa pulang ke pemanggil",
              res_logged.get("target") == "qoder",
              str({k: res_logged.get(k) for k in ("reply", "target")}))
        wo.release_claim(conn, "SQLITE", fast_id, "drainer")
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET status='PENDING', claimed_by=NULL, "
                               "lease_epoch=0 WHERE id=?", (fast_id,))
        check("SEMI dengan tangan masih observe TETAP mengantar perintah operator — `level` "
              "menjaga inisiatif mesin, bukan perintah manusia",
              res_flat["sent"] and len(sent) == 1, str(res_flat))
        wo.release_claim(conn, "SQLITE", fast_id, "drainer")
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET status='PENDING', claimed_by=NULL, "
                               "lease_epoch=0 WHERE id=?", (fast_id,))
        # Remnya `berhenti`: kata berhenti menutup jendela, dan jendela yang lewat harus menahan
        # jalur instan juga — kalau tidak, tombol berhenti hanya menghentikan timer sementara
        # perintah masih mengetik ke pane, dan itu bukan rem, itu jeda.
        wo.run(conn, "SQLITE", "UPDATE autopilot_state SET until_epoch=strftime('%s','now')-5")
        sent.clear()
        res2 = deliver_now(conn, "SQLITE", fast_id, sender=sender, capture=quiet_pane,
                           busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                           runner=runner, dirty=False)
        check("jendela yang ditutup rem menolak jalur instan dan menyebut namanya",
              not res2["sent"] and res2.get("refused") == "WINDOW_CLOSED"
              and res2.get("queued") is True and not sent, str(res2))
        check("penolakan instan tidak meninggalkan klaim: barisnya tetap PENDING untuk tick",
              (wo.get_item(conn, "SQLITE", fast_id) or {}).get("status") == "PENDING",
              str(wo.get_item(conn, "SQLITE", fast_id)))
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (fast_id,))
        wo.run(conn, "SQLITE", "UPDATE autopilot_state SET mode='SEMI', level='dispatch', "
                               "level_until=strftime('%s','now')+3600, "
                               "until_epoch=strftime('%s','now')+3600")

        # (D) keadaan yang tidak boleh mengirim apa pun. Ini sekarang DUA kelas yang dulu menyatu:
        # "" dan "ngawur" bukan kosakata (MODE_UNKNOWN), sedangkan "OFF" adalah state SAH yang
        # kebetulan berarti diam (MODE_OFF). Membedakannya bukan kosmetik — operator yang menekan
        # berhenti harus melihat kata yang ia tulis sendiri di log, bukan tuduhan salah ketik, dan
        # menambah satu mode baru tidak boleh lagi membuat "OFF" lolos sebagai kata yang dikenal.
        _off_events = []
        for bad, expected in (("semi", None), ("OFF", "MODE_OFF"), ("", "MODE_UNKNOWN"),
                              ("ngawur", "MODE_UNKNOWN")):
            sent.clear(); out = []
            hand_new_work(conn, "SQLITE", bad, runner=runner, sender=sender, emit=sink(out),
                          capture=quiet_pane, busy=not_busy, tab_project=cwd_of,
                          pane_cwd=cwd_of, gap=0, dirty=False)
            if bad == "semi":
                # Bukan "salah satu dari tiga kejadian". Yang harus dibuktikan adalah mode
                # huruf kecil memperoleh KEDUA sifat SEMI: prompt perintah-orang DAN pagar
                # satu-perintah-aktif. Versi sebelumnya menerima hasil apa pun, jadi ia tetap
                # hijau ketika bug normalisasi mengirim START_PROMPT roadmap tanpa pagar.
                wo.run(conn, "SQLITE",
                       "UPDATE quest_tasks SET status='COMPLETED', lease_epoch=0 "
                       "WHERE status IN ('WORKING','CLAIMED') AND cli_engine='qoder'")
                wo.run(conn, "SQLITE", "UPDATE quest_tasks SET due_epoch=0")
                sent.clear(); out = []
                opx = make("selftest-semi-lowercase", gate="false", engine_key="qoder",
                           origin="operator", ws=str(REPO))
                hand_new_work(conn, "SQLITE", "semi", runner=runner, sender=sender,
                              emit=sink(out), capture=quiet_pane, busy=not_busy,
                              tab_project=cwd_of, pane_cwd=cwd_of, gap=0, sleeper=lambda _s: None,
                              dirty=False)
                check("mode huruf kecil mendapat prompt SEMI, bukan prompt roadmap",
                      sent and "Perintah operator dari Dashboard" in sent[-1][1], str(sent[-1:])[:220])
                # Kirim SATU perintah lagi ke engine yang sama: kalau pagar SEMI aktif, ia
                # ditahan. Kalau tidak (bug normalisasi), ia terkirim dan `sent` bertambah.
                sent_before = len(sent)
                opy = make("selftest-semi-lowercase-2", gate="false", engine_key="qoder",
                           origin="operator", ws=str(REPO))
                out2 = []
                hand_new_work(conn, "SQLITE", "semi", runner=runner, sender=sender,
                              emit=sink(out2), capture=quiet_pane, busy=not_busy,
                              tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                              sleeper=lambda _x: None, dirty=False)
                check("mode huruf kecil juga mendapat pagar satu-perintah-aktif",
                      len(sent) == sent_before
                      and any(e["action"] == "ENGINE_BUSY" for e in out2), str(out2[-1:]))
                held_now = [r for r in open_claims(conn, "SQLITE")
                            if (r.get("cli_engine") or "") == "qoder"
                            and int(r.get("lease_epoch") or 0) > wo.now()]
                check("setelah pengiriman, engine memang memegang satu klaim (pagar punya gigi)",
                      len(held_now) >= 1, str(held_now))
            else:
                check(f"mode {bad!r} tidak mengirim apa pun dan tidak mengambil keranjang penuh",
                      not sent and any(e["action"] == expected for e in out), str(out))
                if expected == "MODE_OFF":
                    _off_events = [e["action"] for e in out]
        check("OFF dilaporkan sebagai keadaannya sendiri, bukan sebagai salah ketik",
              _off_events == ["MODE_OFF"], str(_off_events))

        check("jeda tidak bisa dinolkan oleh pemanggil mana pun, termasuk yang menyuntik pembaca",
              effective_gap(0) == MIN_PANE_GAP and effective_gap(5) == 5
              and effective_gap(-1) == MIN_PANE_GAP,
              f"0->{effective_gap(0)} 5->{effective_gap(5)} -1->{effective_gap(-1)}")
        # Yang disuntik adalah SLEEPER-nya, jadi tes tidak tidur dan tetap membuktikan jeda nyata
        # benar-benar diminta. Versi sebelumnya memanggil pane_asli(capture=None) dan membaca
        # tmux operator — melanggar aturan yang ditulis modul ini sendiri.
        _waits = []
        pane_is_quiet("qoder", capture=lambda _t: "prompt siap", busy=lambda _s: False,
                      gap=0, sleeper=lambda sec: _waits.append(sec))
        check("dua pembacaan sungguhan dipisahkan jeda lantai walau pemanggil minta nol",
              _waits == [MIN_PANE_GAP], str(_waits))
        check("selftest tidak membaca tmux operator: pembaca state pane benar-benar disuntik",
              _cvx.pane_state is not _orig_pane_state, str(_cvx.pane_state))
        # Status yang DINYATAKAN mesin mengalahkan dugaan dari glyph. Dua arah, dan keduanya wajib:
        #
        # (a) READY dengan layar yang tetap berubah. Inilah cacat yang membuat qoder diperlakukan
        #     berbeda dari antigravity padahal gerbangnya sama: chrome qoder punya jam berjalan dan
        #     glyph tool-call yang berganti, jadi perbandingan dua pembacaan atas LAYAR UTUH tidak
        #     pernah sama, jadi pane yang sedang diam disebut sibuk — selamanya. Tanpa jalur ini,
        #     perintah operator mendarat di antigravity dan menggantung di qoder.
        # (b) WORKING dengan layar yang kebetulan identik dan tanpa glyph. Arah ini justru PENGETATAN:
        #     sebelumnya kombinasi itu terbaca "diam", dan konsekuensinya adalah satu ketikan ke
        #     tengah giliran orang — satu-satunya kegagalan di jalur ini yang tidak bisa ditarik
        #     kembali dengan mengembalikan baris database.
        _tick = {"n": 0}

        def _ticking_screen(_t):
            _tick["n"] += 1
            return f"baris status: 25m{_tick['n']}s · tidak ada spinner"
        ready_q = pane_is_quiet("qoder", capture=_ticking_screen, busy=lambda _s: False,
                                gap=0, sleeper=lambda _x: None, pane_state=lambda _t: "ready")
        check("CLI yang menyatakan Ready dibaca DIAM walaupun chrome-nya berdetak tiap pembacaan",
              ready_q["quiet"] and "Ready" in ready_q["why"], str(ready_q))
        working_q = pane_is_quiet("qoder", capture=lambda _t: "prompt siap",
                                  busy=lambda _s: False, gap=0, sleeper=lambda _x: None,
                                  pane_state=lambda _t: "working")
        check("CLI yang menyatakan Working ditolak walau dua pembacaan identik dan tanpa glyph",
              not working_q["quiet"] and "Working" in working_q["why"], str(working_q))
        # None = tidak ada yang dinyatakan, BUKAN dinyatakan bebas. Jalur lama harus tetap jalan
        # utuh untuk mesin seperti antigravity yang judul panenya hanya berisi nama distro.
        unknown_q = pane_is_quiet("antigravity", capture=lambda _t: "prompt siap",
                                  busy=lambda _s: False, gap=0, sleeper=lambda _x: None,
                                  pane_state=lambda _t: None)
        check("judul tanpa pernyataan state jatuh ke bukti tidak langsung, tidak dianggap Ready",
              unknown_q["quiet"] and "Ready" not in unknown_q["why"], str(unknown_q))
        # Keduanya dulu cek teks. Yang pertama sudah digantikan cek perilaku WORK_DISPATCHED di
        # bawah (ia menuntut `pane_now` terisi padahal pengiriman tetap jalan); yang kedua diganti
        # uji langsung pada fungsi pembentuk path-nya.
        check("`~` diperluas sebelum dibandingkan, bukan ditolak sebagai proyek lain",
              _as_path("~") == Path.home().resolve(strict=False)
              and _as_path("~/Documents") == (Path.home() / "Documents").resolve(strict=False),
              str(_as_path("~")))

        # Beda mode diukur pada ANTREAN YANG SAMA, bukan dengan dua fixture berbeda: satu baris yang
        # bukan perintah operator harus terlihat oleh FULL AUTO dan tidak terlihat oleh SEMI.
        # (Sebelumnya ada cek di sini dengan `... or True` — dan cek yang selalu hijau adalah bug
        # yang kemarin kutangkap di gerbang f109, jadi ia dibuang, tidak diperhalus.)
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE id=?", (op2,))
        # Bersihkan sisa antrean operator dari blok-blok di atas (beberapa sengaja dibiarkan
        # PENDING untuk menguji pagar lain). Tanpa ini, "keranjang SEMI kosong" bergantung pada
        # baris dari tes lain dan hasilnya berubah kalau urutan tes berubah.
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE origin='operator'")
        free = make("selftest-full-auto-row", gate="false", engine_key="qoder")
        wide = next_ready(conn, "SQLITE")
        narrow = next_ready(conn, "SQLITE", origin=wo.ORIGIN_OPERATOR)
        check("perbedaan FULL AUTO dan SEMI adalah isi keranjang — baris bebas terlihat oleh yang "
              "pertama dan tidak terlihat oleh yang kedua, pada antrean yang sama",
              wide is not None and (wide.get("origin") or "legacy") != wo.ORIGIN_OPERATOR
              and narrow is None, str({"wide": wide and wide["id"], "semi": narrow}))

        rm_claim = make("selftest-semi-roadmap-claim", status="WORKING", engine_key="qoder",
                        holder="qoder", lease=wo.now() + 600)
        sent.clear()
        out = tick(conn, "SQLITE", gate=gate_for("resume", mode="SEMI"),
                   beats=[{"id": rm_claim, "state": "AT_REST", "claimed_by": "qoder",
                           "why": "dua sinyal setuju"}], sender=sender, runner=runner, capture=quiet_pane, busy=not_busy, tab_project=cwd_of, pane_cwd=cwd_of, gap=0,
                      dirty=False)
        acts = [e["action"] for e in out]
        check("SEMI tidak mengirim CONTINUE ke klaim roadmap — itu perluasan mandat yang tidak "
              "pernah diberikan", "SKIP_NOT_OPERATOR" in acts and not sent, str(out))

        ran.clear()
        probe = make("selftest-gate-cwd", gate="pwd", engine_key="qoder", ws="/tmp")
        gate_item(conn, "SQLITE", wo.get_item(conn, "SQLITE", probe), runner=runner,
                  stage="before", emit=sink([]))
        check("gerbang baris proyek dijalankan di direktori baris itu",
              ran and "cd /tmp && pwd" in ran[-1], str(ran[-1:]))
        ran.clear()
        probe_old = make("selftest-gate-repo", gate="pwd", engine_key="qoder")
        gate_item(conn, "SQLITE", wo.get_item(conn, "SQLITE", probe_old), runner=runner,
                  stage="before", emit=sink([]))
        check("baris tanpa ws_path masih diukur di REPO — tidak ada satu baris lama yang dipaksa "
              "punya proyek", ran and f"cd {REPO} && pwd" in ran[-1], str(ran[-1:]))

        # --- sifat baru: pelepasan klaim bersyarat, dan daftar siap yang konsisten ----------
        # Premisnya harus dinyatakan: kegagalan dari blok sebelumnya masih ada di jendela 12 jam
        # dan akan membuka pemutus, jadi klaim uji ini ditolak karena alasan yang bukan diuji.
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET completed_at=datetime('now','-2 days') "
                               "WHERE status IN ('FAILED','PARKED')")
        # Engine harus benar-benar bebas: aturan satu-perintah-aktif yang baru dipusatkan di
        # claim() menolak klaim uji ini kalau masih ada klaim qoder yang lease-nya hidup dari
        # blok di atas — dan penolakan itu benar, hanya saja bukan yang sedang diukur di sini.
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET status='COMPLETED', lease_epoch=0 "
                               "WHERE status IN ('WORKING','CLAIMED') AND cli_engine='qoder'")
        keeper = make("selftest-keeper", gate="false", engine_key="qoder", origin="operator",
                      ws=str(REPO))
        ok_keep, why_keep = wo.claim(conn, "SQLITE", keeper, "pihak-a", dirty=False, mode="SEMI")
        check("klaim uji ini sendiri berhasil (kalau tidak, tes di bawah mengukur hal lain)",
              ok_keep, why_keep)
        still = wo.release_claim(conn, "SQLITE", keeper, "pihak-b")
        check("pihak lain TIDAK bisa melepaskan klaim pihak lain",
              still["claimed_by"] == "pihak-a" and still["status"] == "WORKING", str(still))
        wo.release_claim(conn, "SQLITE", keeper, "pihak-a")
        back = wo.get_item(conn, "SQLITE", keeper)
        check("pemegangnya sendiri bisa melepas, dan barisnya kembali PENDING tanpa pemegang",
              back["status"] == "PENDING" and not back["claimed_by"]
              and int(back["lease_epoch"] or 0) == 0, str(back))

        # Daftar siap dan "berikutnya" harus berasal dari aturan yang sama — kalau tidak, angka
        # Dashboard dan apa yang benar-benar ditawarkan boleh berbeda (itu temuan decider).
        wo.run(conn, "SQLITE", "DELETE FROM quest_tasks WHERE origin='operator'")
        a_ok = make("selftest-ready-a", gate="false", engine_key="qoder", origin="operator",
                    ws=str(REPO))
        b_hold = make("selftest-ready-b", gate="false", engine_key="qoder", origin="operator",
                      ws=str(REPO))
        wo.run(conn, "SQLITE", "UPDATE quest_tasks SET due_epoch=? WHERE id=?",
               (wo.now() + 600, a_ok))
        ready_ids = [int(r["id"]) for r in wo.ready_items(conn, "SQLITE", respect_due=True,
                                                          origin=wo.ORIGIN_OPERATOR,
                                                          for_engine="qoder")]
        first = wo.next_item(conn, "SQLITE", respect_due=True, origin=wo.ORIGIN_OPERATOR,
                             for_engine="qoder")
        check("baris yang sedang cooldown tidak muncul di daftar siap maupun 'berikutnya'",
              a_ok not in ready_ids and first and int(first["id"]) == b_hold,
              str({"ready": ready_ids, "first": first and first["id"]}))
        check("laporan antrean Dashboard memakai aturan yang sama dengan yang ditawarkan",
              len(wo.semi_status(conn, "SQLITE")["operator_queue"]) == len(ready_ids),
              str(wo.semi_status(conn, "SQLITE")["operator_pending"]))

        # Penasihat hanya melapor, tidak menyuruh bekerja. Yang diperiksa adalah TEKS yang
        # dihasilkannya — bukan teks sumbernya — jadi ini bukti perilaku, bukan pencarian string.
        try:
            import queue_advisor as _adv2
            _adv2.THIS_ENGINE = "qoder"
            # `build_note` adalah satu-satunya bagian penasihat yang menyentuh database, dan
            # `main()` menelan SEMUA exception-nya menjadi "diam" — jadi nama yang tidak terdefinisi
            # di dalamnya tidak akan pernah terlihat sebagai kegagalan. Ini terjadi sungguhan
            # 2026-10-08 12:2x: suntingan menghapus tiga variabel yang masih dipakai, dan penasihat
            # berhenti bicara selamanya dengan exit 0. Yang dipanggil di sini adalah fungsi nyata;
            # kalau ia melempar, tes ini yang berteriak, bukan sesi operator.
            try:
                _live = _adv2.build_note(str(REPO))
                _ok_live = _live is None or ("bukan perintah" in _live and "jangan mulai" in _live)
            except Exception as _le:  # noqa: BLE001
                _ok_live = False
                _live = f"{type(_le).__name__}: {_le}"
            check("build_note sungguhan berjalan (bukan hanya render), dan tetap melarang dirinya "
                  "dipakai sebagai perintah", _ok_live, str(_live)[:150])
            _note = _adv2.render_note("SEMI", "observe", [{"id": 7, "title": "contoh"}], [], [])
            check("catatan antrean melarang dirinya sendiri dipakai sebagai perintah kerja",
                  bool(_note) and "bukan perintah" in _note and "jangan mulai" in _note,
                  str(_note)[:180])

            # --- F9.1: keadaan lingkungan yang disuapkan ke prompt. Tiga pengukur, dan ketiganya
            # menerima pembacanya sebagai argumen, jadi tes ini tidak menyentuh tmux, /proc, atau
            # basis data produksi sama sekali.
            class _FakeWo:
                def __init__(self, rows=None, boom=False):
                    self.rows, self.boom = rows or [], boom

                def run(self, conn, engine, sql, params=()):
                    if self.boom:
                        raise RuntimeError("no such table: action_log")
                    return self.rows
            # Urutan baris SUNGGUHAN dari PostgreSQL, DESC waktu: exit lebih dulu, command-nya
            # menyusul. Ini bentuk yang membalikkan pairing versi pertama.
            _rows = [
                {"kind": "command_exit", "summary": "aborted=False out_chars=803", "tool": "Bash",
                 "exit_code": 0, "duration_ms": 5},
                {"kind": "command", "summary": "bash bin/check_dash_order.sh", "tool": "Bash",
                 "exit_code": None, "duration_ms": None},
                {"kind": "command_exit", "summary": "aborted=False out_chars=12", "tool": "Bash",
                 "exit_code": 3, "duration_ms": 9},
                {"kind": "command", "summary": "python3 tools/drainer.py selftest", "tool": "Bash",
                 "exit_code": None, "duration_ms": None},
            ]
            _ex = _adv2.env_exit_codes(None, "POSTGRESQL", _FakeWo(_rows))
            check("F9.1 memasangkan exit code dengan perintah yang BENAR pada urutan DESC waktu",
                  _ex == ["bash bin/check_dash_order.sh -> exit=0",
                          "python3 tools/drainer.py selftest -> exit=3"], str(_ex))
            _bad = _adv2.env_exit_codes(None, "POSTGRESQL", _FakeWo(boom=True))
            check("F9.1 melaporkan ketidakmampuan membaca ledger, tidak menggantinya dengan daftar kosong",
                  isinstance(_bad, list) and _bad and "UNKNOWN" in _bad[0], str(_bad))

            class _FakeCw:
                @staticmethod
                def audit(cut_now=False):
                    assert cut_now is False, "hook yang berjalan tiap prompt TIDAK BOLEH memotong proses"
                    return {"ok": True, "persistent": [{"pane": "station-qoder", "pid": 7}],
                            "strays": [], "stray_mb": 0, "in_flight": [],
                            "protected": [{"binary": "qoder", "rss_mb": 900.0, "cmdline": "x" * 400,
                                           "exe": "/p/qoder", "age_s": 1.0, "why": "pane tree"},
                                          {"binary": "qoder", "rss_mb": 100.0, "cmdline": "y",
                                           "exe": "/p/qoder", "age_s": 2.0, "why": "pane tree"}]}
            _pr = _adv2.env_processes(_FakeCw)
            _prtxt = " · ".join(_pr)
            check("F9.1 meringkas pohon proses, bukan men-dump dict mentah ke setiap prompt",
                  "{" not in _prtxt and "cmdline" not in _prtxt
                  and "qoderx2~1000MB" in _prtxt, _prtxt[:180])

            class _FakeCv:
                n = 0
                @staticmethod
                def capture_pane(_t):
                    _FakeCv.n += 1
                    return "baris A\nbaris B\n" if _FakeCv.n == 1 else "baris A\nbaris C\n"
            _base = Path(tempfile.mkdtemp(prefix="f91_baseline_"))
            _real_base = _adv2.BASELINE_DIR
            _adv2.BASELINE_DIR = _base
            try:
                _d1 = " ".join(_adv2.env_pane_delta(_FakeCv, "qoder"))
                _d2 = " ".join(_adv2.env_pane_delta(_FakeCv, "qoder"))
            finally:
                _adv2.BASELINE_DIR = _real_base
            check("F9.1 melaporkan DELTA pane, bukan menyalin isinya ke konteks",
                  "belum ada pembacaan" in _d1 and "+1/-1" in _d2, f"{_d1} || {_d2}")
            _blind = " ".join(_adv2.env_pane_delta(type("X", (), {
                "capture_pane": staticmethod(lambda _t: None)})(), "qoder"))
            check("pane yang tidak terbaca disebut UNKNOWN, bukan 'tidak berubah'",
                  "UNKNOWN" in _blind, _blind)
            _env = _adv2.build_env_note("qoder")
            check("blok lingkungan memuat KETIGA pengukurnya sekaligus",
                  all(k in _env for k in ("exit-code", "proses:", "delta pane:")), _env[:200])
        except Exception as _e4:  # noqa: BLE001
            check("catatan antrean bisa diuji", False, f"{type(_e4).__name__}: {_e4}")

        path = write_events([{"ts": 1, "action": "SELFTEST"}],
                            path=Path("/tmp/drainer_selftest.jsonl"))
        check("log malam ditulis sebagai JSONL yang dibaca baris per baris",
              path.exists() and json.loads(path.read_text().splitlines()[-1])["action"] == "SELFTEST")
    finally:
        _cvx.pane_state = _orig_pane_state
        wo.night_quota_state = _orig_quota_state
        Path("/tmp/drainer_selftest.jsonl").unlink(missing_ok=True)
        Path("/tmp/drainer_canary_selftest").unlink(missing_ok=True)
        conn.close()
        tmp.unlink(missing_ok=True)
    bad = [r for r in results if not r[0]]
    print(f"\ndrainer selftest: {len(results) - len(bad)}/{len(results)} sesuai")
    for _ok, name, detail in bad:
        print(f"  FAIL {name} {detail}")
    return 1 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="F10.3 drainer — satu tick tanpa LLM")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick")
    sub.add_parser("gate")
    p_del = sub.add_parser("deliver", help="antar jawaban yang barusan ditulis operator ke pane-nya")
    p_del.add_argument("--decision", type=int, required=True)
    p_wake = sub.add_parser("wake", help="coba antarkan satu baris yang sudah antre, sekarang juga")
    p_wake.add_argument("--item", type=int, required=True)
    sub.add_parser("selftest")
    a = p.parse_args(argv)

    if a.cmd == "selftest":
        return selftest()
    conn, engine = wo.connect()
    wo.ensure_schema(conn, engine)
    if a.cmd == "deliver":
        out = deliver_answer(conn, engine, a.decision)
        events = [{"ts": wo.now(), "action": "ANSWER_SENT" if out.get("sent") else
                   ("ANSWER_NOT_SENT" if out.get("ok") else "ANSWER_DELIVER_REFUSED"), **out}]
        path = write_events(events)
        print(json.dumps({"result": out, "log": str(path)}, default=str, indent=2))
        return 0 if out.get("sent") or out.get("ok") else 1
    if a.cmd == "gate":
        print(json.dumps(read_gate(conn, engine), default=str, indent=2))
        return 0
    if a.cmd == "wake":
        # Jalur ulang untuk perintah yang tertahan oleh sebuah pagar (tree kotor, jendela lewat,
        # engine sedang sibuk). Tanpa ini, perintah yang ditolak sekali hanya bisa diantar oleh
        # tick berikutnya — dan tick tetap butuh `dispatch`, jadi perintah operator tidak punya
        # jalan kedua selain menulis ulang barisnya. Yang dipanggil tetap `deliver_now` yang sama,
        # jadi tidak ada pagar kedua di tempat ini.
        out = deliver_now(conn, engine, a.item)
        events = out.pop("detail", []) or []
        if events:
            write_events(events)
        print(json.dumps(out, default=str, indent=2))
        return 0 if out.get("sent") else 1
    events = []
    tick(conn, engine, log=events.append)
    path = write_events(events)
    # Satu banner untuk tiap tick yang BERGERAK, BERTANYA, atau GAGAL (permintaan operator
    # 2026-10-08: loop yang tidak kelihatan tidak ada bedanya dengan loop yang mati bagi orang yang
    # bangun dan menemukan komputer diam). Tick yang hanya melihat tetap di log malam saja —
    # bukan karena mahal, tapi karena orang yang dihujani banner akan belajar mengabaikan banner,
    # dan itu membuat banner yang harus dia baca ikut terlewat.
    seen = [(e.get("mode"), e.get("level")) for e in events if isinstance(e, dict)]
    mode_seen = next((m for m, _ in seen if m), None)
    level_seen = next((l for _, l in seen if l), None)
    banner = notify.notify_tick(events, mode=mode_seen, level=level_seen)
    print(json.dumps(events, default=str, indent=2))
    print(json.dumps({"notify": banner, "log": str(path)}, default=str), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
