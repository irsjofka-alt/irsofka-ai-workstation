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
    # Temuan audit independen 2026-10-15 10:3x (verdict PERLU_KOREKSI): blok ALREADY_OPEN yang saya
    # tambahkan memakai {ph}, padahal `ph` hanya didefinisikan di `_wake_rows` dan `open_wake`, tidak
    # di sini. Jalur SEMI keluar lebih dulu sehingga tidak pernah menabraknya — artinya worker
    # melempar NameError TEPAT pada keadaan ia dibutuhkan: FULL AUTO. Tidak ada selftest sisi-diam
    # yang bisa menemukan ini; hanya tes yang memaksa mode ON bisa.
    ph = "%s" if engine == "POSTGRESQL" else "?"
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
    # ALREADY_OPEN TIDAK boleh memakai jendela yang sama dengan anggaran. Baris wake yang gagal
    # terkirim (CLAIM_REFUSED karena pemutus item, PANE_NOT_QUIET) tetap PENDING selamanya, dan
    # `_wake_rows` hanya melihat 60 menit ke belakang — jadi begitu baris itu berumur satu jam,
    # penjaganya buta dan tick berikutnya melahirkan baris baru. Terukur: 272, 273, 274, 275, 276
    # semuanya PENDING dengan alasan yang sama, tercipta berjeda jam. Yang dihitung di sini adalah
    # baris TERBUKA berapa pun umurnya; yang berjendela tetap anggaran dan cooldown.
    still_open = wo.run(conn, engine,
                        f"SELECT id FROM quest_tasks WHERE title={ph} "
                        f"AND status IN ('PENDING','CLAIMED','WORKING') ORDER BY id LIMIT 1",
                        (WAKE_PHRASE,)) or []
    if still_open:
        return False, f"ALREADY_OPEN — item {int(still_open[0]['id'])} masih terbuka"
    if any(r["status"] in ("PENDING", "WORKING", "CLAIMED") for r in rows):
        return False, "ALREADY_OPEN — satu baris wake belum selesai, tidak ditumpuk"
    # NO_WORK — rem yang diminta keputusan 62 (rung `resolver-alt`, engine `gemini-verify`, model
    # `gemini-3.8-flash-high`; jawaban dan yang menjawabnya tersimpan di baris decisions). Yang
    # dipisahkan di sini adalah dua hal yang selama ini memakai satu kata: pane yang DIAM dan antrean
    # yang KOSONG. Terukur 2026-10-16: 5 wake (304, 306, 308, 309, 310) pada dua tick berturut dengan
    # antrean nol, dan tidak satu pun pemutus bisa melihatnya — NIGHT_MAX_FAILURES menghitung
    # FAILED/PARKED sedangkan putaran kosong SELESAI dengan sukses, dan MAX_WAKE_1H /
    # min(item_budget, NIGHT_MAX_ITEMS) mengukur LAJU dan JUMLAH, bukan isi antrean.
    #
    # Pertanyaan yang diajukan adalah pertanyaan yang akan diajukan tick itu sendiri, lewat
    # `work_order.next_item` yang sama (`drainer.next_ready` hanya meminjamnya) — bukan `ready_items`,
    # karena daftar yang masih menyebut baris yang sedang dipegang engine lain adalah definisi kedua
    # dari "apa yang bisa dikerjakan", dan §12 melarang dua definisi beredar.
    #
    # Nol harus TERBACA. Ledger yang tidak terbaca BUKAN antrean kosong: menolak bangun atas dasar
    # ketiadaan bukti akan membungkam loop yang justru diminta operator supaya tidak diam. Maka UNKNOWN
    # melanjutkan ke gerbang bukti pane, dan kata itu ditulis ke alasan yang keluar — supaya "dibangunkan
    # padahal antrean tidak terbaca" bisa dibedakan dari "dibangunkan karena antrean terbaca kosong".
    try:
        _nxt = wo.next_item(conn, engine, respect_due=True)
        _qnote = ""
    except Exception as exc:  # noqa: BLE001 — unreadable, bukan kosong
        _nxt, _qnote = None, f" (antrean TIDAK TERBACA {type(exc).__name__}: {str(exc)[:50]})"
    if _nxt is None and not _qnote:
        return False, ("NO_WORK — antrean terbaca dan tidak ada satu pun item yang bisa diambil "
                       f"(next_item=None, termasuk perintah operator yang menunggu); cap laju "
                       f"{MAX_WAKE_1H}/jam tidak melihat keadaan ini karena putaran kosong selesai "
                       "dengan sukses, bukan dengan kegagalan")

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
                     f"{mine[0].get('resume_budget_left')}{_qnote}"

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
    return True, (f"DONE — item {int(last[0]['id'])} {last[0]['status']}, pane terbukti tenang{_qnote}")


def open_wake(conn, engine, now=None):
    """Baris wake yang masih terbuka DAN tidak sedang di-cooldown.

    `due_epoch` ikut disaring karena temuan audit saya sendiri pada jalur retry ini (pesan
    ai_message id 8, dikirim ke Antigravity): tanpa filter itu satu tick setiap 20 detik akan
    memanggil `deliver_now` pada baris yang baru saja ditahan cooldown, selamanya — dan tidak ada
    satu pun anggaran yang menghitungnya, karena MAX_WAKE_1H menghitung BARIS wake yang ditulis,
    bukan percobaan kirim ulang.
    """
    ph = "%s" if engine == "POSTGRESQL" else "?"
    return wo.run(conn, engine,
                  f"SELECT id, status FROM quest_tasks WHERE title={ph} "
                  f"AND status IN ('PENDING','CLAIMED','WORKING') "
                  f"AND COALESCE(due_epoch, 0) <= {ph} ORDER BY id LIMIT 1",
                  (WAKE_PHRASE, now or wo.now())) or []


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    target = (a[0] if a else "qoder")
    if target == "selftest":
        return selftest()
    conn, engine = db_state.get_db_connection()
    try:
        ok, why = decide_wake(conn, engine, target)
        out = {"target": target, "wake": ok, "why": why}

        # Retry hanya jika SEMUA pagar memutuskan mesin ini memang boleh dibangunkan. Versi
        # pertama meletakkan retry SEBELUM gerbang-gerbang itu, jadi MODE_OFF, ENGINE_BUSY,
        # WINDOW_CLOSED dan BUDGET_SPENT tidak menahan apa pun, dan satu baris wake yang tidak
        # bisa terkirim akan dicoba 180 kali per jam dalam diam — MAX_WAKE_1H tidak menghitungnya
        # karena ia menghitung baris yang ditulis, bukan percobaan kirim. Yang diizinkan di sini
        # hanya satu keadaan: ALREADY_OPEN — wake memang belum mendarat di pane, dan mengirim
        # ulang baris yang sama adalah pekerjaannya, bukan membuat baris kedua di atasnya.
        if not ok and (why or "").startswith("ALREADY_OPEN"):
            stuck = open_wake(conn, engine)
            if not stuck:
                print(json.dumps(dict(out, retried=False,
                                      note="terbuka tapi di-cooldown — belum waktunya"),
                                 ensure_ascii=False))
                return 0
            import drainer as dr
            res = dr.deliver_now(conn, engine, int(stuck[0]["id"]), mode="ON")
            sent = bool(isinstance(res, dict) and res.get("sent"))
            ev = [e["action"] for e in (res.get("events") or [])] if isinstance(res, dict) else []
            out.update({"retried": int(stuck[0]["id"]), "sent": sent, "actions": ev,
                        "refused": (res.get("refused") if isinstance(res, dict) else None)})
            print(json.dumps(out, ensure_ascii=False))
            # Keluar bukan-nol kalau belum terkirim: journal harus bisa membedakan 'bangun'
            # dari 'gagal terus', dan operator yang membaca systemctl harus menemukannya.
            return 0 if sent else 1

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
                                               gate="", ws_path=where, who="awakener atas perintah operator 03:17")
        out.update({"queued": wrote, "item": item_id, "gate": "", "ws_path": where,
                    "queue_why": msg[:200]})
        print(json.dumps(out, ensure_ascii=False))
        return 0 if wrote else 1
    finally:
        conn.close()


def selftest():
    """Gerbang bangun diuji di SQLite sementara: nol ketikan, nol pane sungguhan, nol tulisan produksi.

    Kenapa modul ini punya suite sendiri: `decide_wake` TIDAK pernah diuji sama sekali, dan itu bukan
    kelalaian kecil — audit independen 2026-10-15 menemukan `NameError` di jalur ON yang tidak bisa
    terlihat oleh tes sisi-diam mana pun ("hanya tes yang memaksa mode ON bisa"). Suite di bawah memaksa
    ON, dan mengunci gerbang NO_WORK yang baru: bedanya pane DIAM dengan antrean KOSONG.
    """
    import inspect
    import re as _re
    import sqlite3
    import tempfile
    from pathlib import Path as _P

    results = []

    def check(name, cond, detail=""):
        results.append((bool(cond), name, detail))
        print(f"[{'ok    ' if cond else 'FAIL  '}] {name}" + ("" if cond else f"  <- {detail}"))

    tmp = _P(tempfile.mkdtemp(prefix="awakener_st_")) / "t.db"
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row
    wo.ensure_schema(conn, "SQLITE")
    engine = "SQLITE"
    target = "qoder"
    HB_REST = [{"claimed_by": target, "cli_engine": target, "state": "AT_REST",
                "why": "dua pembacaan pane identik", "resume_budget_left": 2}]

    def dw(beat=None):
        """`decide_wake` tanpa possibility membunuh suite.

         Nama yang merah lebih berguna daripada traceback: satu lemparan di cek pertama membuat empat
        cek berikutnya tidak pernah berjalan, dan suite seperti itu hanya bisa melaporkan "meledak",
        bukan gerbang mana. Ini persis cara NameError 2026-10-15 bertahan hidup — tidak ada yang
        pernah menjalankan jalur ON sampai selesai.
        """
        try:
            return decide_wake(conn, engine, target,
                               heartbeat=beat or (lambda *a, **k: HB_REST))
        except Exception as exc:  # noqa: BLE001
            return False, f"RAISED {type(exc).__name__}: {str(exc)[:90]}"

    def open_window(conn, hours=4, budget=12):
        conn.execute("UPDATE autopilot_state SET mode='ON', level='observe', on_epoch=?, "
                     "until_epoch=?, item_budget=?, items_done=0 WHERE id=1",
                     (wo.now(), wo.now() + int(hours * 3600), budget))
        conn.commit()

    def add(title, status="PENDING", origin="roadmap", deps=None, engine_col=None, due=0):
        conn.execute("INSERT INTO quest_tasks (title, status, origin, phase, depends_on, cli_engine, "
                     "due_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (title, status, origin, "build", deps, engine_col, due))
        conn.commit()

    def wipe():
        conn.execute("DELETE FROM quest_tasks")
        conn.commit()

    try:
        open_window(conn)
        # 1 — inti perubahan: antrean terbaca kosong = jangan bangun, dengan nama yang bisa dicari.
        wipe()
        ok, why = dw()
        check("antrean kosong Terbaca -> NO_WORK (bukan bangun, bukan diam tanpa nama)",
              ok is False and str(why).startswith("NO_WORK"), f"{ok} {why}")

        # 2 — kebalikannya, karena gerbang yang hanya dites merah bukan gerbang.
        add("satu item roadmap yang bisa diambil")
        ok, why = dw()
        check("satu item yang bisa diambil -> bangun (NO_WORK tidak menyapa)",
              ok is True and "AT_REST" in str(why), f"{ok} {why}")
        wipe()

        # 3 — perintah operator adalah niat manusia, bukan beban antrean: ia boleh bangun.
        add("perintah operator yang belum diambil", origin="operator")
        ok, why = dw()
        check("perintah operator PENDING -> bangun (jalur §6 tidak ikut dibungkam rem ini)",
              ok is True, f"{ok} {why}")
        wipe()

        # 4 — UNKNOWN bukan nol. `depends_on` rusak membuat penanya melempar: itu BUKAN bukti antrean
        # kosong, jadi rem tidak boleh menolaknya; yang benar adalah bangun dengan kata terbaca di alasan.
        add("item ber-dependensi rusak", deps="bukan-angka")
        ok, why = dw()
        check("ledger yang TIDAK TERBACA tidak dihitung sebagai antrean kosong — bangun dengan "
              "catatan, dan catatan itu ada di teks yang keluar",
              ok is True and "TIDAK TERBACA" in str(why), f"{ok} {why}")
        wipe()

        # 5 — gerbang lama tidak boleh kehilangan namanya karena gerbang baru disisipkan di tengah.
        open_window(conn)
        conn.execute("UPDATE autopilot_state SET mode='SEMI' WHERE id=1")
        conn.commit()
        ok, why = dw()
        check("SEMI tetap ditolak dengan nama MODE_SEMI (rem baru tidak mengambil namanya)",
              ok is False and str(why).startswith("MODE_SEMI"), f"{ok} {why}")
        conn.execute("UPDATE autopilot_state SET mode='ON', until_epoch=? WHERE id=1", (wo.now() - 10,))
        conn.commit()
        ok, why = dw()
        check("jendela lewat tetap WINDOW_CLOSED",
              ok is False and str(why).startswith("WINDOW_CLOSED"), f"{ok} {why}")

        # 6 — NameError yang ditemukan audit 2026-10-15: jalur ON dengan bariswake terbuka TUA
        # (di luar jendela 60 menit anggaran) harus keluar dengan nama, bukan melempar.
        open_window(conn)
        wipe()
        old = wo.now() - 7200
        conn.execute("INSERT INTO quest_tasks (title, status, origin, phase, created_at) "
                     "VALUES (?, 'PENDING', 'operator', 'build', to_timestamp(?))",
                     (WAKE_PHRASE, old)) if engine == "POSTGRESQL" else conn.execute(
            "INSERT INTO quest_tasks (title, status, origin, phase, created_at) "
            "VALUES (?, 'PENDING', 'operator', 'build', datetime(?, 'unixepoch'))",
            (WAKE_PHRASE, old))
        conn.commit()
        ok, why = dw()
        raised = why if str(why).startswith("RAISED") else ""
        check("baris wake tua (di luar jendela anggaran) -> ALREADY_OPEN, bukan NameError di jalur ON",
              ok is False and str(why).startswith("ALREADY_OPEN"), f"{ok} {why} {raised}")
        wipe()

        # 7 — satu definisi "apa berikutnya": gerbang bertanya ke next_item, bukan menyusun daftar sendiri.
        src = _re.sub(r"^\s*#.*$", "", inspect.getsource(decide_wake), flags=_re.M)
        check("decide_wake meminjam satu definisi antrean (wo.next_item), tidak menyusun definisi "
              "kedua (wo.ready_items)",
              "wo.next_item(" in src and "wo.ready_items(" not in src,
              f"next_item={src.count('wo.next_item(')} ready_items={src.count('wo.ready_items(')}")

        # 8 — tangan: modul ini tidak boleh mengetik sendiri; ia hanya menulis baris perintah.
        body = _re.sub(r"^\s*#.*$", "", Path(__file__).read_text(encoding="utf-8").partition(
            "def selftest(")[0], flags=_re.M)
        # Semua kata ini memang nol di badan modul hari ini, jadi tidak ada pengecualian yang boleh
        # memakluminya: yang berhak mengetik hanya drainer, dan modul yang bangun-tanpa-tangan tidak
        # boleh diam-diam tumbuh tangan lewat `subprocess` satu baris di bawah gerbang lain.
        check("sisi timer tidak punya tangan kedua: nol tmux, nol send-keys, nol subprocess di "
              "seluruh badan modul (komentar dibuang lebih dulu)",
              "tmux" not in body and "send-keys" not in body and "subprocess" not in body,
              f"tmux={'tmux' in body} send_keys={'send-keys' in body} subprocess={'subprocess' in body}")
    finally:
        conn.close()

    total = len(results)
    bad = [n for ok, n, _d in results if not ok]
    print(f"\nawakener selftest: {total - len(bad)}/{total} sesuai")
    for n in bad:
        print("  GAGAL:", n)
    return 0 if not bad else 1



if __name__ == "__main__":
    sys.exit(main())
