#!/usr/bin/env python3
"""Stop ComfyUI after it has been idle for a configurable period.

The guard reads the local ComfyUI queue, records the last time a job was seen
running or pending, and after `--idle-seconds` (default 300) releases VRAM with
`POST /free` and stops the server with `comfy stop --port 8188`. A busy queue
always resets the timer, so long generations are never interrupted.

Run it from a systemd user timer once per minute. It is a no-op when ComfyUI is
not listening, and it never starts ComfyUI.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STATION = Path(os.environ.get("AI_STATION_HOME", Path.home() / ".ai-station"))
STATE = STATION / "data" / "comfy_idle.json"
LOG = STATION / "logs" / "comfy-idle-guard.log"
URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
PORT = int(os.environ.get("COMFYUI_PORT", "8188"))


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def http_json(
    method: str, path: str, body: dict | None = None, timeout: float = 3.0
) -> dict | None:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError):
        return None


def queue_state() -> tuple[bool | None, int, int]:
    payload = http_json("GET", "/queue")
    if payload is None:
        return None, 0, 0
    running = payload.get("queue_running") or []
    pending = payload.get("queue_pending") or []
    return bool(running or pending), len(running), len(pending)


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(last_busy: float) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"last_busy": last_busy}), encoding="utf-8")


def clear_state() -> None:
    try:
        STATE.unlink()
    except OSError:
        pass


def stop_comfy(dry_run: bool) -> bool:
    if dry_run:
        # Dry-run tidak boleh menyentuh server hidup: /free melepas model dan
        # VRAM. Cukup laporkan apa yang akan dilakukan.
        log(f"dry-run: akan POST /free dan menjalankan `comfy stop --port {PORT}`")
        return True

    free = http_json(
        "POST", "/free", {"unload_models": True, "free_memory": True}, timeout=10
    )
    if free is None:
        log("POST /free tidak menjawab; lanjut ke stop")
    else:
        log(f"POST /free: {json.dumps(free)[:200]}")

    cli = shutil.which("comfy")
    if not cli:
        log("comfy CLI tidak ditemukan; tidak bisa stop")
        return False
    try:
        process = subprocess.run(
            [cli, "--skip-prompt", "--where", "local", "stop", "--port", str(PORT)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        log("comfy stop melewati 60 detik")
        return False
    output = ((process.stdout or "") + (process.stderr or "")).strip()
    log(f"comfy stop exit={process.returncode}: {output[-300:]}")
    return process.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop idle ComfyUI to release VRAM")
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=int(os.environ.get("COMFY_IDLE_SECONDS", "300")),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    busy, running, pending = queue_state()
    if busy is None:
        log(f"ComfyUI tidak listening di {URL}; tidak ada yang perlu dihentikan")
        clear_state()
        return 0

    now = time.time()
    if busy:
        write_state(now)
        log(f"ComfyUI sibuk (running={running}, pending={pending}); timer di-reset")
        return 0

    state = read_state()
    if "last_busy" not in state:
        write_state(now)
        log(f"ComfyUI idle; timer {args.idle_seconds} detik dimulai")
        return 0

    last_busy = float(state.get("last_busy") or now)
    idle = now - last_busy
    if idle < args.idle_seconds:
        log(f"ComfyUI idle {int(idle)}s < {args.idle_seconds}s; tunggu")
        return 0

    log(f"ComfyUI idle {int(idle)}s >= {args.idle_seconds}s; lepas VRAM dan stop")
    ok = stop_comfy(args.dry_run)
    if ok and not args.dry_run:
        clear_state()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
