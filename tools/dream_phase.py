#!/usr/bin/env python3
"""F10.5 dream phase — konsolidasi memori yang hanya boleh berjalan ketika mesin sungguh menganggur.

Satu putaran mengukur dulu (idle dari ledger, mode, tingkat tangan, working tree) sebelum menulis
apa pun, dan setiap berkas yang tersentuh wajib disebut oleh baris digest yang dikeluarkannya.

Tangga tangan berlaku penuh di sini dan `--force` TIDAK menaikkannya: force hanya melewatkan gerbang
idle, untuk orang yang berdiri di depan terminal dan ingin melihat satu putaran sekarang. Menulis
berkas dan menulis peringkat butuh `dispatch`, dan `dispatch` hanya bisa dinaikkan dari /dev/tty.
"""

import io
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import contextlib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import work_order as wo  # noqa: E402
import call_workers as cw  # noqa: E402

STATION = Path.home() / ".ai-station"
BRAIN = STATION / "brain"
PROJECTS = BRAIN / "memory" / "projects"
LEARNED = BRAIN / "skills" / "learned"
ARCH_MAP = STATION / "bin" / "arch_map.sh"

IDLE_MINUTES = 30          # angka roadmap F10.5, bukan tebakan
PRE_ANSWER_REPEATS = 4     # satu sebelum THRESHOLD milik incident_recorder (5): menjawab sambil tidur
RECENCY_HALF_DAYS = 7.0    # paruh waktu bobot: seminggu lalu bernilai setengah dari sekarang
RETRIEVAL_SLOTS = 6        # LIMIT pada session_ingestor.handoff() — hanya sebanyak ini yang dibaca mesin
RETRIEVAL_CATEGORIES = ("rule",)
CROWD_LIMIT = 12           # kategori di atas ini berdesakan di dalam slot yang sama
COMPACT_BATCH = 300        # baris session_turns maksimum per putaran
ARCH_TIMEOUT = 40
GIT_TIMEOUT = 12

DIGEST_CATEGORY = "dream"
CURSOR_KEY = "dream_cursor"
RANK_KEY = "dream_rank"
PREFIX_DIGEST = "dream_"


def ph(engine):
    return "%s" if str(engine).upper() == "POSTGRESQL" else "?"


def stamp(ts=None):
    return time.strftime("%Y-%m-%d_%H%M", time.localtime(ts))


def run_stamp(ts=None):
    """Kunci baris digest, sampai mikrodetik.

    Menit tidak cukup, dan detik pun tidak: dua putaran dalam satu kesatuan waktu berbagi kunci dan
    yang kemudian menimpa yang pertama lewat ON CONFLICT — satu catatan penolakan bisa menghapus
    digest putaran selesai, atau sebaliknya. Terukur malam ini di dalam selftest sendiri: empat
    putaran selesai plus satu penolakan menyisakan TEPAT SATU baris, dan yang tinggal adalah
    penolakannya. Timer 30 menit bukan jaminan; satu panggilan manual di samping detak sudah cukup.
    """
    frac = ("%06d" % int((float(ts or time.time()) % 1) * 1_000_000))[:6]
    return time.strftime("%Y-%m-%d_%H%M%S", time.localtime(ts)) + "_" + frac


def now_ts():
    return int(time.time())


def to_epoch(value):
    """Konversi kolom waktu lintas backend ke detik. None tetap None — bukan 0."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return int(dt.timestamp())
    text = str(value).strip().replace("T", " ")[:19]
    try:
        return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp())
    except ValueError:
        return None


# --- gerbang -----------------------------------------------------------------
def last_activity(conn, engine):
    """Detik sejak kegiatan mesin terakhir, DIUKUR dari ledger.

    `UNKNOWN` bukan `idle`. Setiap tabel yang bisa dibaca ikut memberi tanda; yang tidak punya
    baris hanya tidak berkontribusi, bukan memblokir — kalau tidak, mesin yang action_log-nya hidup
    tapi session_turns-nya masih kosong tidak akan pernah boleh bermimpi. Yang membuat UNKNOWN
    adalah tabel yang tidak bisa dibaca SAMA SEKALI, dan ledger yang terbaca tapi kosong di
    segala sumber: tidak ada bukti, dan tidak ada bukti bukan izin untuk mulai menulis di rumah orang.
    """
    best, seen, errors = None, {}, []
    for table, col in (("action_log", "ts"), ("session_turns", "timestamp")):
        try:
            rows = wo.run(conn, engine, f"SELECT MAX({col}) AS m FROM {table}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{table}: {str(exc)[:60]}")
            continue
        e = to_epoch(rows[0]["m"]) if rows else None
        seen[table] = e
        if e is not None:
            best = e if best is None else max(best, e)
    if errors and best is None:
        return {"ok": False, "idle_seconds": None,
                "why": "UNKNOWN: tidak ada ledger yang bisa dibaca (" + "; ".join(errors) + ")"}
    if best is None:
        return {"ok": False, "idle_seconds": None,
                "why": f"UNKNOWN: ledger terbaca tapi tidak ada satu baris pun di {list(seen)}"}
    return {"ok": True, "idle_seconds": max(0, now_ts() - best), "sources": seen,
            "why": "; ".join(errors) or None}


def idle_gate(conn, engine, minutes=IDLE_MINUTES, force=False):
    if force:
        return {"ok": True, "idle_seconds": None, "why": "force dari pemanggil yang berdiri di meja"}
    la = last_activity(conn, engine)
    if not la["ok"]:
        return {"ok": False, "idle_seconds": None, "why": la["why"]}
    need = minutes * 60
    if la["idle_seconds"] < need:
        return {"ok": False, "idle_seconds": la["idle_seconds"],
                "why": f"baru {la['idle_seconds']} detik sejak kegiatan terakhir, mimpi butuh {need}"}
    return {"ok": True, "idle_seconds": la["idle_seconds"], "why": None}


def mode_gate(conn, engine):
    """Mode menjawab BOLEH TIDAK ada kerja latar; level menjawab JAUH MANA tangan boleh bergerak.

    Baris digest adalah medium dream sendiri — itulah satu-satunya hal yang boleh ditulis pada
    tingkat `observe`. Berkas (handoff, skill learned) menuntut `dispatch`, sama seperti jalur
    timer lain, dan `arm` tetap hanya bisa dilakukan dari TTY.
    """
    ap = wo.autopilot(conn, engine)
    mode = (ap.get("mode") or "").upper()
    if mode == wo.OFF_MODE:
        return {"ok": False, "mode": mode, "why": "OFF: keadaan berhenti, tidak ada kerja latar"}
    if mode not in wo.MODES:
        return {"ok": False, "mode": mode or "(kosong)",
                "why": "kolom mode berisi nilai di luar kosakata — UNKNOWN, bukan izin"}
    lvl = wo.drain_level(conn, engine)
    return {"ok": True, "mode": mode, "level": lvl.get("level"), "readable": bool(lvl.get("readable")),
            "may_write_files": bool(lvl.get("allows_dispatch")),
            "why": None if lvl.get("readable") else "tingkat tangan tidak terbaca — turun ke baca-saja"}


def tracked_state():
    """Daftar `git status --porcelain` saat ini. None = tidak terbaca, BUKAN bersih."""
    try:
        r = cw.run_grouped(["git", "-C", str(STATION), "status", "--porcelain"], GIT_TIMEOUT, text=True)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return sorted(x.strip() for x in (r.stdout or "").splitlines() if x.strip())


# --- pekerjaan 1: menata ulang dunia memori ----------------------------------
def _norm(content):
    return " ".join((content or "").lower().split())[:72]


def rank_memories(conn, engine):
    """Skor setiap baris world_memory dari sinyal yang ada di dalam barisnya sendiri.

    Tidak ada alat yang mengukur "penting". Yang ada di dalam baris: umur, apakah filter
    pengambilan bisa melihatnya, berapa banyak saudaranya di kategori yang sama, dan apakah
    isinya sebenarnya salinan. Keempatnya dihitung, tidak dirasa.
    """
    try:
        rows = wo.run(conn, engine, "SELECT key, category, content, tags, updated_at FROM world_memory")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"world_memory tidak terbaca: {str(exc)[:80]}"}

    by_cat, by_prefix = {}, {}
    for r in rows:
        c = (r["category"] or "").strip().lower()
        by_cat[c] = by_cat.get(c, 0) + 1
        p = _norm(r["content"])
        by_prefix[p] = by_prefix.get(p, 0) + 1

    scored = []
    for r in rows:
        cat = (r["category"] or "").strip().lower()
        e = to_epoch(r["updated_at"])
        age_days = 60.0 if e is None else max(0.0, (now_ts() - e) / 86400.0)
        fresh = int(60 * (0.5 ** (age_days / RECENCY_HALF_DAYS)))
        visible = ("handoff" in (r["key"] or "").lower()) or cat in RETRIEVAL_CATEGORIES
        n_cat = by_cat.get(cat, 1)
        crowd = -min(30, 10 * (n_cat // CROWD_LIMIT)) if n_cat > CROWD_LIMIT else 0
        dup = -min(50, 25 * (by_prefix.get(_norm(r["content"]), 1) - 1))
        thin = -10 if len((r["content"] or "").strip()) < 40 else 0
        scored.append({
            "key": r["key"], "category": cat, "age_days": round(age_days, 1), "visible": visible,
            "score": fresh + (30 if visible else 0) + crowd + dup + thin,
            "parts": {"fresh": fresh, "visible": 30 if visible else 0, "crowd": crowd, "dup": dup, "thin": thin},
        })

    scored.sort(key=lambda x: (-x["score"], x["key"]))
    invisible_rules = {}
    for r in scored:
        if r["category"] not in RETRIEVAL_CATEGORIES and ("rule" in r["category"] or "aturan" in r["category"]):
            invisible_rules[r["category"]] = invisible_rules.get(r["category"], 0) + 1
    return {
        "ok": True, "count": len(scored), "rows": scored, "top": scored[:RETRIEVAL_SLOTS],
        "bottom": scored[-3:],
        "crowded": {k: v for k, v in by_cat.items() if v > CROWD_LIMIT},
        "duplicates": {k: v for k, v in by_prefix.items() if v > 1},
        "invisible_rules": invisible_rules,
        "retrievable": sum(1 for r in scored if r["visible"]),
    }


def persist_scores(conn, engine, scored):
    """Tulis peringkat ke kolomnya, supaya jalur pengambilan MEMANG membacanya.

    Ini bagian yang membuat "re-rank" bukan sekadar laporan: tanpa baris ini skor hanya hidup di
    stdout satu proses, dan urutan yang diterima engine saat sesi dimulai tidak berubah sama sekali.
    Penulisan hanya terjadi di bawah `dispatch` — melihat-lihat adalah observe, menilai adalah tangan.
    Yang dilaporkan adalah angka yang DIBACA KEMBALI dari database, bukan hitungan loop penulisan.
    """
    p, written = ph(engine), 0
    for r in scored:
        try:
            wo.run(conn, engine, f"UPDATE world_memory SET rank_score = {p} WHERE key = {p}",
                   (int(r["score"]), r["key"]))
            written += 1
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "written": written, "verified": None,
                    "why": f"UPDATE rank_score gagal di {r['key']}: {str(exc)[:70]}"}
    try:
        n = wo.run(conn, engine,
                   "SELECT COUNT(*) AS c FROM world_memory WHERE rank_score IS NOT NULL")[0]["c"]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "written": written, "verified": None,
                "why": f"penulisan tidak bisa dibaca kembali: {str(exc)[:70]}"}
    return {"ok": True, "written": written, "verified": int(n), "why": None}


# --- pekerjaan 2: memadatkan baris sesi yang menua ---------------------------
def read_cursor(conn, engine):
    try:
        rows = wo.run(conn, engine, f"SELECT content FROM world_memory WHERE key = {ph(engine)}", (CURSOR_KEY,))
    except Exception:  # noqa: BLE001
        return 0
    if not rows:
        return 0
    try:
        return int(str(rows[0]["content"] or "0").strip())
    except ValueError:
        return 0


def compact_stale_turns(conn, engine, cursor, minutes=IDLE_MINUTES, batch=COMPACT_BATCH, write=True):
    """Ubah baris session_turns lewat kurSOR menjadi satu handoff per mesin per hari.

    "Stale" di sini BUKAN "lebih tua dari seminggu". Terukur 2026-10-09: tabel berisi 266 baris
    dan NOL di antaranya berumur tujuh hari, jadi batas itu membuat pekerjaan ini tidak pernah
    menyala — layar, bukan antrean. Yang menyiapkan sebuah baris adalah sudah lewat kurSOR dan
    mesinnya sudah berhenti menulis sejak itu. Baris aslinya tidak pernah dihapus: riwayat yang
    tidak tercatat dianggap belum terjadi, jadi yang dibuang tidak boleh ada di sini.
    """
    p = ph(engine)
    cutoff = datetime.fromtimestamp(now_ts() - minutes * 60).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = wo.run(conn, engine,
                      f"SELECT id, timestamp, cli_engine, model, prompt, response, duration_ms "
                      f"FROM session_turns WHERE id > {p} AND timestamp <= {p} ORDER BY id LIMIT {p}",
                      (cursor, cutoff, batch))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"session_turns tidak terbaca: {str(exc)[:80]}", "files": [], "cursor": cursor}
    if not rows:
        return {"ok": True, "compacted": 0, "files": [], "cursor": cursor,
                "why": f"tidak ada baris di atas kurSOR {cursor} yang lebih tua dari {minutes} menit"}

    groups = {}
    for r in rows:
        key = (r["cli_engine"] or "unknown", str(r["timestamp"] or "")[:10])
        groups.setdefault(key, []).append(r)

    files = []
    for (eng, day), items in sorted(groups.items()):
        path = Path(PROJECTS) / f"handoff_dream_{day}_{eng}.md"
        lines = [] if path.exists() else [f"# Dream handoff — {eng} — {day}", "",
                                          "Dipadatkan dari baris session_turns oleh F10.5 dream phase. Baris "
                                          "aslinya tidak dihapus; ini ringkasan, bukan pemindahan.", ""]
        if path.exists():
            # Ditambah, bukan ditimpa. Satu mesin bisa mimpi lebih dari sekali dalam sehari, dan
            # write_text() pada berkas yang namanya hanya mengandung tanggal akan menghapus ringkasan
            # putaran sebelumnya — destruktif diam-diam, ditemukan verifier 2026-10-09.
            lines.append(f"\n## Putaran {stamp()} — {len(items)} baris\n")
        for r in items:
            lines.append(f"- `#{r['id']} {str(r['timestamp'])[:19]}` **{(r['model'] or '-')[:28]}** "
                         f"({r['duration_ms'] or 0} ms)")
            lines.append(f"  - in: {(r['prompt'] or '').strip()[:240]}")
            lines.append(f"  - out: {(r['response'] or '').strip()[:240]}")
        if write:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            except OSError as exc:
                return {"ok": False, "why": f"gagal menulis {path.name}: {exc}", "files": [], "cursor": cursor}
            files.append(str(path))
    return {"ok": True, "compacted": len(rows), "files": files,
            "cursor": max(int(r["id"]) for r in rows), "groups": len(groups)}


# --- pekerjaan 3: utang dokumentasi -----------------------------------------
def documentation_debt():
    """Jalankan pemeriksa peta arsitektur; laporkan angkanya, jangan perbaiki diam-diam."""
    try:
        r = cw.run_grouped(["bash", str(ARCH_MAP), "--check"], ARCH_TIMEOUT, text=True, merge_stderr=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "count": None, "why": f"UNKNOWN: arch_map tidak berjalan ({str(exc)[:60]})"}
    out = (r.stdout or "").strip()
    n = None
    for line in out.splitlines():
        if "undocumented files:" in line:
            try:
                n = int(line.split(":", 1)[1].strip())
            except ValueError:
                n = None
    if n is None:
        return {"ok": False, "count": None, "why": f"UNKNOWN: keluaran arch_map tidak menyebut angka (exit={r.returncode})"}
    return {"ok": True, "count": n, "exit": r.returncode, "lines": out.splitlines()[:12]}


# --- pekerjaan 4: menjawab kegagalan sebelum ia yang kelima ------------------
def pre_answer_incidents(conn, engine, threshold=PRE_ANSWER_REPEATS, write=True):
    """Cari signature yang terulang `threshold` kali tapi belum punya skill, lalu jawab.

    incident_recorder menulis skill pada kegagalan ke-5, di tengah kerja, sementara orang menunggu.
    Ini hal yang sama satu pola lebih awal, di saat tidak ada yang menunggu — memakai penulis yang
    sama, bukan salinan kedua darinya (§12).
    """
    try:
        rows = wo.run(conn, engine,
                      f"SELECT error_signature, repeat_count, last_error_text, last_seen FROM incident_log "
                      f"WHERE repeat_count >= {ph(engine)} AND auto_skill_id IS NULL "
                      f"ORDER BY repeat_count DESC LIMIT 5", (threshold,))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"incident_log tidak terbaca: {str(exc)[:80]}", "answered": [], "candidates": 0}
    if not rows:
        return {"ok": True, "answered": [], "candidates": 0,
                "why": f"tidak ada signature di atas {threshold} tanpa skill"}
    if not write:
        return {"ok": True, "answered": [], "candidates": len(rows),
                "why": f"tangan pada tingkat baca-saja: {len(rows)} kandidat dicatat, tidak ada berkas ditulis"}

    import incident_recorder as ir  # noqa: PLC0415
    answered = []
    for r in rows:
        sig = r["error_signature"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ir.generate_skill(sig, {
                    "count": r["repeat_count"],
                    "description": (r["last_error_text"] or sig or "")[:600],
                    "solution": "Dijawab lebih awal oleh dream phase: pola ini terulang "
                                f"{r['repeat_count']} kali dan skill-nya ditulis sebelum kegagalan ke-5.",
                    "last_seen": str(r["last_seen"]),
                })
        except Exception as exc:  # noqa: BLE001
            answered.append({"sig": sig, "ok": False, "why": str(exc)[:100], "file": None})
            continue
        back = wo.run(conn, engine,
                      f"SELECT auto_skill_id FROM incident_log WHERE error_signature = {ph(engine)}", (sig,))
        got = back[0] if back else {}
        path = Path(ir.LEARNED_DIR) / f"{ir.safe_name(sig)}.md"
        answered.append({"sig": sig, "rc": r["repeat_count"], "ok": bool(got.get("auto_skill_id")),
                         "skill": got.get("auto_skill_id"), "file": str(path) if path.exists() else None})
    return {"ok": True, "answered": answered, "candidates": len(rows)}


# --- digest ------------------------------------------------------------------
def build_digest(gate, jobs, files, tracked_before, tracked_after, idle_seconds):
    out = [f"[{stamp()}] DREAM — mode={gate['mode']} level={gate.get('level')}",
           f"- gerbang: idle={idle_seconds} detik | berkas_diperbolehkan={gate.get('may_write_files')}"]

    rank = jobs.get("rank") or {}
    if rank.get("ok"):
        out.append(f"- re-rank {rank['count']} baris; yang terlihat jalur pengambilan: "
                   f"{rank['retrievable']} (slot {RETRIEVAL_SLOTS})")
        for r in rank["top"]:
            out.append(f"    · {r['score']:>4} [{r['category']}] {r['key'][:60]}")
        if rank["crowded"]:
            top = sorted(rank["crowded"].items(), key=lambda x: -x[1])[:6]
            out.append(f"- kategori berdesakan (>{CROWD_LIMIT}): " + ", ".join(f"{k}={v}" for k, v in top))
        if rank["invisible_rules"]:
            out.append("- UTANG: kategori aturan yang tidak dibaca filter pengambilan: "
                       + ", ".join(f"{k}({v})" for k, v in rank["invisible_rules"].items()))
        if rank["duplicates"]:
            out.append(f"- isi kembar: {len(rank['duplicates'])} kelompok (tiap anggotanya sudah dihukum skornya)")
    else:
        out.append(f"- re-rank: {rank.get('why', 'UNKNOWN')}")

    per = jobs.get("persist") or {}
    if per:
        out.append(f"- peringkat ditulis: {per.get('verified')} baris dibaca kembali dari database "
                   f"(dicoba {per.get('written')})" if per.get("ok")
                   else f"- peringkat TIDAK tersimpan: {per.get('why')}")
    else:
        out.append("- peringkat tidak ditulis (tingkat baca-saja)")

    comp = jobs.get("compact") or {}
    if comp.get("ok"):
        out.append(f"- padatan: {comp['compacted']} baris menjadi {len(comp['files'])} berkas "
                   f"| kurSOR berikutnya {comp['cursor']}")
    else:
        out.append(f"- padatan: {comp.get('why', 'UNKNOWN')}")

    debt = jobs.get("debt") or {}
    out.append(f"- utang dokumentasi: {debt.get('count') if debt.get('ok') else debt.get('why', 'UNKNOWN')}")

    inc = jobs.get("incidents") or {}
    if inc.get("ok"):
        out.append(f"- insiden terjawab-dulu: {len(inc.get('answered', []))} dari {inc.get('candidates')} kandidat "
                   f"({inc.get('why') or 'ditulis'})")
        for a in inc.get("answered", []):
            out.append(f"    · {a.get('sig')} → {a.get('skill') or a.get('why')}  [{a.get('file')}]")
    else:
        out.append(f"- insiden: {inc.get('why', 'UNKNOWN')}")

    if tracked_before is None or tracked_after is None:
        out.append("- working tree: UNKNOWN — git tidak terbaca, dan itu bukan 'bersih'")
        delta = None
    else:
        delta = sorted(set(tracked_after) - set(tracked_before))
        if delta:
            out.append(f"- BERKAS TERLACAK BERUBAH ({len(delta)}) — disebut di sini karena F10.5 menuntut namanya:")
            out.extend(f"    · {d}" for d in delta)
        else:
            out.append("- berkas terlacak: tidak ada yang berubah")
    if files:
        out.append(f"- berkas yang DITULIS putaran ini ({len(files)}):")
        out.extend(f"    · {f}" for f in files)
    return "\n".join(out), delta


def _upsert(conn, engine, key, body, tags):
    p = ph(engine)
    try:
        wo.run(conn, engine,
               f"INSERT INTO world_memory (key, category, content, tags, updated_at) "
               f"VALUES ({p},{p},{p},{p},{p}) ON CONFLICT (key) DO UPDATE SET "
               f"content = EXCLUDED.content, tags = EXCLUDED.tags, updated_at = EXCLUDED.updated_at",
               (key[:100], DIGEST_CATEGORY, body, str(tags)[:255], datetime.fromtimestamp(now_ts())))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "key": key, "why": f"gagal menulis {key}: {str(exc)[:120]}"}
    return {"ok": True, "key": key, "why": None}


def emit_digest(conn, engine, body, files, key=None):
    return _upsert(conn, engine, key or f"{PREFIX_DIGEST}{run_stamp()}", body, f"files={len(files)}")


def write_rank(conn, engine, rank):
    """Simpan urutan supaya putaran berikutnya bisa membandingkan, bukan menebak ulang."""
    if not rank.get("ok"):
        return {"ok": False, "why": "re-rank tidak menghasilkan apa pun"}
    body = "\n".join(f"{i + 1:>3} {r['score']:>4} [{r['category']}] {r['key']}"
                     for i, r in enumerate(rank["top"]))
    return _upsert(conn, engine, RANK_KEY, body, "rank")


def move_cursor(conn, engine, cursor):
    res = _upsert(conn, engine, CURSOR_KEY, str(int(cursor)), "cursor")
    return res["ok"]


# --- satu putaran ------------------------------------------------------------
def run(force=False, minutes=IDLE_MINUTES, conn=None, engine=None, quiet=False):
    owned = conn is None
    if owned:
        conn, engine = wo.connect()
    jobs, files = {}, []
    try:
        gate = mode_gate(conn, engine)
        if not gate["ok"]:
            # OFF berarti tidak ada jejak di DATABASE sama sekali: bahkan baris digest tidak ditulis,
            # supaya "OFF" tetap satu keadaan di dalam memori, bukan dua laporan yang berbeda.
            # Yang tetap harus keluar adalah alasannya — ke stderr, supaya jurnal systemd membedakan
            # "sakelarnya mati" dari "timernya tidak pernah dijadwalkan".
            if not quiet:
                print(f"DREAM DITOLAK — {gate['why']}", file=sys.stderr)
            return {"ok": False, "refused": "MODE", "why": gate["why"], "files": []}
        may_files = bool(gate.get("may_write_files"))
        # `--force` melewatkan gerbang idle, TIDAK pernah menaikkan tangan. Perbedaannya seluruh
        # isinya: orang di terminal boleh melihat satu putaran sekarang, tapi menulis berkas butuh
        # `dispatch`, dan `arm` menolak tanpa /dev/tty terbuka — kalau force ikut menaikkan tangga,
        # satu proses tanpa pengawas (timer, atau shellku sendiri) bisa memberi dirinya tangan.
        # Ditemukan oleh verifikasi lintas mesin 2026-10-09, dan memang benar.
        before = tracked_state()
        idle = idle_gate(conn, engine, minutes, force)

        if not idle["ok"]:
            body = (f"[{stamp()}] DREAM DITOLAK — mode={gate['mode']} level={gate.get('level')}\n"
                    f"- gerbang idle: {idle['why']}")
            # Satu baris penolakan per hari, bukan per putaran. Dengan kunci microsecond setiap
            # putaran punya barisnya sendiri, dan timer 30 menit berarti sampai 48 catatan "belum
            # waktunya" sehari — nomor memori orang membengkak oleh keadaan yang justru normal.
            # Yang tetap harus bisa dijawab besok: apakah mimpi pernah mencoba. Jawabannya ada di
            # satu baris ini plus jurnal timer (stdout), yang mencatat setiap penolakan.
            res = emit_digest(conn, engine, body, [], key=f"{PREFIX_DIGEST}refused_{datetime.now():%Y-%m-%d}")
            if not quiet:
                # Penolakan yang tidak dicetak adalah penolakan yang tidak ada: timer menulis
                # stdout-nya ke journal, dan journal kosong membuat orang menyimpulkan
                # "mimpi tidak dijadwalkan" padahal yang terjadi adalah "belum waktunya".
                print(body)
            return {"ok": False, "refused": "IDLE", "why": idle["why"], "digest": res, "files": []}

        jobs["rank"] = rank_memories(conn, engine)
        if may_files and jobs["rank"].get("ok"):
            jobs["persist"] = persist_scores(conn, engine, jobs["rank"]["rows"])
        cursor = read_cursor(conn, engine)
        jobs["compact"] = compact_stale_turns(conn, engine, cursor, minutes=minutes, write=may_files)
        jobs["debt"] = documentation_debt()
        jobs["incidents"] = pre_answer_incidents(conn, engine, write=may_files)

        files = [f for f in (jobs["compact"].get("files") or [])
                 if f] + [a["file"] for a in (jobs["incidents"].get("answered") or []) if a.get("file")]
        if may_files:
            if jobs["compact"].get("ok") and jobs["compact"].get("files"):
                move_cursor(conn, engine, jobs["compact"]["cursor"])
            write_rank(conn, engine, jobs["rank"])

        after = tracked_state()
        body, delta = build_digest(gate, jobs, files, before, after, idle.get("idle_seconds"))
        res = emit_digest(conn, engine, body, files)
        out = {"ok": True, "mode": gate["mode"], "level": gate.get("level"), "may_write_files": may_files,
               "idle_seconds": idle.get("idle_seconds"), "digest": res, "files": files, "tracked_delta": delta,
               "jobs": {k: {kk: v.get(kk) for kk in ("count", "compacted", "candidates", "retrievable",
                                                    "written", "verified", "ok", "why")
                            if kk in v} for k, v in jobs.items()}}
        if not quiet:
            print(body)
        return out
    finally:
        if owned:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# --- selftest ----------------------------------------------------------------
def _sandbox(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE world_memory (key TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT, "
                 "tags TEXT, updated_at TEXT, rank_score INTEGER)")
    conn.execute("CREATE TABLE session_turns (id INTEGER PRIMARY KEY, timestamp TEXT, cli_engine TEXT, "
                 "model TEXT, prompt TEXT, response TEXT, duration_ms INTEGER)")
    conn.execute("CREATE TABLE incident_log (error_signature TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, "
                 "repeat_count INTEGER DEFAULT 1, last_error_text TEXT, resolved INTEGER DEFAULT 0, "
                 "auto_skill_id TEXT)")
    conn.execute("CREATE TABLE skills_inventory (id TEXT PRIMARY KEY, name TEXT, category TEXT, level INTEGER, "
                 "description TEXT, file_path TEXT, auto_learned INTEGER DEFAULT 0, use_count INTEGER DEFAULT 0, "
                 "created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE action_log (ts TEXT, engine TEXT, kind TEXT, summary TEXT)")
    # quest_tasks ikut dibuat lengkap karena wo.autopilot() membacanya untuk menghitung item yang
    # selesai dalam jendela. Sandbox dengan kolom kurang akan membuat tes mati di "no such column",
    # yang lulus tanpa membuktikan apa pun tentang vonis mana pun.
    conn.execute("CREATE TABLE quest_tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT, "
                 "model_assigned TEXT, cli_engine TEXT, summary TEXT, created_at TEXT, completed_at TEXT, "
                 "phase TEXT, check_command TEXT, depends_on TEXT, claimed_by TEXT, lease_epoch INTEGER, "
                 "evidence TEXT, resume_count INTEGER DEFAULT 0, escalation_reason TEXT, "
                 "due_epoch INTEGER, origin TEXT, ws_path TEXT, inject_count INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE autopilot_state (id INTEGER PRIMARY KEY, mode TEXT, level TEXT, "
                 "level_until INTEGER, changed_by TEXT, on_epoch INTEGER, until_epoch INTEGER, "
                 "item_budget INTEGER, items_done INTEGER DEFAULT 0, off_reason TEXT, "
                 "items_done_stored INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO autopilot_state (id, mode, level) VALUES (1, 'ON', 'observe')")
    conn.commit()
    return conn


def _ago(minutes):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts() - minutes * 60))


def selftest():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail if not cond else ""))

    restore_leftover = []
    tmp = Path(tempfile.mkdtemp(prefix="dream_selftest_"))
    projects, learned = tmp / "projects", tmp / "learned"
    projects.mkdir(parents=True, exist_ok=True)

    # `LEARNED` sengaja TIDAK disambat: jalur produksi tidak membacanya (pre_answer_incidents memakai
    # ir.LEARNED_DIR), dan menyambat nama yang tidak dipakai adalah sambatan placebo — ditemukan
    # verifier 2026-10-09. Nilainya hanya dipakai di bawah untuk membuktikan berkas produksi utuh.
    orig = {k: globals()[k] for k in ("PROJECTS", "tracked_state", "documentation_debt")}
    globals()["PROJECTS"] = projects
    globals()["tracked_state"] = lambda: []
    globals()["documentation_debt"] = lambda: {"ok": True, "count": 0, "exit": 0, "lines": []}

    import incident_recorder as ir  # noqa: PLC0415
    # generate_skill membuka koneksinya SENDIRI lewat get_db_connection dan menulis SKILLS.md
    # katalog. Tanpa sambatan berikut — ir.LEARNED_DIR, ir.SKILLS_CATALOG, ir.get_db_connection —
    # satu tes "apakah skill pra-jawab bekerja" akan meninggalkan berkas di brain produksi dan
    # membariskan incident_log sungguhan, persis kelas bug yang seluruh sesi ini habiskan untuk
    # menutup. Jumlahnya disebut by name, bukan by count: versi "keempat sambatan" tertulis di atas
    # tiga dan bertahan tiga ronde tanpa ada yang bisa membantahnya.
    ir_seams = (ir.LEARNED_DIR, ir.SKILLS_CATALOG, ir.get_db_connection)
    ir.LEARNED_DIR = learned
    ir.SKILLS_CATALOG = tmp / "SKILLS.md"
    ir.SKILLS_CATALOG.write_text("### 5. Learned Skills (Self-Generated) (`skills/learned/`)\n")
    conn = _sandbox(tmp / "dream.db")
    # Sambatan koneksi memberi generate_skill koneksi KEDUA ke berkas yang sama, bukan koneksi
    # tes. Fungsi itu menutup apa pun yang dibukanya sendiri, dan menyerahkan koneksi tes kepadanya
    # berarti tes berikutnya mati di tengah jalan dengan "Cannot operate on a closed database" —
    # ketemu pada percobaan pertama, bukan ditebak.
    def _sandbox_conn():
        return sqlite3.connect(str(tmp / "dream.db")), "SQLITE"
    ir.get_db_connection = _sandbox_conn
    prod_projects = sorted(p.name for p in Path(orig["PROJECTS"]).glob("handoff_dream_*")) \
        if Path(orig["PROJECTS"]).exists() else []
    prod_skills = sorted(p.name for p in LEARNED.glob("*.md")) \
        if LEARNED.exists() else []
    try:
        conn.execute("INSERT INTO action_log (ts, engine, kind) VALUES (?,?,?)", (_ago(1), "qoder", "command"))
        conn.commit()
        g = idle_gate(conn, "SQLITE")
        check("mesin yang baru bekerja 1 menit lalu ditolak untuk bermimpi", not g["ok"], str(g))

        conn.execute("DELETE FROM action_log")
        conn.execute("INSERT INTO action_log (ts, engine, kind) VALUES (?,?,?)", (_ago(45), "qoder", "command"))
        conn.commit()
        g2 = idle_gate(conn, "SQLITE")
        check("menganggur 45 menit boleh bermimpi", g2["ok"] and g2["idle_seconds"] >= 2700, str(g2))

        conn.execute("DELETE FROM action_log")
        conn.execute("DELETE FROM session_turns")
        conn.commit()
        g3 = idle_gate(conn, "SQLITE")
        check("ledger tanpa satu baris pun = UNKNOWN, BUKAN 'idle' yang boleh jalan",
              (not g3["ok"]) and "UNKNOWN" in (g3.get("why") or ""), str(g3))

        conn.execute("INSERT INTO session_turns (id, timestamp, cli_engine, prompt) VALUES (1,?,?,?)",
                     (_ago(45), "qoder", "p"))
        conn.execute("INSERT INTO action_log (ts, engine, kind) VALUES (?,?,?)", (_ago(45), "qoder", "command"))
        conn.commit()
        m = mode_gate(conn, "SQLITE")
        check("ON level observe: boleh membaca dan menulis digest, belum boleh menulis berkas",
              m["ok"] and m["may_write_files"] is False, str(m))
        conn.execute("UPDATE autopilot_state SET mode='OFF'")
        conn.commit()
        check("OFF menolak sebelum satu pekerjaan pun dimulai", not mode_gate(conn, "SQLITE")["ok"])
        conn.execute("UPDATE autopilot_state SET mode='SEMI'")
        conn.commit()
        ok_lv, why_lv = wo.set_level(conn, "SQLITE", "dispatch", minutes=30, who="selftest", tty=lambda: True)
        sm = mode_gate(conn, "SQLITE")
        check("SEMI boleh bermimpi, dan tingkat tangan tetap yang menentukan berkas",
              ok_lv and sm["ok"] and sm["mode"] == "SEMI" and sm["may_write_files"], f"{sm} {why_lv}")
        conn.execute("UPDATE autopilot_state SET level='dispatch', level_until=NULL")
        conn.commit()
        check("level dispatch tanpa batas waktu TIDAK membuka tangan — dream menulis berkas dilarang",
              mode_gate(conn, "SQLITE")["may_write_files"] is False, str(mode_gate(conn, "SQLITE")))
        conn.execute("UPDATE autopilot_state SET mode='MUNGKIN'")
        conn.commit()
        check("mode di luar kosakata ditolak sebagai UNKNOWN", not mode_gate(conn, "SQLITE")["ok"])
        conn.execute("UPDATE autopilot_state SET mode='ON'")
        ok2, _ = wo.set_level(conn, "SQLITE", "dispatch", minutes=30, who="selftest", tty=lambda: True)
        conn.commit()

        for i in range(14):
            conn.execute("INSERT INTO world_memory (key, category, content, updated_at) VALUES (?,?,?,?)",
                         (f"handoff_auto_{i}", "session_recovery",
                          f"catatan ke-{i} yang isinya cukup panjang untuk tidak dianggap tipis", _ago(i)))
        conn.execute("INSERT INTO world_memory (key, category, content, updated_at) VALUES (?,?,?,?)",
                     ("rule_enforced_cjk", "rule_enforced", "aturan yang diarsipkan dengan nama lain", _ago(30)))
        conn.execute("INSERT INTO world_memory (key, category, content, updated_at) VALUES (?,?,?,?)",
                     ("rule_aktual", "rule", "aturan yang memang dibaca filter pengambilan", _ago(30)))
        conn.execute("INSERT INTO world_memory (key, category, content, updated_at) VALUES (?,?,?,?)",
                     ("handoff_dup", "session_recovery",
                      "catatan ke-3 yang isinya cukup panjang untuk tidak dianggap tipis", _ago(1)))
        conn.commit()
        rk = rank_memories(conn, "SQLITE")
        check("re-rank berjalan dan menilai seluruh baris", rk["ok"] and rk["count"] == 17, str(rk.get("count")))
        check("kategori yang berdesakan dilaporkan, bukan didiamkan",
              rk["crowded"].get("session_recovery") == 15, str(rk["crowded"]))
        check("isi kembar dikenali dari konten, bukan dari kunci",
              rk["duplicates"].get("catatan ke-3 yang isinya cukup panjang untuk tidak dianggap tipis") == 2,
              str(list(rk["duplicates"].keys()))[:120])
        check("rule_enforced dilaporkan sebagai utang karena filter pengambilan tidak membacanya",
              rk["invisible_rules"].get("rule_enforced") == 1, str(rk["invisible_rules"]))
        top_keys = [r["key"] for r in rk["top"]]
        check("yang terlihat jalur pengambilan menempati urutan teratas",
              "rule_aktual" in top_keys and "rule_enforced_cjk" not in top_keys, str(top_keys))
        scores = {r["key"]: r for r in rk["rows"]}
        check("setiap sinyal skor terukur dari barisnya sendiri",
              set(scores["rule_aktual"]["parts"]) == {"fresh", "visible", "crowd", "dup", "thin"},
              str(scores["rule_aktual"]))
        check("tangan dispatch yang dipakai tes berikutnya sungguh terbuka", ok2 and sm["ok"], f"{ok2}")
        check("isi kembar dihukum, baris yang berdiri sendiri tidak",
              scores["handoff_dup"]["parts"]["dup"] == -25
              and scores["handoff_auto_3"]["parts"]["dup"] == -25
              and scores["handoff_auto_0"]["parts"]["dup"] == 0,
              str({k: scores[k]["parts"] for k in ("handoff_dup", "handoff_auto_3", "handoff_auto_0")})[:200])
        check("kategori yang berdesakan dihukum, kategori kecil tidak",
              scores["handoff_dup"]["parts"]["crowd"] == -10 and scores["rule_aktual"]["parts"]["crowd"] == 0,
              f"crowd={scores['handoff_dup']['parts']['crowd']}/{scores['rule_aktual']['parts']['crowd']}")
        check("salinan kalah oleh baris unik yang setara, meski umurnya baru selisih satu menit",
              scores["handoff_dup"]["score"] < scores["handoff_auto_0"]["score"],
              f"dup={scores['handoff_dup']['score']} unik={scores['handoff_auto_0']['score']}")

        for i in range(2, 8):
            conn.execute("INSERT INTO session_turns (id, timestamp, cli_engine, model, prompt, response, duration_ms) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (i, _ago(90), "qoder", "gemini-3.8-flash", f"prompt {i}", f"jawaban {i}", 1000 + i))
        conn.execute("INSERT INTO session_turns (id, timestamp, cli_engine, model, prompt, response) "
                     "VALUES (?,?,?,?,?,?)", (99, _ago(2), "antigravity", "m", "baru", "segar"))
        conn.commit()
        c1 = compact_stale_turns(conn, "SQLITE", 1, write=True)
        check("baris tua di atas kurSOR dipadatkan menjadi berkas",
              c1["ok"] and c1["compacted"] == 6 and len(c1["files"]) == 1, str(c1)[:180])
        check("berkas handoff benar-benar ada di disk sandbox",
              c1["files"] and Path(c1["files"][0]).exists() and str(tmp) in c1["files"][0], str(c1["files"]))
        check("kurSOR maju ke id terbesar yang dipadatkan", c1["cursor"] == 7, str(c1["cursor"]))
        body_txt = Path(c1["files"][0]).read_text() if c1["files"] else ""
        check("isi handoff menyebut mesin dan setiap baris yang dipadatkan",
              "qoder" in body_txt and "#2 " in body_txt and "#7 " in body_txt, body_txt[:90])
        c2 = compact_stale_turns(conn, "SQLITE", c1["cursor"], write=True)
        check("putaran kedua tidak memadamkan ulang baris yang sudah lewat kurSOR",
              c2["compacted"] == 0, str(c2)[:120])
        c3 = compact_stale_turns(conn, "SQLITE", 0, write=True)
        check("kurSOR nol ikut memuat baris tua yang pertama, dan berhenti sebelum yang masih segar",
              c3["compacted"] == 7 and c3["cursor"] == 7, str(c3)[:120])
        check("baris yang baru berumur 2 menit tidak ikut dipadatkan",
              not any("#99 " in Path(f).read_text() for f in c3["files"]), str(c3["files"])[:120])
        c4 = compact_stale_turns(conn, "SQLITE", 1, write=False)
        check("write=False menghitung tanpa menyentuh disk", c4["ok"] and not c4["files"]
              and c4["compacted"] == 6, str(c4)[:120])

        conn.execute("INSERT INTO incident_log (error_signature, repeat_count, last_error_text) VALUES (?,?,?)",
                     ("exit1:something_rare.py", 3, "belum saatnya"))
        conn.execute("INSERT INTO incident_log (error_signature, repeat_count, last_error_text) VALUES (?,?,?)",
                     ("exit1:four_times.py", 4, "empat kali dan belum dijawab"))
        conn.execute("INSERT INTO incident_log (error_signature, repeat_count, auto_skill_id) VALUES (?,?,?)",
                     ("exit1:already.py", 9, "learned_already"))
        conn.commit()
        i3 = pre_answer_incidents(conn, "SQLITE", write=False)
        check("kandidat di bawah ambang tidak dihitung", i3["ok"] and i3["candidates"] == 1, str(i3)[:160])
        check("tangan baca-saja tidak menulis satu berkas pun", not i3["answered"] and not list(learned.glob("*")),
              str(list(learned.glob("*"))))
        i4 = pre_answer_incidents(conn, "SQLITE", write=True)
        got = {a["sig"]: a for a in i4["answered"]}
        check("pola ke-4 dijawab sebelum kegagalan ke-5 terjadi",
              got.get("exit1:four_times.py", {}).get("ok"), str(i4)[:200])
        check("pola ke-3 tidak disentuh", "exit1:something_rare.py" not in got, str(list(got)))
        check("pola yang sudah punya skill tidak ditulis ulang", "exit1:already.py" not in got, str(list(got)))
        check("berkas skill learned benar-benar ditulis di sandbox",
              got.get("exit1:four_times.py", {}).get("file")
              and Path(got["exit1:four_times.py"]["file"]).exists(), str(got)[:200])
        row = wo.run(conn, "SQLITE",
                     "SELECT auto_skill_id, resolved FROM incident_log WHERE error_signature=?",
                     ("exit1:four_times.py",))
        check("baris insiden ditandai selesai di database, bukan hanya di berkas",
              row and row[0]["auto_skill_id"] and row[0]["resolved"], str(row))
        inv = wo.run(conn, "SQLITE", "SELECT id FROM skills_inventory WHERE id=?",
                     (f"learned_{ir.safe_name('exit1:four_times.py')}",))
        check("skill baru terdaftar di skills_inventory sehingga 'self-healing' bisa dihitung",
              bool(inv), str(inv))

        r = run(conn=conn, engine="SQLITE", force=True, quiet=True)
        check("satu putaran penuh mengeluarkan baris digest", r["ok"] and r["digest"]["ok"], str(r)[:200])
        drow = wo.run(conn, "SQLITE", "SELECT content, category, tags FROM world_memory WHERE key=?",
                      (r["digest"]["key"],))
        check("digest tercatat di world_memory dengan kategori dream",
              drow and drow[0]["category"] == DIGEST_CATEGORY, str(drow)[:120])
        text = drow[0]["content"] if drow else ""
        check("digest menyebut mode dan tingkat tangan yang berlaku", "mode=ON" in text, text[:120])
        check("digest melaporkan utang dokumentasi sebagai angka", "utang dokumentasi: 0" in text, text[:200])
        check("digest melaporkan pekerjaan yang benar-benar terjadi",
              "re-rank 17 baris" in text and "insiden terjawab-dulu" in text, text[:200])
        check("setiap berkas yang ditulis putaran ini disebut di digest-nya sendiri",
              all(f in text for f in r["files"]), str(r["files"])[:160])

        # --- peringkat yang sungguh tersimpan, bukan hanya tercetak -----------------------------
        pers = r["jobs"].get("persist") or {}
        check("re-rank menulis ke kolom rank_score dan membacanya kembali dari database",
              pers.get("ok") and pers.get("verified") == 17, str(pers)[:160])
        back = wo.run(conn, "SQLITE", "SELECT rank_score FROM world_memory WHERE key=?", ("rule_aktual",))
        want = {x["key"]: x["score"] for x in rk["rows"]}["rule_aktual"]
        check("nilai di kolom adalah nilai yang dihitung, bukan placeholder",
              back and back[0]["rank_score"] == want, f"db={back and back[0]['rank_score']} hitung={want}")
        rated = wo.run(conn, "SQLITE", "SELECT COUNT(*) c FROM world_memory WHERE rank_score IS NOT NULL")[0]["c"]
        check("setiap baris yang dinilai mendapat angka; catatan mimpi sendiri sengaja tidak dinilai",
              rated == 17, f"rated={rated}")
        conn.execute("UPDATE world_memory SET updated_at = ? WHERE key LIKE ?", (_ago(5), "dream%"))
        conn.execute("INSERT INTO world_memory (key, category, content, updated_at) VALUES (?,?,?,?)",
                     ("handoff_baru_sekali_pukul", "session_recovery", "catatan yang ditulis setelah mimpi",
                      _ago(0)))
        conn.commit()
        import session_ingestor as si  # noqa: PLC0415
        ordered = wo.run(conn, "SQLITE",
                         f"SELECT key FROM world_memory ORDER BY COALESCE(rank_score, {si.UNRANKED_TOP}) DESC, "
                         f"updated_at DESC LIMIT 1")
        check("jalur pengambilan memperlakukan baris belum bernilai sebagai yang paling baru",
              ordered and ordered[0]["key"] == "handoff_baru_sekali_pukul", str(ordered)[:120])
        check("konstanta cakupan berada di satu tempat, bukan disalin dua kali",
              si.UNRANKED_TOP == 1_000_000, str(si.UNRANKED_TOP))

        # --- tambah, bukan timpa ------------------------------------------------------------------
        first_body = Path(c1["files"][0]).read_text()
        marker = first_body.count("#2 ")
        c5 = compact_stale_turns(conn, "SQLITE", 0, write=True)
        again = Path(c5["files"][0]).read_text()
        check("putaran kedua pada hari yang sama MENAMBAH ringkasannya, tidak menghapus punya putaran pertama",
              marker >= 1 and again.count("#2 ") == marker + 1
              and again.count("# Dream handoff") == 1 and "Putaran" in again,
              f"sebelum={marker} sesudah={again.count('#2 ')} baru={c5['compacted']}")

        # --- force tidak menaikkan tangan --------------------------------------------------------
        wo.set_level(conn, "SQLITE", "observe", who="selftest", tty=lambda: False)
        conn.commit()
        files_before_force = sorted(p.name for p in projects.glob("*.md"))
        conn.execute("DELETE FROM action_log")
        conn.execute("INSERT INTO action_log (ts, engine, kind) VALUES (?,?,?)", (_ago(1), "qoder", "x"))
        conn.execute("DELETE FROM world_memory WHERE key = 'handoff_baru_sekali_pukul'")
        conn.commit()
        rf = run(conn=conn, engine="SQLITE", force=True, quiet=True)
        check("--force melewati gerbang idle TAPI tidak menaikkan tangan",
              rf["ok"] and not rf["may_write_files"] and not rf["files"], str(rf)[:200])
        check("di tingkat observe, force tidak menulis berkas apa pun di disk",
              sorted(p.name for p in projects.glob("*.md")) == files_before_force,
              str(sorted(p.name for p in projects.glob("*.md")))[:120])
        check("di tingkat observe, force tidak menulis peringkat ke database",
              "persist" not in rf["jobs"], str(list(rf["jobs"]))[:120])

        states = iter([["M tools/keep.py"], ["M tools/keep.py", "M tools/fake.py"]])
        globals()["tracked_state"] = lambda: next(states)
        r2 = run(conn=conn, engine="SQLITE", force=True, quiet=True)
        d2 = wo.run(conn, "SQLITE", "SELECT content FROM world_memory WHERE key=?", (r2["digest"]["key"],))
        after_marker = (d2[0]["content"] if d2 else "").split("BERKAS TERLACAK BERUBAH")
        check("berkas terlacak yang berubah disebut namanya di digest",
              r2["ok"] and len(after_marker) == 2 and "M tools/fake.py" in after_marker[1], str(r2)[:160])
        check("hanya selisih yang dilaporkan, bukan seluruh working tree",
              len(after_marker) == 2 and "keep.py" not in after_marker[1],
              after_marker[1][:120] if len(after_marker) == 2 else "")
        globals()["tracked_state"] = lambda: None
        r3 = run(conn=conn, engine="SQLITE", force=True, quiet=True)
        d3 = wo.run(conn, "SQLITE", "SELECT content FROM world_memory WHERE key=?", (r3["digest"]["key"],))
        check("git yang tidak terbaca dilaporkan UNKNOWN di digest, bukan 'bersih'",
              "UNKNOWN" in (d3[0]["content"] if d3 else ""), (d3[0]["content"] if d3 else "")[:200])
        globals()["tracked_state"] = lambda: []

        conn.execute("DELETE FROM action_log")
        conn.execute("INSERT INTO action_log (ts, engine, kind) VALUES (?,?,?)", (_ago(1), "qoder", "command"))
        conn.commit()
        n_mem = wo.run(conn, "SQLITE", "SELECT COUNT(*) c FROM world_memory")[0]["c"]
        files_before = sorted(p.name for p in projects.glob("*.md"))
        r4 = run(conn=conn, engine="SQLITE", quiet=True)
        check("putaran yang ditolak gerbang idle tetap menulis baris digest penolakan",
              (not r4["ok"]) and r4.get("refused") == "IDLE" and r4["digest"]["ok"], str(r4)[:200])
        check("penolakan idle tidak menulis berkas apa pun",
              not r4.get("files") and sorted(p.name for p in projects.glob("*.md")) == files_before,
              str(r4)[:120])
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            run(conn=conn, engine="SQLITE", quiet=False)
        check("penolakan idle tercetak ke stdout, supaya jurnal timer tidak diam",
              "DREAM DITOLAK" in out_buf.getvalue() and "gerbang idle" in out_buf.getvalue(),
              out_buf.getvalue()[:120])
        n_before_off = wo.run(conn, "SQLITE", "SELECT COUNT(*) c FROM world_memory")[0]["c"]
        conn.execute("UPDATE autopilot_state SET mode='OFF'")
        conn.commit()
        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            run(conn=conn, engine="SQLITE", quiet=False)
        n_after_off = wo.run(conn, "SQLITE", "SELECT COUNT(*) c FROM world_memory")[0]["c"]
        check("penolakan mode tercetak ke stderr TANPA meninggalkan baris di memori",
              "DITOLAK" in err2.getvalue() and not out2.getvalue() and n_before_off == n_after_off,
              f"err={err2.getvalue()[:80]} rows={n_before_off}->{n_after_off}")

        # Penolakan tidak boleh membanjiri memori: sampai 48 detak sehari, semuanya "belum waktunya".
        for _ in range(3):
            run(conn=conn, engine="SQLITE", quiet=True)
        refused_rows = wo.run(conn, "SQLITE",
                              "SELECT key, content FROM world_memory WHERE key LIKE ?",
                              (f"{PREFIX_DIGEST}refused_%",))
        check("tiga penolakan berturut-turut menghasilkan SATU baris harian, bukan tiga",
              len(refused_rows) == 1, f"baris={[r['key'] for r in refused_rows]}")
        check("baris harian itu tetap berisi judul penolakan, jadi ia tidak dihitung sebagai mimpi",
              refused_rows and "DREAM DITOLAK" in (refused_rows[0]["content"] or ""),
              str(refused_rows[:1])[:120])

        # clauses has-digest: penolakan bukan putaran yang selesai
        conn.execute("UPDATE autopilot_state SET mode='ON'")
        conn.commit()
        cls = wo.run(conn, "SQLITE",
                     "SELECT content FROM world_memory WHERE category=? AND key LIKE ? "
                     "AND content NOT LIKE ?", (DIGEST_CATEGORY, f"{PREFIX_DIGEST}2%", "%DREAM DITOLAK%"))
        refused_rows = wo.run(conn, "SQLITE",
                              "SELECT COUNT(*) c FROM world_memory WHERE content LIKE ?", ("%DREAM DITOLAK%",))
        check("baris penolakan dikenali dari isinya dan tidak dihitung sebagai putaran selesai",
              len(cls) >= 1 and refused_rows[0]["c"] >= 1
              and all("DREAM DITOLAK" not in (c["content"] or "")[:40] for c in cls),
              f"lulus={len(cls)} penolakan={refused_rows[0]['c']}")

        conn.execute("UPDATE autopilot_state SET mode='OFF'")
        conn.commit()
        n_now = wo.run(conn, "SQLITE", "SELECT COUNT(*) c FROM world_memory")[0]["c"]
        r5 = run(conn=conn, engine="SQLITE", quiet=True)
        n_after = wo.run(conn, "SQLITE", "SELECT COUNT(*) c FROM world_memory")[0]["c"]
        check("OFF menolak sebelum menulis satu baris pun",
              (not r5["ok"]) and r5.get("refused") == "MODE" and n_now == n_after, str(r5)[:160])
        conn.execute("UPDATE autopilot_state SET mode='ON'")
        conn.execute("DELETE FROM session_turns WHERE id = 99")
        conn.execute("DELETE FROM action_log")
        conn.execute("INSERT INTO action_log (ts, engine, kind) VALUES (?,?,?)", (_ago(45), "qoder", "command"))
        conn.commit()
        ok_obs, _ = wo.set_level(conn, "SQLITE", "observe", who="selftest", tty=lambda: False)
        robs = run(conn=conn, engine="SQLITE", quiet=True)
        check("level observe menulis digest TAPI tidak satu berkas pun",
              ok_obs and robs["ok"] and not robs["may_write_files"] and not robs["files"], str(robs)[:200])
        check("berkas handoff produksi tidak tersentuh selama selftest",
              sorted(x.name for x in Path(orig["PROJECTS"]).glob("handoff_dream_*")) == prod_projects,
              str(sorted(x.name for x in Path(orig["PROJECTS"]).glob("handoff_dream_*"))[:3]))
        check("berkas skill produksi tidak bertambah selama selftest",
              sorted(x.name for x in LEARNED.glob("*.md")) == prod_skills,
              str(sorted(x.name for x in LEARNED.glob("*.md"))))
    finally:
        for k, v in orig.items():
            globals()[k] = v
        ir.LEARNED_DIR, ir.SKILLS_CATALOG, ir.get_db_connection = ir_seams
        # Jangan percaya bahwa pelepasan terjadi — ukur. Selftest yang menyambat modul lalu bocor
        # meninggalkan keadaan di mana tes BERIKUTNYA berjalan di atas sambatan tes sebelumnya,
        # dan itu lulus tanpa membuktikan apa pun.
        leftover = [k for k, v in orig.items() if globals()[k] is not v]
        if ir.LEARNED_DIR != ir_seams[0] or ir.SKILLS_CATALOG != ir_seams[1]                 or ir.get_db_connection is not ir_seams[2]:
            leftover.append("incident_recorder")
        restore_leftover = leftover
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    checks.append(("setiap sambatan yang dipasang dilepas lagi, termasuk milik incident_recorder",
                   not restore_leftover, str(restore_leftover)))
    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(f"[{'ok' if ok else 'NO'}] {name}" + ("" if ok else f"   ← {detail}"))
    print(f"dream phase selftest: {len(checks) - len(failed)}/{len(checks)} lulus")
    return 0 if not failed else 1


def gates_report():
    conn, engine = wo.connect()
    try:
        out = {"mode": mode_gate(conn, engine), "idle": idle_gate(conn, engine),
               "tracked_readable": tracked_state() is not None}
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["mode"]["ok"] else 1
    finally:
        conn.close()


def has_digest_today(completed_only=True):
    """Keluar 0 bila ada baris digest bermeterai hari ini.

    Dengan `completed_only` penolakan gerbang TIDAK dihitung: baris "DREAM DITOLAK" membuktikan
    jalur emit hidup, tapi bukan bahwa satu putaran sungguh menata memori. Gerbang F10.5 menuntut
    yang kedua, karena cláusula ini adalah satu-satunya tempat ia mengukur dunia, bukan kode.
    """
    conn, engine = wo.connect()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        p = ph(engine)
        sql = (f"SELECT key FROM world_memory WHERE category = {p} AND key LIKE {p} "
               f"AND substr(CAST(updated_at AS TEXT), 1, 10) = {p}")
        if completed_only:
            # Penolakan dikenali dari judulnya, dan judul itu selalu didahului cap waktu, jadi
            # polanya `%...%` — versi `\[DREAM DITOLAK%` tidak pernah cocok dan diam-diam
            # menghitung penolakan sebagai putaran selesai.
            sql += " AND content NOT LIKE " + p
        rows = wo.run(conn, engine, sql + " LIMIT 1",
                      tuple([DIGEST_CATEGORY, f"{PREFIX_DIGEST}2%", today]
                            + (["%DREAM DITOLAK%"] if completed_only else [])))
        return 0 if rows else 1
    except Exception as exc:  # noqa: BLE001
        print(f"UNKNOWN: world_memory tidak terbaca ({str(exc)[:80]})")
        return 2
    finally:
        conn.close()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "selftest":
        return selftest()
    if cmd == "gates":
        return gates_report()
    if cmd == "has-digest":
        return has_digest_today()
    if cmd == "run":
        minutes = IDLE_MINUTES
        for a in argv[2:]:
            if a.startswith("--minutes="):
                minutes = int(a.split("=", 1)[1])
        res = run(force="--force" in argv, minutes=minutes)
        if res.get("ok"):
            return 0
        # 3 = ada yang menolak, dan penolakan itu adalah keputusan yang benar. Unit systemd
        # mendeklarasikan 0 dan 3 sebagai sukses; apa pun yang lain berarti putaran ini rusak.
        return 3 if res.get("refused") else 1
    print(f"perintah tidak dikenal: {cmd} (run|gates|selftest|has-digest)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
