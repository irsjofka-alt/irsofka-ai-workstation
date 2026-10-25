#!/usr/bin/env python3
"""
Irsofka AI Workstation - Native Local System MCP Server
Exposes Pop!_OS COSMIC Desktop, Hardware Telemetry (RTX 3060), Audio, Wayland Vision,
dan Game-State SQL Database ke Google Antigravity & Qoder melalui Model Context Protocol (MCP).
"""

import sys
import json
import os
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path

AI_STATION = Path.home() / ".ai-station"
BRAIN_DIR = AI_STATION / "brain"
LOGS_DIR = AI_STATION / "logs"
SCREENSHOT_PATH = LOGS_DIR / "current_screen.png"
DB_PATH = BRAIN_DIR / "workstation.db"

# Sumber kebenaran save-state: adapter dual-engine (PostgreSQL primer, SQLite fallback).
# Menghindari "split-brain" di mana MCP membaca SQLite sementara daemon Rust membaca PostgreSQL.
sys.path.insert(0, str(AI_STATION / "tools"))
from db_state import get_db_connection  # noqa: E402

READONLY_TABLES = {
    "player_profile", "skills_inventory", "quest_tasks", "world_memory",
    "incident_log", "session_turns", "action_log",
}


def db_ph(engine: str) -> str:
    return "%s" if engine == "POSTGRESQL" else "?"


STATION_PORT = int(os.environ.get("STATION_PORT", "8999"))


def station_api(path: str, method: str = "GET", timeout: float = 8.0, payload=None):
    """Panggil HTTP API daemon workstation itu sendiri.

    Sengguh lewat API, bukan subprocess systemctl: daemon sudah punya endpoint resmi untuk
    ini, jadi jalur izin dan pencatatan ke spool event tetap satu pintu. Kalau daemon mati,
    kita kembalikan error yang jujur, bukan pura-pura sukses.
    """
    url = f"http://127.0.0.1:{STATION_PORT}{path}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    elif method == "POST":
        data = b"{}"
    try:
        req = urllib.request.Request(url, method=method, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
        return True, body or "{}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

TOOLS = [
    {
        "name": "get_hardware_telemetry",
        "description": "Mengambil status hardware real-time: NVIDIA RTX 3060 VRAM, GPU Load, Suhu, RAM, CPU, dan sisa kapasitas NVMe SSD.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "take_screenshot_wayland",
        "description": "Mengambil screenshot desktop Pop!_OS COSMIC (Wayland) untuk inspeksi visual mata AI.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "send_desktop_notification",
        "description": "Mengirimkan notifikasi banner langsung ke layar desktop Pop!_OS COSMIC pengguna.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Judul notifikasi"},
                "message": {"type": "string", "description": "Isi pesan notifikasi"}
            },
            "required": ["title", "message"]
        }
    },
    {
        "name": "control_system_volume",
        "description": "Mengatur volume speaker/headphone Pop!_OS melalui PipeWire/PulseAudio pactl.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["up", "down", "mute", "unmute"], "description": "Aksi volume: up (+10%), down (-10%), mute, unmute"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "query_workstation_db",
        "description": "Membaca save-state dari PostgreSQL (fallback SQLite): profil, skill, quest, memori, insiden, turn, dan action_log (riwayat perintah AI untuk pemulihan sesi).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "enum": sorted(READONLY_TABLES)},
                "limit": {"type": "integer", "description": "Batas jumlah baris (default 10)"}
            },
            "required": ["table_name"]
        }
    },
    {
        "name": "update_quest_task",
        "description": "Mencatat atau memperbarui task/quest coding di database save-state workstation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Nama quest / task"},
                "status": {"type": "string", "enum": ["IN_PROGRESS", "COMPLETED", "FAILED"]},
                "model_assigned": {"type": "string", "description": "Model yang mengerjakan"},
                "summary": {"type": "string", "description": "Rangkuman hasil pekerjaan"}
            },
            "required": ["title", "status"]
        }
    },
    {
        "name": "refresh_workstation_ui",
        "description": "Memuat ulang jendela GUI Irsofka AI Workstation agar HTML/state terbaru tampil. Aman: hanya menyuruh halaman WebView reload, tidak menyentuh tab PTY maupun sesi CLI di dalamnya.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "restart_workstation_daemon",
        "description": "Restart daemon irsofka-ai-workstation.service lewat endpoint resminya. Sejak tab berjalan di tmux (-L irsofka), sesi Qoder/Antigravity TIDAK ikut mati. Pakai bila perubahan biner atau konfigurasi butuh dimuat ulang.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Alasan singkat restart, dicatat ke spool event workstation"
                }
            },
            "required": ["reason"]
        }
    },
    {
        "name": "read_terminal",
        "description": "Baca ISI LAYAR sebuah tab workstation (qoder | antigravity | shell) secara seketika lewat tmux capture-pane. Pakai ini untuk melihat jawaban mesin lain tanpa menunggu apa pun (~3 ms). Pasangkan dengan send_to_terminal untuk bertanya.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "enum": ["qoder", "antigravity", "shell"], "description": "tab yang dibaca"},
                "tail_lines": {"type": "integer", "description": "berapa baris terakhir diambil (default 60)"}
            },
            "required": ["tab"]
        }
    },
    {
        "name": "send_to_terminal",
        "description": "Ketik ke tab CLI workstation lalu tekan Enter di sana (CR, bukan LF — TUI tidak mengenali LF). Dipakai untuk meminta mesin lain meninjau pekerjaan, mis. Gemini. Balasnya dibaca dengan read_terminal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "enum": ["qoder", "antigravity", "shell"], "description": "tab tujuan"},
                "text": {"type": "string", "description": "isi yang diketik"},
                "submit": {"type": "boolean", "description": "tekan Enter setelah mengetik (default true)"}
            },
            "required": ["tab", "text"]
        }
    },
    {
        "name": "ask_peer",
        "description": "Tinggalkan permintaan verifikasi untuk AI LAIN di workstation ini lewat tabel bersama ai_message. Kedua CLI memakai server MCP yang sama, jadi Qoder bisa meminta pendapat Gemini dan sebaliknya. Balasnya dibaca dengan check_messages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "enum": ["qoder", "antigravity", "any"], "description": "mesin tujuan"},
                "from_engine": {"type": "string", "description": "siapa kamu: qoder atau antigravity"},
                "topic": {"type": "string", "description": "judul singkat"},
                "body": {"type": "string", "description": "yang perlu diperiksa + berkas/baris terkait"},
                "reply_to": {"type": "integer", "description": "id pesan yang dibalas (opsional)"}
            },
            "required": ["to", "from_engine", "body"]
        }
    },
    {
        "name": "check_messages",
        "description": "Baca kotak masuk AI di workstation ini (tabel ai_message). Pakai untuk menjawab permintaan verifikasi dari mesin lain, atau melihat apakah pasanganmu sudah membalas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "for_engine": {"type": "string", "enum": ["qoder", "antigravity", "all"], "description": "kotak masuk siapa"},
                "only_open": {"type": "boolean", "description": "hanya yang belum dijawab (default true)"},
                "limit": {"type": "integer", "description": "berapa pesan (default 10)"}
            },
            "required": ["for_engine"]
        }
    },
    {
        "name": "resolve_message",
        "description": "Tandai sebuah pesan ai_message sudah dijawab, supaya kotak masuk tidak penuh oleh permintaan basi.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["ANSWERED", "DISPUTED", "CANCELLED"]}
            },
            "required": ["message_id", "status"]
        }
    },
    {
        "name": "ask_user",
        "description": "Ajukan pertanyaan pilihan kepada operator lewat antrean Human Decide. "
                       "Mode header menentukan alurnya: OFF = manusia menjawab; SEMI = @Decider "
                       "memberi rekomendasi, manusia mengonfirmasi; AUTO = @Decider memutuskan "
                       "(irreversible tetap manusia). Opsi boleh string atau objek {label, evidence, "
                       "cost, recommended, recommended_reason}. Tool langsung mengembalikan "
                       "decision_id (non-blocking); gunakan check_user_answer untuk polling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "pertanyaan yang harus dijawab manusia"},
                "options": {
                    "type": "array",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "evidence": {"type": "string"},
                                    "cost": {"type": "string"},
                                    "recommended": {"type": "boolean"},
                                    "recommended_reason": {"type": "string"}
                                },
                                "required": ["label"]
                            }
                        ]
                    }
                },
                "weight": {"type": "string", "enum": ["heavy", "light", "irreversible"], "default": "heavy"},
                "item_id": {"type": "integer", "description": "quest id yang menunggu jawaban ini"}
            },
            "required": ["question"]
        }
    },
    {
        "name": "check_user_answer",
        "description": "Baca status dan jawaban satu pertanyaan Human Decide berdasarkan decision_id. "
                       "Gunakan berulang untuk polling; terminal=true berarti tidak perlu menunggu lagi.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"}
            },
            "required": ["decision_id"]
        }
    },
    {
        "name": "worker_context",
        "description": "Bangun context pack AI-Station untuk command `worker load` atau `worker recovery`. "
                       "load = seluruh konfigurasi, model (Nvidia/Qoder/Antigravity), MCP, worker, dan keadaan "
                       "sekarang; recovery = ledger/SQL/handoff terakhir + resume. Selalu pakai ini sebelum "
                       "melanjutkan pekerjaan di AI-Station.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["load", "recovery"]},
                "limit": {"type": "integer", "description": "jumlah event recovery, default 40"}
            },
            "required": ["mode"]
        }
    },
    {
        "name": "auto_pilot_task",
        "description": "Daftarkan satu Planning.md sebagai Auto Pilot Task. Task akan mengirim satu "
                       "putaran ke worker pilihan setiap 1 menit selama mode Auto Pilot ON, dengan "
                       "kebijakan worker sibuk: skip (lewati), queue (antrekan), atau replace (ganti). "
                       "Panggil ini ketika operator berkata 'tambahkan Planning.md ke auto pilot task'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_path": {"type": "string", "description": "path absolut berkas Planning.md"},
                "waker_id": {"type": "string", "description": "id worker (opsional)"},
                "waker": {"type": "string", "description": "nama worker, mis. Assistance Nvidia (opsional bila waker_id diisi)"},
                "name": {"type": "string", "description": "nama task (opsional)"},
                "overlap_policy": {"type": "string", "enum": ["skip", "queue", "replace"], "default": "skip"}
            },
            "required": ["plan_path"]
        }
    },
    {
        "name": "site_post",
        "description": "Tulis satu post ke situs Playground lewat tools/site/publish.py. "
                       "type=blog|showcase|generation|changelog|project-update. Post tanpa metadata "
                       "lengkap dan tanpa visibility=public tetap tersimpan sebagai local. Post yang "
                       "mengandung kunci API/token/password DITOLAK; email/telepon/path rumah diredaksi.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["blog", "showcase", "generation", "changelog", "project-update"]},
                "project": {"type": "string", "description": "slug project dari site_list_projects, mis. ai-station"},
                "title": {"type": "string"},
                "body_md": {"type": "string"},
                "summary": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "worker_id": {"type": "string"},
                "worker_name": {"type": "string"},
                "visibility": {"type": "string", "enum": ["local", "public"], "default": "local"},
                "media": {"type": "array", "items": {"type": "object"},
                          "description": "entri media; source_path absolut akan disalin + dihitung sha256"},
                "source": {"type": "object"}
            },
            "required": ["type", "project", "title"]
        }
    },
    {
        "name": "site_publish_media",
        "description": "Publikasikan satu berkas gambar/video/model/render: salin ke site/media/<project>/YYYY-MM/, "
                       "hitung sha256 (dedupe), lalu buat post showcase/generation yang membuatnya muncul di gallery.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "path absolut berkas sumber di mesin ini"},
                "project": {"type": "string"},
                "kind": {"type": "string", "enum": ["image", "video", "model", "render"]},
                "mode": {"type": "string", "enum": ["txt2img", "img2img", "txt2video", "img2video", "mesh", "render", "other"]},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "body_md": {"type": "string"},
                "post_type": {"type": "string", "enum": ["showcase", "generation", "project-update"]},
                "model": {"type": "string"},
                "prompt": {"type": "string"},
                "seed": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "worker_id": {"type": "string"},
                "worker_name": {"type": "string"},
                "visibility": {"type": "string", "enum": ["local", "public"], "default": "local"},
                "create_post": {"type": "boolean", "default": True}
            },
            "required": ["source_path", "project"]
        }
    },
    {
        "name": "site_list_projects",
        "description": "Daftar project situs Playground: slug, codename (@Project), workspace, dokumen acuan "
                       "beserta ukurannya, jumlah post, dan URL halaman.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "project_context",
        "description": "Muat konteks dokumen @Project (registry + seluruh .md terdaftar) untuk dibaca worker. "
                       "Di atas 400.000 karakter berkas wajib tetap utuh dan sisanya menjadi outline. "
                       "Pakai ini ketika pesan menyebut @ReadyAsset, @MeshCleanRoom, @AIStation, atau @ComfyUI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "slug atau codename, mis. meshcleanroom atau @MeshCleanRoom"},
                "max_chars": {"type": "integer", "default": 400000}
            },
            "required": ["project"]
        }
    }
]

def _work_order(args, timeout=60):
    script = AI_STATION / "tools" / "work_order.py"
    if not script.exists():
        return None, f"work_order.py tidak ada di {script}"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"work_order.py melewati batas waktu {timeout}s"
    text = (proc.stdout or "").strip()
    start = text.find("{")
    if start < 0:
        return None, ((proc.stderr or text) or "work_order.py tidak mengembalikan JSON")[:500]
    try:
        return json.loads(text[start:]), None
    except Exception as exc:  # noqa: BLE001
        return None, f"JSON work_order.py tidak terbaca: {exc}: {text[:300]}"


def _decision_row(decision_id):
    return _work_order(["decision", str(int(decision_id))], timeout=30)


def _ask_user(args):
    question = str(args.get("question") or "").strip()
    if not question:
        return json.dumps({"ok": False, "error": "question wajib diisi"}, ensure_ascii=False)
    raw_options = args.get("options") or []
    if isinstance(raw_options, (str, dict)):
        raw_options = [raw_options]
    options = []
    for raw in raw_options:
        if isinstance(raw, str):
            label = raw.strip()
            if label:
                options.append({"label": label})
        elif isinstance(raw, dict):
            label = str(raw.get("label") or "").strip()
            if not label:
                continue
            option = {"label": label}
            for key in ("evidence", "cost", "recommended_reason"):
                if raw.get(key):
                    option[key] = str(raw[key])[:1000]
            if raw.get("recommended"):
                option["recommended"] = True
            options.append(option)
    if options and len(options) < 2:
        return json.dumps(
            {"ok": False, "error": "options harus berisi minimal dua pilihan, atau kosong untuk jawaban bebas"},
            ensure_ascii=False,
        )
    weight = str(args.get("weight") or "heavy").strip().lower()
    if weight not in ("heavy", "light", "irreversible"):
        # Weight yang tidak dikenal tidak boleh masuk jalur otomatis; manusia yang memutuskan.
        weight = "irreversible"
    cmd = ["decide", question, "--weight", weight]
    if options:
        cmd += ["--options", json.dumps(options, ensure_ascii=False)]
    item_id = args.get("item_id")
    if item_id:
        try:
            cmd += ["--item", str(int(item_id))]
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": "item_id harus bilangan bulat"}, ensure_ascii=False)
    row, error = _work_order(cmd, timeout=60)
    if error:
        return json.dumps({"ok": False, "error": error}, ensure_ascii=False)
    decision_id = row.get("id") if isinstance(row, dict) else None
    try:
        decision_id = int(decision_id)
    except (TypeError, ValueError):
        decision_id = 0
    if decision_id <= 0:
        return json.dumps({
            "ok": False,
            "error": "work_order.py tidak mengembalikan decision id",
            "raw": row,
        }, ensure_ascii=False)
    mode_row, _ = _work_order(["decision-mode", "show"], timeout=30)
    mode = (mode_row or {}).get("mode") if isinstance(mode_row, dict) else None
    mode_label = mode if mode in ("OFF", "SEMI", "AUTO") else "OFF"
    action = None
    if mode_label == "SEMI":
        action = "recommend-decider"
    elif mode_label == "AUTO":
        action = "recommend-decider" if weight == "irreversible" else "resolve-decider"
    if action and len(options) < 2:
        # Pertanyaan bebas (0/1 opsi) tidak bisa dinilai Decider; manusia yang menjawab.
        action = None
    if action:
        def run_decider():
            _work_order([action, str(decision_id)], timeout=300)
        threading.Thread(target=run_decider, daemon=True).start()
    return json.dumps({
        "ok": True,
        "decision_id": decision_id,
        "state": row.get("state"),
        "waiting": True,
        "mode": mode_label,
        "decider_started": bool(action),
        "decider_action": action,
        "question": question,
        "options": options,
        "note": ("@Decider sedang memproses pertanyaan ini; poll check_user_answer."
                 if action else
                 ("Tanpa dua pilihan, @Decider tidak dipakai; jawab manual di /station atau console Human Decide."
                  if mode_label in ("SEMI", "AUTO") and len(options) < 2 else
                  "Jawab di /station atau console Human Decide; gunakan check_user_answer.")),
    }, ensure_ascii=False)


def _check_user_answer(args):
    decision_id = args.get("decision_id") or args.get("id")
    try:
        decision_id = int(decision_id)
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "decision_id wajib bilangan bulat"}, ensure_ascii=False)
    if decision_id <= 0:
        return json.dumps({"ok": False, "error": "decision_id harus bilangan bulat positif"}, ensure_ascii=False)
    row, error = _decision_row(decision_id)
    if error:
        return json.dumps({"ok": False, "decision_id": decision_id, "error": error}, ensure_ascii=False)
    if not row:
        return json.dumps({"ok": False, "decision_id": decision_id, "error": "decision tidak ditemukan"}, ensure_ascii=False)
    state = row.get("state")
    terminal = bool(row.get("answer")) or state in ("ANSWERED", "RESOLVED", "CANCELLED", "REFUSED")
    mode_row, _ = _work_order(["decision-mode", "show"], timeout=30)
    mode = (mode_row or {}).get("mode") if isinstance(mode_row, dict) else None
    if mode not in ("OFF", "SEMI", "AUTO"):
        mode = "OFF"
    try:
        option_count = len(json.loads(row.get("options") or "[]"))
    except (TypeError, ValueError):
        option_count = 0
    weight = (row.get("weight") or "").lower()
    # AUTO yang masih OPEN dengan dua opsi berarti Decider masih bekerja, bukan alarm.
    # Irreversible selalu milik manusia; mode yang gagal dibaca juga fail-safe ke manusia.
    needs_human = (not terminal) and (
        weight == "irreversible"
        or mode in ("OFF", "SEMI")
        or state in ("ESCALATED", "UNAVAILABLE")
        or option_count < 2
    )
    return json.dumps({
        "ok": True,
        "decision_id": decision_id,
        "state": state,
        "terminal": terminal,
        "needs_human": needs_human,
        "decision_mode": mode,
        "question": row.get("question"),
        "answer": row.get("answer"),
        "answered_by": row.get("answered_by"),
        "rung_used": row.get("rung_used"),
        "model_id": row.get("model_id"),
        "note": row.get("note"),
        "options": row.get("options"),
        "resolved_epoch": row.get("resolved_epoch"),
    }, ensure_ascii=False)


def _auto_pilot_task(args):
    raw_path = str(args.get("plan_path") or "").strip()
    if not raw_path:
        return json.dumps({"ok": False, "error": "plan_path wajib diisi"}, ensure_ascii=False)
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path)
    path = path.resolve()
    if not path.exists() or not path.is_file():
        return json.dumps({"ok": False, "error": f"Planning.md tidak ditemukan: {path}"}, ensure_ascii=False)
    if path.name.lower() != "planning.md":
        return json.dumps({
            "ok": False,
            "error": f"berkas harus bernama Planning.md, bukan {path.name}",
        }, ensure_ascii=False)
    overlap = str(args.get("overlap_policy") or "skip").lower()
    if overlap not in ("skip", "queue", "replace"):
        return json.dumps({"ok": False, "error": "overlap_policy harus skip, queue, atau replace"}, ensure_ascii=False)

    ok, body = station_api("/api/wakers")
    if not ok:
        return json.dumps({"ok": False, "error": f"daemon tidak bisa dibaca: {body}"}, ensure_ascii=False)
    try:
        wakers = json.loads(body).get("wakers") or []
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"daftar waker tidak terbaca: {exc}"}, ensure_ascii=False)

    waker_id = str(args.get("waker_id") or "").strip()
    waker_name = str(args.get("waker") or "").strip()
    if not waker_id:
        active = [w for w in wakers if w.get("status") == "active"]
        if not waker_name:
            names = ", ".join(sorted(w.get("name") or w.get("id") for w in active)) or "(tidak ada)"
            return json.dumps({
                "ok": False,
                "error": f"pilih worker: isi waker_id atau waker. Worker aktif: {names}",
            }, ensure_ascii=False)
        exact = [w for w in active if (w.get("name") or "").lower() == waker_name.lower()]
        partial = [w for w in active if waker_name.lower() in (w.get("name") or "").lower()]
        matches = exact or partial
        if len(matches) != 1:
            names = ", ".join(sorted(w.get("name") or w.get("id") for w in matches or active)) or "(tidak ada)"
            return json.dumps({
                "ok": False,
                "error": f"worker '{waker_name}' tidak unik/tidak ditemukan. Kandidat: {names}",
            }, ensure_ascii=False)
        waker_id = matches[0]["id"]
        waker_name = matches[0].get("name") or waker_id
    else:
        found = next((w for w in wakers if w.get("id") == waker_id), None)
        if not found:
            return json.dumps({"ok": False, "error": f"waker_id {waker_id} tidak ada"}, ensure_ascii=False)
        waker_name = found.get("name") or waker_id

    name = str(args.get("name") or "").strip() or path.parent.name or "Auto Pilot Task"
    prompt = "\n".join([
        f"[[auto-pilot-plan:{path}]]",
        f"Auto Pilot Task: {name}",
        "",
        f"Baca berkas Planning.md di {path}.",
        "Kerjakan SATU langkah berikutnya yang belum selesai — yang belum bertanda ✅ — dengan bukti nyata (berkas, commit, atau uji).",
        "Perbarui Planning.md: centang langkah yang sudah selesai dan tulis bukti singkat. Jangan mengubah tujuan atau menambah lingkup.",
        "Jangan menunggu perintah tambahan; ini putaran otomatis berikutnya.",
        'Jika SELURUH langkah sudah bertanda ✅, balas tepat satu baris "AUTO_PILOT_DONE: <ringkasan>" dan jangan mengerjakan hal lain.',
    ])
    ok, body = station_api("/api/automations", "POST", payload={
        "waker_id": waker_id,
        "name": name,
        "cron": "*/1 * * * *",
        "timezone": "local",
        "overlap_policy": overlap,
        "kind": "autopilot",
        "prompt": prompt,
    })
    if not ok:
        return json.dumps({"ok": False, "error": f"gagal membuat Auto Pilot Task: {body}"}, ensure_ascii=False)
    try:
        created = json.loads(body)
    except Exception:  # noqa: BLE001
        created = {"raw": body}
    mode_ok, mode_body = station_api("/api/auto-pilot-mode")
    mode = None
    if mode_ok:
        try:
            mode = json.loads(mode_body).get("mode")
        except Exception:  # noqa: BLE001
            mode = None
    return json.dumps({
        "ok": True,
        "task": created,
        "plan_path": str(path),
        "worker": waker_name,
        "every": "1 menit",
        "mode": mode,
        "note": ("Auto Pilot ON: putaran berjalan." if mode == "AUTO"
                 else "Task tersimpan, tetapi mode Auto Pilot masih OFF. Nyalakan di menu Auto Pilot Task."),
    }, ensure_ascii=False)



def _worker_context(args):
    mode = str(args.get("mode") or "load").lower()
    if mode not in ("load", "recovery"):
        return json.dumps({"ok": False, "error": "mode harus load atau recovery"}, ensure_ascii=False)
    limit = int(args.get("limit") or 40)
    ok, body = station_api("/api/worker/context", "POST", payload={"mode": mode, "limit": limit}, timeout=90)
    if not ok:
        return json.dumps({"ok": False, "error": f"context pack gagal: {body}"}, ensure_ascii=False)
    return body


def _site_script(script_name, argv, request=None, timeout=180):
    """Jalankan skrip situs sebagai proses lokal, bukan lewat daemon.

    Penegak aturannya tetap satu: publish.py. MCP hanya menyiapkan argv/berkas
    request, jadi menulis post tetap berhasil meski daemon sedang di-restart —
    dan daemon memakai skrip yang sama untuk endpoint HTTP-nya.
    """
    script = AI_STATION / "tools" / "site" / script_name
    if not script.is_file():
        return json.dumps({"ok": False, "error": f"skrip situs tidak ada: {script}"}, ensure_ascii=False)
    args = [sys.executable, str(script), *[str(a) for a in argv]]
    tmp_path = None
    try:
        if request is not None:
            fd, tmp_path = tempfile.mkstemp(prefix="site-request-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(request, handle, ensure_ascii=False)
            args += ["--request", tmp_path]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"{script_name} tidak selesai dalam {timeout}s"}, ensure_ascii=False)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    if (proc.stdout or "").strip():
        return proc.stdout.strip()
    return json.dumps({
        "ok": False,
        "error": f"{script_name} gagal (exit {proc.returncode})",
        "stderr": (proc.stderr or "").strip()[:800],
    }, ensure_ascii=False)


def handle_call_tool(name, args):
    if name == "get_hardware_telemetry":
        data = {}
        try:
            gpu_out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                text=True, timeout=2
            ).strip().split(",")
            if len(gpu_out) >= 5:
                data["gpu"] = {
                    "name": gpu_out[0].strip(),
                    "vram_used_mb": int(gpu_out[1].strip()),
                    "vram_total_mb": int(gpu_out[2].strip()),
                    "load_percent": int(gpu_out[3].strip()),
                    "temp_c": int(gpu_out[4].strip())
                }
        except Exception as e:
            data["gpu_error"] = str(e)

        try:
            mem_out = subprocess.check_output(["free", "-m"], text=True, timeout=2).splitlines()
            if len(mem_out) > 1:
                p = mem_out[1].split()
                data["ram"] = {"used_gb": round(int(p[2]) / 1024, 1), "total_gb": round(int(p[1]) / 1024, 1)}
        except Exception:
            pass

        try:
            d = subprocess.check_output(["df", "-h", "/"], text=True, timeout=2).splitlines()
            if len(d) > 1:
                dp = d[1].split()
                data["disk"] = {"free": dp[3], "total": dp[1]}
        except Exception:
            pass

        return json.dumps(data, indent=2)

    elif name == "take_screenshot_wayland":
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(["python3", str(AI_STATION / "tools" / "wayland_actor.py"), "screenshot"], stdout=subprocess.PIPE, text=True)
        return f"Screenshot berhasil diambil dan disimpan di {SCREENSHOT_PATH}. Log: {res.stdout.strip()}"

    elif name == "refresh_workstation_ui":
        ok, body = station_api("/api/ui/reload", "POST")
        if not ok:
            return f"GAGAL meminta muat ulang UI (daemon di port {STATION_PORT} tidak menjawab): {body}"
        return f"Permintaan muat ulang UI terkirim. {body}"

    elif name == "restart_workstation_daemon":
        reason = (args.get("reason") or "").strip()
        if not reason:
            return "DITOLAK: isi 'reason' dulu, supaya restart tercatat dan bisa ditelusuri."
        ok, body = station_api("/api/restart-server", "POST")
        if not ok:
            return f"GAGAL meminta restart daemon: {body}"
        return (f"Daemon di-restart (alasan: {reason}). {body}\n"
                "Tab qoder/antigravity/shell hidup di tmux dan tidak ikut mati. "
                "Verifikasi: systemctl --user is-active irsofka-ai-workstation.service")

    elif name == "read_terminal":
        tab = args.get("tab") or "qoder"
        tail = int(args.get("tail_lines") or 60)
        sock = os.environ.get("STATION_TMUX_SOCKET", "irsofka")
        try:
            res = subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", f"station-{tab}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=10)
        except FileNotFoundError:
            return "tmux tidak tersedia — tab workstation tidak bisa dibaca."
        if res.returncode != 0:
            return f"tab '{tab}' tidak dapat dibaca: {res.stderr.strip()[:200]}"
        screen = res.stdout
        body = [l.rstrip() for l in screen.splitlines() if l.strip()]
        busy = (any(ch in screen for ch in "⠁⠂⠄⡀⢀⠐⠈⠋⠙⠹⠸⠼⠴⠦⠇⠏⣾⣽⢿⣷")
                or "esc to cancel" in screen.lower())
        return (f"tab {tab} | {len(body)} baris konten | status={'BEKERJA' if busy else 'IDLE'}\n"
                + "\n".join(body[-tail:]))

    elif name == "send_to_terminal":
        tab = args.get("tab") or "antigravity"
        text = str(args.get("text") or "")
        submit = args.get("submit", True)
        if not text.strip():
            return "DITOLAK: teks kosong."
        sock = os.environ.get("STATION_TMUX_SOCKET", "irsofka")
        target = f"station-{tab}"
        try:
            # -l = literal, supaya karakter khusus tidak ditafsir sebagai nama tombol
            subprocess.run(["tmux", "-L", sock, "send-keys", "-t", target, "-l", text],
                           check=True, timeout=10)
            if submit:
                # Enter = CR. tmux menormalkannya sendiri; mengirim "\n" manual tidak akan
                # dianggap Enter oleh TUI (penyebab bug dispatch yang diperbaiki 2026-10-06).
                subprocess.run(["tmux", "-L", sock, "send-keys", "-t", target, "Enter"],
                               check=True, timeout=10)
        except FileNotFoundError:
            return "tmux tidak tersedia — tidak bisa mengirim ke tab."
        except subprocess.CalledProcessError as exc:
            return f"gagal mengirim ke tab '{tab}': {exc.stderr.strip()[:200] if exc.stderr else exc}"
        return (f"Terkirim ke tab {tab}{' + Enter' if submit else ''}. "
                f"Baca jawabannya dengan read_terminal(tab='{tab}').")

    elif name == "ask_peer":
        to = args.get("to") or "any"
        frm = (args.get("from_engine") or "").strip()
        body = (args.get("body") or "").strip()
        if not frm or not body:
            return "DITOLAK: 'from_engine' dan 'body' wajib diisi."
        try:
            conn, engine = get_db_connection()
            cur = conn.cursor()
            ph = db_ph(engine)
            cur.execute(
                f"INSERT INTO ai_message (from_engine, to_engine, topic, body, reply_to) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}) RETURNING id"
                if engine == "POSTGRESQL" else
                f"INSERT INTO ai_message (from_engine, to_engine, topic, body, reply_to) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                (frm[:32], to[:32], (args.get("topic") or "")[:120], body,
                 args.get("reply_to")))
            mid = None
            try:
                row = cur.fetchone()
                mid = row[0] if row else None
            except Exception:  # noqa: BLE001
                mid = None
            conn.commit()
            conn.close()
            return (f"Pesan terkirim ke {to} (id={mid}). Balas dengan check_messages "
                    f"lagi nanti, lalu resolve_message(id, 'ANSWERED').")
        except Exception as exc:  # noqa: BLE001
            return f"GAGAL mengirim pesan: {exc}"

    elif name == "check_messages":
        who = args.get("for_engine") or "all"
        only_open = args.get("only_open", True)
        limit = int(args.get("limit") or 10)
        try:
            conn, engine = get_db_connection()
            cur = conn.cursor()
            ph = db_ph(engine)
            q = (f"SELECT id, ts, from_engine, topic, left(body,600), status FROM ai_message "
                 f"WHERE to_engine = {ph} OR to_engine = 'any'")
            params = [who]
            if who == "all":
                q = ("SELECT id, ts, from_engine, topic, left(body,600), status FROM ai_message")
                params = []
            if only_open:
                q += f" AND status = {ph}"
                params.append("OPEN")
            q += f" ORDER BY id DESC LIMIT {ph}"
            params.append(limit)
            cur.execute(q, tuple(params))
            rows = list(cur.fetchall())
            conn.close()
            if not rows:
                return f"Kotak masuk {who} kosong" + (" (tidak ada yang OPEN)" if only_open else "") + "."
            out = [f"{len(rows)} pesan untuk {who}:"]
            for rid, ts, frm, topic, body, status in reversed(rows):
                out.append(f"  #{rid} [{str(ts)[:16]}] dari {frm} — {topic or '(tanpa judul)'} [{status}]")
                for line in (body or "").splitlines()[:12]:
                    out.append(f"      {line}")
            return "\n".join(out)
        except Exception as exc:  # noqa: BLE001
            return f"GAGAL membaca kotak masuk: {exc}"

    elif name == "resolve_message":
        mid = args.get("message_id")
        status = args.get("status") or "ANSWERED"
        if not mid:
            return "DITOLAK: message_id wajib diisi."
        try:
            conn, engine = get_db_connection()
            cur = conn.cursor()
            cur.execute(f"UPDATE ai_message SET status = {db_ph(engine)} WHERE id = {db_ph(engine)}",
                        (status[:16], int(mid)))
            conn.commit()
            n = cur.rowcount
            conn.close()
            return f"Pesan #{mid} ditandai {status} ({n} baris)."
        except Exception as exc:  # noqa: BLE001
            return f"GAGAL memperbarui pesan: {exc}"

    elif name == "ask_user":
        return _ask_user(args)

    elif name == "check_user_answer":
        return _check_user_answer(args)

    elif name == "auto_pilot_task":
        return _auto_pilot_task(args)
    elif name == "worker_context":
        return _worker_context(args)

    elif name == "site_post":
        fields = ("type", "project", "title", "body_md", "summary", "tags", "worker_id",
                  "worker_name", "visibility", "media", "source")
        post = {key: args[key] for key in fields if args.get(key) not in (None, "")}
        if not post.get("title") or not post.get("project"):
            return json.dumps({"ok": False, "error": "title dan project wajib diisi"}, ensure_ascii=False)
        public = str(post.get("visibility") or "").lower() == "public"
        return _site_script("publish.py", ["post"], {"post": post, "public": public})

    elif name == "site_publish_media":
        fields = ("source_path", "project", "kind", "mode", "title", "summary", "body_md",
                  "post_type", "model", "prompt", "seed", "tags", "worker_id", "worker_name",
                  "visibility", "create_post", "source")
        request = {key: args[key] for key in fields if args.get(key) not in (None, "")}
        if not request.get("source_path") or not request.get("project"):
            return json.dumps({"ok": False, "error": "source_path dan project wajib diisi"}, ensure_ascii=False)
        request["public"] = str(request.get("visibility") or "").lower() == "public"
        return _site_script("publish.py", ["media"], request)

    elif name == "site_list_projects":
        return _site_script("publish.py", ["projects"])

    elif name == "project_context":
        wanted = str(args.get("project") or "").strip()
        if not wanted:
            return json.dumps({"ok": False, "error": "project wajib diisi"}, ensure_ascii=False)
        max_chars = int(args.get("max_chars") or 400000)
        return _site_script("project_context.py", ["--project", wanted, "--max-chars", max_chars])

    elif name == "send_desktop_notification":
        title = args.get("title", "Irsofka AI Workstation")
        msg = args.get("message", "")
        subprocess.Popen(["notify-send", "-a", "Irsofka AI Workstation", title, msg])
        return f"Notifikasi terkirim ke desktop: '{title} - {msg}'"

    elif name == "control_system_volume":
        action = args.get("action", "up")
        if action == "up":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"])
            return "Volume dinaikkan +10%"
        elif action == "down":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"])
            return "Volume diturunkan -10%"
        elif action == "mute":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"])
            return "Speaker di-mute"
        elif action == "unmute":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
            return "Speaker di-unmute"
        return "Aksi tidak dikenali"

    elif name == "query_workstation_db":
        table = args.get("table_name", "skills_inventory")
        limit = int(args.get("limit", 10) or 10)
        if table not in READONLY_TABLES:
            return json.dumps({"error": f"tabel tidak diizinkan: {table}"})
        conn, engine = get_db_connection()
        try:
            cur = conn.cursor()
            # action_log & session_turns bertumpuk tiap detik: ambil yang TERBARU, bukan paling tua.
            order = "ORDER BY id DESC" if table in ("action_log", "session_turns", "quest_tasks") else ""
            cur.execute(f"SELECT * FROM {table} {order} LIMIT {db_ph(engine)}", (limit,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return json.dumps({"db_engine": engine, "rows": rows}, indent=2, default=str)

    elif name == "update_quest_task":
        title = args.get("title")
        status = args.get("status", "IN_PROGRESS")
        model = args.get("model_assigned", "Qwen/Gemini")
        summary = args.get("summary", "")
        if not title:
            return "Gagal: argumen 'title' wajib diisi."
        conn, engine = get_db_connection()
        ph = db_ph(engine)
        try:
            cur = conn.cursor()
            done = "CURRENT_TIMESTAMP" if status == "COMPLETED" else "NULL"
            if engine == "POSTGRESQL":
                cur.execute(f"""
                INSERT INTO quest_tasks (title, status, model_assigned, cli_engine, summary, completed_at)
                VALUES ({ph}, {ph}, {ph}, 'qoder', {ph}, {done});
                """, (title, status, model, summary))
            else:
                cur.execute("""
                INSERT INTO quest_tasks (title, status, model_assigned, cli_engine, summary, completed_at)
                VALUES (?, ?, ?, 'qoder', ?, CASE WHEN ? = 'COMPLETED' THEN CURRENT_TIMESTAMP ELSE NULL END);
                """, (title, status, model, summary, status))
            conn.commit()
            cur.close()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return f"Gagal mencatat quest: {exc}"
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return f"Quest Task '{title}' status '{status}' berhasil dicatat di save-state {engine}."

    # Tool tak dikenal harus sampai ke pemanggil sebagai error: station hanya
    # membaca `isError` dari hasil MCP, jadi teks biasa akan terbaca "sukses".
    raise ValueError(f"Tool {name} tidak ditemukan.")

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "irsofka-local-workstation-mcp",
                        "version": "1.0.0"
                    }
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "ping":
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": TOOLS
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                content_text = handle_call_tool(tool_name, tool_args)
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(content_text)}]
                    }
                }
            except Exception as e:
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True
                    }
                }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        else:
            # BUG YANG MEMBUAT ANTIGRAVITY TIDAK MELIHAT SATU PUN TOOL:
            # klien mengirim request dengan "id" dan BERHARAP ada jawaban. Kalau kita diam,
            # ia menunggu sampai handshake timeout, lalu menganggap server ini gagal.
            # Qoder tidak kena karena tidak memanggil metode di luar tools/*, Antigravity memanggil.
            if msg_id is None:
                continue  # notifikasi: memang tidak boleh dibalas
            if method == "ping":
                result = {}
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method in ("resources/subscribe", "resources/unsubscribe", "logging/setLevel"):
                result = {}
            else:
                result = None
            if result is not None:
                res = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            else:
                res = {"jsonrpc": "2.0", "id": msg_id,
                       "error": {"code": -32601, "message": f"method not found: {method}"}}
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
