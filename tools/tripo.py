#!/usr/bin/env python3
"""Jalur Tripo3D — disiapkan, belum dipakai, dan tidak bisa menghabiskan poin tanpa diperintah.

Permintaan operator 2026-10-15 11:0x: "persiapkan juga nanti jalur TRIPO3d.ai (hanya persiapkan
saja) … saya sudah login tripo di browser default dan memiliki 500 point, jadi kalau mau test juga
bisa tapi jangan test yang point nya mahal."

Tiga batas yang membuat modul ini aman dibiarkan ada:

* **Tidak ada satu pun panggilan berbayar tanpa `--spend`.** `submit` default-nya dry-run: ia
  mencetak permintaan yang AKAN dikirim dan berhenti. Poin hanya bergerak kalau ada kata itu.
* **Kunci tidak lewat argv dan tidak lewat URL.** Dibaca dari `config/tripo.json` (di-gitignore)
  atau env `TRIPO_API_KEY`, dikirim sebagai header `Authorization`. argv terlihat oleh `ps` ke
  semua pengguna mesin yang sama; URL terlihat oleh log proxy.
* **Yang belum diverifikasi diberi label.** WebFetch ke dokumen resmi diblokir batas kuota
  (error 115) pada saat modul ini ditulis, jadi daftar endpoint di bawah adalah pengetahuan
  yang belum dikonfrontasi ke dokumen maupun ke server. `probe` ada supaya label itu bisa
  dihapus dengan satu panggilan gratis.

FAKTA PENTING dari pencarian web (bukan dari dokumen langsung): **API V2 Tripo akan resmi
dipensiunkan dan endpoint V2 berhenti menerima request; migrasi ke V3 dianjurkan.** Menyiapkan
klien V2 di sini berarti menyiapkan rute mati, jadi `base_url` dan `version` disimpan di config
dan dibaca ulang setiap panggilan — bukan di-hardcode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "tripo.json"

# Semua baris ini BELUM TERVERIFIKASI. Labelnya sengaja menempel di data, bukan di komentar,
# supaya layar yang menampilkan daftar ini tidak bisa membuatnya terlihat seperti fakta.
ENDPOINTS = {
    "balance": {"path": "/v2/account/balance", "method": "GET", "biaya_poin": 0,
                "status": "belum diverifikasi — inilah yang harus dipanggil pertama, karena gratis"},
    "image_to_model": {"path": "/v2/generate/image-to-3d", "method": "POST", "biaya_poin": "tidak diketahui",
                       "status": "belum diverifikasi; JANGAN dijalankan tanpa --spend"},
    "text_to_model": {"path": "/v2/generate/text-to-3d", "method": "POST", "biaya_poin": "tidak diketahui",
                      "status": "belum diverifikasi; JANGAN dijalankan tanpa --spend"},
    "task_status": {"path": "/v2/tasks/{task_id}", "method": "GET", "biaya_poin": 0,
                    "status": "belum diverifikasi"},
}

DEFAULTS = {"base_url": "https://api.tripo3d.ai", "version": "v2", "model": "Tripo/TripoSG"}


def load():
    if CONFIG.exists():
        try:
            return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
        except Exception:  # noqa: BLE001
            return dict(DEFAULTS)
    return dict(DEFAULTS)


def save(cfg):
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG)
    try:
        os.chmod(CONFIG, 0o600)      # berkas ini akan berisi kunci
    except OSError:
        pass


def key_of(cfg=None):
    cfg = cfg or load()
    return (os.environ.get("TRIPO_API_KEY") or cfg.get("api_key") or "").strip()


def call(cfg, path, method="GET", body=None, timeout=60):
    """Satu panggilan. Kunci hanya lewat header; tidak pernah ke URL maupun argv."""
    k = key_of(cfg)
    if not k:
        return {"ok": False, "why": "NO_KEY — isi config/tripo.json atau env TRIPO_API_KEY"}
    url = cfg["base_url"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {k}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return {"ok": True, "status": r.status, "json": json.loads(raw)}
        except json.JSONDecodeError:
            return {"ok": True, "status": r.status, "text": raw[:400]}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "why": (e.read() or b"").decode("utf-8", "replace")[:300].replace(k, "[KEY]")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"TRANSPORT {str(e)[:160]}"}


def probe(cfg=None, transport=None):
    """Verifikasi gratis: base URL + autentikasi, tanpa menyentuh poin.

    Kalau ini 200, label 'belum diverifikasi' pada baris balance boleh dihapus dan sisanya bisa
    dibangun di atas dasar yang sama. Kalau 401, kuncinya salah. Kalau 404, dugaan path-nya salah
    (kemungkinan besar karena V2 dipensiunkan) dan itu justru informasi yang paling berguna.
    """
    cfg = cfg or load()
    fn = transport or (lambda c, p, m, b: call(c, p, m, b))
    # Kunci diperiksa DI SINI, bukan hanya di dalam call(). probe() menerima transport yang bisa
    # disuntik supaya bisa diuji tanpa jaringan — dan kalau satu-satunya penjaga kunci hidup di
    # call(), jalur yang diuji justru melewati jalur tanpa kunci. Terukur: tes "NO_KEY" dulu lulus
    # dengan salah sampai transport palsunya menjawab 200 untuk permintaan tanpa kunci.
    if not key_of(cfg):
        return {"endpoint": ENDPOINTS["balance"]["path"], "ok": False, "status": None,
                "why": "NO_KEY — isi config/tripo.json atau env TRIPO_API_KEY",
                "verdict": "UNVERIFIED"}
    r = fn(cfg, ENDPOINTS["balance"]["path"], "GET", None)
    out = {"endpoint": ENDPOINTS["balance"]["path"], "ok": bool(r.get("ok")),
           "status": r.get("status"), "why": (r.get("why") or "")[:200]}
    if r.get("ok"):
        bal = (r.get("json") or {}).get("data") or r.get("json") or r.get("text")
        out["balance_shape"] = str(bal)[:160]
        out["verdict"] = "BASE-URL DAN AUTENTIKASI TERBUKTI"
    elif r.get("status") == 404:
        out["verdict"] = ("PATH DITOLAK — cocok dengan pengumuman bahwa API V2 dipensiunkan; "
                          "cari base/path V3 sebelum menulis klien apa pun di atasnya")
    elif r.get("status") == 401:
        out["verdict"] = "KUNCI DITOLAK — buat API key di dashboard, bukan pakai sesi browser"
    else:
        out["verdict"] = "UNVERIFIED"
    return out


def status(cfg=None):
    cfg = cfg or load()
    k = key_of(cfg)
    return {"base_url": cfg.get("base_url"), "version": cfg.get("version"),
            "model": cfg.get("model"), "key_present": bool(k),
            "key_length": len(k), "key_fingerprint": hashlib.sha256(k.encode()).hexdigest()[:12] if k else None,
            "config_path": str(CONFIG), "gitignored": True, "key_shown": False}


def submit(cfg, kind, source, spend=False):
    """Dry-run kecuali `--spend` disebut. Yang dicek sebelum mengirim adalah apakah biayanya diketahui."""
    spec = ENDPOINTS.get(kind)
    if not spec:
        return {"ok": False, "why": f"jenis {kind!r} tidak ada di daftar"}
    plan = {"would_call": f"{spec['method']} {cfg['base_url'].rstrip('/')}{spec['path']}",
            "source": str(source), "cost_credits": spec["biaya_poin"], "model": cfg.get("model")}
    if not spend:
        return {"ok": True, "sent": False, "dry_run": plan,
                "why": "dry-run: tidak ada permintaan yang berangkat, tidak ada poin yang terpakai. "
                       "Tambahkan --spend untuk benar-benar mengirim."}
    if spec["biaya_poin"] == "tidak diketahui":
        return {"ok": False, "sent": False, "plan": plan,
                "why": "DITOLAK: biaya operasi ini belum terukur. Operator meminta test yang tidak "
                       "mahal, jadi mengirim tanpa tahu harganya adalah cara paling cepat melanggar "
                       "permintaan itu. Ukur dulu lewat dashboard atau dokumen, lalu isi angkanya."}
    body = {"type": kind, "input": str(source)}
    r = call(cfg, spec["path"], spec["method"], body)
    r["plan"] = plan
    r["sent"] = True
    return r


def selftest():
    """Semuanya diuji pada transport palsu — tidak ada satu byte pun yang keluar dari mesin."""
    cfg = {**DEFAULTS, "api_key": "TRIPO-KEY-PALSU-12345"}
    seen = []

    def fake_ok(c, path, method, body):
        seen.append((method, path, body))
        return {"ok": True, "status": 200, "json": {"data": {"available": 500}}}

    p = probe(cfg, transport=fake_ok)
    ok1 = p["ok"] and seen and seen[0][1].endswith("balance") and seen[0][2] is None
    print(f"[{'ok' if ok1 else 'GAGAL'}] tripo: probe memanggil endpoint GRATIS lebih dulu "
          f"(dipanggil: {seen})")

    d = submit(cfg, "image_to_model", "/tmp/x.png")
    ok2 = d.get("ok") and d.get("sent") is False and len(seen) == 1
    print(f"[{'ok' if ok2 else 'GAGAL'}] tripo: submit TANPA --spend tidak mengirim apa pun "
          f"(jumlah panggilan total tetap {len(seen)})")

    s = submit(cfg, "image_to_model", "/tmp/x.png", spend=True)
    ok3 = (not s.get("ok")) and s.get("sent") is False and "belum terukur" in s.get("why", "")
    print(f"[{'ok' if ok3 else 'GAGAL'}] tripo: --spend pada operasi berbiaya-tak-diketahui DITOLAK "
          f"— permintaan operator 'jangan test yang mahal' ditegakkan kode, bukan oleh ingatan")

    st = status(cfg)
    ok4 = st["key_shown"] is False and st["key_present"] and st["key_length"] == len("TRIPO-KEY-PALSU-12345") and len(st["key_fingerprint"]) == 12
    print(f"[{'ok' if ok4 else 'GAGAL'}] tripo: status melapor panjang + sidik jari, tidak pernah "
          f"menampilkan kunci")

    nokey = probe({**DEFAULTS}, transport=fake_ok)
    ok5 = not nokey["ok"] and "NO_KEY" in nokey["why"]
    print(f"[{'ok' if ok5 else 'GAGAL'}] tripo: tanpa kunci ia melapor NO_KEY, tidak menebak-nebak")

    p404 = probe(cfg, transport=lambda c, path, m, b: {"ok": False, "status": 404, "why": "not found"})
    ok6 = "V2" in p404["verdict"]
    print(f"[{'ok' if ok6 else 'GAGAL'}] tripo: 404 dibaca sebagai 'kemungkinan V2 dipensiunkan', "
          f"bukan sebagai keberhasilan")
    return 0 if all([ok1, ok2, ok3, ok4, ok5, ok6]) else 1


def main(argv=None):
    a = argparse.ArgumentParser(prog="tripo.py")
    a.add_argument("cmd", choices=["status", "probe", "capabilities", "submit", "selftest"])
    a.add_argument("--kind", default="image_to_model")
    a.add_argument("--input", default=None)
    a.add_argument("--spend", action="store_true")
    x = a.parse_args(argv)
    if x.cmd == "selftest":
        return selftest()
    if x.cmd == "capabilities":
        print(json.dumps({"endpoints": ENDPOINTS, "defaults": DEFAULTS,
                          "warning": "API V2 Tripo dinyatakan akan dipensiunkan; verifikasi lewat "
                                     "`tripo.py probe` sebelum membangun apa pun di atas path ini"},
                         ensure_ascii=False, indent=1))
        return 0
    cfg = load()
    if x.cmd == "status":
        print(json.dumps(status(cfg), ensure_ascii=False)); return 0
    if x.cmd == "probe":
        r = probe(cfg); print(json.dumps(r, ensure_ascii=False)); return 0 if r["ok"] else 1
    if not x.input:
        print(json.dumps({"ok": False, "why": "submit butuh --input"}, ensure_ascii=False)); return 1
    r = submit(cfg, x.kind, x.input, spend=x.spend)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
