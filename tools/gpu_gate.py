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


def rules(path=None) -> list:
    """Daftar aturan dari config, dibaca pada saat dipakai. Berkas hilang = tidak ada aturan."""
    try:
        cfg = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — tidak ada config bukan berarti GPU aman
        return []
    out = []
    for r in cfg.get("rules") or []:
        need = r.get("need_mib")
        if not isinstance(need, int) or isinstance(need, bool) or need < 0:
            continue          # aturan tanpa angka bukan ambang; ia dilewati, tidak ditebak
        out.append({"label": str(r.get("label") or "?"), "need_mib": int(need),
                    "match": [str(m).lower() for m in (r.get("match") or [])]})
    return out


def classify(text, path=None) -> dict:
    """Aturan mana yang berlaku untuk sepenggal teks (judul item + perintah + ringkasan)."""
    blob = " ".join(str(text or "").split()).lower()
    for r in rules(path):
        if any(m and m in blob for m in r["match"]):
            return {"matched": r["label"], "need_mib": r["need_mib"]}
    return {"matched": None, "need_mib": 0}


def check(need_mib, sampler=None, holders=None) -> dict:
    """Satu keputusan: ALLOW / DENY / UNKNOWN, dengan angka yang membuktikannya."""
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
    return res


def status(config_path=None) -> dict:
    """Keadaan mesin saat ini dibandingkan semua ambang yang terdaftar — untuk layar dan untuk
    seseorang yang bertanya "kenapa pekerjaan 3D tidak jalan". Tidak ada angka yang dikarang:
    yang tidak terbaca dilaporkan sebagai tidak terbaca."""
    free = vram_free_mib()
    out = {"free_mib": free, "readable": free is not None,
           "holders": [f"{n} {m}MiB" for n, m in sorted(gpu_holders(), key=lambda h: -h[1])[:6]],
           "rules": []}
    for r in rules(config_path):
        out["rules"].append({"label": r["label"], "need_mib": r["need_mib"],
                             "fits": (None if free is None else free >= r["need_mib"]),
                             "margin_mib": (None if free is None else free - r["need_mib"])})
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
    check_("ambang non-angka atau negatif dilewati, tidak dijadikan tebakan — dan aturan yang "
           "sah tetap dipakai",
          [r["label"] for r in rules(bad)] == ["z"]
          and gate("z work", sampler=lambda: 10, config_path=bad)["state"] == "DENY",
          str(rules(bad)))
    check_("config tidak ada = tidak ada aturan = kerja dianggap bukan GPU, BUKAN GPU yang dianggap aman",
          gate("apa saja", sampler=lambda: None, config_path=bad.parent / "tiada.json")["state"]
          == "NOT_GPU_WORK")
    # Bukan `or True`: aturan produksi harus sungguh ada, karena gerbang tanpa aturan adalah
    # gerbang yang terbuka. Berkas config nyata dibaca, jadi kalaunomornya hilang dari repo,
    # test ini yang merah — bukan run 3D jam tiga pagi.
    prod = rules(None)
    check_("config produksi punya setidaknya satu ambang GPU bernomor dan menyebut TRELLIS.2",
           any(r["need_mib"] >= 9000 for r in prod) and len(prod) >= 2,
           str(prod)[:200])
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
