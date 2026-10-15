"""Gerbang VRAM yang DITEGAK scheduler, bukan yang dibaca engine.

Alasannya satu kejadian yang sudah tercatat di ROADMAP F7: sebuah run 3D butuh 9,73 GiB sementara
mesin hanya punya 6,38 GiB bebas, dan tidak ada satu pun yang menghentikan permintaannya selain
kalimat di sebuah skill file yang harus dibaca oleh engine yang sedang tidak membaca apa pun.
Aturan yang keberlangsungannya bergantung pada kesadaran pemanggil bukan aturan — ia harapan.

Tiga keadaan keluar, dan ketiganya berbeda harganya:

  ALLOW   -- bebas >= perlu. Kerja dijalankan.
  DENY    -- bebas < perlu, dan PEMEGANGNYA DISEBUT. Menolak tanpa menyebut siapa yang menahan
             VRAM membuat operator mencari sendiri selama setengah jam; `gpu_holders()` dari
             `local_llm` dipakai di sini supaya jawaban "siapa" tetap satu definisi, bukan dua (§12).
  UNKNOWN -- `nvidia-smi` tidak bisa dibaca. UNKNOWN DITOLAK, bukan dianggap cukup. Ini bukan
             kehati-hatian tanpa biaya: menolak kerja GPU ketika alat ukurnya mati lebih baik
             daripada mengOOM-kan ComfyUI dan meninggalkan operator dengan gejala connection-loss
             yang tidak namanya pun menyebut VRAM.

Angka ambang TIDAK ada di kode ini. Semuanya di `config/gpu_budget.json`, dan dibaca ulang setiap
pemanggilan — ambang yang dihafal adalah ambang yang salah minggu depan.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
CONFIG = REPO / "config" / "gpu_budget.json"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# Satu definisi "berapa VRAM yang bebas" dan satu definisi "siapa yang memegangnya", keduanya
# dipinjam dari modul yang sudah memilikinya. Menulis ulang pembacaan nvidia-smi di sini akan
# menghasilkan dua angka yang boleh berbeda untuk kartu yang sama.
from local_llm import gpu_holders, vram_free_mib  # noqa: E402


# Cadangan yang dipakai HANYA kalau config tidak terbaca. Bukan pengganti config — ia ada supaya
# "berkas hilang" tidak pernah berarti "GPU aman". Versi pertama mengembalikan [] saat config gagal
# dibaca, lalu classify menyimpulkan need 0, lalu check meloloskannya sebagai NOT_GPU_WORK: gerbang
# yang terbuka persis ketika pengamannya hilang, dan selftest-nya mengunci perilaku itu karena
# testnya ditulis dari asumsi yang sama. Ditemukan audit 2026-10-15, dibalik di sini.
#
# Cadangan mencakup DUA kelas GPU, bukan cuma yang satu. Audit putaran kedua menemukannya: dengan
# hanya 3d-trellis di sini, config yang hilang membuat `flux`/`sdxl`/`z-image` lolos tanpa penjaga
# sama sekali — fail-closed yang separuh masih fail-open.
FALLBACK_RULES = [
    {"label": "3d-trellis", "need_mib": 9966,
     "match": ["trellis", "pixal3d", "image-to-3d", "image_to_3d", "3d_pipeline", "voxel remesh"]},
    {"label": "image-diffusion", "need_mib": 5919,
     "match": ["z-image", "flux", "sdxl", "txt2img", "juggernaut"]},
]


def _keywords(r) -> list:
    """Daftar kata kunci sebuah aturan, dibaca tanpa bisa menjatuhkan scheduler.

    `{"match": 123}` itu valid sebagai JSON dan membuat list comprehension meledak dengan
    `TypeError: 'int' object is not iterable` — di ronde kedua audit inilah satu-satunya jalan
    tersisa untuk membuat detak autopilot mati karena sebuah berkas config. Yang benar: bentuk
    match yang tidak bisa dibaca berarti aturan yang tidak bisa dipakai, dan aturan tak terpakai
    adalah UNKNOWN (menolak), bukan "tidak ada yang cocok" (mengizinkan).
    """
    m = r.get("match")
    if isinstance(m, (list, tuple)):
        return [str(x).lower() for x in m]
    return []


def _rule_is_usable(r) -> bool:
    m = r.get("match")
    return isinstance(m, (list, tuple)) and len(m) > 0


def rules(path=None) -> list:
    """Daftar aturan dari config, dibaca pada saat dipakai.

    Berkas hilang/rusak TIDAK menghasilkan "tidak ada aturan": yang menghasilkan daftar cadangan,
    dan keputusan berikutnya menandai dirinya `degraded` supaya pembaca tahu ambang mana yang
    sebenarnya dipakai malam itu. Seluruh pembacaan — termasuk loop yang menyusun aturan — ada di
    dalam pagar yang sama: versi kedua memisahkannya, dan satu field `match` bertipe int cukup untuk
    melempar TypeError ke luar fungsi, membuat detak mati alih-alih memakai cadangan.
    """
    try:
        cfg = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
        raw = cfg.get("rules")
        assert isinstance(raw, list) and raw, "rules kosong"
        # Elemen yang bukan dict membuat seluruh config tidak dipercaya, bukan dilewati: kita tidak
        # tahu kata kunci apa yang hilang bersamanya, dan "tidak tahu" tidak boleh berarti "boleh".
        assert all(isinstance(r, dict) for r in raw), "anggota rules bukan objek"
        # Aturan tanpa kata kunci tidak bisa "tidak cocok dengan apa pun" — itu persis bentuk
        # gerbang yang terbuka. Ia membuat seluruh config tidak dipercaya, sama seperti anggota
        # yang bukan objek: yang tidak bisa dibaca bukan yang diabaikan.
        assert all(_rule_is_usable(r) for r in raw), "ada aturan tanpa kata kunci yang bisa dipakai"
        out = []
        for r in raw:
            need = r.get("need_mib")
            if not isinstance(need, int) or isinstance(need, bool) or need < 0:
                # Ambang yang tidak bisa dibaca BUKAN nol. Aturan tetap dipakai, dengan need None,
                # dan `check(None)` menjawab UNKNOWN — menolak. Versi pertama melewati aturan
                # begitu saja, sehingga item yang cocok dengannya jatuh ke need 0 dan jalan.
                out.append({"label": str(r.get("label") or "?"), "need_mib": None,
                            "match": _keywords(r), "bad_threshold": True})
                continue
            out.append({"label": str(r.get("label") or "?"), "need_mib": int(need),
                        "match": _keywords(r)})
        return out
    except Exception:  # noqa: BLE001 — apa pun yang tidak bisa dibaca = pakai cadangan, bukan buka
        return [dict(r, degraded=True) for r in FALLBACK_RULES]


def _word_match(word: str) -> str:
    """Pencocokan berbatas kata, bukan substring mentah.

    `_` dan alfanumerik di kiri-kanan dianggap bagian dari kata yang sama, jadi `flux` tidak lagi cocok
    dengan `reflux`, `flux_store`, atau `test_flux` — judul item biasa yang tidak menyentuh GPU
    sama sekali, dan menolak itu karena VRAM sedang penuh adalah gerbang yang menahan pekerjaan
    yang benar. `.` sengaja TIDAK masuk daftar: nama produk nyata yang dipakai di antrean ini
    adalah `TRELLIS.2`, dan membatalkan deteksinya demi satu false positive adalah tukar yang rugi.
    """
    w = re.escape(word.strip().lower())
    return rf"(?<![0-9a-z_]){w}(?![0-9a-z_])"


def classify(text, path=None) -> dict:
    """Aturan mana yang berlaku untuk sepenggal teks (judul item + perintah + ringkasan)."""
    blob = " ".join(str(text or "").split()).lower()
    rs = rules(path)
    for r in rs:
        if any(m and re.search(_word_match(m), blob) for m in r["match"]):
            return {"matched": r["label"], "need_mib": r.get("need_mib"),
                    "bad_threshold": r.get("bad_threshold", False),
                    "degraded": any(x.get("degraded") for x in rs)}
    return {"matched": None, "need_mib": 0}


def check(need_mib, sampler=None, holders=None) -> dict:
    """Satu keputusan: ALLOW / DENY / UNKNOWN, dengan angka yang membuktikannya."""
    if need_mib is None:
        # Aturan cocok tapi ambangnya tidak terbaca. Menolak adalah satu-satunya jawaban yang
        # tidak mengarang angka: "mungkin 0" akan mengizinkan, dan itu persis kegagalan yang
        # gerbang ini dipasang untuk cegah.
        return {"ok": False, "state": "UNKNOWN", "need_mib": None, "free_mib": None,
                "why": "aturan GPU cocok tapi ambangnya bukan angka — menolak, tidak menebak"}
    need = int(need_mib or 0)
    if need <= 0:
        return {"ok": True, "state": "NOT_GPU_WORK", "need_mib": need, "free_mib": None}
    free = (sampler or vram_free_mib)()
    if free is None:
        return {"ok": False, "state": "UNKNOWN", "need_mib": need, "free_mib": None,
                "why": "nvidia-smi tidak bisa dibaca — UNKNOWN ditolak, tidak dianggap cukup"}
    if free >= need:
        return {"ok": True, "state": "ALLOW", "need_mib": need, "free_mib": int(free),
                "headroom_mib": int(free) - need}
    who = (holders if holders is not None else (gpu_holders() or []))
    top = sorted(who, key=lambda h: -h[1])[:4]
    return {"ok": False, "state": "DENY", "need_mib": need, "free_mib": int(free),
            "short_mib": need - int(free),
            "holders": [f"{n} {m}MiB" for n, m in top if m > 0],
            "why": f"butuh {need} MiB, bebas {int(free)} MiB (kurang {need - int(free)} MiB)"}


def gate(text, sampler=None, holders=None, config_path=None) -> dict:
    """Pintu tunggal yang dipakai scheduler: teks kerja masuk, keputusan keluar."""
    cls = classify(text, config_path)
    res = check(cls["need_mib"], sampler=sampler, holders=holders)
    res["rule"] = cls["matched"]
    if cls.get("degraded"):
        res["degraded"] = True      # ambang yang dipakai malam ini berasal dari cadangan, bukan config
    if cls.get("bad_threshold"):
        res["bad_threshold"] = True
    return res


def status(config_path=None, sampler=None, holders=None) -> dict:
    """Keadaan mesin saat ini dibandingkan semua ambang yang terdaftar — untuk layar dan untuk
    seseorang yang bertanya "kenapa pekerjaan 3D tidak jalan". Tidak ada angka yang dikarang:
    yang tidak terbaca dilaporkan sebagai tidak terbaca.

    Sampler bisa disuntik supaya selftest tidak bergantung pada GPU operator malam ini. Versi
    tanpa injeksi akan membuat satu pemeriksaan "status tidak crash" jadi lulus tanpa mengukur
    apa pun setiap kali nvidia-smi tidak terbaca — test yang diam-diam tidak menjalankan apa
    pun adalah test yang hilang, bukan test yang hijau.
    """
    free = (sampler or vram_free_mib)()
    out = {"free_mib": free, "readable": free is not None,
           "holders": [f"{n} {m}MiB" for n, m in
                       sorted((holders if holders is not None else (gpu_holders() or [])),
                              key=lambda h: -h[1])[:6]],
           "rules": []}
    for r in rules(config_path):
        need = r["need_mib"]
        # `need` bisa None (ambang rusak). Membandingkan int dengan None melempar TypeError, dan
        # sebuah laporan keadaan yang crash justru saat config bermasalah adalah cara gerbang ini
        # membungkam dirinya sendiri pada saat ia paling dibutuhkan.
        usable = need is not None and isinstance(need, int)
        out["rules"].append({"label": r["label"], "need_mib": need,
                             "fits": (None if (free is None or not usable) else free >= need),
                             "margin_mib": (None if (free is None or not usable) else free - need),
                             "threshold_readable": usable})
    return out


def selftest() -> int:
    """Sampler disuntik di semua jalur — tidak ada satu pun panggilan nvidia-smi di sini, dan
    sebuah test yang membaca GPU asli akan lulus atau merah karena keadaan orang lain."""
    checks = []

    def check_(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    cfg = {"rules": [{"label": "3d-trellis", "match": ["trellis", "image-to-3d"], "need_mib": 9966},
                     {"label": "image-z", "match": ["z-image", "sdxl"], "need_mib": 5919},
                     {"label": "cloud", "match": ["tripo"], "need_mib": 0}]}
    import tempfile
    p = Path(tempfile.mkdtemp()) / "gpu_budget.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")

    check_("3D butuh 9.73 GiB: dengan 10 GiB bebas ia DIZINKAN",
           gate("jalankan TRELLIS.2 image-to-3d", sampler=lambda: 10240, holders=[],
                config_path=p)["state"] == "ALLOW")
    r = gate("jalankan TRELLIS.2 image-to-3d", sampler=lambda: 6533,
             holders=[("ComfyUI", 2866), ("gnome-shell", 46)], config_path=p)
    check_("dengan 6.38 GiB bebas ia DITOLAK dan menyebut kekurangan serta pemegangnya "
           "(angka persis dari catatan F7 2026-10-15: perlu 9966 vs bebas 6533)",
           r["state"] == "DENY" and r["ok"] is False and r["short_mib"] == 9966 - 6533
           and "ComfyUI 2866MiB" in r["holders"] and r["rule"] == "3d-trellis", str(r))
    u = gate("TRELLIS.2 run", sampler=lambda: None, config_path=p)
    check_("alat ukur mati = UNKNOWN = DITOLAK, bukan dianggap bebas",
          u["state"] == "UNKNOWN" and u["ok"] is False, str(u))
    n = gate("perbaiki selftest work_order", sampler=lambda: 1, config_path=p)
    check_("kerja yang bukan GPU tidak ditahan oleh rendahnya VRAM (aturan tidak cocok, bukan menebak)",
          n["state"] == "NOT_GPU_WORK" and n["ok"] is True and n["rule"] is None, str(n))
    c = gate("tripo make a rock", sampler=lambda: 1, config_path=p)
    check_("aturan ber-need 0 (cloud vendor) tetap jalan walau VRAM habis — yang diukur bentuk angkanya",
          c["state"] == "NOT_GPU_WORK" and c["rule"] == "cloud", str(c))
    check_("batasnya tepat: bebas == perlu itu DIZINKAN, bukan ditolak",
          check(9966, sampler=lambda: 9966, holders=[])["state"] == "ALLOW")
    bad = Path(tempfile.mkdtemp()) / "gpu_budget.json"
    bad.write_text(json.dumps({"rules": [{"label": "x", "match": ["x"], "need_mib": "sembilan ribu"},
                                         {"label": "y", "match": ["y"], "need_mib": -5},
                                         {"label": "z", "match": ["z"], "need_mib": 100}]}),
                   encoding="utf-8")
    # Dua pemeriksaan di bawah ini PERNAH menyatakan sebaliknya. Yang lama menulis "ambang
    # non-angka dilewati" dan "config tidak ada = kerja dianggap bukan GPU" — dan keduanya mengunci
    # gerbang yang TERBUKA saat pengamannya hilang: item TRELLIS yang cocok dengan aturan rusak
    # menjadi need 0, lalu lolos sebagai bukan-kerja-GPU. Audit 2026-10-15 menemukannya; testnya
    # tidak, karena testnya ditulis dari asumsi yang sama. Arah gagalnya sekarang dibalik:
    # aturan rusak MENAHAN, config hilang memakai cadangan yang menahan.
    check_("ambang rusak MENAHAN, tidak meloloskan: aturan yang cocok tapi need-nya bukan angka "
           "atau negatif keluar sebagai UNKNOWN dengan ok False",
          [r["label"] for r in rules(bad)] == ["x", "y", "z"]
          and gate("x job", sampler=lambda: 99999, config_path=bad)["state"] == "UNKNOWN"
          and gate("x job", sampler=lambda: 99999, config_path=bad)["ok"] is False
          and gate("y job", sampler=lambda: 99999, config_path=bad)["state"] == "UNKNOWN"
          and gate("z job", sampler=lambda: 10, config_path=bad)["state"] == "DENY",
          str(rules(bad))[:220])
    missing = gate("jalankan TRELLIS.2 image-to-3d", sampler=lambda: 100,
                   config_path=bad.parent / "tiada.json")
    check_("config hilang BUKAN berarti GPU aman: cadangan menahan kerja 3D dan menandai dirinya "
           "degraded supaya laporan jujur tentang ambang mana yang sebenarnya dipakai",
          missing["state"] == "DENY" and missing["rule"] == "3d-trellis"
          and missing.get("degraded") is True, str(missing)[:200])
    check_("dan kerja yang memang bukan GPU tetap jalan saat config hilang — cadangan menahan "
           "yang mirip GPU, bukan seluruh antrean",
          gate("perbaiki selftest work_order", sampler=lambda: 1,
               config_path=bad.parent / "tiada.json")["state"] == "NOT_GPU_WORK")
    # Fail-closed yang separuh masih fail-open: audit putaran kedua menemukan FALLBACK hanya
    # melindungi 3D, jadi config yang hilang membuat `flux`/`sdxl` berjalan tanpa penjaga sama
    # sekali — tepatnya pekerjaan 5,9 GiB yang paling sering dijalankan di mesin ini.
    check_("config hilang juga menjaga difusi gambar, bukan cuma 3D — cadangan menutup DUA kelas",
          gate("flux txt2img poster", sampler=lambda: 1,
               config_path=bad.parent / "tiada.json")["state"] == "DENY"
          and gate("jalankan TRELLIS.2", sampler=lambda: 1,
                   config_path=bad.parent / "tiada.json")["state"] == "DENY")
    junk = Path(tempfile.mkdtemp()) / "gpu_budget.json"
    junk.write_text(json.dumps({"rules": ["bukan objek", {"label": "k", "match": ["k"],
                                                           "need_mib": 100}]}), encoding="utf-8")
    check_("anggota rules yang bukan objek membuat SELURUH config tidak dipercaya (cadangan dipakai), "
           "kita tidak tahu kata kunci apa yang ikut hilang",
          all(r.get("degraded") for r in rules(junk))
          and gate("flux job", sampler=lambda: 1, config_path=junk)["state"] == "DENY",
          str(rules(junk))[:180])
    # `{"match": 123}` adalah JSON yang sah. Loop penyusun aturan dulu hidup DI LUAR pagar
    # try/except, jadi satu field bertipe int cukup untuk melempar TypeError keluar `rules()` dan
    # menghentikan detak — kegagalan yang jauh lebih mahal daripada memakai cadangan.
    weird = Path(tempfile.mkdtemp()) / "gpu_budget.json"
    weird.write_text(json.dumps({"rules": [{"label": "w", "match": 123, "need_mib": 5000}]}),
                     encoding="utf-8")
    check_("field `match` bertipe int tidak menjatuhkan rules() — config tidak dipercaya, cadangan "
           "dipakai, dan TypeError tidak pernah keluar ke scheduler",
          all(r.get("degraded") for r in rules(weird))
          and gate("flux job", sampler=lambda: 1, config_path=weird)["state"] == "DENY",
          str(rules(weird))[:180])
    empty = Path(tempfile.mkdtemp()) / "gpu_budget.json"
    empty.write_text(json.dumps({"rules": [{"label": "e", "match": [], "need_mib": 5000}]}),
                     encoding="utf-8")
    check_("aturan tanpa kata kunci membuat SELURUH config tidak dipercaya — 'tidak cocok dengan "
           "apa pun' adalah bentuk gerbang terbuka yang paling mudah tidak terlihat",
          all(r.get("degraded") for r in rules(empty))
          and gate("flux job", sampler=lambda: 1, config_path=empty)["state"] == "DENY",
          str(rules(empty))[:180])
    check_("cadangan juga mengenal istilah `voxel remesh` — audit ronde ketiga menemukan ia hanya "
           "ada di config, jadi tanpa config pekerjaan 3D berlubang lolos sebagai bukan-kerja-GPU",
          gate("rapikan voxel remesh stage", sampler=lambda: 1,
               config_path=bad.parent / "tiada.json")["state"] == "DENY"
          and gate("rapikan voxel remesh stage", sampler=lambda: 1,
                   config_path=bad.parent / "tiada.json")["rule"] == "3d-trellis")
    check_("batas kata menghormati underscore: flux_store, test_flux dan reflux bukan kerja GPU, "
           "tapi TRELLIS.2 tetap terdeteksi (nama produk nyata di antrean ini)",
          gate("rapikan flux_store helpers", sampler=lambda: 1)["state"] == "NOT_GPU_WORK"
          and gate("test_flux.py merah", sampler=lambda: 1)["state"] == "NOT_GPU_WORK"
          and gate("REFLUX guard di compositor", sampler=lambda: 1)["state"] == "NOT_GPU_WORK"
          and gate("render TRELLIS.2 jadi glb", sampler=lambda: 1)["rule"] == "3d-trellis")
    st_box = Path(tempfile.mkdtemp()) / "gpu_budget.json"
    st_box.write_text(json.dumps({"rules": [{"label": "rusak", "match": ["q"],
                                             "need_mib": "sembilan"}]}), encoding="utf-8")
    stt = status(st_box, sampler=lambda: 12000, holders=[("ComfyUI", 1624)])
    check_("status() tidak crash saat ambang rusak: need None dilaporkan threshold_readable False "
           "dan fits None, bukan dibandingkan dengan int (int >= None pernah melempar TypeError)",
          stt["rules"][0]["threshold_readable"] is False and stt["rules"][0]["fits"] is None
          and stt["free_mib"] == 12000 and stt["holders"] == ["ComfyUI 1624MiB"], str(stt)[:200])
    check_("pencocokan berbatas kata: 'reflux' tidak memicu aturan 'flux', dan 'git checkpoint' "
           "tidak lagi ditahan ambang difusi (istilah itu dibuang dari config karena itu)",
          gate("perbaiki reflux test", sampler=lambda: 1)["state"] == "NOT_GPU_WORK"
          and gate("tambah git checkpoint hook", sampler=lambda: 1)["state"] == "NOT_GPU_WORK"
          and gate("flux txt2img poster", sampler=lambda: 1)["state"] == "DENY")
    # Bukan `or True`: aturan produksi harus sungguh ada, karena gerbang tanpa aturan adalah
    # gerbang yang terbuka. Berkas config nyata dibaca, jadi kalau nomornya hilang dari repo,
    # test ini yang merah — bukan run 3D jam tiga pagi.
    prod = rules(None)
    check_("config produksi punya setidaknya satu ambang GPU bernomor dan menyebut TRELLIS.2",
           any(isinstance(r["need_mib"], int) and r["need_mib"] >= 9000 for r in prod)
           and len(prod) >= 2, str(prod)[:200])
    check_("ambang produksi terbaca sebagai angka, bukan kalimat",
           all(isinstance(r["need_mib"], int) for r in prod), str(prod)[:160])
    for name, ok, detail in checks:
        print(f"[{'ok' if ok else 'GAGAL'}] gpu_gate: {name}" + (f" — {detail}" if not ok else ""))
    n_ok = sum(1 for _n, c_, _d in checks if c_)
    print(f"gpu_gate selftest: {n_ok}/{len(checks)} sesuai")
    return 0 if n_ok == len(checks) else 1


def main(argv=None):
    import argparse
    a = argparse.ArgumentParser(prog="gpu_gate.py")
    a.add_argument("cmd", choices=["status", "check", "selftest"])
    a.add_argument("text", nargs="*", default=[])
    x = a.parse_args(argv)
    if x.cmd == "selftest":
        return selftest()
    if x.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    txt = " ".join(x.text)
    r = gate(txt)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
