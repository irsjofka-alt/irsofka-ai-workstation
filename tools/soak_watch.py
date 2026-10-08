"""Watchdog soak: membuktikan timer autopilot masih benar-benar menyala.

F10.7 mengawasi ENGINE (apakah sebuah sesi berhenti), dan F10.3 mengawasi PEKERJAAN. Tidak ada satu
pun yang mengawasi detaknya sendiri. Padahal kegagalan yang operator takutkan justru di sana:
"sekali saja auto mati maka Komputer jadi IDLE" — timer yang berhenti menyala tidak menulis satu
baris pun, tidak menaikkan satu kegagalan pun, dan tidak mengubah satu keadaan pun, jadi dari luar
ia identik dengan mesin yang sedang tidak ada kerja.

Ini satu-satunya pembaca kesunyian itu. Ia membandingkan waktu peristiwa terakhir di log malam
dengan batas yang seharusnya, dan kalau lewat, ia mengangkat suara lewat `notify` — bukan mengirim
tick baru diam-diam. Menyalakan ulang unit boleh sekali per kejadian dan selalu dicatat; membuka
jendela atau menaikkan tangan TIDAK PERNAH dilakukan di sini, karena itu dua hal yang hanya boleh
dilakukan orang dari terminalnya sendiri (§6).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import notify  # noqa: E402

LOG_DIR = Path.home() / ".ai-station" / "logs"
TICK_GLOB = "drainer-*.jsonl"
TICK_UNIT = "irsofka-autopilot.timer"
TICK_PERIOD = 300          # detik; OnUnitActiveSec di unit
GRACE = 240                # detik; RandomizedDelaySec + waktu jalan sebuah tick
POLL = 60                  # detik; seberapa sering pengawas ini melihat
RESTART_COOLDOWN = 1800    # detik; satu kesempatan menyalakan ulang per setengah jam


def last_tick_epoch(day=None) -> int | None:
    """Epoch peristiwa tick terakhir dari log malam, atau None kalau tidak ada yang terbaca.

    None BUKAN nol. Sebuah berkas yang hilang, hari yang baru berganti, atau log yang tidak bisa
    dibaca semuanya akan terbaca sebagai "sudah lama tidak ada tick" kalau nol dipakai sebagai
    jawaban, dan itu membuat pengawas ini menyalakan alarm palsu tepat saat orang paling tidak
    siap membaca alarm.
    """
    names = sorted(LOG_DIR.glob(TICK_GLOB))
    if not names:
        return None
    newest = names[-1]
    if day and newest.name != f"drainer-{day}.jsonl":
        newest = LOG_DIR / f"drainer-{day}.jsonl"
        if not newest.exists():
            return None
    last = None
    try:
        with newest.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("ts")
                if isinstance(ts, (int, float)) and (last is None or int(ts) > last):
                    last = int(ts)
    except OSError:
        return None
    return last


def timer_state() -> bool | None:
    """True aktif, False tidak, None tidak terukur."""
    try:
        p = subprocess.run(["systemctl", "--user", "is-active", TICK_UNIT],
                           capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 — tidak terukur bukan berarti mati
        return None
    out = (p.stdout or "").strip()
    if out == "active":
        return True
    if out in ("inactive", "failed"):
        return False
    return None


def decide(now: int, last: int | None, active: bool | None,
           restart_at: int | None = None) -> dict:
    """Keputusan murni: apa yang harus dilakukan pengawas pada satu pengamatan.

    Dipisah dari eksekusi supaya batas-batasnya bisa diuji tanpa menunggu lima menit sungguhan —
    dan supaya seseorang bisa membaca aturannya tanpa membaca sistem berkas.
    """
    if last is None:
        return {"action": "WATCH", "why": "log tick belum terbaca — UNKNOWN, belum tentu mati"}
    age = now - last
    limit = TICK_PERIOD + GRACE
    if active is False:
        return {"action": "RESTART", "why": f"unit {TICK_UNIT} terbaca tidak aktif; tick terakhir "
                                            f"{age}s lalu", "age": age}
    if age > limit:
        return {"action": "RESTART" if (restart_at is None or now - restart_at > RESTART_COOLDOWN)
                else "ALERT",
                "why": f"tidak ada tick selama {age}s (batas {limit}s)", "age": age}
    return {"action": "OK", "why": f"tick terakhir {age}s lalu, unit aktif", "age": age}


def restart_timer() -> dict:
    """Nyalakan ulang detak. Tidak pernah membuka jendela dan tidak pernah menaikkan tangan."""
    try:
        p = subprocess.run(["systemctl", "--user", "restart", TICK_UNIT],
                           capture_output=True, text=True, timeout=20)
        return {"ok": p.returncode == 0, "rc": p.returncode,
                "err": ((p.stderr or p.stdout or "").strip()[:200] or None)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rc": None, "err": f"{type(exc).__name__}: {exc}"}


def journal(path: Path, row: dict) -> None:
    row = {"ts": int(time.time()), **row}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def watch(until: int, sleeper=time.sleep, emit=None) -> dict:
    """Pantau sampai `until`. `sleeper` dan `emit` disuntik supaya bisa diuji tanpa menunggu."""
    log = emit or (lambda row: journal(LOG_DIR / f"soak-{time.strftime('%Y-%m-%d')}.jsonl", row))
    restart_at = None
    seen = {"ok": 0, "restart": 0, "alert": 0}
    while int(time.time()) < until:
        now = int(time.time())
        last = last_tick_epoch()
        active = timer_state()
        d = decide(now, last, active, restart_at)
        if d["action"] == "OK":
            seen["ok"] += 1
        elif d["action"] == "RESTART":
            r = restart_timer()
            restart_at = now
            seen["restart"] += 1
            log({"action": "SOAK_RESTARTED", "why": d["why"], **r})
            notify.notify_event("SOAK_RESTARTED", f"{d['why']} → unit dinyalakan ulang: "
                                f"{'ok' if r['ok'] else r.get('err')}", urgent=True)
        elif d["action"] == "ALERT":
            seen["alert"] += 1
            log({"action": "SOAK_SILENT", "why": d["why"], "already_restarted_at": restart_at})
            notify.notify_event("SOAK_SILENT", f"detak autopilot diam: {d['why']}", urgent=True)
        else:
            seen["ok"] += 1
            log({"action": "SOAK_WATCHING", "why": d["why"]})
        sleeper(POLL)
    # Penutup yang mengirim kabar sendiri. Operator meminta "nanti kalau selesai tolong chat aku di
    # notif desktop", dan penjadwal yang bergantung pada sesi seorang AI bisa mati bersama sesi itu —
    # sebuah janji yang hanya ditepati selama proses lain tetap hidup bukan janji. Jadi yang
    # membangunkan orang adalah proses yang sama yang menjaganya tidur: watchdog ini.
    last = last_tick_epoch()
    log({"action": "SOAK_ENDED", **seen, "last_tick": last})
    notify.notify_event(
        "SOAK_ENDED",
        f"soak selesai · {seen['ok']} pengamatan aman · {seen['restart']} kali detak dinyalakan "
        f"ulang · {seen['alert']} kali dibunyikan tanpa ulang · tick terakhir "
        + (f"{int(time.time()) - last}s lalu" if last else "tidak terbaca"))
    return seen


def selftest() -> int:
    """Batas-batas keputusan diuji sebagai fungsi murni — tanpa systemd, tanpa menunggu."""
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
    check("tanpa log = UNKNOWN, bukan 'mati'",
          decide(1000, None, True)["action"] == "WATCH")
    check("tick 60 detik lalu dengan unit aktif = aman",
          decide(1000, 940, True)["action"] == "OK")
    check("unit terbaca mati mengalahkan segalanya, dan langsung ditangani",
          decide(1000, 990, False)["action"] == "RESTART")
    check("unit tidak terukur TIDAK dibaca sebagai mati",
          decide(1000, 990, None)["action"] == "OK")
    check("kesenyapan melewati batas memicu tindakan",
          decide(2000, 1000, True)["action"] == "RESTART")
    check("satu kesempatan ulang per cooldown; yang kedua hanya berteriak",
          decide(2000, 1000, True, restart_at=1900)["action"] == "ALERT")
    check("setelah cooldown habis, kesempatan ulang terbuka lagi",
          decide(2000, 1000, True, restart_at=100)["action"] == "RESTART")
    limit = TICK_PERIOD + GRACE
    check("batasnya adalah period + grace, bukan angka bulat yang enak diingat",
          decide(1000 + limit - 1, 1000, True)["action"] == "OK"
          and decide(1000 + limit + 1, 1000, True)["action"] != "OK", f"limit={limit}")
    bad = [c for c in checks if not c[1]]
    for name, okval, detail in checks:
        print(f"[{'ok    ' if okval else 'GAGAL '}] {name}" + (f" — {detail}" if detail and not okval else ""))
    print(f"\nsoak_watch selftest: {len(checks) - len(bad)}/{len(checks)} sesuai")
    return 1 if bad else 0


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="watchdog detak autopilot untuk sebuah soak")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--minutes", type=int, required=True)
    sub.add_parser("selftest")
    sub.add_parser("status")
    a = p.parse_args(argv)
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "status":
        now = int(time.time())
        last, active = last_tick_epoch(), timer_state()
        print(json.dumps({"now": now, "last_tick": last, "timer_active": active,
                          **decide(now, last, active)}, ensure_ascii=False))
        return 0
    seen = watch(int(time.time()) + a.minutes * 60)
    # Keluar 0 walau ada kejadian: pengawas yang exit non-zero akan dibaca systemd/shell sebagai
    # "pengawasanya yang rusak", padahal yang ingin dikatakan adalah "aku melihat sesuatu". Hasil
    # loop dicetak supaya bisa dibaca ulang dari log, bukan disandikan di kode keluar.
    print(json.dumps(seen, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
