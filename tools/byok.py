#!/usr/bin/env python3
"""Kolam BYOK Gemini milik workstation — baca, rotasi, panggil, tanpa pernah menyebut kunci.

Perintah operator, item 241/242 (2026-10-15 03:5x, kata-katanya sendiri): "Cek Plugin CORE UE 5.8,
disana ada 3 API Gemini yang terdaftar. Setelah kamu cek ini masukan ke system ai-station kita
dengan: 5 Byok Gemini API — jika byok 1 habis memakai byok 2, jika 2 habis memakai 3 dan seterusnya
— karena akun Saya PRO."

Yang terukur sebelum kode ini ditulis, di mesin ini: `config/byok.json` menyimpan
`GeminiApiKeys` sepanjang 5 slot dengan TIGA di antaranya terisi dan ketiganya berbeda,
`ActiveGeminiKeyIndex = 0`. Di sisi plugin, `IRSofkaBYOKManager.h` sudah punya rotasi yang sama
(`RotateToNextGeminiKey`, `GetValidGeminiKeyCount`, `AttemptKeyCount`, 14 rujukan 429/rotasi di
.cpp). Jadi modul ini TIDAK menulis ulang rotasi sebagai kebijakan kedua: ia satu-satunya jalur
CLI (`kind: cli` + template argv di `config/engines.json`) sehingga `cross_verify.run_engine`,
pagar kuota, dan pembacaan jawaban tetap milik di sana — bukan replika di tempat lain.

Aturan yang mengikat implementasinya:

* Kunci tidak pernah dicetak, tidak pernah masuk URL query, tidak pernah masuk log. Header
  `x-goog-api-key` saja. `status` hanya melaporkan ANGKA.
* `byok.json` di-gitignore karena berisi nilai nyata; repo ini publik.
* Habis = 429 / `RESOURCE_EXHAUSTED` / kata quota atau rate limit di badan respons. BUKTINYA kode
  respons, bukan tebakan: satu kegagalan jaringan biasa tidak memajukan kolam, karena kalau ia
  memajukan, satu koneksi putus akan menandai semua kunci habis dan meninggalkan kolam kosong.
* Index maju ditulis balik ke berkas, jadi rotasi berlanjut lintas proses — bukan di dalam memori
  satu panggilan.
* Kalau SEMUA slot terisi habis: laporkan `POOL_EXHAUSTED`, keluar bukan-nol. Tidak ada
  fallback diam-diam ke engine lain; itu tanggung jawab tangga resolver, bukan kolam ini.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "byok.json"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
EXHAUSTED_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit")
# SINYAL KAPASITAS, BUKAN KUOTA. Terukur 2026-10-15 04:0x pada panggilan nyata pertama lewat
# registry: `gemini-3.8-flash` menjawab 503 "This model is currently experiencing high demand"
# dalam 3.9 detik, pada kunci yang barusan menjawab dengan benar. Membuang seluruh kolam karena
# satu model sedang padat adalah kerusakan yang dibuat sendiri, dan plugin UE sudah memutuskan
# ini lebih dulu: `bAutoFallback` + `bIsRetryModel` + `AttemptKeyCount` di IRSofkaBYOKManager.
# Yang ditiru di sini adalah kebijakan itu, bukan kebijakan baru: sinyal kapasitas menaiki MODEL
# pada kunci yang sama, sedangkan kuota habis menaiki KUNCI.
CAPACITY_MARKERS = ("503", "HIGH DEMAND", "UNAVAILABLE", "NO AVAILABLE", "OVERLOADED")
MODEL_ORDER = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
               "gemini-3.5-flash", "gemini-3.1-flash-lite")

# Peta kapasitas yang diukur, bukan diasumsikan — dibaca Station lewat `byok.py capabilities`
# supaya layar tidak menyimpan klaimnya sendiri (§12: satu definisi). Statusnya sengaja tiga
# tingkat: "TERUKUR" berarti ada panggilan nyata dan statusnya tercatat; "ADA DI DAFTAR" berarti
# namanya muncul di ListModels tetapi belum satu pun panggilan dilakukan; "DITOLAK UKUR" berarti
# sudah dicoba dan sistem menjawab tidak. Yang terakhir inilah yang harus dilihat orang sebelum
# merancang pipeline di atas sebuah model.
CAPABILITIES = {
    "measured_at": "2026-10-15T10:5x+07:00",
    "keys": {"slots": 5, "filled": 3, "identical_model_list": True, "models_visible": 63},
    "text": {"gemini-3.8-flash": "TERUKUR: 200, dipakai mengaudit diff yang ditahan",
             "gemini-4-argon": "DITOLAK UKUR: 429 pada KETIGA kunci termasuk PRO; -latest/-preview "
                               "= 404, jadi nama dasarnya benar. Kuota dihitung per model."},
    "music": {"lyra": "DITOLAK UKUR: 404 di kunci non-PRO DAN PRO. Nama lain ikut dicoba: lyra-2.0, "
                      "lyra-2.0-realtime, gemini-lyra, lyra-realtime, gemini-music — semuanya 404, "
                      "dan tidak ada satu pun nama berunsur lyra/music di daftar 63 model.",
              "operator_claim": "Operator menyatakan Lyra available di sisi modelnya. Dicatat sebagai "
                                "pernyataan, bukan sebagai jalur yang bisa dipakai modul ini."},
    "video": {"veo-3.1-generate-preview": "ADA DI DAFTAR; endpoint-nya TERBUKTI ADA "
                                          "(predictLongRunning menjawab 429, bukan 404) tetapi "
                                          "generateContent TIDAK mendukungnya — jadi ask() di modul "
                                          "ini tidak bisa memakainya. Butuh jalur predictLongRunning "
                                          "+ polling operation.",
              "veo-3.1-fast-generate-preview": "ADA DI DAFTAR, belum dipanggil",
              "veo-3.1-lite-generate-preview": "ADA DI DAFTAR, belum dipanggil"},
    "image": {"MEASURED": "DITOLAK UKUR 2026-10-15 11:2x: 0 gambar, 0 titik. Empat nama × dua kunci "
                          "semuanya 429 RESOURCE_EXHAUSTED dengan metrik "
                          "`generate_content_free_tier_requests` dan "
                          "`generate_content_free_tier_input_token_count` pada `limit: 0` — kunci "
                          "dibaca sebagai free tier untuk model gambar, bukan kunci mati: kunci PRO "
                          "yang SAMA menjawab 200 untuk `gemini-3.8-flash` beberapa detik "
                          "sebelumnya. Nama yang diukur: gemini-nano-banana-2.1, "
                          "gemini-3.1-flash-lite-image, gemini-3.1-flash-image, "
                          "gemini-2.5-flash-image (layanan memetakannya sebagai "
                          "gemini-2.5-flash-preview-image). Pro-image (nano-banana-pro-preview, "
                          "gemini-3-pro-image) TIDAK dipanggil — operator melarang uji berbiaya "
                          "besar, dan nama non-pro sudah membuktikan pagarnya.",
              "reset": "Semua jawaban menyebut 'Please retry in 19h45m' dan hitungan mundurnya "
                       "sama antar kunci sampai di bawah satu detik. Itu cocok baik untuk satu "
                       "bucket bersama maupun batas harian tetap di semua proyek: datanya tidak "
                       "memutuskan mana yang benar, jadi jangan dijual sebagai 'kolamnya satu'.",
              "module": "ask() hanya mengirim `parts:[{text}]` tanpa responseModalities, jadi "
                        "jalur gambar memang TIDAK ADA di modul ini — walau pagarnya terbuka, "
                        "yang keluar tetap teks. Gambar mesin ini jalurnya ComfyUI lokal (F7, "
                        "terbukti dan gratis), bukan BYOK."},
    "audio": {"gemini-2.5-flash-native-audio-latest": "ADA DI DAFTAR, belum dipanggil",
              "gemini-3.5-transcribe-live": "ADA DI DAFTAR, belum dipanggil"},
}


def load(path=CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(cfg, path=CONFIG):
    tmp = Path(path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)          # atomik: tab yang kebagian sinyal tidak membaca setengah berkas


def filled(cfg):
    """Daftar (index, kunci) untuk slot yang benar-benar terisi — urutan index dijaga."""
    return [(i, k.strip()) for i, k in enumerate(cfg.get("GeminiApiKeys") or [])
            if (k or "").strip()]


def order(cfg):
    """Urutan coba: mulai dari index aktif lalu melingkar. Tidak pernah menebak kunci baru."""
    f = filled(cfg)
    if not f:
        return []
    start = int(cfg.get("ActiveGeminiKeyIndex") or 0)
    idx = [i for i, _ in f]
    try:
        pos = idx.index(start)
    except ValueError:
        pos = 0
    return f[pos:] + f[:pos]


def is_exhausted(status, body):
    blob = f"{status} {body}".upper()
    return any(m.upper() in blob for m in EXHAUSTED_MARKERS)


def post(model, key, prompt, transport=None, timeout=120):
    """Satu panggilan generateContent. Mengembalikan (teks|None, status, pesan_eror)."""
    url = ENDPOINT.format(model=model)
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    if transport is not None:
        return transport(url, key, payload)
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        parts = [p.get("text", "") for c in data.get("candidates", [])
                 for p in c.get("content", {}).get("parts", [])]
        return ("\n".join(parts).strip(), 200, "")
    except urllib.error.HTTPError as e:
        return (None, e.code, (e.read() or b"").decode("utf-8", "replace")[:300])
    except Exception as e:  # noqa: BLE001
        return (None, 0, str(e)[:200])


def is_capacity(status, body):
    blob = f"{status} {body}".upper()
    return any(m.upper() in blob for m in CAPACITY_MARKERS)


def candidate_models(preferred):
    """Model yang dicoba pada satu kunci: yang dipilih dulu, lalu sisa daftar tanpa duplikat."""
    seen, out = set(), []
    for m in [preferred] + list(MODEL_ORDER):
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def ask(prompt, cfg=None, transport=None, model=None, timeout=120, persist=True,
         save_to=None):
    """Coba kolam sesuai urutan; majukan dan simpan index hanya pada bukti habis.

    Dalam satu kunci, sinyal kapasitas (503 / high demand) menaiki MODEL lebih dulu — itu yang
    plugin UE lakukan lewat `bAutoFallback`, dan terukur perlu: panggilan nyata pertama keluar
    503 pada model sementara kuncinya sehat. Yang TIDAK dilakukan di sini: memajukan KUNCI karena
    model sedang padat (kolam akan habis tanpa ada kunci yang habis), dan memajukan kunci karena
    koneksi putus (bukan bukti apa pun).

    `persist=False` adalah tambalan atas kerusakan yang saya buat sendiri malam ini: selftest
    memanggil `ask()` dengan cfg palsu (`kunci-a/b/c`) dan fungsi itu menulis index balik ke
    `config/byok.json` lewat `save(cfg)` — jadi berkas kunci nyata operator ditimpa oleh berkas
    uji berisi placeholder, dan panggilan nyata berikutnya keluar 400 "API key not valid".
    Test tidak boleh punya jalur tulis ke berkas produksi; itu bukan aturan gaya, itu data orang.
    """
    cfg = cfg if cfg is not None else load()
    write = save if persist else (lambda c, path=None: None)
    if save_to:
        write = lambda c, path=None: save(c, save_to)
    if not filled(cfg):
        return {"ok": False, "why": "POOL_EMPTY — tidak ada slot terisi di config/byok.json"}
    base_model = model or cfg.get("GeminiModel") or MODEL_ORDER[0]
    tried = []
    exhausted_any = False
    for pos, (idx, key) in enumerate(order(cfg), 1):
        for m in candidate_models(base_model):
            text, status, err = post(m, key, prompt, transport=transport, timeout=timeout)
            tried.append({"slot": idx, "model": m, "status": status})
            if status == 200 and text:
                if pos > 1:
                    cfg["ActiveGeminiKeyIndex"] = idx
                    write(cfg)
                return {"ok": True, "slot": idx, "model": m, "tried": tried, "text": text}
            if is_capacity(status, err):
                continue                     # model sedang padat: naik model, tetap kunci ini
            break                            # exhaustion atau eror lain keluar dari loop model
        if is_exhausted(status, err):
            exhausted_any = True
            nxt = order(cfg)[pos] if pos < len(order(cfg)) else None
            if nxt:
                cfg["ActiveGeminiKeyIndex"] = nxt[0]
                write(cfg)
            continue
        if not is_capacity(status, err):
            return {"ok": False, "why": f"KEY_{idx}_ERROR status={status} {err[:120]}",
                    "tried": tried, "fatal": True}
    # Kesimpulan harus menyebut yang sebenarnya terjadi. Dua keadaan ini berbeda dan punya
    # tindakan berbeda: kolam yang benar-benar habis menunggu hari esok, kolam yang modelnya
    # sedang padat bisa dicoba sedetik lagi.
    if exhausted_any:
        # "Semua kunci habis" dan "model ini habis untuk semua kunci" adalah dua keadaan yang
        # berbeda dan menuntut tindakan berbeda. Terukur 2026-10-15 10:1x: ketiga kunci menjawab
        # 429 untuk `gemini-4-argon` sementara kunci yang SAMA menjawab 200 untuk
        # `gemini-3.8-flash` dalam hitungan detik sebelumnya — kuota Google dihitung per model.
        # Melaporkan itu sebagai POOL_EXHAUSTED membuat operator kira kolamnya mati, lalu ia
        # membuang waktu menambang kunci baru. Probe kecil pada model kolam memutuskan mana
        # yang sebenarnya: kalau model lain masih menjawab, yang habis adalah MODEL ini.
        # Probe memilih model LAIN dari kolam, bukan model yang baru saja gagal. Versi pertama
        # menulis `probe = default if not model else model` — jadi setiap kali pemanggil menyebut
        # model eksplisit (mis. gemini-4-argon), probe mengulang model yang sama, selalu gagal, dan
        # kolam dilaporkan mati padahal hidup. Terukur: argon 429 pada ketiga kunci sementara
        # gemini-3.8-flash menjawab 200 pada kunci yang sama beberapa menit sebelumnya.
        pool_default = (cfg.get("GeminiModel") or MODEL_ORDER[0])
        probe_model = next((m for m in [pool_default] + list(MODEL_ORDER) if m != base_model), None)
        pool_alive = False
        probed = []
        if probe_model:
            for _, k in order(cfg):
                _, st, _e = post(probe_model, k, "ok", transport=transport, timeout=30)
                probed.append((probe_model, st))
                if st == 200:
                    pool_alive = True
                    break
        if pool_alive:
            return {"ok": False, "tried": tried, "probe": probed,
                    "why": f"MODEL_EXHAUSTED {base_model} — {len(tried)} kunci menjawab habis untuk "
                           f"model ini, sementara {probe_model} masih hidup di kunci yang sama"}
        return {"ok": False, "why": "POOL_EXHAUSTED — semua slot terisi menjawab habis "
                                    f"(probe {probe_model} ikut gagal: {probed})",
                "tried": tried, "probe": probed}
    return {"ok": False, "why": "POOL_UNAVAILABLE — semua kunci dicoba; yang menjawab adalah "
                                "kapasitas model, bukan kuota habis", "tried": tried}


def status(cfg=None):
    cfg = cfg if cfg is not None else load()
    f = filled(cfg)
    return {"slots": len(cfg.get("GeminiApiKeys") or []), "filled": len(f),
            "distinct": len({k for _, k in f}), "active": int(cfg.get("ActiveGeminiKeyIndex") or 0),
            "model": cfg.get("GeminiModel"), "provider": cfg.get("ActiveProvider"),
            "keys_shown": False}


def selftest():
    """Rotasi diuji pada transport palsu — tidak ada kunci yang keluar dari mesin.

    `persist=False` di SEMUA panggilan, plus satu pemeriksaan yang mengikatnya: sidik jari
    `config/byok.json` tidak boleh berubah sedikit pun selama selftest. Versi pertama fungsi ini
    menulis cfg palsu ke berkas nyata dan menghabiskan kunci operator; angka di bawah adalah hal
    yang membuktikan itu tidak terjadi lagi.
    """
    import hashlib
    # Checkout bersih TIDAK punya config/byok.json (berkas itu di-gitignore karena berisi kunci
    # nyata), jadi membaca sidik jarinya tidak bisa menjadi prasyarat lulus. Yang diuji tetap ada:
    # kalau berkasnya ADA, selftest tidak boleh mengubahnya barang satu byte.
    disk_before = hashlib.sha256(CONFIG.read_bytes()).hexdigest() if CONFIG.exists() else None
    good = {"slot0": "kunci-a", "slot1": "kunci-b", "slot2": "kunci-c"}
    cfg = {"GeminiApiKeys": [good["slot0"], good["slot1"], good["slot2"], "", ""],
           "ActiveGeminiKeyIndex": 0, "GeminiModel": "gemini-3.8-flash"}
    seen = []

    def t_first_two_dead(url, key, payload):
        seen.append(key)
        if len(seen) <= 2:
            return (None, 429, "RESOURCE_EXHAUSTED: quota per hari habis")
        return ("jawaban tiga", 200, "")

    r = ask("halo", cfg=cfg, transport=t_first_two_dead, persist=False)
    ok1 = r.get("ok") and r.get("slot") == 2 and cfg["ActiveGeminiKeyIndex"] == 2 and seen == [
        good["slot0"], good["slot1"], good["slot2"]]
    print(f"[{'ok' if ok1 else 'GAGAL'}] byok: dua kunci habis -> pindah ke slot 3, index "
          f"disimpan di kolam (tried={seen})")
    seen.clear()

    def t_network(url, key, payload):
        seen.append(key)
        return (None, 0, "Connection refused")

    cfg2 = dict(cfg, ActiveGeminiKeyIndex=0)
    r2 = ask("halo", cfg=cfg2, transport=t_network, persist=False)
    ok2 = (not r2.get("ok")) and "KEY_0_ERROR" in r2.get("why", "") and seen == [good["slot0"]] \
        and cfg2["ActiveGeminiKeyIndex"] == 0
    print(f"[{'ok' if ok2 else 'GAGAL'}] byok: kegagalan BUKAN-bukti-habis tidak memajukan kolam "
          f"— koneksi putus tidak boleh menandai kunci habis")

    cfg3 = {"GeminiApiKeys": ["", "", "", "", ""], "ActiveGeminiKeyIndex": 0}
    r3 = ask("halo", cfg=cfg3, transport=t_network, persist=False)
    ok3 = (not r3.get("ok")) and "POOL_EMPTY" in r3.get("why", "")
    print(f"[{'ok' if ok3 else 'GAGAL'}] byok: kolam kosong dilaporkan dengan nama, bukan ditebak")

    cfg4 = dict(cfg, ActiveGeminiKeyIndex=0)

    def t_all_dead(url, key, payload):
        return (None, 429, "RESOURCE_EXHAUSTED")
    r4 = ask("halo", cfg=cfg4, transport=t_all_dead, persist=False)
    ok4 = (not r4.get("ok")) and "POOL_EXHAUSTED" in r4.get("why", "") and len(r4["tried"]) == 3
    print(f"[{'ok' if ok4 else 'GAGAL'}] byok: tiga-tiganya habis -> POOL_EXHAUSTED, "
          f"bukan fallback diam-diam (tried={len(r4['tried'])})")
    st = status(cfg4)
    ok5 = st["slots"] == 5 and st["filled"] == 3 and st["keys_shown"] is False
    print(f"[{'ok' if ok5 else 'GAGAL'}] byok: status melapor angka (5 slot / 3 terisi) dan "
          f"tidak pernah menampilkan kunci")

    # Kapasitas model bukan kuota habis: terukur nyata, 503 "high demand" dalam 3.9 detik pada
    # kunci yang barusan menjawab benar. Kalau ini diperlakukan sebagai habis, satu model padat
    # menghabiskan seluruh kolam dan operator bangun dengan POOL_EXHAUSTED palsu.
    seen2 = []
    hits = []

    def t_capacity(url, key, payload):
        seen2.append(key)
        hits.append(url.split("/models/")[1].split(":")[0])
        if len(hits) == 1:
            return (None, 503, "This model is currently experiencing high demand")
        return ("jawaban model kedua", 200, "")

    cfg5 = dict(cfg, ActiveGeminiKeyIndex=0)
    r5 = ask("halo", cfg=cfg5, transport=t_capacity, persist=False)
    ok6 = (r5.get("ok") and r5.get("slot") == 0 and len(hits) == 2 and seen2.count(
        good["slot0"]) == 2 and hits[1] != hits[0] and cfg5["ActiveGeminiKeyIndex"] == 0)
    print(f"[{'ok' if ok6 else 'GAGAL'}] byok: 503 kapasitas menaiki MODEL pada kunci yang sama, "
          f"KUNCI tidak maju (model dicoba: {hits})")

    # Dan pemeriksaan yang seharusnya ada sejak dulu, karena inilah yang menghilangkan kunci
    # operator: berkas produksi tidak boleh berubah sedikit pun selama selftest berjalan.
    ok7 = True if disk_before is None else (
        hashlib.sha256(CONFIG.read_bytes()).hexdigest() == disk_before)
    print(f"[{'ok' if ok7 else 'GAGAL'}] byok: selftest TIDAK menulis ke config/byok.json "
          f"(sidik jari sama)" + (" — berkas tidak ada di checkout bersih, dilewati" if disk_before is None else ""))
    return 0 if all([ok1, ok2, ok3, ok4, ok5, ok6, ok7]) else 1


def slot_view(cfg=None):
    """Bentuk yang aman untuk layar: sidik jari dan panjang, TIDAK pernah isi kunci."""
    cfg = cfg if cfg is not None else load()
    out = []
    for i, k in enumerate(cfg.get("GeminiApiKeys") or []):
        k = (k or "").strip()
        out.append({"slot": i, "filled": bool(k), "length": len(k),
                    "shape": ("AIza" if k.startswith("AIza") else "lain") if k else "-",
                    "fingerprint": hashlib.sha256(k.encode()).hexdigest()[:12] if k else None})
    return {"slots": out, "model": cfg.get("GeminiModel"),
            "active": int(cfg.get("ActiveGeminiKeyIndex") or 0),
            "path": str(CONFIG), "gitignored": True}


def set_from_file(path):
    """Ganti satu slot dari berkas sementara. Kunci TIDAK pernah lewat argv.

    Berkas berisi JSON: {"slot": N, "key": "..."} atau {"keys": ["...", ...]} untuk menimpa
    sekaligus. Setelah dibaca, berkasnya dihapus — meninggalkan salinan kunci di /tmp adalah
    kebocoran yang lebih dekat daripada yang dikira orang.
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    cfg = load()
    keys = list(cfg.get("GeminiApiKeys") or [])
    while len(keys) < 5:
        keys.append("")
    if "keys" in data:
        incoming = list(data["keys"])[:5]
        for i, v in enumerate(incoming):
            keys[i] = (v or "").strip()
    else:
        slot = int(data.get("slot", -1))
        if not 0 <= slot < 5:
            return {"ok": False, "why": f"slot {slot} di luar 0..4"}
        keys[slot] = (data.get("key") or "").strip()
    cfg["GeminiApiKeys"] = keys
    if data.get("model"):
        cfg["GeminiModel"] = str(data["model"]).strip()
    save(cfg)
    return {"ok": True, **slot_view(cfg)}


def main(argv=None):
    a = argparse.ArgumentParser(prog="byok.py")
    a.add_argument("cmd", choices=["ask", "status", "selftest", "slots", "set", "capabilities",
                                   "station"])
    a.add_argument("prompt", nargs="*", default=[])
    a.add_argument("--model", default=None)
    a.add_argument("--timeout", type=int, default=120)
    a.add_argument("--from-file", default=None)
    x = a.parse_args(argv)
    if x.cmd == "selftest":
        return selftest()
    if x.cmd in ("status", "slots"):
        print(json.dumps(slot_view() if x.cmd == "slots" else status(), ensure_ascii=False))
        return 0
    if x.cmd == "set":
        if not x.from_file:
            print(json.dumps({"ok": False, "why": "set butuh --from-file <path>"},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        r = set_from_file(x.from_file)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if x.cmd == "capabilities":
        print(json.dumps(CAPABILITIES, ensure_ascii=False, indent=1))
        return 0
    if x.cmd == "station":
        # Satu proses untuk satu layar. Rust hanya memancarkan byte ini apa adanya: begitu sisi
        # HTTP mulai menggabungkan atau menguraikan laporan, workstation punya dua definisi
        # "apa yang diketahui tentang kolam" (§12).
        print(json.dumps({"slots": slot_view(), "capabilities": CAPABILITIES},
                         ensure_ascii=False))
        return 0
    prompt = " ".join(x.prompt).strip()
    if not prompt:
        print(json.dumps({"ok": False, "why": "prompt kosong"}, ensure_ascii=False))
        return 1
    r = ask(prompt, model=x.model, timeout=x.timeout)
    if r.get("ok"):
        print(r["text"])
        return 0
    print(json.dumps({k: v for k, v in r.items() if k != "text"}, ensure_ascii=False),
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
