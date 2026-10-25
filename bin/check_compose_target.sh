#!/usr/bin/env bash
# Membuktikan mesin tujuan yang dipilih operator di composer BERTAHAN setelah render berikutnya,
# dengan menjalankan composeCard sungguhan di atas DOM tiruan — bukan dengan mencari string.
#
# Cacat yang ditutup berasal dari pengukuran 2026-10-08: `known_engines` datang berurutan
# ["antigravity","qoder","shell"], dan composer membangun ulang <select> dari nol setiap render.
# <select> yang dibangun ulang tanpa nilai yang diingat selalu jatuh ke baris pertama, dan poll
# `/api/autopilot` berjalan tiap 10.000 ms. Jadi operator memilih qoder, menunggu sepuluh detik,
# menekan "Kirim ke SQL", dan barisnya tersimpan sebagai antigravity. Guard fokus saja tidak
# cukup: pilihan hilang begitu elemen kehilangan fokus.
#
# Tes ini dua arah, karena guard yang hanya bisa lulus lebih buruk daripada tidak ada:
# render ulang harus MENAHAN "qoder", dan harus TIDAK menahan nilai yang sudah tidak terdaftar.
set -uo pipefail

STATION="${STATION_DIR:-$HOME/.ai-station}"
PAGE="$STATION/engine-rust/src/station.html"
NODE="$(command -v node || true)"
[ -n "$NODE" ] || NODE="$HOME/.local/bin/node"

[ -f "$PAGE" ] || { echo "✗ $PAGE tidak ada"; exit 1; }
[ -x "$NODE" ] || { echo "✗ node tidak ditemukan — target mesin TIDAK terverifikasi"; exit 1; }

TMP="$(mktemp /tmp/compose_target.XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT

# Batas ekstraksi dibaca dari berkas itu sendiri; rentang baris tetap menghasilkan berkas kosong,
# dan `node --check` atas berkas kosong keluar 0 — pemeriksaan yang lulus karena tidak memeriksa.
awk '/^<script>/{f=1;next} /^<\/script>/{f=0} f' "$PAGE" > "$TMP"
[ -s "$TMP" ] || { echo "✗ blok <script> tidak terbaca — pemeriksaan ini tidak bisa lulus"; exit 1; }

"$NODE" -e '
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");

const compose = src.match(/function composeCard\(host\)\s*\{[\s\S]*?\n  \}/);
if (!compose) { console.log("✗ composeCard tidak ditemukan di halaman"); process.exit(1); }
/* Kepemilikan draf dicek terpisah dari penyuntikannya: di halaman asli `drafts` hidup di cakupan
   modul dan composeCard menutup atasnya. Disuntik ke dalam badan fungsi hanya akan membuatnya
   terisi ulang tiap render — justru bug yang sedang ditutup. Jadi objeknya diserahkan dari luar, dan
   yang diassert di sini hanyalah bahwa halaman memang punya satu tempat penahan draf. */
if (!/var drafts *= *\{ *\};/.test(src)) {
  console.log("✗ halaman tidak lagi menyimpan `var drafts = {}` — tes ini menguji berkas yang salah");
  process.exit(1);
}

/* DOM tiruan yang meniru perilaku browser yang MENIMBULKAN bug: appendChild <option> pertama ke
   sebuah <select> kosong menjadikan option itu nilai terpilih. Tidak ada baris di bawah yang
   mengasumsikan hasilnya — yang dipanggil benar-benar composeCard sungguhan, dua kali. */
function makeDom() {
  var selects = [];
  function el(tag, cls, txt) {
    var n = { tag: tag, cls: cls, txt: txt, kids: [], listeners: {}, value: "" };
    n.appendChild = function (c) {
      this.kids.push(c);
      if (this.tag === "select" && c && c.tag === "option" && this.value === "") this.value = c.txt;
      return c;
    };
    n.addEventListener = function (ev, fn) { this.listeners[ev] = fn; };
    n.classList = { add: function () {} };
    if (tag === "select") selects.push(n);
    return n;
  }
  return { el, selects };
}

function render(engines, drafts) {
  var dom = makeDom();
  var ctx = {
    el: dom.el,
    drafts: drafts,
    ap: { workspace: { known_engines: engines, focus: "/tmp" } },
    dashCard: function () { return dom.el("div"); },
    postWork: function () {},
  };
  var keys = Object.keys(ctx);
  var build = new Function(...keys, compose[0] + "\nreturn composeCard;");
  build(...keys.map((k) => ctx[k]))({ appendChild: function () {} });
  if (!dom.selects.length) { console.log("✗ composer tidak membangun <select> mesin tujuan"); process.exit(1); }
  return dom.selects[0];
}

var ENGINES = ["antigravity", "qoder", "shell"];
var drafts = {};

/* Render 1 — operator memilih qoder lalu elemen kehilangan fokus (klik di mana saja). */
var sel1 = render(ENGINES, drafts);
if (sel1.value !== "antigravity") {
  console.log("✗ DOM tiruan tidak meniru browser: nilai awal seharusnya baris pertama, dapat \"" + sel1.value + "\"");
  process.exit(1);
}
sel1.value = "qoder";
if (!sel1.listeners.change) {
  console.log("✗ pilihan mesin tidak pernah dicatat — poll berikutnya pasti mengembalikannya");
  process.exit(1);
}
sel1.listeners.change();

/* Render 2 — poll 10 detik membangun ulang seluruh kartu. Tidak ada fokus di mana pun. */
var sel2 = render(ENGINES, drafts);
if (sel2.value !== "qoder") {
  console.log("✗ poll ke-2 mengembalikan mesin tujuan ke \"" + sel2.value + "\" padahal operator memilih \"qoder\"");
  console.log("   inilah yang menyimpan cli_engine=antigravity pada baris yang ditulis untuk qoder");
  process.exit(1);
}
console.log("✓ mesin tujuan bertahan di render berikutnya: " + sel2.value);

/* Arah sebaliknya — draf yang sudah tidak ada di registri tidak boleh ditahan. Registri adalah
   kontrak (§12), jadi pilihan harus jatuh ke nilai nyata, bukan ke string sisa. */
var stale = render(["qoder", "shell"], { engine: "antigravity" });
if (["qoder", "shell"].indexOf(stale.value) < 0) {
  console.log("✗ draf mesin tak terdaftar menahan nilai \"" + stale.value + "\" yang tidak ada di registri");
  process.exit(1);
}
console.log("✓ mesin yang tak terdaftar tidak ditahan; jatuh ke \"" + stale.value + "\"");
' "$TMP"
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

"$NODE" --check "$TMP" || { echo "✗ JavaScript halaman Station tidak bisa diurai"; exit 1; }
exit 0
