#!/usr/bin/env bash
# Satu putaran dream phase (F10.5): menata ulang memori, memadatkan baris sesi yang menua,
# melaporkan utang dokumentasi, dan menjawab pola error yang terulang — sambil tidak ada yang menunggu.
#
# Dibungkus skrip dengan alasan yang sama seperti bin/autopilot_tick.sh: systemd user tidak mewarisi
# PATH shell, dan berkas ini `cd` ke akar repositori supaya `tools/dream_phase.py` serta gerbang
# `bash bin/arch_map.sh --check` menunjuk tempat yang benar.
set -uo pipefail

STATION="${STATION_DIR:-$HOME/.ai-station}"
cd "$STATION" || { echo "dream: tidak bisa masuk $STATION" >&2; exit 1; }

PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || { echo "dream: python3 tidak ditemukan di PATH systemd" >&2; exit 1; }

# Kode keluar dipilih oleh tools/dream_phase.py dan dijelaskan di unit-nya: 0 = bekerja,
# 3 = ditolak gerbang (mode OFF, atau mesin belum menganggur 30 menit), yang lain = rusak.
# Menelan semuanya menjadi 0 akan membuat timer yang selalu gagal tampak sehat selamanya.
exec "$PYTHON" tools/dream_phase.py run
