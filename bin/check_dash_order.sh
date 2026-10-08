#!/usr/bin/env bash
# Membuktikan urutan kolom Dashboard adalah yang operator minta, dengan MENJALANKAN fungsi
# render-nya sungguhan di atas DOM tiruan — bukan dengan mencari string di halaman.
#
# Alasannya konkret: `grep projectCard\(c1\)` akan tetap hijau setelah seseorang memindah satu
# kartu ke kolom lain, karena string itu masih ada. Yang ditanya di sini adalah keadaan akhir:
# kolom mana yang berisi kartu apa, sesudah renderDashboard benar-benar berlari.
set -uo pipefail

STATION="${STATION_DIR:-$HOME/.ai-station}"
PAGE="$STATION/engine-rust/src/station.html"
NODE="$(command -v node || true)"
[ -n "$NODE" ] || NODE="$HOME/.local/bin/node"

if [ ! -f "$PAGE" ]; then echo "✗ $PAGE tidak ada"; exit 1; fi
if [ ! -x "$NODE" ]; then echo "✗ node tidak ditemukan — urutan TIDAK terverifikasi"; exit 1; fi

TMP="$(mktemp /tmp/dash_order.XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT

# Ambil isi <script> dengan batas yang DITUKAR dari berkas itu sendiri. Versi pertama memakai
# rentang baris tetap dan menghasilkan berkas kosong, lalu `node --check` atas berkas kosong
# keluar 0 — pemeriksaan yang lulus karena tidak ada yang diperiksa.
awk '/^<script>/{f=1;next} /^<\/script>/{f=0} f' "$PAGE" > "$TMP"
if [ ! -s "$TMP" ]; then echo "✗ blok <script> tidak terbaca — pemeriksaan ini tidak bisa lulus"; exit 1; fi

# Harness urutan: jalankan renderDashboard dan bandingkan kolom yang benar-benar terbentuk.
"$NODE" -e '
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const m = src.match(/function renderDashboard\(\)\s*\{[\s\S]*?\n  \}/);
if (!m) { console.log("✗ renderDashboard tidak ditemukan di halaman"); process.exit(1); }
const cols = [];
function el(tag, cls, txt) {
  const o = { tag, cls, txt, kids: [] };
  o.appendChild = (c) => { o.kids.push(c && c.__card ? c.__card : String(c)); return c; };
  if (cls === "dash-col") cols.push(o);
  return o;
}
const grid = el("div", "dash"), feed = { textContent: "", appendChild: (x) => x };
const document = { getElementById: (id) => (id === "feed" ? feed : null), querySelector: () => null };
const ap = { autopilot: { mode: "SEMI" }, night: { ok: true }, drain: { level: "observe" },
             workspace: { operator_pending: 0 } };
const card = (n) => () => ({ __card: n, appendChild(){} });
const stub = (n) => (host) => { if (host && host.kids) host.kids.push(n); };
const ctx = { document, el, feed, grid, ap,
  projectCard: stub("projectCard"), guidesCard: stub("guidesCard"),
  composeCard: stub("composeCard"), queueCard: stub("queueCard"),
  decisionsCards: stub("decisionsCards"), switchCard: stub("switchCard"),
  summaryCard: stub("summaryCard"), diagnosticFold: card("diagnosticFold"), fold: card("fold") };
const names = Object.keys(ctx);
const render = new Function(...names, m[0] + "\nreturn renderDashboard;")(...names.map(k => ctx[k]));
render();
if (cols.length !== 3) { console.log("✗ kolom yang dibangun = " + cols.length + ", bukan 3"); process.exit(1); }
const want = [["projectCard","guidesCard","composeCard","queueCard"], ["decisionsCards"],
              ["switchCard","summaryCard","diagnosticFold"]];
const got = cols.map(c => c.kids);
if (JSON.stringify(want) !== JSON.stringify(got)) {
  console.log("✗ urutan kolom berubah dari yang operator minta");
  got.forEach((g,i) => console.log("   kolom " + (i+1) + ": " + g.join(", ")));
  console.log("   diminta: proyek/pedoman/perintah kiri · Human Decide tengah · Autopilot+ringkasan kanan");
  process.exit(1);
}
console.log("✓ urutan Dashboard: proyek|pedoman|perintah → Human Decide → Autopilot+ringkasan");
' "$TMP"
rc=$?
if [ "$rc" -ne 0 ]; then exit "$rc"; fi

# Dan sintaksisnya tetap utuh: satu karakter rusak membuat seluruh halaman putih.
"$NODE" --check "$TMP" || { echo "✗ JavaScript halaman Station tidak bisa diurai"; exit 1; }
exit 0
