#!/usr/bin/env python3
"""worker_context.py — context pack for the `worker load` and `worker recovery` commands.

Output is JSON so the console/daemon can hand the same pack to Nvidia, Qoder,
Antigravity, or any other worker without re-implementing the collector.

`load` answers: what is connected, which models are ready, what is configured,
what is running now. `recovery` answers: what was the last work, what is still
open, and where do I resume. Neither command prints secrets: API keys are only
reported as present/absent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".ai-station"
API = "http://127.0.0.1:8999"
CORE_DOCS = ["README.md", "ROADMAP.md", "AUTO_PILOT.md", "Planning.md", "INSTALL.md"]

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bnvapi-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(0).split(":")[0].split("=")[0] + ": [REDACTED]", text)
    return text


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def http_json(path: str, timeout: float = 8.0):
    try:
        with urllib.request.urlopen(API + path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace")), None
    except Exception as exc:  # noqa: BLE001 — caller needs the fact, not a stack trace
        return None, f"{type(exc).__name__}: {exc}"


def run_cmd(cmd: list[str], timeout: float = 30.0):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return 1, "", f"{type(exc).__name__}: {exc}"


def read_doc(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(tidak terbaca: {exc})"
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[TRUNCATED]"
    return redact(text)


def doc_entry(rel: str, max_chars: int) -> dict:
    path = ROOT / rel
    if not path.exists():
        return {"path": rel, "exists": False}
    data = path.read_bytes()
    return {
        "path": rel,
        "exists": True,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "excerpt": read_doc(path, max_chars),
    }


def readiness() -> list[dict]:
    rows: list[dict] = []

    def add(item: str, status: str, evidence: str) -> None:
        rows.append({"item": item, "status": status, "evidence": redact(evidence)})

    stats, err = http_json("/api/stats")
    add("daemon", "READY" if stats else "BLOCKED", "API 127.0.0.1:8999" if stats else str(err))

    automations, err = http_json("/api/automations")
    backend = (automations or {}).get("backend") if isinstance(automations, dict) else None
    add("storage", "READY" if backend else "UNKNOWN", f"backend={backend}" if backend else str(err))

    engines, err = http_json("/api/engines")
    engine_list = (engines or {}).get("engines") or []
    keys = [e.get("key") for e in engine_list if isinstance(e, dict)]
    add("engines", "READY" if keys else "UNKNOWN", f"{len(keys)} engine: {', '.join(keys[:12])}")

    wakers, err = http_json("/api/wakers")
    waker_list = (wakers or {}).get("wakers") or []
    active = [w for w in waker_list if w.get("status") == "active"]
    add("workers", "READY" if active else "DEGRADED", f"{len(active)}/{len(waker_list)} active")

    qoder, err = http_json("/api/qoder/account")
    q = (qoder or {}).get("qoder") or {}
    add("qoder", "READY" if q.get("logged_in") else "DEGRADED",
        f"version={q.get('version')} logged_in={q.get('logged_in')}" if q else str(err))

    agy, err = http_json("/api/antigravity/account")
    agy_data = agy or {}
    agy_info = agy_data.get("antigravity") if isinstance(agy_data, dict) else None
    agy_info = agy_info or agy_data
    agy_ok = bool(agy_info and (agy_info.get("logged_in") or agy_info.get("status") == "authenticated"))
    agy_state = (agy_info or {}).get("status") or (agy_info or {}).get("account_hint") or "tidak tersedia"
    add("antigravity", "READY" if agy_ok else "UNKNOWN", f"status={agy_state}" if agy_ok else (str(err) or f"status={agy_state}"))

    nvidia_path = ROOT / "config" / "nvidia.json"
    if nvidia_path.exists():
        try:
            nvidia = json.loads(nvidia_path.read_text(encoding="utf-8"))
            lane_count = len(nvidia.get("lanes") or []) if isinstance(nvidia.get("lanes"), list) else 0
            has_key = bool(str(nvidia.get("api_key") or "").strip())
            add("nvidia", "READY" if has_key else "BLOCKED",
                f"key={'present' if has_key else 'missing'} lanes={lane_count or 1} base={nvidia.get('base_url') or 'default'}")
        except Exception as exc:  # noqa: BLE001
            add("nvidia", "UNKNOWN", f"config tidak terbaca: {exc}")
    else:
        add("nvidia", "BLOCKED", "config/nvidia.json tidak ada")

    comfy, comfy_err = None, None
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=3) as response:
            comfy = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        comfy_err = f"{type(exc).__name__}: {exc}"
    add("comfyui", "READY" if comfy else "DEGRADED",
        "system_stats menjawab" if comfy else f"tidak berjalan: {comfy_err}")

    rc, out, err = run_cmd(["blender", "--version"], timeout=15)
    first = out.splitlines()[0] if out else err.splitlines()[0] if err else ""
    add("blender", "READY" if rc == 0 else "BLOCKED", first or f"rc={rc}")

    mcp, err = http_json("/api/mcp/tools")
    count = (mcp or {}).get("count") if isinstance(mcp, dict) else None
    add("mcp", "READY" if count else "UNKNOWN", f"{count} tools" if count is not None else str(err))

    site_ready = (ROOT / "site" / "index.html").exists()
    add("site", "READY" if site_ready else "BLOCKED",
        "site/index.html ada" if site_ready else "site/ belum dibangun")

    return rows


def load_pack(max_doc_chars: int) -> dict:
    warnings: list[str] = []
    docs = [doc_entry(rel, max_doc_chars) for rel in CORE_DOCS]
    rows = readiness()
    state = {
        "autopilot": http_json("/api/autopilot")[0],
        "runs": http_json("/api/runs")[0],
        "automations": http_json("/api/automations")[0],
        "decision_mode": http_json("/api/decision-mode")[0],
        "auto_pilot_mode": http_json("/api/auto-pilot-mode")[0],
    }
    running = [
        r for r in ((state["runs"] or {}).get("runs") or [])
        if r.get("status") in ("running", "queued")
    ]
    open_decisions = (state["autopilot"] or {}).get("decisions_open") or []
    enabled_automations = [
        a for a in ((state["automations"] or {}).get("automations") or []) if a.get("enabled")
    ]
    mode = (state["decision_mode"] or {}).get("mode")
    ap_mode = (state["auto_pilot_mode"] or {}).get("mode")

    context = [
        "# WORKER LOAD — konteks AI Station",
        f"Dibuat: {now()}",
        f"Root: {ROOT}",
        "",
        "## Readiness",
        "| Item | Status | Bukti |",
        "|---|---|---|",
    ]
    context += [f"| {r['item']} | {r['status']} | {r['evidence']} |" for r in rows]
    context += [
        "",
        "## Keadaan sekarang",
        f"- Human Decide terbuka: {len(open_decisions)}",
        f"- Run berjalan/queued: {len(running)}",
        f"- Automation aktif: {len(enabled_automations)}",
        f"- Mode keputusan: {mode or 'UNKNOWN'}",
        f"- Mode Auto Pilot: {ap_mode or 'UNKNOWN'}",
        "",
        "## Dokumen inti",
    ]
    for doc in docs:
        if not doc.get("exists"):
            context.append(f"### {doc['path']} — TIDAK ADA")
            continue
        context.append(
            f"### {doc['path']} — {doc['bytes']} byte, sha {doc['sha256']}\n\n{doc['excerpt']}"
        )
    context.append(
        "\n## Instruksi worker\n"
        "Gunakan konteks di atas sebagai keadaan nyata. Jangan mengaku model terhubung "
        "tanpa status READY. Jika DEGRADED/BLOCKED, sebutkan alasannya dan lanjutkan dengan "
        "yang READY. Untuk tugas besar, baca `Planning.md` dan kerjakan per fase."
    )
    return {
        "ok": True,
        "mode": "load",
        "generated_at": now(),
        "readiness": rows,
        "warnings": warnings,
        "context": redact("\n".join(context)),
    }


def recovery_pack(limit: int) -> dict:
    warnings: list[str] = []
    rec, rec_err = http_json(f"/api/recovery?limit={limit}", timeout=20)
    runs, runs_err = http_json("/api/runs", timeout=10)
    ap, ap_err = http_json("/api/autopilot", timeout=15)
    autos, autos_err = http_json("/api/automations", timeout=10)
    if rec_err:
        warnings.append(f"recovery API: {rec_err}")
    if runs_err:
        warnings.append(f"runs API: {runs_err}")
    if ap_err:
        warnings.append(f"autopilot API: {ap_err}")
    if autos_err:
        warnings.append(f"automations API: {autos_err}")

    rc, out, err = run_cmd(["python3", str(ROOT / "tools" / "work_order.py"), "status"], timeout=30)
    wo = None
    if rc == 0 and out:
        try:
            wo = json.loads(out[out.find("{"):])
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"work_order status tidak terbaca: {exc}")
    else:
        warnings.append(f"work_order status gagal: {err or rc}")

    rc_git, head, err_git = run_cmd(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], timeout=10)
    rc_status, status, _ = run_cmd(["git", "-C", str(ROOT), "status", "--short"], timeout=10)
    rc_log, log, _ = run_cmd(["git", "-C", str(ROOT), "log", "--oneline", "-8"], timeout=10)

    recent_runs = (runs or {}).get("runs") or []
    running = [r for r in recent_runs if r.get("status") in ("running", "queued")]
    terminal = [r for r in recent_runs if r.get("status") not in ("running", "queued")][:10]
    open_decisions = (ap or {}).get("decisions_open") or []
    enabled_automations = [
        a for a in ((autos or {}).get("automations") or []) if a.get("enabled")
    ]
    events = (rec or {}).get("events") or []
    handoffs = (rec or {}).get("handoff_memory") or []
    active_quests = (rec or {}).get("active_quests") or []

    handoff_files = sorted(
        (ROOT / "brain" / "memory" / "projects").glob("handoff_auto_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:2]

    context = [
        "# WORKER RECOVERY — konteks terakhir AI Station",
        f"Dibuat: {now()}",
        f"Root: {ROOT}",
        "",
        "## Git",
        f"- HEAD: {head or 'UNKNOWN'}",
        f"- Status kotor: {'ya' if status else 'bersih' if rc_status == 0 else 'UNKNOWN'}",
        "```",
        status[:3000] if status else "(bersih)",
        "```",
        "```",
        log[:2000] if log else "(tidak ada log)",
        "```",
        "",
        "## Pekerjaan/ledger",
        f"- Quest aktif: {len(active_quests)}",
        f"- Human Decide terbuka: {len(open_decisions)}",
        f"- Run berjalan/queued: {len(running)}",
        f"- Automation aktif: {len(enabled_automations)}",
    ]
    if wo:
        context += [
            f"- work_order mode: {wo.get('mode') or wo.get('autopilot', {}).get('mode') or 'UNKNOWN'}",
            f"- work_order engine: {wo.get('engine') or 'UNKNOWN'}",
        ]
    context += ["", "## Run terakhir"]
    for run in terminal:
        context.append(
            f"- {run.get('id')} · {run.get('status')} · {run.get('waker_id')} · "
            f"{(run.get('content') or '')[:120]}"
        )
    if not terminal:
        context.append("- (tidak ada run terminal)")
    context += ["", "## Human Decide terbuka"]
    for decision in open_decisions[:8]:
        context.append(f"- #{decision.get('id')} [{decision.get('state')}] {str(decision.get('question') or '')[:180]}")
    if not open_decisions:
        context.append("- (tidak ada)")
    context += ["", "## Automation aktif"]
    for automation in enabled_automations[:10]:
        context.append(
            f"- {automation.get('name')} · {automation.get('cron')} · "
            f"{automation.get('waker_name') or automation.get('waker_id')} · last={automation.get('last_status')}"
        )
    if not enabled_automations:
        context.append("- (tidak ada)")
    context += ["", "## Handoff terakhir"]
    for handoff in handoffs[:3]:
        content = str(handoff.get("content") or "")
        context.append(f"- {handoff.get('category') or 'handoff'}: {content[:1200]}")
    if not handoffs:
        context.append("- (tidak ada handoff di API)")
    for path in handoff_files:
        context.append(f"- berkas: {path} ({path.stat().st_size} byte)")
    context += ["", "## Event sesi terakhir"]
    for event in events[:12]:
        context.append(
            f"- {event.get('ts')} · {event.get('kind')} · {event.get('engine')} · "
            f"{str(event.get('summary') or '')[:160]}"
        )
    context += [
        "",
        "## Resume",
        "1. Baca `Planning.md` dan item yang masih terbuka.",
        "2. Jangan ulangi pekerjaan yang sudah COMPLETED di ledger.",
        "3. Mulai dari run/quest yang berstatus running/queued atau Human Decide yang terbuka.",
        "4. Tulis bukti berupa id, path, commit, atau angka sebelum menutup task.",
    ]
    return {
        "ok": True,
        "mode": "recovery",
        "generated_at": now(),
        "limit": limit,
        "warnings": warnings,
        "context": redact("\n".join(context)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Context pack for worker load/recovery")
    sub = parser.add_subparsers(dest="mode", required=True)
    load = sub.add_parser("load")
    load.add_argument("--max-doc-chars", type=int, default=6000)
    recovery = sub.add_parser("recovery")
    recovery.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    if args.mode == "load":
        pack = load_pack(max(2000, min(args.max_doc_chars, 20000)))
    else:
        pack = recovery_pack(max(5, min(args.limit, 200)))
    sys.stdout.write(json.dumps(pack, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
