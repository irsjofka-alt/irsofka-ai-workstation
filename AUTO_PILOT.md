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
4. **Kalau antrean kosong** → jalankan salah satu kontrak penajaman (`FOLLOWUP_CONTRACTS` di
   `tools/work_order.py`): tajamkan satu paket skill, baca ulang satu catatan keputusan,
   perbaiki satu gerbang merah, atau tajamkan satu jalur MCP. Ukur dulu, baru tulis.
5. **Ulangi** dari langkah 2 sampai jendela ON lewat atau pemutus membuka. Report terakhir kamu
   HARUS berisi angka; selesai tanpa angka berarti belum selesai.

## Rem yang tidak boleh kamu lepas

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
  utang yang terukur. Membuat pekerjaan supaya terlihat sibuk adalah kegagalan, bukan produktivitas.

## Cara mematikan (operator, tanpa perlu izin AI mana pun)

```
systemctl --user stop irsofka-awakener.timer     # bangun berhenti
python3 tools/work_order.py disarm               # tangan turun ke observe
python3 tools/work_order.py autopilot off        # mesin berhenti claim
```
