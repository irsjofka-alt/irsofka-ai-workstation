#!/usr/bin/env bash
# Deploy (or roll back) the freshly built IRSofkaStationCore binaries into the engine install.
#
# Why this exists as a script and not as a paragraph: the plugin fixes are compiled from the repo and
# verified by `RunUAT BuildPlugin`, but they are inert until the editor loads them — and loading them
# means touching the operator's live session, which is a human action (decision 55, row 301 HUMAN).
# A human still has to DECIDE; this only makes the deciding cheap, reversible, and evidence-producing
# instead of a chain of commands typed from memory.
#
# It refuses to run when a editor is live unless --force is given, and it never deletes the backup.
set -euo pipefail

ENGINE="${UE_ENGINE:-$HOME/Apps/LinuxUnrealEngine5.8.3}"
PLUGIN_NAME="IRSofkaStationCore"
DEST="$ENGINE/Engine/Plugins/Marketplace/$PLUGIN_NAME"
BUILD="${1:?usage: deploy_ue_plugin.sh <built-plugin-dir> [--deploy|--restore] [--force]}"
MODE="${2:---inspect}"

[ -d "$DEST" ] || { echo "deploy: tujuan tidak ada: $DEST" >&2; exit 1; }
SRC_BIN="$BUILD/Binaries/Linux"
[ -d "$SRC_BIN" ] || { echo "deploy: build ini tidak punya Binaries/Linux: $SRC_BIN" >&2; exit 1; }

stamp() { date +%m%d%H%M; }

echo "== yang akan diganti ($DEST/Binaries/Linux) =="
if [ -d "$DEST/Binaries/Linux" ]; then
  ( cd "$DEST/Binaries/Linux" && md5sum *.so 2>/dev/null | sort ) || echo "  (kosong)"
else
  echo "  (belum ada)"
fi
echo "== yang tersedia dari build =="
( cd "$SRC_BIN" && md5sum *.so | sort )

case "$MODE" in
  --inspect)
    echo
    echo "hanya membaca — tidak ada yang disentuh. Jalankan ulang dengan --deploy untuk memasang."
    exit 0 ;;

  --deploy)
    # `pgrep -f` cocok pada baris perintah yang MEMUAT kata itu — termasuk shell yang
    # menjalankan skrip ini. Nama proses persis adalah satu-satunya pembacaan yang tidak
    # membalik keadaan: editor hidup saat tidak ada proses bernama UnrealEditor sama sekali.
    if [ -n "$(pgrep -x UnrealEditor || true)" ] && [ "${3:-}" != "--force" ]; then
      echo "deploy: UnrealEditor sedang HIDUP. Memasang modul di sesi yang berjalan adalah keputusan" >&2
      echo "manusia (decision 55). Tutup editor, atau tambahkan --force kalau Anda memang sedang" >&2
      echo "memerintahkannya langsung." >&2
      exit 1
    fi
    bak="${DEST}/Binaries/Linux.bak-$(stamp)"
    cp -a "$DEST/Binaries/Linux" "$bak"
    echo "backup utuh di: $bak  (tidak pernah dihapus skrip ini)"
    cp -a "$SRC_BIN/." "$DEST/Binaries/Linux/"
    echo "== sesudah =="
    ( cd "$DEST/Binaries/Linux" && md5sum *.so | sort )
    echo
    echo "Pasang. Rollback: $0 $BUILD --restore   (atau cp -a $bak/* ke tujuan)"
    ;;

  --restore)
    last="$(ls -1d "${DEST}"/Binaries/Linux.bak-* 2>/dev/null | tail -1 || true)"
    [ -n "$last" ] || { echo "restore: tidak ada backup untuk dipulihkan" >&2; exit 1; }
    echo "memulihkan dari: $last"
    cp -a "$last/." "$DEST/Binaries/Linux/"
    ( cd "$DEST/Binaries/Linux" && md5sum *.so | sort )
    ;;

  *)
    echo "mode tidak dikenal: $MODE (pakai --inspect | --deploy | --restore)" >&2
    exit 1 ;;
esac
