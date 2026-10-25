#!/usr/bin/env bash
# Satu tick worker pembangunkan: baca ledger, bangunkan engine yang terbukti diam, lalu mati.
# Dibungkus berkas karena systemd tidak boleh memutuskan kebijakan — keputusannya ada di
# tools/awakener.py, dan unit ini hanya menyalakan jam.
set -uo pipefail
cd "$HOME/.ai-station" || exit 1
exec python3 tools/awakener.py qoder
