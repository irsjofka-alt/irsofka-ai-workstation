#!/usr/bin/env bash
# Membuktikan panel mode Autopilot di Station adalah CERMINAN dari `work_order.MODES`, bukan
# daftar yang disalin ke JavaScript.
#
# §12 melarang satu kosakata punya dua definisi, dan halaman ini pernah menyimpan salinannya
# sendiri: ketika OFF dibuang sebagai mode (2026-10-08) halaman menulis dua tombol plus sebuah
# tombol berhenti, lalu ketika OFF kembali sebagai state (2026-10-09) salinan itu tinggal jadi
# kebohongan — mesin yang berhenti dilaporkan sebagai "SEMI". Yang diuji di sini karena itu
# bukan tampilan, tapi sumber kata: tambah satu mode di Python tanpa menyentuh halaman, atau
# sebaliknya, harus membuat pemeriksaan ini merah.
#
# Dua arah, karena guard yang hanya bisa lulus lebih buruk daripada tidak ada:
#   (1) tombol yang dirender == MODES, dengan ON ditampilkan sebagai "FULL AUTO";
#   (2) satu mode tambahan di Python saja -> render tetap harus memuatnya, dan tes gagal kalau
#       halaman tidak punya tempat untuknya.
set -uo pipefail

STATION="${STATION_DIR:-$HOME/.ai-station}"
PAGE="$STATION/engine-rust/src/station.html"
WORK_ORDER="$STATION/tools/work_order.py"
NODE="$(command -v node || true)"
[ -n "$NODE" ] || NODE="$HOME/.local/bin/node"

[ -f "$PAGE" ] || { echo "✗ $PAGE tidak ada"; exit 1; }
[ -f "$WORK_ORDER" ] || { echo "✗ $WORK_ORDER tidak ada"; exit 1; }
[ -x "$NODE" ] || { echo "✗ node tidak ditemukan — panel mode TIDAK terverifikasi"; exit 1; }

TMP="$(mktemp /tmp/ap_modes.XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT

awk '/^<script>/{f=1;next} /^<\/script>/{f=0} f' "$PAGE" > "$TMP"
[ -s "$TMP" ] || { echo "✗ blok <script> tidak terbaca — pemeriksaan ini tidak bisa lulus"; exit 1; }

# MODES dibaca sebagai AST, bukan dengan regex: `MODES = ("SEMI", "OFF", "ON")` dan
# `MODES = ("SEMI", "ON")` punya bentuk yang sama bagi regex tapi arti yang berbeda, dan yang
# kedua pernah lolos dari pemeriksaan teks.
MODES_JSON="$(python3 - "$WORK_ORDER" <<'PY'
import ast, json, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "MODES" and isinstance(node.value, (ast.Tuple, ast.List)):
                print(json.dumps([ast.literal_eval(e) for e in node.value.elts]))
                sys.exit(0)
print("NOT FOUND"); sys.exit(1)
PY
)" || { echo "✗ MODES tidak terbaca dari work_order.py — pemeriksaan ini tidak bisa lulus"; exit 1; }
case "$MODES_JSON" in NOT_FOUND) echo "✗ MODES bukan tuple/list harfiah di tingkat modul"; exit 1;; esac

# Tambahan untuk arah (2): satu mode hantu disuntik ke daftar EXPECT, dan halaman harus
# KEHABISAN tempat — dibuktikan dengan EXPECT yang dimuat paksa, bukan dengan mengubah berkas.
"$NODE" -e '
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const modes = JSON.parse(process.argv[2]);

const fn = src.match(/function switchCard\(host\)\s*\{[\s\S]*?\n  \}/);
if (!fn) { console.log("✗ switchCard tidak ditemukan di halaman"); process.exit(1); }

function run(mode) {
  const buttons = [], others = [];
  function el(tag, cls, txt) {
    const n = { tag, cls, txt, kids: [], listeners: {}, dataset: {} };
    n.appendChild = (c) => { n.kids.push(c); return c; };
    n.addEventListener = (ev, f) => { n.listeners[ev] = f; };
    n.classList = { add() {}, toggle() {} };
    if (tag === "button") buttons.push(n);
    return n;
  }
  const ctx = {
    el,
    ap: { autopilot: { mode, changed_by: "operator", off_reason: null, until_epoch: 0 },
          drain: { level: "observe" }, night: { ok: true, why: [] }, off_paths: [] },
    apBusySend: false,
    dashCard: (t, d) => { const c = el("div"); c.__title = t; others.push(c); return c; },
    dashSwitch: () => {},
    timeLeft: () => "0s",
    fold: (t, f) => el("details"),
  };
  const names = Object.keys(ctx);
  const card = new Function(...names, fn[0] + "\nreturn switchCard;")(...names.map(k => ctx[k]));
  card({ appendChild() {} });
  return buttons.map(b => ({ mode: b.dataset.mode, label: b.txt }));
}

function report(bs) {
  return bs.map(b => (b.mode || "alias:" + b.label) + "=" + b.label).join(" ");
}

/* Arah (1): setiap kata di MODES harus punya tombolnya sendiri, dan ON harus berlabel
   "FULL AUTO" karena itu yang operator sebut dengan kata itu. */
const rendered = run(modes[0] === "SEMI" ? "SEMI" : modes[0]);
const byMode = {};
rendered.forEach(b => { if (b.mode) byMode[b.mode] = b.label; });
const missing = modes.filter(m => !(m in byMode));
if (missing.length) {
  console.log("✗ mode yang disimpan tapi tidak punya panel: " + missing.join(", "));
  console.log("   dirender: " + report(rendered));
  process.exit(1);
}
if (modes.includes("ON") && byMode["ON"] !== "FULL AUTO") {
  console.log("✗ ON dirender sebagai \"" + byMode["ON"] + "\", halaman harus menyebutnya FULL AUTO");
  process.exit(1);
}
const extra = Object.keys(byMode).filter(m => !modes.includes(m));
if (extra.length) {
  console.log("✗ panel untuk mode yang tidak ada di kosakata mesin: " + extra.join(", "));
  process.exit(1);
}
console.log("✓ panel mode = MODES (" + modes.join("/") + "): " + report(rendered));

/* Tombol berhenti tetap wajib ada: §6 menuntut jalan mati yang tidak butuh kerja sama AI,
   dan menghapusnya bersamaan dengan menjadikan OFF sebuah panel adalah cara menutup pintu. */
if (!rendered.some(b => !b.mode && /berhenti|stop/i.test(b.label || ""))) {
  console.log("✗ tidak ada tombol berhenti yang berdiri sendiri — tiga panel saja tidak memenuhi "
              + "aturan §6 bahwa menghentikan mesin tidak boleh butuh kerja sama AI");
  process.exit(1);
}
console.log("✓ tombol berhenti yang tidak butuh AI tetap ada di samping tiga panel");

/* Arah (2): kalau MODES bertambah satu kata dan halaman tidak tahu, pemeriksaan di atas harus
   jatuh. Diuji dengan daftar yang dimuat paksa, bukan dengan menyunting berkas. */
const ghost = modes.concat(["ZULU"]);
const bs2 = run(ghost[0]);
const have2 = {}; bs2.forEach(b => { if (b.mode) have2[b.mode] = 1; });
if (ghost.every(m => m in have2)) {
  console.log("✗ pemeriksaan ini tidak bisa gagal: mode asing " + ghost[ghost.length - 1]
              + " tetap ditemukan di halaman");
  process.exit(1);
}
console.log("✓ mode yang tidak punya panel terdeteksi sebagai kehilangan (ZULU ditolak)");
' "$TMP" "$MODES_JSON"
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

"$NODE" --check "$TMP" || { echo "✗ JavaScript halaman Station tidak bisa diurai"; exit 1; }
exit 0
