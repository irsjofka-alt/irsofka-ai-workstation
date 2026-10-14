#!/usr/bin/env python3
"""Worker lokal pembangunkan: satu tugas, mengetik satu baris tetap ketika ledger
membuktikan engine tujuan DONE atau sungguh diam.

Kenapa modul ini ada, dan kenapa ia BUKAN bagian drainer: tangga `level` menahan gerakan
yang dimulai mesin (timer mengambil antrean, CONTINUE diketik ke pane), dan `arm` hanya bisa
dinaikkan dari /dev/tty — itu pagar yang benar, dan modul ini TIDAK melewatinya. Yang dipakai
jalur tulis perintah operator (`queue_command` → `deliver_now`): sebuah perintah yang barusan
ditulis manusia sudah menjadi niat sebelum fungsi ini dipanggil, dan operator memerintahkan
wake ini langsung, dengan kata-kata dan teks perintah yang ia pilih sendiri, pada
2026-10-15 03:17.

Yang membuatnya tetap aman:

* **Tidak pernah menaikkan `level`, tidak pernah menyentuh `autopilot_state`.** Tidak ada satu
  baris kode di sini yang memanggil `arm`/`disarm`. Loop yang memberi dirinya sendiri hands
  tidak punya rem (§6).
* **Hanya bangun yang terbukti diam.** `heartbeat()` yang menjawab; `UNKNOWN` (pane tidak
  terbaca, ledger tidak terbaca, dua pembacaan tanpa jeda) BUKAN `AT_REST` dan tidak mengetik.
  Absence of evidence is not permission, di kedua sisi.
* **Angka datang dari ledger, bukan dari ingatan atau berkas state.** Anggaran dan cooldown
  dihitung dari baris wake itu sendiri, jadi tidak ada penghitung yang bisa busuk atau bisa
  dinaikkan sendiri.
* **Satu teks tetap.** Yang diketik adalah `WAKE_PHRASE`, tidak pernah hasil model, tidak pernah
  isi antrean. Tidak ada jalur dari sini ke sembarang perintah.
* **OFF adalah rem.** Mode dibaca dari database pada saat dipakai; OFF/SEMI menolak, dan
  `systemctl --user stop irsofka-awakener.timer` menghentikannya tanpa perlu mesin ini sama sekali.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db_state          # noqa: E402
import work_order as wo  # noqa: E402

WAKE_PHRASE = "RUN AUTO_PILOT.md"
COOLDOWN_S = 45        # operator menuntut bangun 10-20 detik sesudah report; 240s adalah
                       # rem yang saya pasang sendiri dan justru membuat mesin tampak mati
MAX_WAKE_1H = 20       # langit-langit keras; yang melampauinya harus dilaporkan, bukan dikerjakan
SUMMARY = "dibangunkan worker lokal"


def _epoch_expr(engine, column):
    """Ekspresi epoch per backend — `created_at_epoch` TIDAK ada di skema, dan menyebutnya
    akan membuat anggaran wake tampak dibaca padahal query-nya gagal."""
    return (f"EXTRACT(EPOCH FROM {column}::timestamptz)" if engine == "POSTGRESQL"
            else f"CAST(strftime('%s', {column}) AS INTEGER)")


def _wake_rows(conn, engine, since_epoch):
    ph = "%s" if engine == "POSTGRESQL" else "?"
    e = _epoch_expr(engine, "created_at")
    return wo.run(conn, engine,
                  f"SELECT id, status, {e} AS c FROM quest_tasks "
                  f"WHERE title={ph} AND {e} >= {ph} ORDER BY id DESC",
                  (WAKE_PHRASE, since_epoch)) or []


def decide_wake(conn, engine, target, now=None, beats=None, heartbeat=None):
    """Keputusan bangun TANPA menulis apa pun — supaya bisa diuji dan dibaca orang.

    Mengembalikan (boleh: bool, alasan: str). Semua penolakan dinamai, karena penolakan tanpa
    nama adalah keadaan yang tidak bisa dibedakan dari 'tidak ada yang melihat'.
    """
    now = now or wo.now()
    beats = beats if beats is not None else []
    ap = wo.autopilot(conn, engine) or {}
    mode = str(ap.get("mode") or "").upper()
    if mode != "ON":
        return False, f"MODE_{mode or 'UNKNOWN'} — wake hanya milik FULL AUTO"
    if int(ap.get("until_epoch") or 0) <= now:
        return False, "WINDOW_CLOSED — jendela ON lewat; tidak ada yang dibangun untuk tidak ada"
    live = wo.run(conn, engine,
                  f"SELECT id, status, COALESCE(lease_epoch,0) AS l FROM quest_tasks "
                  f"WHERE cli_engine='{target}' AND status IN ('WORKING','CLAIMED') "
                  f"AND COALESCE(lease_epoch,0) > {now}") or []
    if live:
        return False, f"ENGINE_BUSY — {len(live)} klaim hidup ({live[0]['id']}), tidak ditumpuki"
    rows = _wake_rows(conn, engine, now - 3600)
    if len(rows) >= MAX_WAKE_1H:
        return False, f"BUDGET_SPENT — {len(rows)} wake dalam 60 menit (cap {MAX_WAKE_1H})"
    if rows and now - int(rows[0]["c"]) < COOLDOWN_S:
        return False, f"COOLDOWN — wake terakhir {int(now - rows[0]['c'])}s lalu (< {COOLDOWN_S}s)"
    if any(r["status"] in ("PENDING", "WORKING", "CLAIMED") for r in rows):
        return False, "ALREADY_OPEN — satu baris wake belum selesai, tidak ditumpuk"

    # Yang menentukan bangun atau tidak adalah bukti, bukan ketiadaan baris.
    #
    # Dua keadaan yang harus dibedakan, dan versi pertama fungsi ini mencampur keduanya sampai
    # mati: `heartbeat()` hanya melaporkan klaim HIDUP, jadi engine yang baru saja selesai
    # (tidak memegang apa-apa) mengembalikan daftar kosong. Daftar kosong itu adalah DONE —
    # justru keadaan yang operator minta dibangunkan — tapi diperlakukan sebagai UNKNOWN, sehingga
    # worker menolak selamanya dan loop tetap tidak pernah menyala.
    #
    # Klaim hidup  -> wajib ada baris atas nama target dan state-nya AT_REST; WORKING menolak.
    # Tanpa klaim   -> DONE dibuktikan dari ledger (satu baris akhir milik target di jendela ini)
    #                 DAN pane yang terbukti tenang; keduanya UNKNOWN -> menolak.
    read = (heartbeat or wo.heartbeat)(conn, engine, sample_gap=2)
    mine = [r for r in (read or [])
            if target in {str(r.get("claimed_by") or ""), str(r.get("cli_engine") or "")}]
    if mine:
        states = [str(r.get("state") or "").upper() for r in mine]
        if any(s == "WORKING" for s in states):
            return False, f"WORKING — {states}"
        if not any(s == "AT_REST" for s in states):
            # UNKNOWN datang dari tiga keadaan berbeda (pane buta, ledger buta, pembacaan tanpa
            # jeda) dan tidak satunya adalah bukti diam.
            return False, f"NOT_PROVEN_IDLE — state={states}, dan UNKNOWN tidak pernah berarti boleh"
        return True, f"AT_REST — {mine[0].get('why') or 'bukti diam'}; resume_budget=" \
                     f"{mine[0].get('resume_budget_left')}"

    last = wo.run(conn, engine,
                  f"SELECT id, status, completed_at FROM quest_tasks WHERE cli_engine='{target}' "
                  f"AND status IN ('COMPLETED','FAILED','PARKED') ORDER BY id DESC LIMIT 1") or []
    if not last:
        return False, f"NO_EVIDENCE — tidak ada klaim hidup dan tidak ada baris akhir atas nama " \
                      f"{target!r}; diam tidak dibuktikan, hanya belum terlihat"
    import drainer as dr
    quiet = dr.pane_is_quiet(target)
    if not quiet.get("quiet"):
        return False, f"PANE_NOT_QUIET — {quiet.get('why')}"
    return True, f"DONE — item {int(last[0]['id'])} {last[0]['status']}, pane terbukti tenang"


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    target = (a[0] if a else "qoder")
    conn, engine = db_state.get_db_connection()
    try:
        ok, why = decide_wake(conn, engine, target)
        out = {"target": target, "wake": ok, "why": why}
        if not ok:
            print(json.dumps(out, ensure_ascii=False))
            return 0
        # TIDAK ada gerbang penerimaan pada baris wake, dan itu bukan kelalaian. `hand_new_work`
        # mengembalikan lebih dulu item yang `check_command`-nya sudah keluar 0 di baseline
        # ("yang gerbangnya sudah lolos tidak pernah perlu dikerjakan") — jadi gerbang berupa
        # `test -f AUTO_PILOT.md`, yang lolos SEBELUM satu huruf pun diketik, membuat setiap wake
        # berhenti tepat di situ. Terukur pada dua tick 03:25 dan 03:28: ledger mencatat
        # GATE_PASSED lalu diam — tanpa WORK_DISPATCHED, tanpa penolakan bernama — dan pane tidak
        # pernah menerima apa pun. Bukti pembandingnya #246, baris ber-gerbang kosong: ia
        # terkirim. Yang menahan wake adalah gerbang yang saya pilih sendiri.
        # Arahkan barisnya ke proyek tempat tab itu benar-benar duduk. `REPO` adalah keputusan
        # yang salah: pengukuran hari ini tab qoder berdiri di /home/irsofka/Documents/
        # ai-workstation, dan mengirim baris ber-ws_path lain ditolak PANE_WRONG_DIR oleh
        # hand_new_work — jadi wake yang tampaknya terkirim tidak pernah sampai ke sesiapa.
        # Temuan audit `gemini-byok` atas diff ini (verdict PERLU_KOREKSI, 04:3x): versi pertama
        # menulis `cv.tab_project(target) or str(wo.REPO)` — fail-open ke direktori TEBAKAN ketika
        # proyek tab tidak terbaca. §6 melarang menebak tujuan: yang benar adalah menolak dengan
        # nama dan tidak menulis baris sama sekali. Alasannya bukan estetika: baris ber-ws_path
        # karangan akan diantar ke pane yang duduk di proyek lain, dan engine mengerjakan satu
        # tree sambil melaporkan tree lain.
        try:
            import cross_verify as cv
            where = (cv.tab_project(target) or "").strip()
        except Exception as exc:  # noqa: BLE001 — tidak terbaca BUKAN berarti REPO
            where = ""
            why_read = f"({str(exc)[:60]})"
        else:
            why_read = ""
        if not where:
            out.update({"queued": False, "why": f"PANE_DIR_UNKNOWN {why_read}".strip(),
                        "ws_path": None, "gate": ""})
            print(json.dumps(out, ensure_ascii=False))
            return 1        # keluar bukan-nol: jam berikutnya mencoba lagi, bukan menulis diam-diam
        wrote, msg, item_id = wo.queue_command(conn, engine, WAKE_PHRASE, target,
                                               gate="", ws_path=where, who="operator")
        out.update({"queued": wrote, "item": item_id, "gate": "", "ws_path": where,
                    "queue_why": msg[:200]})
        print(json.dumps(out, ensure_ascii=False))
        return 0 if wrote else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
