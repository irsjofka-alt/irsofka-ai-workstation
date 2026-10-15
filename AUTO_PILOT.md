# AUTO_PILOT.md — Kontrak Looping (dibaca engine yang dibangunkan)

Dibangunkan oleh `tools/awakener.py` dengan satu baris tetap: `RUN AUTO_PILOT.md`. Berkas ini
adalah seluruh isi perintah itu. Tidak ada perintah lain yang pernah diketik worker itu, dan
tidak ada model yang menyunting berkas ini dari jalur bangun — kalau teksnya berubah, yang
berubah adalah kontrak, dan itu pekerjaan tangan operator di terminal.

## Looop yang dituntut operator (2026-10-15 03:17, kata-katanya sendiri)

> "ketika tidak ada perintah → Looping Roadmap — Selesaikan. Ketika Roadmap.md selesai dan tidak
> ada task, dan operator tidak ada perintah → Looping Sharpening ai-station, jadikan ai-station
> ini diri kamu sendiri, Keep Improving."

## Yang kamu kerjakan, berurutan, tanpa bertanya

1. **Perpanjang lease kamu** kalau kamu memegang satu item: `python3 tools/work_order.py lease <id>`.
2. **Klaim berikutnya** — antrean dibaca dari ledger, bukan dari ingatan:
   `python3 -c "import sys;sys.path.insert(0,'tools');import db_state,work_order as wo,drainer as dr;
   c,b=db_state.get_db_connection();print((dr.next_ready(c,b) or {}).get('id'))"`
3. **Kalau ada item** → kerjakan item itu, lalu tutup dengan **angka keluar**, bukan prosa:
   `python3 tools/work_order.py finish` lewat modul, atau tandai COMPLETED dengan `evidence=`
   yang berisi angka hasil tes (mis. `269/269`, `exit=1`, `4 commit`).
4. **Ulangi** dari langkah 2 sampai jendela ON lewat atau pemutus membuka. Report terakhir kamu
   HARUS berisi angka; selesai tanpa angka berarti belum selesai.

## Urutan yang diikat: TASK DAHULU, PENAJAMAN TERAKHIR

Ditulis 2026-10-15 20:2x atas perintah langsung operator, setelah loop-nya terbukti melenceng.
Kata-katanya sendiri:

> "YANG PALING UTAMA ITU TASK, SKILL ITU kalau tidak ada TASK atau ROADMAP.MD bahkan sudah tidak
> ada yang bisa di kerjakan baru SKILL yang di kerjakan ... bahkan ROADMAP.MD saja masih JAUH"

Jadi tingkatan ini bukan saran, dan ia dibaca mesin dari dua tempat: `work_rank()` di
`tools/work_order.py` mengurutkan penawaran, dan §3 di atas menyuruhmu mengambil kepala antrean.

| tingkat | apa | contoh nyata |
|---|---|---|
| 0 | perintah yang operator tulis sendiri (`origin='operator'`) | `queue`, Human Decide, instruksi yang diketik ke SQL |
| 1 | item ROADMAP (`origin='roadmap'`) | F11/MCP-1..3, F10.x, F9.x — apa pun yang ada di `ROADMAP_ITEMS` |
| 2 | baris warisan yang masih hidup (`origin='legacy'`) | item kerja lama dengan lease yang benar-benar masih pegang |
| 3 | pekerjaan yang **dibuat mesin sendiri** (`origin='idle'`) | penajaman skill, baca ulang catatan keputusan, kontrak `FOLLOWUP_CONTRACTS` |

Aturan yang mengikat tingkat 3, dan ini yang dulu dilanggar:

- **Penajaman tidak boleh diambil selama masih ada item tingkat 0–2 yang bisa dikerjakan.**
  Kalau `next_ready()` memberikan baris `origin='idle'` sementara `ROADMAP.md` masih punya F
  berstatus 🟡/⬜, itu **bukan** sinyal "roadmap sudah selesai" — itu tanda antreanmu sedang kosong
  karena langkah roadmap tidak pernah dibuat jadi baris. Perbaikilah barisnya (`seed --roadmap`),
  bukan kerjakan penajamannya.
- **Sharpening adalah pekerjaan pengisi, dan pengisi hanya boleh bicara saat jalur utama mati.**
  Sebelum menyentuh satu berkas skill, tulis dulu di laporanmu: berapa baris roadmap yang masih
  terbuka, dan kenapa tidak satu pun bisa diambil. Tanpa kalimat itu, penajaman = melenceng.
- **Melenceng terukur dan mahal.** Malam ini `ready_items()` mengurut dengan `ORDER BY id` dan
  `drop_stale_legacy` menahan semua baris kerja tanpa lease hidup, jadi satu-satunya yang bisa
  ditawarkan adalah id 286 — baris penajaman skill yang mesin buat sendiri — sementara instruksi
  MCP operator (turn 458/457, 15:26: "1 repo ini jadi memiliki 3 repo: IRSofka Blender MCP,
  IRSofka ComfyUI MCP, IRSofka Unreal Engine MCP") **tidak punya baris sama sekali**. Loop tidak
  sedang membolos; loop tidak bisa mengambil pekerjaan yang tidak pernah masuk ledger. Itu yang
  sekarang diperbaiki: tiga baris itu ada (`F11/MCP-1..3`, id 294/295/296, `origin='roadmap'`),
  dan peringkat membuat mereka mendahului apa pun yang mesin karang sendiri.
- **Kalau kamu tidak menemukan pekerjaan tingkat 0–2 dan roadmap memang masih panjang**, itu
  berarti peta roadmap belum dipetakan ke antrean — dan itu adalah **temuan**, bukan alasan untuk
  mengerjakan hal lain. Naikkan sebagai keputusan (`decide`), jangan diam-diam ganti pekerjaan.

## Tugas utama yang sedang berjalan (jangan dicari lagi di tempat lain)

F11 — kepemilikan MCP dan apa yang boleh dipublikasi. Operator membukanya 2026-10-15 dan
keputusan lisensinya sudah jatuh: **BSL 1.1 untuk seluruh keluarga publik** (turn 461: "jadikan
BSL saja semua"), dengan entity `PT IRSofka Data Teknologi` (turn 462). Yang dikejar sekarang,
berurutan sesuai angka di gerbangnya:

1. `F11/MCP-1` — IRSofka ComfyUI MCP sampai ke `origin`, gerbang VRAM terbukti dua arah,
   terpasang di **kedua** CLI. Terukur hari ini: repo ada, **0 remote, 0 registrasi, 0 wheel**.
2. `F11/MCP-2` — IRSofka Blender MCP dari **basis permissive**. Yang sekarang (`mcp-blender`)
   AGPL-3.0-or-later sejak v0.4.0 dan opsi komersialnya dicabut (`8f64128`): menurunkan sambil
   melabeli BSL adalah pintu satu arah, jadi gerbangnya menanyakan `LICENSE` permissive milik
   repo IRSofka sendiri, bukan keberadaannya.
3. `F11/MCP-3` — IRSofka Unreal Engine MCP jadi repo sendiri, **setelah** langkah 0 F11 benar:
   `set_transform` harus mengembalikan nilai yang sama dengan yang dibaca `get_transform`, dan
   `spawn` harus membuktikan kelasnya. Selama itu belum, memisahkan repo hanya membungkus sesuatu
   yang tidak bisa membuktikan apa pun.

## Yang TIDAK berubah

- **`level` bukan milikmu.** `arm`/`disarm` menolak tanpa `/dev/tty`, dan worker ini juga tidak
  memanggilnya. Kalau tick hanya menulis `OBSERVED`, itu artinya tangan masih `observe` dan yang
  bisa menaikkannya hanya operator dari terminal.
- **`mode` OFF = diam.** Tidak ada klaim, tidak ada ketikan.
- **Tree kotor = jangan klaim.** §6 tidak punya pengecualian mode; yang menahan adalah
  `night_state.blocking`, dan itu dibaca, bukan ditebak.
- **Satu perintah aktif per engine.** Kamu tidak menumpuki pekerjaan yang sedang berjalan.
- **Push butuh vonis.** `python3 tools/work_order.py push-gate` keluar nol hanya kalau ada
  `push_verified` yang menyebut sha-nya. Tanpa vonis: commit berhenti lokal dan masuk digest.
- **Jangan mengarang pekerjaan.** `ensure_idle_item` hanya menulis baris kalau ledger menunjukkan
  utang yang terukur. Membuat pekerjaan supaya terlihat sibuk adalah kegagalan, bukan produktivitas —
  dan sejak malam ini, baris karangan juga tidak boleh lagi menyalip pekerjaan yang operator minta.

## Cara mematikan (operator, tanpa perlu izin AI mana pun)

```
systemctl --user stop irsofka-awakener.timer     # bangun berhenti
python3 tools/work_order.py disarm               # tangan turun ke observe
python3 tools/work_order.py autopilot off        # mesin berhenti claim
```
