"""Satu-satunya pemilik kalimat "beri tahu operator di desktop".

Modul ini ada karena satu lubang yang dilaporkan operator sendiri pada 2026-10-08: tick berjalan
setiap lima menit dan tidak ada satu pun yang bisa dilihat orang. Loop yang tidak kelihatan tidak
berarti loop itu mati — tapi tidak ada bedanya bagi orang yang bangun dan menemukan komputer diam.
Maka tiap tick harus meninggalkan satu banner, dan banner itu harus bisa dibaca tanpa membuka
konsol.

Aturan yang menahan modul ini tetap jujur:

- Isinya SELALU turunan dari peristiwa yang benar-benar di-emit. Tidak ada angka yang dihitung di
  sini, tidak ada pembacaan database, tidak ada tebakan. Kalau sebuah peristiwa tidak membawa
  `item`, banner berkata `item?` — bukan menampilkan id yang kebetulan masih ada di ingatan.
- Tidak pernah mengangkat suara sendiri. Sebuah kegagalan `notify-send` dilaporkan sebagai
  `ok: False` beserta alasannya; ia tidak pernah mengubah kesimpulan tick, dan tidak pernah
  menyamar jadi tick yang gagal.
- Kosakata peristiwa dimiliki drainer, pengelompokannya dimiliki modul ini, dan selftest drainer
  membuktikan bahwa setiap nama yang bisa di-emit drainer sudah dikelompokkan di sini. Nama baru
  yang belum diklasifikasikan membuat tes itu merah, bukan masuk keranjang "aman" secara diam-diam.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

APP_ID = "AI Workstation Core"
# Akar workstation, dihitung ulang dari berkas ini — bukan di-hardcode, supaya pindah mesin
# tidak membuat config terbaca dari tempat yang salah.
REPO = Path(__file__).resolve().parent.parent
SEND_TIMEOUT = 2.0
EXPIRE_MS = 8000
MAX_ACTIONS_SHOWN = 4

# Peristiwa yang berarti "loop tidak bergerak, atau bergerak tapi tidak seperti yang dimaksud".
# Ini yang harus terlihat dari kejauhan, karena kegagalan yang operator takutkan bukan item sulit
# — melainkan auto yang berhenti dan komputer yang lalu diam sampai pagi.
URGENT = {
    "CLOSED_UNKNOWN", "HELD_BY_LEVEL", "HELD_BY_MODE",
    "MODE_UNKNOWN", "NO_TARGET", "SEND_FAILED", "GATE_FAILED",
    "ANSWER_DELIVER_REFUSED", "PANE_DIR_UNKNOWN", "PANE_WRONG_DIR", "DELIVER_REFUSED",
    # "QUEUED_NOT_DELIVERED" ditulis `work_order.queue_command` saat perintah orang sudah di ledger
    # tapi belum sampai ke pane — bentuk kegagalan yang paling mudah terlewat, karena layarnya
    # mengatakan "antre" dan tidak ada yang bilang antre itu tidak bergerak.
    "QUEUED_NOT_DELIVERED",
    # Dua jalur yang barusan dibuka di `work_order`: pertanyaan yang naik ke manusia, dan perintah
    # yang sudah tertulis di ledger tapi TIDAK terkirim ke CLI. Yang kedua harus berbanner justru
    # karena bentuk kegagalannya diam: orang menulis perintah, layarnya bilang "antre", dan tidak ada
    # yang pernah mengetuk pane-nya.
    "ESCALATED", "QUEUED_NOT_DELIVERED",
    # Batas kuota per malam yang menghentikan sebuah run adalah persis keadaan "auto berhenti dan
    # komputer diam sampai pagi" yang operator takutkan — bedanya dengan OFF adalah OFF itu pilihan
    # dia, sedangkan ini mesin yang kehabisan bahan bakar. Yang kedua harus terdengar.
    "QUOTA_CLOSED",
    # Keluarga `*_UNKNOWN` untuk kerja idle: mesin tidak diam karena tidak ada kerja, tapi karena
    # ia tidak bisa MEMBACA apakah ada kerja. Itu bukan "nol", dan diam karena tidak terbaca
    # persis sama kelihatannya dengan diam karena selesai.
    "IDLE_DEBT_UNKNOWN",
    # Gerbang VRAM menolak pekerjaan GPU: bukan rem yang dipatuhi (itu WINDOW_CLOSED/MODE_OFF di
    # keranjang note) melainkan mesin yang kehabisan bahan bakar — sama seperti QUOTA_CLOSED.
    # Peristiwa inilah yang menjawab "kenapa 3D tidak jalan tadi malam" sementara satu-satunya
    # penolakan yang bisa diperbaiki orang adalah membebaskan memori atau menutup pemegangnya,
    # dan peristiwa itu menyebut pemegangnya di badan banner.
    "VRAM_DENIED", "VRAM_UNKNOWN",
}
# Peristiwa yang berarti "mesin bekerja": boleh pendek, tidak boleh berteriak.
PROGRESS = {"WORK_DISPATCHED", "GATE_PASSED", "CONTINUE_SENT", "ANSWER_SENT", "CLAIMED",
            # Hook yang menyuntik item ke CLI yang sedang diam ADALAH "mengirim perintah ke CLI"
            # — jalur yang persis diminta operator untuk selalu kelihatan.
            "SEMI_INJECTED",
            # Baris kerja idle baru dibuka. Bukan alarm: ini jawaban atas "lalu untuk apa ON",
            # dan kejadiannya tetap dilaporkan lewat WORK_DISPATCHED saat benar-benar terkirim.
            "IDLE_QUEUED",
            # Sebuah item selesai dan penerusnya langsung diantrikan — inilah peristiwa "DONE ->
            # lanjut" yang operator minta, jadi ia harus terlihat sebagai kemajuan, bukan catatan.
            "FOLLOWUP_QUEUED"}
# Peristiwa yang layak masuk banner tanpa alarming dan tanpa ditahan: tercatat sebagai pilihan,
# bukan sebagai sisa yang tidak kebagian keranjang.
NOTE = {"SEMI_HAND_FLAT", "OBSERVED", "GATE_BASELINE", "GATE_NO_COMMAND", "HELD", "ENGINE_BUSY",
        "SEMI_NOT_IDLE", "PANE_NOT_QUIET", "ANSWER_NOT_SENT", "COOLDOWN", "GATE_CHECKED",
        "GATE_DEFERRED", "STUCK_PARKED", "SKIP_NOT_OPERATOR", "SKIP_NOT_RESTING",
        "RECONCILED", "RELEASED", "REFUSED_UNDOABLE", "QUEUE_EMPTY", "SEMI_QUEUED",
        # Rem yang dipatuhi bukan kegagalan: `WINDOW_CLOSED` berarti seseorang bilang berhenti dan
        # jalur instan menurutinya. Kabarnya tetap sampai — lewat QUEUED_NOT_DELIVERED, yang urgent
        # — jadi nama ini sendiri tidak boleh membunyikan alarm kedua untuk satu kejadian.
        "WINDOW_CLOSED", "SELFTEST",
        # `MODE_OFF` satu keranjang dengan WINDOW_CLOSED karena keduanya adalah rem yang dipatuhi,
        # bukan mesin yang macet. Yang membuat perintah orang tidak sampai sudah berbanner lewat
        # QUEUED_NOT_DELIVERED; membunyikan banner kedua untuk satu kejadian itulah yang membuat
        # orang berhenti membaca banner.
        "MODE_OFF",
        # Empat alasan kerja idle TIDAK dibuka. Semuanya adalah loop yang menahan dirinya sendiri
        # karena sebuah syarat tidak terpenuhi — mesin sibuk, sudah ada satu baris idle, tidak ada
        # utang terukur, atau anggaran idle habis — bukan kegagalan pembacaan. Yang terakhir itu
        # sengaja dibedakan: `IDLE_BUDGET_SPENT` berarti pengayaan-diri berhenti karena sudah
        # cukup banyak dikerjakan malam ini, dan itu hasil, bukan kerusakan.
        "IDLE_ENGINE_BUSY", "IDLE_ALREADY_OPEN", "IDLE_NO_DEBT", "IDLE_BUDGET_SPENT",
        # Mode tersimpan bukan ON: rem yang dipatuhi, bukan mesin macet. `hand_new_work` sudah
        # memeriksa ini di pemanggil; pemeriksaan kedua di `ensure_idle_item` melaporkan lewat sini
        # supaya pagar kedua tidak pernah diam-diam mengasumsikan yang pertama sudah jalan.
        "IDLE_HELD_BY_MODE",
        # Gerbang VRAM yang Lolos adalah kejadian rutin: ia berarti scheduler memeriksa dan
        # boleh lanjut. Yang harus terdengar adalah yang sebaliknya (VRAM_DENIED / VRAM_UNKNOWN
        # di keranjang urgent) — melaporkan setiap izin berarti satu banner per item GPU, persis
        # denyut yang operator minta dibungkam.
        "VRAM_ALLOWED",
        # `CLOSED_NIGHT` pindah dari URGENT pada 2026-10-15 atas keluhan operator yang terukur:
        # cabangnya `return` di awal tick dan di-emit ULANG pada setiap tick selama jendela ON tidak
        # terbuka, jadi satu keadaan diam ("tidak ada jendela malam") menghasilkan banner yang sama
        # tiap 10-30 menit selama mesin berjalan SEMI+observe. Bentuknya sama dengan WINDOW_CLOSED dan
        # MODE_OFF di atas: rem yang dipatuhi, bukan mesin yang macet — dan penyebab yang membuat
        # perintah operator tidak sampai sudah berbanner sendiri lewat QUEUED_NOT_DELIVERED, jadi yang
        # dibungkam di sini hanya pengulangan, bukan kabarnya. Yang TIDAK ikut turun: CLOSED_UNKNOWN,
        # MODE_UNKNOWN, QUOTA_CLOSED — ketiganya adalah mesin yang tidak bisa membaca atau kehabisan
        # bahan bakar, dan itu tetap darurat walau berulang (berulang sekarang dibatasi
        # `REPEAT_WINDOW_S`, bukan dengan membuat keadaan darurat jadi diam).
        "CLOSED_NIGHT"}
# `CLOSED_OFF` pernah ada di daftar ini dan dibuang dengan alasan "OFF bukan lagi sebuah mode".
# Alasan itu tidak berlaku sejak 2026-10-09: OFF kembali menjadi state yang disimpan, dan nama
# kejadiannya `MODE_OFF` (lihat keranjang NOTE di atas). Yang tetap benar dari komentar lama:
# nama keadaan yang tidak di-emit siapa pun tidak boleh punya perilaku diam — `classify` membacanya
# `unknown`, dan `unknown` selalu berbanner. Itulah sebabnya tes klasifikasi memeriksa SETIAP nama
# yang bisa di-emit, bukan hanya yang terlintas saat seseorang menulis daftarnya.


def muted_classes(path=None) -> set:
    """Kelas banner yang operator bungkam, dibaca dari config pada saat dipakai (§12).

    Permintaannya langsung, 2026-10-15 11:2x: "notif autopilot/semi ini dari ai-workstation tolong
    di disable aja, agak sedikit mengganggu ketika daily hampir 10/30 menit sekali keluar terus."
    Yang keluar sesering itu adalah kelas `progress` (WORK_DISPATCHED, CLAIMED, GATE_PASSED) —
    bukan pertanyaan dan bukan kegagalan. Jadi yang dibungkam adalah denyut, dan `urgent` tetap
    berbunyi: orang yang mematikan semua banner tidak akan pernah tahu bahwa loop berhenti
    bekerja.

    `unknown` TIDAK pernah bisa dibungkam, dan itu bukan untuk ditawar: peristiwa baru yang lupa
    diklasifikasikan akan jatuh ke `unknown`, dan kalau `unknown` bisa dimute, cara tercepat untuk
    membungkam sebuah loop selamanya hanyalah lupa mengklasifikasikannya.
    """
    p = Path(path or (REPO / "config" / "notify.json"))
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — tidak ada config = tidak ada yang dibungkam
        return set()
    m = {str(x).lower() for x in (cfg.get("muted") or [])}
    # `urgent` ikut dibuang, dan itu temuan audit independen atas commit saya sendiri
    # (2026-10-15): versi pertama hanya membuang `unknown`, jadi satu baris `{"muted":["urgent"]}`
    # di config akan membungkam SEND_FAILED, ESCALATED, dan QUOTA_CLOSED diam-diam — orang yang
    # menambang kunci API tidak pernah melihat bahwa loop-nya mati. Kelas yang membungkam alarm
    # tidak boleh bisa ditulis dari config; untuk berhenti total ada sakelar mode dan `disarm`,
    # dan keduanya menyebut dirinya sendiri.
    return m - {"unknown", "urgent"}


STATE_PATH = Path.home() / "runtime" / "notify_last.json"
# Seberapa lama satu keadaan yang SAMA boleh mengulangi bannernya. Enam jam dipilih karena batas
# bawahnya adalah apa yang dikeluhkan operator ("10/30 menit sekali") dan batas atasnya adalah
# keadaan yang masih darurat: jendela ON yang ditutup kuota harus tetapingatkan orang sebelum
# pagi, tapi tidak setiap lima menit.
REPEAT_WINDOW_S = 6 * 3600
# Jendela yang sama untuk alarm adalah pembungkaman, bukan pengulangan. Terukur dari audit
# independen 2026-10-15 terhadap commit db7b47d: `SEND_FAILED` yang menetap karena mesin memang
# rusak hanya akan berbunyi sekali lalu diam enam jam — operator kehilangan satu-satunya tanda
# bahwa loop berhenti bekerja. Jadi dua angka, dua niat: denyut (progress) boleh ditahan lama,
# keadaan darurat harus kembali mengetuk dalam setengah jam.
REPEAT_URGENT_WINDOW_S = 30 * 60


def repeat_window(cls) -> int:
    """Satu tempat yang memutuskan berapa lama satu kelas boleh diam (§12)."""
    return REPEAT_WINDOW_S if cls == "progress" else REPEAT_URGENT_WINDOW_S


def signature(events) -> str:
    """Sidik keadaan yang banner kabarkan — bukan sekadar daftar nama peristiwa.

    `item` dan `why` ikut masuk supaya kegagalan baru pada item lain tidak dianggap pengulangan,
    dan supaya satu kegagalan yang penyebabnya berubah (mis. "Pane tidak bisa dibaca" menjadi
    "quota window 5h kosong") tetap boleh berbanner lagi.
    """
    parts = []
    for e in (events or []):
        if not isinstance(e, dict):
            continue
        parts.append(f"{e.get('action')}|{e.get('item', e.get('id', ''))}|"
                     f"{str(e.get('why', ''))[:120]}")
    return hashlib.sha256(";".join(sorted(parts)).encode()).hexdigest()[:16]


def last_sent(path=None) -> dict:
    """Riwayat {sidik: detik} — bukan satu slot.

    Audit yang sama menemukan versi satu-slot: dua alarm yang muncul bergantian setiap tick
    saling menimpa, dan pengulangan tidak tertahan sama sekali. Rambu yang harusnya tahan untuk
    satu keadaan malah menghapus rambu keadaan lain.
    """
    try:
        raw = json.loads(Path(path or STATE_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — tidak ada state = belum pernah dikirim
        return {}
    # Bentuk lama adalah {"sig": ..., "ts": ...}; dibaca supaya tick pertama setelah perubahan ini
    # tidak kehilangan riwayat yang sudah ada, bukan supaya kode lama boleh tetap ditulis.
    if set(raw) == {"sig", "ts"}:
        return {str(raw["sig"]): float(raw["ts"])}
    return {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def is_repeat(sig, now=None, path=None, window=REPEAT_WINDOW_S) -> bool:
    """Banner dengan sidik yang sama sudah dikirim kurang dari `window` detik lalu?

    Arah gagalnya sengaja MEMBUNYIKAN, bukan membungkam: berkas state yang tidak terbaca atau
    bertimestamp rusak mengembalikan False (bukan pengulangan), sama seperti pemotong call-worker
    yang menolak memotong saat daftar pane tidak bisa dibaca. Absence of evidence bukan izin untuk
    membungkam alarm.
    """
    st = last_sent(path)
    try:
        ts = float(st.get(sig))
    except (TypeError, ValueError):
        return False
    age = (time.time() if now is None else now) - ts
    return 0 <= age < window


def record_sent(sig, now=None, path=None) -> None:
    p = Path(path or STATE_PATH)
    ts = time.time() if now is None else now
    try:
        hist = last_sent(p)
        hist[sig] = ts
        # Yang tua dibuang: tanpa ini berkas tumbuh selamanya, dan setiap keadaan yang pernah
        # lewat suatu saat akan membuat pembacanya mengira ia masih baru.
        hist = {k: v for k, v in hist.items() if ts - float(v) < REPEAT_WINDOW_S * 2}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(hist), encoding="utf-8")
    except Exception:  # noqa: BLE001 — gagal mencatat hanya berarti tick berikutnya tidak tertahan
        pass


def worth_notifying(events, mute=None) -> tuple[bool, str]:
    """Hanya tick yang BERGERAK, BERTANYA, atau GAGAL yang boleh menyentuh desktop.

    Aturan ini bukan kenyamanan: operator menyetujui notifikasi per kejadian justru supaya
    notifikasi tidak menjadi noise yang dia matikan. Tick yang hanya melihat (OBSERVED) adalah
    keadaan normal mesin yang sehat dan harus tetap berada di log malam saja.

    Kelas `note` sengaja TIDAK ada di perulangan bawah: ada persis satu jalur yang boleh mengangkat
    suara untuk sebuah keadaan, dan itu jalur ini. Kalau `note` juga bisa naik (misalnya lewat
    mute yang salah tulis), satu kejadian bisa menghasilkan dua banner — dan dua banner untuk satu
    keadaan adalah cara orang berhenti membaca banner mana pun.
    """
    acts = [str((e or {}).get("action") or "?") for e in (events or []) if isinstance(e, dict)]
    # `mute` bisa diserahkan: selftest harus menguji kedua sisi tanpa bergantung pada apakah
    # kebetulan ada config/notify.json di mesin yang menjalankan tes. Perilaku produksi tetap
    # membaca config pada saat dipakai, bukan mengingatnya.
    #
    # `urgent` dibuang di SINI, bukan hanya di pembaca config. Versi pertama membuang
    # {"unknown","urgent"} di `muted_classes()` saja, jadi jalur yang menyerahkan mute sebagai
    # argumen — termasuk pemanggil yang suatu saat menyaring config sendiri — masih bisa
    # membungkam seluruh alarm dengan satu kata. Aturan yang hanya hidup di satu pembaca config
    # bukan aturan, itu default yang kebetulan aman.
    mute = (muted_classes() if mute is None else set(mute)) - {"unknown", "urgent"}
    for a in acts:
        c = classify(a)
        if c == "progress":
            if "progress" in mute:
                continue
            return True, a
    for a in acts:
        c = classify(a)
        # Tidak ada pemeriksaan `"urgent" in mute` di sini, dan itu disengaja. Baris itu pernah
        # ada dan menjadi dead code sejak mute dibersihkan di atas — audit putaran keempat
        # menemukannya. Bahayanya bukan baris mati, tapi apa yang ia yakinkan ke pembaca
        # berikutnya: bahwa urgent bisa dibungkam, karena ada kode yang LOOKS like it tries.
        if c in ("urgent", "unknown"):
            return True, a
    # Alasan diam harus menyebut penyebab yang sebenarnya. Bentuk sebelumnya selalu menulis
    # `MUTED:<kelas>` selama ada config mute apa pun, jadi tick yang isinya hanya `note`
    # (CLOSED_NIGHT, OBSERVED) tercatat sebagai "dibungkam progress" padahal tidak ada satu pun
    # peristiwa berkelas progress di situ. Operator membaca baris ini untuk memutuskan apakah
    # mesin diam karena disuruh atau karena rusak — label yang salah menjawab pertanyaan itu.
    dropped = [a for a in acts if classify(a) == "progress" and "progress" in mute]
    base = ",".join(acts) or "tanpa peristiwa"
    if dropped:
        return False, "MUTED:progress:" + base
    return False, "NOTHING_WORTHY:" + base


def classify(action: str) -> str:
    """urgent | progress | note | unknown.

    `unknown` ada sebagai nama, bukan sebagaidefault yang ramah: selftest drainer menuntut setiap
    peristiwa yang bisa di-emit masuk salah satu dari tiga keranjang pertama. Peristiwa baru yang
    lupa diklasifikasikan membuat tes itu merah — kalau tidak, ia lulus sebagai "tidak mendesak"
    tanpa pernah dibaca siapa pun, dan itu cara sebuah loop berhenti berteriak saat ia berhenti
    bekerja.
    """
    if action in URGENT:
        return "urgent"
    if action in PROGRESS:
        return "progress"
    if action in NOTE:
        return "note"
    return "unknown"


def bus_address() -> str | None:
    """Alamat session bus, dengan fallback ke socket standar.

    Unit systemd user hampir selalu mewarisi DBUS_SESSION_BUS_ADDRESS, tapi "hampir" bukan
    "selalu", dan notify-send yang gagal karena tidak menemukan bus akan mengembalikan banner
    kosong — kegagalan yang persis jenis yang tidak akan pernah diketahui orang di meja.
    """
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if addr:
        return addr
    sock = Path(f"/run/user/{os.getuid()}/bus")
    return f"unix:path={sock}" if sock.exists() else None


def summarize(events, mode=None, level=None, clock=None) -> dict:
    """Judul + isi banner dari peristiwa tick. Murni: tidak spawning, tidak membaca database."""
    rows = [e for e in (events or []) if isinstance(e, dict)]
    acts = [(str(e.get("action") or "?"), e) for e in rows]
    # `unknown` ikut mengangkat suara. `worth_notifying` sudah memutuskan bahwa peristiwa yang belum
    # diklasifikasi LAYAK berbanner; kalau `summarize` hanya mencari "urgent", peristiwa baru yang
    # belum punya keranjang akan muncul sebagai banner biasa tanpa ⚠ dan tanpa urgency DBus —
    # persis bentuk "diam yang tersamar" yang fungsi ini dibuat untuk cegah.
    urgent = [a for a, e in acts if classify(a) in ("urgent", "unknown")]
    ts = time.strftime("%H:%M", time.localtime(clock() if callable(clock) else time.time()))
    head = "Autopilot" + ((" · " + str(mode)) if mode else "") + ((" · " + str(level)) if level else "")
    if urgent:
        title = f"⚠ {head}: {urgent[0]}"
    elif not acts:
        title = f"{head}: tick tanpa peristiwa"
    else:
        title = f"{head}: {len(acts)} peristiwa"

    lines = [f"{ts}  mode={mode or '?'}  level={level or '?'}"]
    for a, e in acts[:MAX_ACTIONS_SHOWN]:
        bit = a
        if e.get("item") is not None:
            bit += f" item {e['item']}"
        if e.get("target"):
            bit += f" → {e['target']}"
        if e.get("next") is not None:
            bit += f"  (next {e['next']})"
        if e.get("claims") is not None:
            bit += f"  (claims {e['claims']})"
        if e.get("why"):
            bit += f"  — {str(e['why'])[:120]}"
        lines.append(bit)
    if len(acts) > MAX_ACTIONS_SHOWN:
        lines.append(f"+{len(acts) - MAX_ACTIONS_SHOWN} peristiwa lagi di log malam")
    return {"title": title, "body": "\n".join(lines), "urgent": bool(urgent),
            "actions": [a for a, _ in acts]}


def _dbus_argv(title: str, body: str, urgent: bool) -> list[str]:
    """Argumen `gdbus call` untuk satu banner freedesktop.

    Bentuknya dibangun di sini, bukan di dalam `send`, supaya bisa DIUJI tanpa menyentuh desktop:
    tiga percobaan pertama format hint-nya salah dan kegagalannya hanya terlihat sebagai rc=1, jadi
    yang diperiksa sekarang adalah teks yang benar-benar berangkat, bukan harapan.
    """
    hint = "{'urgency': <byte 2>}" if urgent else "{}"
    return [
        "gdbus", "call", "--session",
        "--dest", "org.freedesktop.Notifications",
        "--object-path", "/org/freedesktop/Notifications",
        "--method", "org.freedesktop.Notifications.Notify",
        APP_ID, "0", "", title, body, "[]", hint, str(EXPIRE_MS),
    ]


def send(title: str, body: str, urgent: bool = False, timeout: float = SEND_TIMEOUT) -> dict:
    """Satu banner. Tidak pernah melempar — kegagalan dilaporkan apa adanya.

    `ok: True` berarti **daemon notifikasi menerima dan memberi id**. Itu bukan bukti bahwa seorang
    manusia melihatnya: Do-Not-Disturb, layar terkunci, atau session bus tanpa compositor semuanya
    bisa menghasilkan id tanpa satu piksel pun di desktop. Yang bisa dijanjikan jalur ini hanya
    "sudah diserahkan ke daemon", dan kata itu yang dipakai di mana-mana — bukan "dibaca".


    Kenapa bukan `notify-send`: biner itu TIDAK ADA di mesin ini (diukur 2026-10-08: `which` kosong,
    `/usr/bin/notify-send` tidak ada), dan daemon COSMIC memegang `org.freedesktop.Notifications`
    di session bus. Setiap call site lama memakai `notify-send` sambil menelan errornya —
    `let _ = Command::new(...)`, `except Exception: pass` — sehingga seluruh permukaan notifikasi
    workstation ini mati tanpa pernah mengaku. Jalur ini bicara langsung ke bus-nya, dan rc-nya
    dibaca.
    """
    env = dict(os.environ)
    addr = bus_address()
    if addr:
        env["DBUS_SESSION_BUS_ADDRESS"] = addr
    cmd = _dbus_argv(title, body, urgent)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        # `(uint32 43,)` adalah id yang diberikan daemon. rc 0 saja tidak cukup: spesifikasi
        # freedesktop menetapkan `replaces_id = 0` sebagai jawaban atas Notifikasi yang TIDAK bisa
        # ditampilkan, jadi menerima angka nol sebagai keberhasilan berarti melapor terkirim tepat
        # ketika desktop menolak menampilkannya — dan itu satu-satunya kegagalan yang tidak akan
        # pernah diketahui orang.
        out = p.stdout.strip()
        rid = None
        if p.returncode == 0 and out.startswith("(uint32"):
            try:
                rid = int(out.split()[1].rstrip(",)"))
            except (IndexError, ValueError):
                rid = None
        accepted = p.returncode == 0 and rid is not None and rid > 0
        return {"ok": accepted, "rc": p.returncode, "id": rid,
                "err": None if accepted else ((p.stderr or p.stdout or "").strip()[:200]
                                              or ("daemon menolak (id 0)" if rid == 0 else None))}
    except Exception as exc:  # noqa: BLE001 — banner yang gagal tidak boleh membunuh tick
        return {"ok": False, "rc": None, "err": f"{type(exc).__name__}: {exc}"}


def notify_tick(events, mode=None, level=None, runner=None, mute=None,
                 now=None, state_path=None) -> dict:
    """Jalur yang dipakai tick: hanya kejadian yang layak, dan hanya sekali per keadaan."""
    want, reason = worth_notifying(events, mute=mute)
    if not want:
        return {"sent": False, "skipped": True, "reason": reason, "ok": True}
    # Jendela yang dipakai adalah yang PALING KECIL di antara kelas yang hadir. Versi pertama
    # memakai nama yang pulang dari worth_notifying — dan fungsi itu menyapu progress lebih dulu,
    # jadi tick berisi WORK_DISPATCHED + SEND_FAILED menahan alarmnya selama jendela denyut 6 jam.
    cls = str(reason).split(":", 1)[0]
    names = [str((e or {}).get("action") or "?") for e in (events or []) if isinstance(e, dict)]
    loud = {classify(a) for a in names} & {"progress", "urgent", "unknown"}
    window = min((repeat_window(c) for c in loud), default=repeat_window(cls))
    sig = signature(events)
    if is_repeat(sig, now=now, path=state_path, window=window):
        return {"sent": False, "skipped": True, "reason": f"REPEAT:{reason}", "ok": True,
                "sig": sig, "window": window}
    out = notify_events(events, mode=mode, level=level, runner=runner)
    # Dicatat dari hasil KIRIM, bukan dari hasil ringkas: `sent` di sini sudah dibuktikan oleh
    # daemon (id banner non-nol), jadi kegagalan kirim tidak pernah membuat tick berikutnya
    # diam karena "sudah pernah memberi tahu" sesuatu yang sebenarnya tidak sampai.
    if out.get("sent") and out.get("ok"):
        record_sent(sig, now=now, path=state_path)
    return {**out, "sig": sig, "window": window}


def notify_events(events, mode=None, level=None, runner=None) -> dict:
    """Rangkum lalu kirim, tanpa bertanya dulu apakah layak."""
    s = summarize(events, mode=mode, level=level)
    out = {**s, "sent": False}
    out.update((runner or send)(s["title"], s["body"], urgent=s["urgent"]))
    out["sent"] = bool(out.get("ok"))
    return out


def notify_event(action, detail="", *, urgent=False, runner=None) -> dict:
    """Satu peristiwa tunggal dari jalur di luar tick: hook yang membangunkan CLI, pertanyaan
    yang naik ke antrean manusia. Judulnya memakai nama peristiwa dari kosakata database, jadi
    banner dan baris log memakai kata yang sama — dan tidak ada angka yang tidak diukur."""
    title = ("⚠ " if urgent else "") + f"Station · {action}"
    out = {"title": title, "body": detail, "urgent": bool(urgent), "sent": False}
    out.update((runner or send)(title, detail, urgent=bool(urgent)))
    out["sent"] = bool(out.get("ok"))
    return out


def selftest() -> int:
    """Rangkuman diuji sebagai fungsi murni; pengiriman diuji tanpa menyentuh desktop."""
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    check("tick yang mendedikasikan pekerjaan dilaporkan sebagai progress, bukan darurat",
          classify("WORK_DISPATCHED") == "progress" and classify("CLOSED_NIGHT") == "note")
    # Dua sisi dari satu perubahan: jendela yang tertutup adalah rem yang dipatuhi (note, diam),
    # sementara gerbang yang TIDAK BISA DIBACA tetap darurat. Kalau yang kedua ikut turun, operator
    # kehilangan satu-satunya tanda bahwa loop berhenti bekerja — itu bukan kenyamanan, itu buta.
    check("jendela ON yang tertutup tidak lagi berbanner, tapi gerbang yang tidak terbaca MASIH",
          classify("CLOSED_NIGHT") == "note" and classify("CLOSED_UNKNOWN") == "urgent"
          and classify("MODE_UNKNOWN") == "urgent" and classify("QUOTA_CLOSED") == "urgent")
    s = summarize([{"action": "CLOSED_UNKNOWN", "why": "level tidak terbaca"}], mode="ON", level="resume")
    check("gerbang tertutup mengangkat suara dan menyebut alasannya",
          s["urgent"] and "⚠" in s["title"] and "CLOSED_UNKNOWN" in s["title"]
          and "level tidak terbaca" in s["body"], json.dumps(s))
    s_cn = summarize([{"action": "CLOSED_NIGHT", "why": "jendela ON lewat"}], mode="SEMI",
                     level="observe")
    check("CLOSED_NIGHT tidak mengangkat suara dan tidak dapat tanda ⚠ — tapi tetap bernama di judul",
          not s_cn["urgent"] and "⚠" not in s_cn["title"] and "CLOSED_NIGHT" not in s_cn["title"],
          json.dumps(s_cn))
    w_cn, _r_cn = worth_notifying([{"action": "CLOSED_NIGHT", "why": "jendela ON lewat"}], mute=set())
    check("tick yang hanya melaporkan jendela tertutup TIDAK menyentuh desktop, walau tanpa mute",
          w_cn is False, _r_cn)
    s2 = summarize([{"action": "WORK_DISPATCHED", "item": 88, "target": "qoder"},
                    {"action": "GATE_PASSED", "item": 91}], mode="SEMI", level="dispatch")
    check("isi banner menyebut item dan engine dari peristiwa, tidak mengarang",
          "item 88" in s2["body"] and "→ qoder" in s2["body"] and "item 91" in s2["body"], s2["body"])
    s3 = summarize([{"action": "WORK_DISPATCHED"}], mode="SEMI")
    check("peristiwa tanpa item tidak mendapat id karangan", "item" not in s3["body"], s3["body"])
    s4 = summarize([], mode="SEMI")
    check("tick tanpa peristiwa dilaporkan sebagai tick tanpa peristiwa — bukan 'aman'",
          "tanpa peristiwa" in s4["title"] and not s4["urgent"], s4["title"])
    many = [{"action": "GATE_PASSED", "item": i} for i in range(9)]
    s5 = summarize(many, mode="ON")
    check("banner memotong, tapi mengakui sisanya ada di log",
          "9 peristiwa" in s5["title"] and "lagi di log malam" in s5["body"], s5["body"])

    calls = []
    fake = {"ok": False, "rc": 1, "err": "notification server tidak ditemukan"}

    def runner(title, body, urgent=False):
        calls.append((title, body, urgent))
        return fake
    # Kejadian yang dipilih harus yang MASIH layak berbanner sesudah `CLOSED_NIGHT` turun ke note,
    # dan state pengulangan harus berkas sementara: kalau tidak, dua tes di bawah mengukur
    # pengulangan tes sebelumnya, bukan gerbangnya.
    import tempfile
    _st = Path(tempfile.mkdtemp()) / "notify_last.json"
    out = notify_tick([{"action": "SEND_FAILED", "why": "pane tidak bisa dibaca"}], mode="ON",
                      runner=runner, state_path=_st)
    check("satu kejadian layak = satu banner", len(calls) == 1, str(calls))
    check("banner yang gagal kirim dilaporkan sebagai gagal, bukan sebagai tick yang berhasil",
          out["sent"] is False and out["ok"] is False and "tidak ditemukan" in (out.get("err") or ""),
          json.dumps(out, default=str))
    check("kegagalan kirim tidak mengubah isi rangkuman", out["title"] == calls[0][0])
    # Pengulangan hanya boleh menahan banner yang SUNGGUH terkirim. Kegagalan kirim tidak boleh
    # dihitung sebagai "sudah pernah memberi tahu" — akibatnya adalah alarm yang dibungkam
    # justru karena ia tidak sampai.
    check("banner yang GAGAL terkirim tidak dicatat sebagai pengulangan",
          not _st.exists(), str(_st))
    _ok_calls = []
    okrunner = lambda title, body, urgent=False: (_ok_calls.append(title), {"ok": True, "rc": 0, "err": ""})[1]
    a = notify_tick([{"action": "SEND_FAILED", "why": "pane tidak bisa dibaca"}], mode="ON",
                    runner=okrunner, state_path=_st)
    b = notify_tick([{"action": "SEND_FAILED", "why": "pane tidak bisa dibaca"}], mode="ON",
                    runner=okrunner, state_path=_st)
    c = notify_tick([{"action": "SEND_FAILED", "why": "disk penuh"}], mode="ON",
                    runner=okrunner, state_path=_st)
    check("keadaan yang sama persis hanya berbanner sekali dalam jendela enam jam",
          len(_ok_calls) == 2 and a.get("sent") and b.get("skipped")
          and str(b.get("reason")).startswith("REPEAT:") and c.get("sent"),
          f"{len(_ok_calls)} panggilan · b={b.get('reason')}")
    check("penghitung mundur yang kedaluwarsa membangunkan banner lagi",
          is_repeat(signature([{"action": "SEND_FAILED", "why": "pane tidak bisa dibaca"}]),
                    now=time.time() + REPEAT_URGENT_WINDOW_S + 1, path=_st,
                    window=repeat_window("urgent")) is False, str(_st))
    check("berkas state yang tidak terbaca TIDAK membungkam alarm (arah gagal = berbunyi)",
          is_repeat("deadbeefdeadbeef", path=_st) is False
          and last_sent(path=_st.parent / "tidak-ada.json") == {})
    # Temuan audit atas commit saya sendiri: dua alarm yang muncul bergantian saling menimpa
    # state satu slot, jadi TIDAK ADA pengulangan yang tertahan. Sekarang riwayat per-sidik.
    _alt = Path(tempfile.mkdtemp()) / "notify_last.json"
    _alt_calls = []
    alt_runner = lambda title, body, urgent=False: (_alt_calls.append(title),
                                                    {"ok": True, "rc": 0, "err": ""})[1]
    ea = [{"action": "SEND_FAILED", "why": "pane tidak bisa dibaca"}]
    eb = [{"action": "GATE_FAILED", "item": 7, "why": "gerbang shell keluar bukan-nol"}]
    seq = [notify_tick(ea, runner=alt_runner, state_path=_alt),
           notify_tick(eb, runner=alt_runner, state_path=_alt),
           notify_tick(ea, runner=alt_runner, state_path=_alt),
           notify_tick(eb, runner=alt_runner, state_path=_alt)]
    check("dua alarm berbeda yang bergantian tidak saling menimpa: keduanya tetap tercatat dan "
          "yang berulang-dua-kali ditahan, bukan yang pertama",
          len(_alt_calls) == 2 and seq[0].get("sent") and seq[1].get("sent")
          and seq[2].get("skipped") and seq[3].get("skipped"),
          json.dumps([{k: s.get(k) for k in ("sent", "skipped")} for s in seq]))
    check("alarm darurat berbunyi lagi dalam 30 menit, denyut ditahan 6 jam — dua niat, dua angka",
          repeat_window("urgent") == REPEAT_URGENT_WINDOW_S == 1800
          and repeat_window("progress") == REPEAT_WINDOW_S == 6 * 3600
          and repeat_window("unknown") == REPEAT_URGENT_WINDOW_S,
          f"urgent={repeat_window('urgent')} progress={repeat_window('progress')}")
    # Tick yang mencampur progress dan urgent tidak boleh ikut tertahan jendela progress.
    _mix = Path(tempfile.mkdtemp()) / "notify_last.json"
    _mix_calls = []
    mix_runner = lambda title, body, urgent=False: (_mix_calls.append(title),
                                                    {"ok": True, "rc": 0, "err": ""})[1]
    mixed = [{"action": "WORK_DISPATCHED", "item": 12},
             {"action": "SEND_FAILED", "why": "pane mati"}]
    m1 = notify_tick(mixed, runner=mix_runner, state_path=_mix, mute=set())
    m2 = notify_tick(mixed, runner=mix_runner, state_path=_mix, mute=set())
    check("tick yang berisi progress DAN urgent memakai jendela urgent, bukan jendela denyut",
          m1.get("sent") and m2.get("skipped") and m2.get("window") == REPEAT_URGENT_WINDOW_S
          and _mix_calls[0] == m1["title"], json.dumps(m2, default=str))
    check("urgent tidak bisa dibungkam dari config — satu baris muted:['urgent'] tidak boleh "
          "membungkam seluruh alarm",
          worth_notifying([{"action": "SEND_FAILED", "why": "x"}], mute={"urgent", "progress"})[0]
          is True and classify("SEND_FAILED") == "urgent")
    check("fallback bus menunjuk socket user yang nyata atau None",
          bus_address() is None or bus_address().startswith("unix:path="), str(bus_address()))
    # Bentuk argv diuji TANPA mengirim. Tiga percobaan pertama format hint `a{sv}` salah dan satu-
    # satunya bukti adalah rc=1 — yang hanya berarti apa pun kalau yang diperiksa adalah teks yang
    # berangkat, bukan hanya ada-tidaknya proses.
    argv = _dbus_argv("judul", "isi", True)
    # Posisi dibaca dari kata kunci yang dicari, bukan dari indeks tetap: menambah satu flag di
    # `_dbus_argv` tidak boleh membuat tes ini merah tanpa alasan, dan tidak boleh membuatnya hijau
    # padahal argumen bertukar tempat.
    i = argv.index("--method")
    # Balasan daemon diuraikan tanpa menyentuh desktop: id 0 adalah penolakan, dan rc 0 saja
    # tidak boleh cukup untuk menyebut sebuah banner "terkirim".
    def fake_run(reply, rc=0, stderr=""):
        class R:
            returncode, stdout, stderr_ = rc, reply, stderr
            def __init__(self):
                self.stderr = stderr
        return lambda *a, **k: R()
    import types
    _real = subprocess.run
    try:
        subprocess.run = fake_run("(uint32 0,)")
        refused = send("a", "b")
        subprocess.run = fake_run("(uint32 77,)")
        shown = send("a", "b")
        subprocess.run = fake_run("", rc=1, stderr="The connection is closed")
        broken = send("a", "b")
    finally:
        subprocess.run = _real
    check("id 0 dari daemon dibaca sebagai PENOLAKAN, bukan keberhasilan",
          refused["ok"] is False and refused["id"] == 0 and refused["err"], json.dumps(refused))
    check("id bukan-nol dibaca sebagai diterima, dengan id itu sendiri sebagai bukti",
          shown["ok"] is True and shown["id"] == 77, json.dumps(shown))
    check("dbus mati dilaporkan mati — bukan rc 0 yang disalahartikan",
          broken["ok"] is False and "closed" in (broken["err"] or ""), json.dumps(broken))
    check("argv dbus menyebut tujuan dan metode freedesktop yang benar",
          argv[0] == "gdbus" and argv[argv.index("--dest") + 1] == "org.freedesktop.Notifications"
          and argv[i + 1] == "org.freedesktop.Notifications.Notify", str(argv[:i + 2]))
    check("urgency critical memakai bentuk variant yang diterima gdbus",
          argv[-2] == "{'urgency': <byte 2>}" and argv[-1] == str(EXPIRE_MS), argv[-2])
    check("banner biasa tidak membawa hint yang bisa menempel dari panggilan sebelumnya",
          _dbus_argv("a", "b", False)[-2] == "{}", _dbus_argv("a", "b", False)[-2])
    check("posisi arg freedesktop benar: app, replaces_id, icon, summary, body, actions, hints, ms",
          argv[i + 2:] == [APP_ID, "0", "", "judul", "isi", "[]", "{'urgency': <byte 2>}"]
          + [str(EXPIRE_MS)], str(argv[i + 2:]))

    # --- yang diminta operator: bukan tiap tick, tapi tiap GERAKAN, tiap PERTANYAAN, tiap ERROR ---
    calls.clear()
    skipped = notify_tick([{"action": "OBSERVED", "claims": 0}], mode="SEMI", runner=runner)
    check("tick yang hanya melihat (OBSERVED) tidak menyentuh desktop",
          not calls and skipped["sent"] is False and skipped["skipped"] is True, str(skipped))
    moved = notify_tick([{"action": "WORK_DISPATCHED", "item": 88, "target": "qoder"}],
                        mode="SEMI", runner=runner, mute=set())
    check("tick yang mengirim perintah ke CLI berbanner (mute dikosongkan secara eksplisit, "
          "supaya tes tidak berubah nasib karena ada/tidaknya config lokal)",
          len(calls) == 1 and "item 88" in calls[0][1], str(calls))
    # dua sisi dari sakelar yang sama
    m_yes, m_why = worth_notifying([{"action": "WORK_DISPATCHED", "item": 88}], mute={"progress"})
    check("denyut yang dibungkam TIDAK menyentuh desktop, dan alasannya menyebut kelas yang dibungkam",
          m_yes is False and m_why.startswith("MUTED:progress"), m_why)
    check("tick yang diam karena tidak ada yang layak berbanner menyebut NOTHING_WORTHY, "
          "bukan berpura-pura ada yang dibungkam",
          worth_notifying([{"action": "CLOSED_NIGHT", "why": "x"}], mute={"progress"})[1]
          .startswith("NOTHING_WORTHY:")
          and worth_notifying([{"action": "OBSERVED"}], mute=set())[1].startswith("NOTHING_WORTHY:"),
          worth_notifying([{"action": "CLOSED_NIGHT", "why": "x"}], mute={"progress"})[1])
    check("pertanyaan manusia tetap berbunyi walau denyut dibungkam — yang boleh mati adalah noise, "
          "bukan jalur yang memanggil orang",
          worth_notifying([{"action": "ESCALATED", "id": 9}], mute={"progress"})[0] is True)
    check("`unknown` tidak bisa dibungkam meski diminta — kalau bisa, cara termudah membungkam "
          "loop selamanya hanyalah lupa mengklasifikasikan peristiwanya",
          worth_notifying([{"action": "PERISTIWA_BARU"}, ], mute={"progress", "unknown", "urgent"})[0]
          is True)
    check("peristiwa yang belum diklasifikasi TIDAK bisa lulus diam",
          classify("NAMA_BARU") == "unknown"
          and worth_notifying([{"action": "NAMA_BARU"}])[0] is True)
    off_word = summarize([{"action": "CLOSED_OFF", "why": "kode lama"}], mode="SEMI")
    check("kata yang sudah dibuang (CLOSED_OFF) tidak punya keranjang diam",
          classify("CLOSED_OFF") == "unknown"
          and worth_notifying([{"action": "CLOSED_OFF"}])[0] is True, classify("CLOSED_OFF"))
    # Lanjutan dari temuan verifier: `worth_notifying` sudah menyebut `unknown` layak berbanner,
    # tapi kalau `summarize` hanya mencari "urgent", peristiwa baru keluar tanpa ⚠ dan tanpa
    # urgency DBus — alarm yang berbunyi pelan tetap alarm yang tidak dibunyikan.
    check("peristiwa yang belum diklasifikasi ikut mengangkat suara (⚠ + urgency), bukan lulus "
          "sebagai banner biasa",
          off_word["urgent"] is True and off_word["title"].startswith("⚠"), json.dumps(off_word))
    one = notify_event("ESCALATED", "tiga opsi, tidak ada meter yang bisa dibaca", runner=runner)
    check("pertanyaan yang naik ke antrean manusia punya jalurnya sendiri dan menyebut alasannya",
          "ESCALATED" in one["title"] and "meter" in one["body"], json.dumps(one, default=str))

    # Dua keranjang untuk satu nama = dua laporan yang boleh berbeda (§12 aturan satu kosakata).
    overlap = (URGENT & PROGRESS) | (URGENT & NOTE) | (PROGRESS & NOTE)
    check("tidak ada peristiwa yang diklasifikasi dua kali", not overlap, str(sorted(overlap)))

    bad = [c for c in checks if not c[1]]
    for name, okval, detail in checks:
        print(f"[{'ok    ' if okval else 'GAGAL '}] {name}" + (f" — {detail}" if detail and not okval else ""))
    print(f"\nnotify selftest: {len(checks) - len(bad)}/{len(checks)} sesuai")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    print("pakai: python3 tools/notify.py selftest", file=sys.stderr)
    sys.exit(2)
