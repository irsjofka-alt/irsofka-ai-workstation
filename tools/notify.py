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

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

APP_ID = "AI Workstation Core"
SEND_TIMEOUT = 2.0
EXPIRE_MS = 8000
MAX_ACTIONS_SHOWN = 4

# Peristiwa yang berarti "loop tidak bergerak, atau bergerak tapi tidak seperti yang dimaksud".
# Ini yang harus terlihat dari kejauhan, karena kegagalan yang operator takutkan bukan item sulit
# — melainkan auto yang berhenti dan komputer yang lalu diam sampai pagi.
URGENT = {
    "CLOSED_NIGHT", "CLOSED_UNKNOWN", "HELD_BY_LEVEL", "HELD_BY_MODE",
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
}
# Peristiwa yang berarti "mesin bekerja": boleh pendek, tidak boleh berteriak.
PROGRESS = {"WORK_DISPATCHED", "GATE_PASSED", "CONTINUE_SENT", "ANSWER_SENT", "CLAIMED",
            # Hook yang menyuntik item ke CLI yang sedang diam ADALAH "mengirim perintah ke CLI"
            # — jalur yang persis diminta operator untuk selalu kelihatan.
            "SEMI_INJECTED"}
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
        "MODE_OFF"}
# `CLOSED_OFF` pernah ada di daftar ini dan dibuang dengan alasan "OFF bukan lagi sebuah mode".
# Alasan itu tidak berlaku sejak 2026-10-09: OFF kembali menjadi state yang disimpan, dan nama
# kejadiannya `MODE_OFF` (lihat keranjang NOTE di atas). Yang tetap benar dari komentar lama:
# nama keadaan yang tidak di-emit siapa pun tidak boleh punya perilaku diam — `classify` membacanya
# `unknown`, dan `unknown` selalu berbanner. Itulah sebabnya tes klasifikasi memeriksa SETIAP nama
# yang bisa di-emit, bukan hanya yang terlintas saat seseorang menulis daftarnya.


def worth_notifying(events) -> tuple[bool, str]:
    """Hanya tick yang BERGERAK, BERTANYA, atau GAGAL yang boleh menyentuh desktop.

    Aturan ini bukan kenyamanan: operator menyetujui notifikasi per kejadian justru supaya
    notifikasi tidak menjadi noise yang dia matikan. Tick yang hanya melihat (OBSERVED) adalah
    keadaan normal mesin yang sehat dan harus tetap berada di log malam saja.
    """
    acts = [str((e or {}).get("action") or "?") for e in (events or []) if isinstance(e, dict)]
    for a in acts:
        if classify(a) == "progress":
            return True, a
    for a in acts:
        if classify(a) in ("urgent", "unknown"):
            return True, a
    return False, ",".join(acts) or "tanpa peristiwa"


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


def notify_tick(events, mode=None, level=None, runner=None) -> dict:
    """Jalur yang dipakai tick: hanya kejadian yang layak yang menyentuh desktop."""
    want, reason = worth_notifying(events)
    if not want:
        return {"sent": False, "skipped": True, "reason": reason, "ok": True}
    return notify_events(events, mode=mode, level=level, runner=runner)


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
          classify("WORK_DISPATCHED") == "progress" and classify("CLOSED_NIGHT") == "urgent")
    s = summarize([{"action": "CLOSED_NIGHT", "why": "jendela ON lewat"}], mode="ON", level="resume")
    check("gerbang tertutup mengangkat suara dan menyebut alasannya",
          s["urgent"] and "⚠" in s["title"] and "CLOSED_NIGHT" in s["title"]
          and "jendela ON lewat" in s["body"], json.dumps(s))
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
    out = notify_tick([{"action": "CLOSED_NIGHT", "why": "jendela ON lewat"}], mode="ON",
                      runner=runner)
    check("satu kejadian layak = satu banner", len(calls) == 1, str(calls))
    check("banner yang gagal kirim dilaporkan sebagai gagal, bukan sebagai tick yang berhasil",
          out["sent"] is False and out["ok"] is False and "tidak ditemukan" in (out.get("err") or ""),
          json.dumps(out, default=str))
    check("kegagalan kirim tidak mengubah isi rangkuman", out["title"] == calls[0][0])
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
                        mode="SEMI", runner=runner)
    check("tick yang mengirim perintah ke CLI berbanner",
          len(calls) == 1 and "item 88" in calls[0][1], str(calls))
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
