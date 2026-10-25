#!/usr/bin/env python3
"""Jalankan Cline (provider openai-native) untuk endpoint Nvidia memakai dua lane.

Setiap lane adalah data-dir Cline terpisah yang menyimpan SATU kunci Nvidia
(mode 0600, gitignored). Skrip ini memilih lane yang paling lama menganggur,
menegakkan batas 40 RPM per lane, lalu exec `cline` — kunci tidak pernah lewat
argv, stdout, atau state file.

Dipakai oleh engine `nvidia-tools` di config/engines.json.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATION = Path.home() / ".ai-station"
LANES_FILE = STATION / "engines" / "cline-nvidia-lanes.json"
LANE_DIRS = [
    STATION / "engines" / "cline-nvidia-1",
    STATION / "engines" / "cline-nvidia-2",
]
RPM_PER_LANE = 40
CLINE_FALLBACK = Path("/home/irsofka/runtime/local/node-v24.21.0-linux-x64/bin/cline")


def load_state() -> dict:
    try:
        return json.loads(LANES_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    LANES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LANES_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle)
    os.chmod(tmp, 0o600)
    tmp.replace(LANES_FILE)


def usable_lanes() -> list:
    lanes = []
    for index, directory in enumerate(LANE_DIRS, start=1):
        if (directory / "settings" / "providers.json").is_file():
            lanes.append((index, directory))
    return lanes


def pick_lane(state: dict, lanes: list) -> tuple:
    now = time.time()
    interval = 60.0 / RPM_PER_LANE
    best = None
    for index, directory in lanes:
        entry = state.get(str(index), {})
        recent = [t for t in entry.get("requests", []) if t > now - 60]
        last = float(entry.get("last", 0))
        wait = max(0.0, last + interval - now)
        if len(recent) >= RPM_PER_LANE:
            wait = max(wait, min(recent) + 60 - now)
        candidate = (wait, last, index, directory, recent)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return best


def main() -> int:
    lanes = usable_lanes()
    if not lanes:
        print("cline-nvidia: tidak ada lane terkonfigurasi", file=sys.stderr)
        return 1

    # Pilih + pesan slot di bawah kunci berkas supaya dua run paralel tidak
    # memilih lane yang sama pada saat yang sama.
    lock_path = LANES_FILE.with_suffix(".lock")
    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state()
        wait, _last, index, directory, recent = pick_lane(state, lanes)
        start_at = time.time() + wait
        state[str(index)] = {"last": start_at, "requests": recent + [start_at]}
        save_state(state)
        fcntl.flock(lock, fcntl.LOCK_UN)

    if wait > 0:
        print(f"[cline-lane] lane {index}: menunggu {wait:.1f}s (40 RPM)", file=sys.stderr)
        time.sleep(wait)

    cline = os.environ.get("CLINE_BIN") or shutil_which("cline") or str(CLINE_FALLBACK)
    base = [cline, "-P", "openai-native", "--data-dir", str(directory)]
    code, stderr_tail = run_cline(base + sys.argv[1:])
    # Nemotron kadang mengalirkan argumen tool yang gagal di-parse SDK
    # ("InputParam"); Cline menganggapnya fatal padahal run berikutnya biasanya
    # bersih. Ulangi SEKALI dengan instruksi memecah tool call, bukan berulang-
    # ulang: efek samping tool sebelumnya sudah terlihat oleh model.
    if code == 1 and "InputParam" in stderr_tail:
        print("[cline-lane] tool-call tidak terbaca; mengulang sekali dengan instruksi ringkas", file=sys.stderr)
        argv = list(sys.argv[1:])
        if argv:
            argv[-1] = argv[-1] + "\n\n" + RETRY_NOTE
        code, _ = run_cline(base + argv)
    return code


RETRY_NOTE = (
    "Panggil tepat satu tool per balasan. Jangan menyertakan teks apa pun pada "
    "balasan yang berisi tool call. Argumen harus persis mengikuti skema, dan "
    "pecah perubahan berkas besar menjadi beberapa langkah kecil."
)


def run_cline(cmd: list) -> tuple:
    """Jalankan cline sambil meneruskan stderr ke pemanggil dan menyimpan ekornya."""
    try:
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        message = f"cline tidak ditemukan: {cmd[0]}"
        print(f"[cline-lane] {message}", file=sys.stderr)
        return 127, message
    tail = []
    if proc.stderr is not None:
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
    return proc.wait(), "".join(tail)


def shutil_which(name: str) -> str:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


if __name__ == "__main__":
    sys.exit(main())
