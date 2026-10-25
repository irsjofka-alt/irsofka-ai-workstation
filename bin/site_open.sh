#!/usr/bin/env bash
# Buka AI Station Playground. Build dulu (idempoten), lalu buka lewat daemon
# 127.0.0.1; bila daemon mati, buka berkas lokal supaya situs tetap terlihat.
set -euo pipefail

ROOT="${HOME}/.ai-station"
PORT="${STATION_PORT:-8999}"
URL="http://127.0.0.1:${PORT}/site/"

open_target() {
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" >/dev/null 2>&1 &
  elif command -v gio >/dev/null 2>&1; then
    gio open "$1" >/dev/null 2>&1 &
  else
    echo "Buka manual: $1"
  fi
}

python3 "$ROOT/tools/site/build.py" --quiet

if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/stats" >/dev/null 2>&1; then
  echo "Membuka ${URL}"
  open_target "$URL"
else
  echo "Daemon belum aktif di 127.0.0.1:${PORT}; membuka berkas lokal."
  echo "Untuk route /site, jalankan: ${ROOT}/bin/launch_gui.sh"
  open_target "${ROOT}/site/index.html"
fi
