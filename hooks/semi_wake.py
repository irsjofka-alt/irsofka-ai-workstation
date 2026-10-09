"""Semi Autopilot — tangan yang bukan tangan.

Satu-satunya tujuan berkas ini: menghilangkan kata DIAM. Ketika Qoder berhenti karena item
`finish`, perintah yang operator tulis sendiri ke PostgreSQL tidak boleh menunggu engine berikutnya
sadar. Hook `Stop` adalah tempat yang tepat untuk itu karena ia dipanggil pada batas giliran itu
juga — ia tidak mengetik ke pane siapa pun, ia hanya menolak sebuah penghentian di sesi yang
sudah berjalan.

Mengapa ini bukan `arm` yang menyamar
-------------------------------------
Tangga `level` menjaga tangan yang menulis ke pane MILIK ORANG LAIN (`default_sender` /
`/api/cli/run`) dan karena itu `arm` hanya bisa dinaikkan dari `/dev/tty`. Jalur ini berbeda secara
struktural: ia hanya hidup di dalam sesi yang memanggilnya, nama mesin tujuan ditulis di berkas ini
dan harus sama dengan barisnya, dan yang menyalakannya adalah satu baris `mode='SEMI'` yang hanya
manusia yang bisa tulis lewat klik di Dashboard. Sakelar matinya tetap empat dan tidak satu pun
meminta kerja sama AI (invarian 6): `off` dari shell mana pun menutup jalur ini pada tembakan
berikutnya, karena yang dibaca setiap tembakan adalah database, bukan harapan.

Berhenti berlarut tidak mungkin secara konstruksi, bukan karena ada yang ingat menjaga: sebuah
blok HANYA boleh terjadi sebagai akibat dari klaim yang berhasil atas satu baris PENDING, dan baris
yang diklaim berhenti menjadi PENDING. Antrean kosong berarti tidak ada satu byte pun yang keluar.
Pemutus malam (`NIGHT_MAX_FAILURES`) dan aturan working-tree kotor (§6) ditegakkan oleh `claim()`
itu sendiri, jadi mode yang naik ke SEMI tidak punya jalur pintas ke keduanya.

Kesalahan apa pun membuat berkas ini diam dan keluar 0. Hook yang rusak tidak boleh mencegah
seseorang menutup pekerjaannya.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path.home() / ".ai-station" / "tools"
sys.path.insert(0, str(TOOLS))

# Mesin milik sesi yang memanggil hook ini. Bukan tebakan dan bukan hiasan: hook yang sama
# terdaftar di dua CLI berbeda, dan sebuah baris perintah hanya boleh diambil oleh mesin yang
# namanya tertulis padanya. Dibaca dari argv[1] supaya yang menentukan adalah pendaftarannya.
SESSION_ENGINE = (sys.argv[1] if len(sys.argv) > 1 else "qoder")
# Kata keputusannya berbeda di tiap CLI dan keduanya terdokumentasi di tempatnya sendiri:
# qodercli menolak penghentian dengan `decision:"block"`, Antigravity dengan `"continue"`.
# Salah satu kata ini membuat hook berjalan, mengembalikan sesuatu, dan tidak ada yang lanjut —
# kegagalan yang dari luar terlihat sukses.
BLOCK_DECISION = {"qoder": "block", "antigravity": "continue"}.get(SESSION_ENGINE, "block")
NIGHT_LOG = Path.home() / ".ai-station" / "logs" / "semi_wake.jsonl"
# Peristiwa yang boleh menyentuh desktop dari jalur ini. Satu nama = satu makna yang sama dengan
# yang ditulis ke log, jadi banner dan baris log tidak pernah bisa berbeda cerita (§12).
BANNER_ACTIONS = {"SEMI_INJECTED", "SEMI_BROKE", "SEMI_EMIT_FAILED", "SEND_FAILED"}
# Plafon ini TIDAK sama dengan milik auto_restore.py (3200) dan tidak lagi mengaku begitu:
# dinaikkan karena anggaran dibagi per berkas, dan dengan 3200 setiap berkas kontrak hanya
# kebagian ~800 karakter.
GUIDE_MAX_CHARS = 9000
GUIDE_MIN_PER_FILE = 900        # walau plafon habis dibagi rata, tiap berkas dapat setidaknya ini


def session_cwd(data: dict):
    """Direktori proyek sesi ini, dari bentuk payload CLI yang memanggil.

    qodercli mengirim `cwd` (satu string); Antigravity mengirim `workspacePaths` (array), dan itu
    justru lebih benar — yang menentukan "proyek siapa yang dikerjakan" adalah workspace yang
    dilaporkan agent sendiri, bukan cwd shell tempat ia diluncurkan.
    """
    raw = data.get("cwd")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    paths = data.get("workspacePaths")
    if isinstance(paths, list) and paths and isinstance(paths[0], str) and paths[0].strip():
        return paths[0].strip()
    return None


def is_really_idle(data: dict) -> bool:
    """`fullyIdle` milik Antigravity: ia MELAPORKAN dirinya, bukan disimpulkan dari teks pane.

    Kalau field itu tidak ada (qodercli), kembali ke aturan lama: hook ini hanya dipanggil pada
    batas giliran, jadi penghentian yang sedang terjadi memang akhir pekerjaannya. Kalau ada dan
    False, masih ada tugas latar berjalan — dan menyuntik perintah di sana berarti melanggar
    "kalau Working, tunggu sampai selesai" yang diminta operator.
    """
    flag = data.get("fullyIdle")
    return True if flag is None else bool(flag)


def log(action: str, **rest) -> None:
    """Jejak tembakan — termasuk yang tidak melakukan apa pun.

    Sebuah pemicu yang diam tanpa catatan tidak bisa dibedakan dari pemicu yang tidak pernah
    dipanggil, dan itu berarti operator menyangka perintahnya hilang padahal mesin menolaknya.

    Parameter pertama bernama `action`, bukan `why`, dan itu bukan selera: panggilan seperti
    `log("X", why=msg)` pernah terbukti melempar TypeError karena namanya menabrak parameter
    posisional — dan tabrakan itu terjadi Justru di cabang penanganan kesalahan, sehingga hook
    keluar non-zero pada peristiwa Stop. Sebuah pemicu yang bisa crash saat melaporkan penolakan
    lebih buruk daripada pemicu yang diam.
    """
    try:
        NIGHT_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
               "action": action, **rest}
        # Satu peristiwa = satu baris. Versi pertama menulis barisnya, lalu menulis BARIS KEDUA
        # dengan hasil banner, jadi jejak yang sama muncul dua kali dan pembaca log tidak bisa
        # membedakan "hook menembak dua kali" dari "hook menulis hasil kirim". Hasil kirim sekarang
        # dihitung lebih dulu dan menempel pada baris yang sama.
        if action in BANNER_ACTIONS:
            detail = " ".join(f"{k}={v}" for k, v in rest.items()
                              if k in ("item", "title", "engine", "target", "why", "reason"))[:400]
            try:
                import sys as _sys
                _sys.path.insert(0, str(TOOLS))
                import notify
                row["notify"] = notify.notify_event(action, detail,
                                                    urgent=action != "SEMI_INJECTED")
            except Exception as exc:  # noqa: BLE001 — banner yang gagal dilaporkan, tidak dikarang
                row["notify"] = {"sent": False, "err": f"{type(exc).__name__}: {exc}"}
        with NIGHT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 — mencatat kegagalan tidak boleh ikut menggagalkan
        return
    # Catatan: keputusan banner sudah diambil di dalam blok tulis di atas. Yang lain (mesin belum
    # selesai, tangan belum diangkat, baris proyek lain) tetap hanya di log tanpa banner — operator
    # meminta notifikasi untuk AKSI dan ERROR, bukan untuk setiap detik yang dilewati mesin.


def read_input() -> dict:
    raw = (sys.stdin.read() or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def guides_for(conn, engine) -> tuple[str, list[str], int]:
    """Pedoman yang TERKUNCI — dibaca dari kontrak, bukan dari pilihan layar.

    Operator mengunci daftar ini 2026-10-08 11:05. Hook yang membaca pilihan dari tabel `workspaces`
    akan patuh pada siapa pun yang bisa menulis ke tabel itu, dan itu membuat kontrak bisa
    dimatikan dari UI yang seharusnya iaikatinya.
    """
    try:
        import work_order as wo
        wanted = wo.locked_guides()
    except Exception as exc:  # noqa: BLE001
        # Tiga nilai, bukan dua. Pemanggilnya `body, notes, nguides = guides_for(...)`, dan
        # cabang gagal ini terjadi SETELAH baris diklaim — mengembalikan dua nilai di sana
        # membuat hook melempar ValueError setelah klaim sukses: baris jadi WORKING dan tidak
        # ada yang menyuntik, kegagalan yang tidak terlihat dari mana pun.
        return "", [f"daftar terkunci tidak terbaca: {type(exc).__name__}"], 0
    blob, taken, notes = [], [], []
    # Anggaran dibelah PER BERKAS, bukan satu kolam yang dihabiskan berkas pertama.
    # Terukur 2026-10-08 11:28: `documents/AGENTS.md` 27 KB menghabiskan seluruh 3200 karakter,
    # sehingga ROADMAP / DESIGN_NOTES / ARCHITECTURE tidak terkirim sama sekali — pesan menyebut
    # "pedoman TERKUNCI" padahal yang sampai cuma awalan satu berkas. Kunci empat berkas yang
    # mengirim satu berarti engine menaati seperempat kontrak sambil mengira sudah menaati semuanya.
    wanted = wo.locked_guides()
    budget = max(GUIDE_MIN_PER_FILE, GUIDE_MAX_CHARS // max(1, len(wanted)))
    for g in wanted:
        p = Path(g["abs"])
        if not g.get("exists") or not p.is_file():
            notes.append(f"{g['path']} hilang dari disk")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{g['path']} tidak terbaca ({type(exc).__name__})")
            continue
        clipped = len(text) > budget
        taken.append(g["path"])
        head = text[:budget]
        blob.append(f"### {g['path']}  (kutipan {len(head)}/{len(text)} karakter — "
                    f"isi penuh: {g['abs']})\n{head}")
        if clipped:
            notes.append(f"{g['path']} dipotong ke {budget} karakter; baca berkasnya untuk penuh")
    # Jumlah pedoman yang benar-benar terkirim, bukan jumlah baris teks: log melaporkan angka
    # yang dibaca operator, dan `len(body.splitlines())` menghitung baris dari satu berkas.
    return "\n\n".join(blob), notes, len(taken)


def prepare_prompt(item, cwd, mode="SEMI"):
    """Susun prompt SEBELUM ada klaim — dan kembalikan None kalau gagal, bukan melempar.

    `mode` menentukan naskahnya, dengan aturan yang SAMA dengan `drainer.hand_new_work`: SEMI
    berarti "perintah operator, ada manusia di meja", ON berarti "tugas antrean, kerjakan".
    Memilihnya sendiri di sini akan membuat satu kata punya dua definisi (§12), dan yang berbeda
    adalah instruksi yang dibaca engine saat ia memulai pekerjaan.

    Ini alasan fungsinya dipisah: property yang ingin ditegakkan adalah "tidak ada baris yang
    tertinggal WORKING tanpa ada yang menyuntik". Selama pembentuk prompt hidup di dalam `main`
    setelah `claim`, satu exception dari `import drainer`, `get_item`, atau `.format` akan
    meninggalkan klaim itu — dan satu-satunya bukti yang tersisa adalah log SEMI_BROKE dengan
    exit 0. Dibuat fungsi terpisah, property-nya bisa dites: bikin fungsi ini gagal, lalu
    tunjukkan bahwa tidak ada apa pun yang diklaim.
    """
    import work_order as wo
    try:
        import drainer
        conn, engine = wo.connect()
        full = wo.get_item(conn, engine, int(item["id"])) or item
        body, notes, nguides = guides_for(conn, engine)
        # Naskah dipilih dari mode, dengan aturan yang sama persis seperti `hand_new_work`:
        # SEMI_PROMPT berbicara kepada "manusia di meja" tentang perintah yang ia tulis sendiri;
        # START_PROMPT adalah penugasan antrean. Memakai SEMI_PROMPT di bawah ON berarti menyuruh
        # engine mengerjakan tugas roadmap sambil memberitahunya bahwa yang mengirim adalah
        # operator yang sedang menonton — instruksi yang salah, bukan sekadar tidak rapi.
        gate_text = full.get("check_command") or "tanpa gerbang"
        if (mode or "").upper() == "SEMI":
            prompt = drainer.SEMI_PROMPT.format(id=item["id"], title=item["title"], ws=cwd,
                                                gate=gate_text)
        else:
            prompt = drainer.START_PROMPT.format(id=item["id"], title=item["title"],
                                                 gate=gate_text)
    except Exception as exc:  # noqa: BLE001 — sebelum ada klaim, gagal berarti diam
        log("SEMI_PREPARE_FAILED", item=int(item["id"]), engine=SESSION_ENGINE,
            reason=f"{type(exc).__name__}: {exc}",
            note="belum ada yang diklaim — barisnya tetap PENDING dan akan dicoba lagi")
        return None, 0
    if body:
        prompt += "\n\nPedoman yang mengikat (TERKUNCI oleh kontrak):\n" + body
    if notes:
        prompt += "\n\nCatatan pedoman: " + "; ".join(notes)
    # SEMI_PROMPT ditulis untuk jalur timer, di mana drainer memegang lease sampai engine mulai.
    # Di jalur hook ini klaimnya atas nama mesin ini sendiri, jadi kalimat itu dikoreksi.
    prompt += (f"\n\nCatatan jalur: hook ini akan mengklaim item {item['id']} atas nama "
               f"{SESSION_ENGINE} (bukan drainer). Perpanjang lease dengan "
               f"`python3 tools/work_order.py lease {item['id']}`.")
    return prompt, nguides


def main() -> int:
    data = read_input()
    cwd_raw = session_cwd(data)
    try:
        cwd = Path(cwd_raw).expanduser().resolve(strict=False) if cwd_raw else None
    except OSError:
        cwd = None

    import work_order as wo
    conn, engine = wo.connect()
    ap = wo.autopilot(conn, engine) or {}
    mode_now = (ap.get("mode") or "").upper()
    # ON DI SINI JUGA. Guard lama memulangkan hook untuk setiap mode KECUALI SEMI, dengan komentar
    # "ON: jalur ini tidak pernah ikut campur, tanpa jejak" — dan itu membuat FULL AUTO mati secara
    # struktural.
    # Rantainya: satu-satunya jalur yang bergerak tanpa TTY adalah batas giliran mesin ini sendiri;
    # timer butuh `level >= dispatch`, dan `arm` menolak tanpa `/dev/tty` yang bisa dibuka. Jadi di
    # keadaan normal ON + observe, TIDAK ADA satu pun yang melanjutkan pekerjaan: engine berhenti
    # untuk melapor, dan tidak ada yang membangunkannya. Operator harus bilang "lanjut" — yang
    # persis meniadakan kata "auto" pada autopilot. Terukur 2026-10-09, dan ini keluhan keduanya
    # setelah perbaikan keranjang: perbaikan itu tidak pernah berjalan, karena fungsi keluar 68
    # baris sebelumnya.
    #
    # Yang menahan jalur ini tetap sama, dan tidak dilonggarkan di sini: keranjang diturunkan dari
    # mode (`wo.queue_origin_for`), `claim()` menegakkannya saat penulisan, satu perintah aktif per
    # mesin, `MAX_INJECT` membatasi suntikan per baris, dan pemutus malam (kegagalan beruntun,
    # batas item, jendela kedaluwarsa) tetap menutup seluruh loop. Yang berubah hanyalah: ON berarti
    # mesin ini memang diserahkan ke engine.
    if mode_now == wo.OFF_MODE:
        log("SEMI_REFUSED", engine=SESSION_ENGINE, mode=mode_now,
            reason="OFF adalah keadaan berhenti: tidak ada yang disuntik pada batas giliran ini",
            stop_hook_active=bool(data.get("stop_hook_active")))
        return 0
    if mode_now not in wo.MODES:
        log("SEMI_REFUSED", engine=SESSION_ENGINE, mode=mode_now,
            reason="kolom mode berisi nilai di luar kosakata — UNKNOWN, dan UNKNOWN tidak "
                   "pernah menyuntik sesuatu ke sesi orang",
            stop_hook_active=bool(data.get("stop_hook_active")))
        return 0

    # Tangga `level` TIDAK menahan jalur ini, dan itu keputusan yang bisa dibaca, bukan kelalaian.
    # Yang dijaga `arm` adalah mesin yang bergerak sendiri: timer yang mengetik ke pane milik orang
    # lain tanpa ada yang memintanya. Peristiwa di sini bentuknya berbeda — sebuah sesi yang ORANG
    # itu sendiri mulai baru saja berhenti, dan yang akan disuntik adalah perintah yang dia tulis
    # sendiri ke antrean (keranjang SEMI ditegakkan `claim()`, bukan di sini). Memakai rem yang
    # sama di kedua tempat membuat permintaannya sendiri ("perintahku harus nyampe ke CLI, bukan
    # nunggu tick") tidak bisa dipenuhi tanpa mengangkat tangan lewat TTY setiap kali dia menulis
    # satu baris. Boot tetap diam: timer tidak pernah di-enable, dan jalur timer tetap butuh
    # `dispatch`. Yang berubah malam ini hanya bahwa perintah orang tidak lagi menunggu timer.

    # REM MASUK-KEMBALI. `stop_hook_active` adalah cara CLI memberitahu bahwa peristiwa Stop ini
    # terjadi SEDANG sebuah stop hook masih berjalan — yaitu kita sendiri. Field ini selama ini
    # hanya dicatat sebagai kolom log dan tidak pernah dijadikan keputusan, sehingga rangkaiannya
    # tidak punya pengereman: setiap blokir memicu Stop baru, yang memicu blokir baru. Yang diminta
    # operator persis ini ("jangan melakukan yang saya tidak bisa cancel"). Yang dilakukan di sini
    # hanya membiarkan penghentian itu selesai; barisnya tetap PENDING dan diambil pada batas
    # giliran berikutnya yang benar-benar bersih.
    # Ini rem pertama, bukan satunya: audit mengukur tiga Suntikan berantai 14 dtk/49 dtk terpisah
    # justru melaporkan field ini `false`, jadi batas injeksi per baris (`MAX_INJECT`) dan cooldown
    # pada setiap penolakan di bawah yang menutup sisanya.
    if data.get("stop_hook_active"):
        log("SEMI_REENTRY_HELD", engine=SESSION_ENGINE,
            reason="stop hook sudah aktif dalam rantai ini — penghentian dibiarkan selesai, "
                   "tidak ada yang disuntik",
            stop_hook_active=True)
        return 0

    if not is_really_idle(data):
        # Yang diminta operator: "kalau melihat kamu Working, nunggu sampai selesai". Antigravity
        # mengirim fullyIdle=false ketika masih ada tugas latar — di sini jawabannya adalah diam,
        # dan batas giliran berikutnya yang akan memanggil kita lagi.
        log("SEMI_NOT_IDLE", engine=SESSION_ENGINE,
            reason="laporan sesi: masih ada pekerjaan latar — penghentian ini bukan akhirnya")
        return 0

    # Satu perintah aktif per mesin. Jalur drainer sudah punya aturan ini (`ENGINE_BUSY`); tanpa
    # padanannya di sini, setiap `Stop` bisa menyuntik perintah BARU sementara mesin masih memegang
    # item yang belum di-complete — persis yang dilarang kalimat "kalau Working, tunggu sampai
    # selesai". Yang menunggu tidak hilang: ia tetap PENDING dan diambil pada batas giliran
    # berikutnya setelah yang ini selesai.
    # Pilih baris yang cocok dengan MESIN dan PROYEK sesi ini, dari daftar yang benar-benar siap.
    # Dua hal yang dulu salah di sini: (1) hook mengambil baris pertama lalu menolak seluruh tick
    # kalau baris itu milik proyek lain — tanpa cooldown, sehingga satu item proyek B di kepala
    # antrean membekukan selamanya setiap item proyek A; (2) aturannya dibandingkan dengan
    # `ws_path` mentah, sementara drainer memakai `resolve_workspace`. Sekarang keduanya memakai
    # `ready_items` + `resolve_workspace`, dan yang dilewati diberi jeda, bukan diabaikan diam-diam.
    #
    # Aturan "satu perintah aktif per mesin" TIDAK diperiksa di sini lagi — ia milik `claim()`,
    # yang menegakkannya pada saat penulisan. Dua salinan di dua tempat (hook memeriksa SEBELUM
    # klaim, drainer SESUDAH) justru celah yang dipakai audit putaran lima untuk menunjukkan dua
    # perintah bisa masuk ke satu mesin.
    if cwd is None:
        # Tanpa proyek yang diketahui, setiap baris akan "cocok" dan itu justru bocornya: sesi di
        # proyek A bisa menerima perintah proyek B hanya karena tidak ada yang memeriksa. Diam.
        log("SEMI_REFUSED", engine=SESSION_ENGINE,
            reason="hook dipanggil tanpa cwd/workspacePaths — tidak ada proyek yang bisa dicocokkan",
            stop_hook_active=bool(data.get("stop_hook_active")))
        return 0

    # Batas `due_epoch` sengaja TIDAK ditaati mentah di sini. Yang dijalankan jalur ini adalah
    # satu-satunya peristiwa yang membuktikan mesin sudah diam: batas giliran. Untuk alasan
    # "sedang sibuk" (pane belum tenang, engine masih memegang satu perintah), bukti itu
    # membatalkan jeda-nya — dan itulah yang operator minta: IDLE berarti terima perintah, supaya
    # ia bisa berputar sendiri. Untuk alasan lain (direktori salah, tanpa target, injeksi habis,
    # jendela ditutup) jeda tetap berlaku, karena mengulangi kegagalan yang tidak berubah di setiap
    # batas adalah loop tanpa rem.
    # Keranjang TIDAK ditulis di sini. Dulu `origin=wo.ORIGIN_OPERATOR` tanpa syarat, sementara
    # jalur timer menyaring menurut mode — dan karena `level=observe` mengunci timer, hook inilah
    # satu-satunya yang hidup. Akibatnya terukur: sakelar ON, antrean roadmap penuh, tidak ada yang
    # bergerak, dan operator harus menyuruh "lanjut" secara manual. Aturan keranjang milik
    # `work_order.queue_origin_for`, satu tempat untuk satu kata.
    ready = [r for r in wo.ready_items(conn, engine, respect_due=False,
                                       origin=wo.queue_origin_for(ap.get("mode")),
                                       for_engine=SESSION_ENGINE)
             if wo.retry_now(r)]
    item = None
    skipped = []
    for r in ready:
        r_ws = wo.resolve_workspace(r)
        # Pagar proyek, dengan satu pengecualian yang punya alasan: tab ini DILUNCURKAN di
        # ~/Documents/ai-workstation (di situ `.qoder/rules` dan kontrak berada) tetapi kode yang
        # digarapnya ada di ~/.ai-station. Membandingkan kedua path secara butuh membuat seluruh
        # antrean roadmap diveto — lihat `work_order.row_workable_here`.
        if cwd is not None and not wo.row_workable_here(r_ws, cwd):
            skipped.append(int(r["id"]))
            continue
        item = r
        break
    if item is None:
        if skipped:
            import drainer
            for sid in skipped:
                drainer.cooldown(conn, engine, sid, drainer.CLAIM_COOLDOWN)
            log("SEMI_OTHER_PROJECT", items=skipped, session_cwd=str(cwd), engine=SESSION_ENGINE,
                reason="bukan untuk proyek sesi ini — diberi jeda, tidak dibatalkan")
        return 0                      # tidak ada yang untuk mesin+proyek ini: diam itu benar

    # Klaim DITAHAN sampai prompt sudah utuh. Urutan ini bukan kerapian: versi pertama mengklaim
    # lebih dulu, lalu `get_item`, `guides_for`, `SEMI_PROMPT.format` dan `import drainer` bisa
    # melempar setelahnya — `__main__` menelannya sebagai SEMI_BROKE dengan exit 0, dan barisnya
    # tinggal WORKING tanpa ada yang menyuntik. Itulah persis kegagalan yang temuan (f) minta
    # dihapus, dan memperbaiki salah satu cabangnya saja tidak menghapus akarnya.
    prompt, nguides = prepare_prompt(item, cwd, mode_now)
    if prompt is None:
        # Tanpa jeda di sini, satu baris yang gagal karena CACAT KODE (misalnya template yang tidak
        # bisa di-format) dicoba lagi pada setiap batas giliran, selamanya — dan ia berdiri di kepala
        # antrean, jadi tidak ada baris lain yang pernah mendapat kesempatan. Zero backoff adalah
        # loop yang tidak bisa dibatalkan dari kursi operator, dan itu permintaan eksplisitnya.
        import drainer
        drainer.cooldown(conn, engine, int(item["id"]), drainer.CLAIM_COOLDOWN)
        wo.note_refusal(conn, engine, int(item["id"]),
                        "SEMI_PREPARE_FAILED · lihat logs/semi_wake.jsonl untuk tipe errornya")
        return 0

    # Mode diteruskan, BUKAN ditulis "SEMI". `claim()` menurunkan keranjang dari mode yang
    # dibawanya: mengirim "SEMI" di bawah ON membuat claim menolak baris roadmap dengan
    # alasan "SEMI hanya mengerjakan perintah operator" — yaitu bug ON yang ketiga, dan ia
    # akan muncul tepat setelah dua pertama diperbaiki.
    ok, msg = wo.claim(conn, engine, int(item["id"]), SESSION_ENGINE, mode=mode_now)
    if not ok:
        # claim() sudah memuat pemutus malam dan aturan tree kotor, jadi penolakan di sini adalah
        # keputusan yang tercatat, bukan kegagalan jalur ini. Tapi ia tetap butuh jeda: tanpa
        # cooldown, satu baris yang ditolak ENGINE_BUSY dicoba lagi pada Stop berikutnya, lalu
        # berikutnya lagi, sementara perintah di belakangnya tidak pernahgilir.
        import drainer
        drainer.cooldown(conn, engine, int(item["id"]), drainer.CLAIM_COOLDOWN)
        wo.note_refusal(conn, engine, int(item["id"]), f"CLAIM_REFUSED · {msg}"[:500])
        log("SEMI_CLAIM_REFUSED", item=int(item["id"]), reason=msg, engine=SESSION_ENGINE,
            stop_hook_active=bool(data.get("stop_hook_active")))
        return 0

    # Batas injeksi per baris. Klaim sudah terjadi, jadi kalau batasnya habis klaimnya dilepas
    # lagi: menghitung lalu melepas lebih aman daripada menyuntik lalu menghitung, karena yang
    # terakhir meninggalkan satu ketikan tambahan setiap putaran pada mesin yang jelas-jelas tidak
    # menyelesaikan baris ini.
    cap_ok, cap_msg = wo.nudge_inject(conn, engine, int(item["id"]))
    if not cap_ok:
        wo.release_claim(conn, engine, int(item["id"]), SESSION_ENGINE)
        import drainer
        drainer.cooldown(conn, engine, int(item["id"]), drainer.CLAIM_COOLDOWN)
        wo.note_refusal(conn, engine, int(item["id"]), f"INJECT_CAPPED · {cap_msg}"[:500])
        log("SEMI_INJECT_CAPPED", item=int(item["id"]), reason=cap_msg, engine=SESSION_ENGINE,
            note="klaim dilepas, baris kembali PENDING dan diberi jeda — tidak disuntik lagi")
        return 0

    # Satu-satunya hal yang tersisa setelah klaim adalah menulis satu baris ke stdout. Kalau itu
    # pun gagal, klaimnya dilepas — tidak ada jendela lagi tempat baris bisa tertinggal WORKING
    # tanpa ada yang menyuntik.
    try:
        print(json.dumps({"decision": BLOCK_DECISION, "reason": prompt}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        wo.finish(conn, engine, int(item["id"]), "PENDING",
                  reason=f"stdout gagal: {type(exc).__name__}")
        import drainer
        drainer.cooldown(conn, engine, int(item["id"]), drainer.CLAIM_COOLDOWN)
        log("SEMI_EMIT_FAILED", item=int(item["id"]), engine=SESSION_ENGINE,
            reason=f"{type(exc).__name__}: {exc}", note="klaim dilepas, baris kembali PENDING")
        return 0
    log("SEMI_INJECTED", item=int(item["id"]), title=str(item["title"])[:80],
        engine=SESSION_ENGINE, decision=BLOCK_DECISION,
        claim=msg, guides=nguides,
        stop_hook_active=bool(data.get("stop_hook_active")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — hook yang meledak tidak boleh menahan orang pulang
        log("SEMI_BROKE", reason=f"{type(exc).__name__}: {exc}")
        sys.exit(0)
