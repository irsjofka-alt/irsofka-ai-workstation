#!/usr/bin/env bash
# Membuktikan Command center TIDAK menjadi blank ketika satu pembacaan /api/autopilot gagal.
#
# Keluhan operator 2026-10-09: "dashboard command center suka ngeblank tiba-tiba, menu hilang".
# Penyebabnya terukur di station.html: catch pada loadAutopilot menulis ap = {error: ...}, lalu
# renderDashboard melakukan feed.textContent = "" dan menyisakan satu kalimat — satu fetch yang gagal
# (daemon sibuk, satu respons bukan JSON) menghapus seluruh halaman DAN membuang data baik terakhir,
# sehingga tidak ada yang bisa ditampilkan lagi sampai poll berikutnya kebetulan berhasil.
#
# Harness: SELURUH blok <script> dijalankan di satu cakupan dengan browser globals yang ditiru, lalu
# renderDashboard dipanggil dari dalamnya. Itu pilihan, bukan kemalasan — mendaftarkan helper satu per
# satu (dashCard, listOrNull, kvRows, ...) membuat penguji tertinggal satu nama setiap kali halaman
# menambah satu, dan kegagalannya berbunyi seperti "halaman rusak" padahal pengujiannya yang basi.
# Dengan cakupan utuh, nama yang hilang tidak mungkin lagi terlewat.
set -uo pipefail

STATION="${STATION_DIR:-$HOME/.ai-station}"
PAGE="$STATION/engine-rust/src/station.html"
NODE="$(command -v node || true)"
[ -n "$NODE" ] || NODE="$HOME/.local/bin/node"

[ -f "$PAGE" ] || { echo "✗ $PAGE tidak ada"; exit 1; }
[ -x "$NODE" ] || { echo "✗ node tidak ditemukan — ketahanan dashboard TIDAK terverifikasi"; exit 1; }

# Tanpa payload hidup tes ini hanya menguji DOM tiruan dan akan mengaku menguji halaman. Jadi ia
# dinyatakan tidak berjalan, bukan dinyatakan lulus.
PAYLOAD="$(curl -s --max-time 10 "http://127.0.0.1:8999/api/autopilot" 2>/dev/null || true)"
if [ -z "$PAYLOAD" ]; then
  echo "✗ /api/autopilot tidak terjawab — tes ini tidak bisa membuktikan apa pun tentang keadaan nyata"
  exit 1
fi
printf '%s' "$PAYLOAD" > /tmp/dash_resil_payload.json

TMP="$(mktemp /tmp/dash_resil.XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT
awk '/^<script>/{f=1;next} /^<\/script>/{f=0} f' "$PAGE" > "$TMP"
[ -s "$TMP" ] || { echo "✗ blok <script> tidak terbaca — pemeriksaan ini tidak bisa lulus"; exit 1; }

"$NODE" -e '
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const live = JSON.parse(fs.readFileSync("/tmp/dash_resil_payload.json", "utf8"));

let fails = 0;
function check(label, cond, detail) {
  if (cond) { console.log("[ok    ] " + label); }
  else { console.log("[GAGAL ] " + label + "  <- " + detail); fails++; }
}

function makeNode(tag) {
  const n = { tag: tag, kids: [], children: [], listeners: {}, dataset: {}, style: {},
              value: "", txt: "", placeholder: "", disabled: false, type: "", innerText: "",
              textContent: "" };
  n.appendChild = (c) => { n.kids.push(c); n.children.push(c); return c; };
  n.addEventListener = (ev, fn) => { (n.listeners[ev] = n.listeners[ev] || []).push(fn); };
  n.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  n.querySelector = () => null;
  n.querySelectorAll = () => [];
  n.setAttribute = () => {}; n.removeAttribute = () => {};
  n.focus = () => {}; n.blur = () => {}; n.remove = () => {}; n.closest = () => null;
  n.addEventListener.toString = () => "node:" + tag;
  return n;
}

/* Satu browser per percobaan, supaya feed yang dihitung benar-benar feed percobaan itu. */
function boot() {
  const byId = {};
  const document = {
    getElementById: (id) => byId[id] || (byId[id] = makeNode("div")),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (t) => makeNode(t),
    createTextNode: (t) => ({ txt: t }),
    addEventListener: () => {},
    hidden: false, title: "",
    head: makeNode("head"), body: makeNode("body"), documentElement: makeNode("html"),
  };
  const window = { scrollTo() {}, addEventListener() {}, localStorage: null, location: { hash: "" } };
  const sandbox = {
    document, window, console,
    location: { hash: "" },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
    requestAnimationFrame: () => 0,
    fetch: () => Promise.resolve({ status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") }),
    alert: () => {}, matchMedia: () => ({ matches: false, addEventListener() {} }),
    Intl, URL, encodeURIComponent, decodeURIComponent,
  };
  const keys = Object.keys(sandbox);
  /* Isi halaman dibungkus (function(){ "use strict"; ... })(), jadi renderDashboard, ap dan apError
     privat. Satu-satunya cara menguji fungsi ASLI tanpa menyalinnya (dan salinan akan basi) adalah
     menyuntik sebuah probe tepat sebelum IIFE ditutup, lalu membacanya lewat `window` yang kita
     serahkan sendiri. Kalau penutupnya tidak ditemukan, tes ini berhenti dengan suara, bukan lulus
     diam-diam atas sumber yang salah. */
  const PROBE = "\n  window.__probe = { render: renderDashboard,"
    + " set: function (a, e) { ap = a; apError = e; },"
    + " feed: function () { return document.getElementById(\"feed\"); } };\n";
  if (!/\n\}\)\(\);/.test(src)) {
    console.log("✗ penutup IIFE tidak ditemukan — penguji tidak bisa menjangkau fungsi halaman yang asli");
    process.exit(1);
  }
  const body = src.replace(/\n\}\)\(\);/, PROBE + "})();\n");
  const fn = new Function(...keys, body);
  fn(...keys.map((k) => sandbox[k]));
  const probe = window.__probe;
  if (!probe || typeof probe.render !== "function") {
    console.log("✗ probe tidak terpasang — renderDashboard tidak teruji, hanya DOM tiruan");
    process.exit(1);
  }
  return probe;
}

function render(state, apError) {
  let probe, threw = null;
  try { probe = boot(); }
  catch (exc) { return { bootError: exc }; }
  try { probe.set(state, apError); } catch (exc) { threw = exc; }
  try { probe.render(); } catch (exc) { threw = exc; }
  let kids = -1;
  try { kids = probe.feed().kids.length; } catch (exc) { /* tidak bisa dibaca */ }
  return { kids: kids, threw: threw };
}

// 1. Keadaan nyata: payload hidup + satu pembacaan yang gagal. Halaman harus tetap berisi kartu.
const a = render(live, "daemon unreachable — is irsofka-ai-workstation running?");
check("boot halaman sungguhan tanpa melempar", !a.bootError, a.bootError && (a.bootError.name + ": " + a.bootError.message));
check("satu pembacaan gagal tidak melempar di tengah render", !a.threw,
      a.threw && (a.threw.name + ": " + a.threw.message));
check("payload hidup + kegagalan baca: feed TIDAK direduksi jadi satu kalimat",
      a.kids > 1, "anak feed = " + a.kids);

// 2. Keadaan tanpa data sama sekali: di sini memang benar menampilkan pesan dan berhenti.
const b = render({ error: "daemon unreachable" }, null);
check("tanpa data apa pun halaman menampilkan pesan, bukan kartu kosong",
      b.kids === 1, "anak feed = " + b.kids);

// 3. Kontrol negatif: inilah bentuk state pada bug aslinya (data dibuang, hanya error yang tersisa).
//    Kalau tes ini tidak lagi melihatnya blank, berarti tesnya yang berhenti berarti.
const c = render({ error: "daemon unreachable" }, "daemon unreachable");
check("keadaan bug asli (data dibuang) terdeteksi blank oleh tes ini",
      c.kids === 1, "anak feed = " + c.kids);

// 4. Pembacaan normal: dashboard penuh.
const d = render(live, null);
check("pembacaan normal merender dashboard lengkap tanpa melempar",
      !d.threw && d.kids >= 1, d.threw ? (d.threw.name + ": " + d.threw.message) : "anak feed = " + d.kids);

// 5. Angka kartu antrean harus benar-benar dibaca dari payload hidup, bukan diasumsikan.
const held = ((live.workspace || {}).operator_held || []).length;
const pend = ((live.workspace || {}).operator_pending || 0);
check("payload hidup punya field antrean yang dibaca kartu (pending + held terhitung)",
      typeof pend === "number" && typeof held === "number",
      "pending=" + pend + " held=" + held);

if (fails) { console.log("ketahanan dashboard: " + fails + " gagal"); process.exit(1); }
console.log("✓ satu pembacaan yang gagal tidak lagi menghapus Command center (held=" + held + ", pending=" + pend + ")");
' "$TMP"
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

"$NODE" --check "$TMP" || { echo "✗ JavaScript halaman Station tidak bisa diurai"; exit 1; }
exit 0
