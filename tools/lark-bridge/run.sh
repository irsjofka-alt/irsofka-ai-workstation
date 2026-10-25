#!/usr/bin/env bash
set -euo pipefail

# Path dihitung dari lokasi skrip, bukan ditulis absolut: repo ini bisa dikloning
# ke direktori mana pun tanpa menyunting jembatan.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${LARK_BRIDGE_ENV:-$HOME/.config/lark-bridge.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
if [ -z "$NODE_BIN" ]; then
  NODE_BIN="$HOME/runtime/local/bin/node"
fi
exec "$NODE_BIN" "$HERE/index.js"
