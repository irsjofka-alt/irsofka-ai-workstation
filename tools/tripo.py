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

DUA TEMPAT YANG BERBEDA, dan mengira keduanya sama adalah cara tercepat salah menghitung biaya:

* `studio.tripo3d.ai` — aplikasi web tempat operator login dan melihat **500 point**. Ini permukaan
  produk, bukan permukaan API.
* `platform.tripo3d.ai` / `developers.tripo3d.ai` — sisi developer: dokumen, billing, dan tempat
  API key dibuat.

**BELUM DIKETAHUI: apakah poin Studio dan kredit API adalah wallet yang sama.** Pencarian tidak
menjawabnya dan dokumen tidak bisa diambil saat modul ini ditulis (WebFetch kena batas, error 115).
`probe` ada justru untuk itu: ia memanggil endpoint balance yang tidak memakai poin, dan jawabannya
mengubah kalimat "mungkin" di atas menjadi angka. Sebelum itu, jangan tulis rencana produksi di atas
asumsi bahwa 500 poin Studio bisa dihabiskan oleh API — atau sebaliknya.
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
# Endpoint yang dulu ada di sini adalah TEBAKAN path V2 saya, dan salah semua. Yang di bawah ini
# TERUKUR: keluaran `tripo make ... --dry-run` pada 2026-10-15 11:1x, tanpa API key, tanpa poin
# terpakai. Sumber kebenaran tetap CLI-nya (VAST, repo vast-enterprise/Tripo-API-CLI), bukan daftar ini.
SPEND_COMMANDS = {"make", "ai", "redo"}

ENDPOINTS = {
    "text_to_model": {"kind": "http", "path": "/v3/generation/text-to-model", "method": "POST",
                      "task_type": "text_to_model",
                      "status": "TERUKUR lewat dry-run (rencana valid=true, model P1-20260311)",
                      "biaya_poin": "cost_notes kosong saat dry-run tanpa kunci — ukur lewat whoami"},
    "texture": {"kind": "http", "path": "/v3/models/texture", "method": "POST", "task_type": "texture_model",
                "status": "TERUKUR lewat dry-run", "biaya_poin": "belum terukur"},
    "convert": {"kind": "http", "path": "/v3/models/convert", "method": "POST", "task_type": "convert_model",
                "status": "TERUKUR lewat dry-run", "biaya_poin": "belum terukur"},
    "balance": {"path": "whoami", "method": "-", "kind": "cli",
                "status": "GRATIS dan wajib dipanggil pertama; butuh `tripo login` lebih dulu",
                "biaya_poin": 0},
}

# Dua label yang hanya DILAPORKAN (status/plan), tidak pernah dikirim sebagai payload — jadi kalau
# keduanya salah, yang salah adalah laporan, dan laporan yang salah dipercaya lebih mudah daripada
# yang tidak ada. `version` di bawah dulu bertuliskan "v2" sementara seluruh ENDPOINTS di berkas yang
# sama sudah saya ganti ke /v3 hasil dry-run: pembaca yang jujur akan menyimpulkan modul ini masih
# menargetkan API yang akan dipensiunkan. `model` adalah nama yang benar-benar muncul di keluaran
# `tripo make --dry-run` (2026-10-15 11:1x), bukan yang saya kira.
DEFAULTS = {"base_url": "https://api.tripo3d.ai", "version": "v3", "model": "Tripo/P1-20260311"}


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
    """Satu panggilan HTTP. Kunci hanya lewat header; tidak pernah ke URL maupun argv."""
    k = key_of(cfg)
    if not k:
        return {"ok": False, "why": "NO_KEY — isi config/tripo.json atau env TRIPO_API_KEY"}
    if not str(path).startswith("/"):
        # Penjaga kedua, di tempat URL dibentuk. `balance` bukan path REST tapi perintah CLI, dan
        # satu-satunya cara salah pakai fungsi ini adalah menyambungnya ke base_url lalu membaca
        # 404 sebagai bukti sesuatu tentang API-nya.
        return {"ok": False, "why": f"PATH_TIDAK_VALID {path!r} — bukan path REST; routes ber-kind "
                                    "'cli' harus lewat cli(), bukan call()"}
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


def probe(cfg=None, transport=None, cli_runner=None):
    """Verifikasi gratis: autentikasi + saldo, tanpa menyentuh poin.

    Rutenya dibaca dari `kind`, bukan ditebak. Terukur 2026-10-15: `balance` adalah perintah CLI
    (`tripo whoami`), bukan path REST — dan versi pertama fungsi ini melakukan
    `base_url + "tripo whoami (CLI)"`, yaitu URL karangan yang pasti 404 dan akan saya baca sebagai
    "API V2 sudah dipensiunkan". Sebuah pagar yang mengirim URL tidak masuk akal lebih berbahaya
    daripada tidak ada pagar, karena jawabannya terlihat seperti bukti.

    Kalau 200, label 'belum diverifikasi' boleh dihapus dan sisanya dibangun di atas dasar yang
    sama. Kalau 401, kuncinya salah. Kalau 404, dugaan path-nya salah dan itu justru informasi
    paling berguna.
    """
    cfg = cfg or load()
    spec = ENDPOINTS["balance"]
    kind = spec.get("kind")
    # Kunci diperiksa DI SINI, bukan hanya di dalam call(). probe() menerima transport yang bisa
    # disuntik supaya bisa diuji tanpa jaringan — dan kalau satu-satunya penjaga kunci hidup di
    # call(), jalur yang diuji justru melewati jalur tanpa kunci. Terukur: tes "NO_KEY" dulu lulus
    # dengan salah sampai transport palsunya menjawab 200 untuk permintaan tanpa kunci.
    if not key_of(cfg):
        return {"endpoint": spec["path"], "kind": kind, "ok": False, "status": None,
                "why": "NO_KEY — jalankan `tripo login` (operator), atau isi config/tripo.json / "
                       "env TRIPO_API_KEY untuk jalur HTTP",
                "verdict": "UNVERIFIED"}
    if kind == "cli":
        run = cli_runner or (lambda argv: cli(argv, spend=False))
        r = run(["whoami", "--json"])
        out = {"endpoint": f"tripo {' '.join(['whoami', '--json'])}", "kind": "cli",
               "ok": bool(r.get("ok")), "status": r.get("exit"),
               "why": (r.get("why") or r.get("stderr") or "")[:200]}
        if r.get("ok"):
            out["balance_shape"] = str(r.get("stdout"))[:200]
            out["verdict"] = ("SALDO DAN AUTENTIKASI TERBUKTI lewat CLI — catat angkanya, karena "
                              "inilah satu-satunya angka biaya yang boleh dipakai memutuskan "
                              "--spend")
        elif "login" in out["why"].lower() or r.get("exit") in (1, 2):
            out["verdict"] = ("CLI MENOLAK — paling sering karena belum `tripo login`. Itu pekerjaan "
                              "operator (sesi browser), bukan sesuatu yang boleh dilewati modul ini")
        else:
            out["verdict"] = "UNVERIFIED"
        return out
    if kind != "http":
        # kind yang tidak dikenal = tidak ada panggilan sama sekali. Menebak "mungkin HTTP" adalah
        # cara fungsi ini kembali mengarang URL seperti versi pertama.
        return {"endpoint": spec["path"], "kind": kind, "ok": False, "status": None,
                "why": f"KIND_TIDAK_DIKENAL {kind!r} — hanya 'http' dan 'cli' yang tahu cara memanggil",
                "verdict": "UNVERIFIED"}
    fn = transport or (lambda c, p, m, b: call(c, p, m, b))
    r = fn(cfg, spec["path"], spec["method"], None)
    out = {"endpoint": spec["path"], "kind": kind, "ok": bool(r.get("ok")),
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


def cost_is_known(v) -> bool:
    """Biaya dianggap TERUKUR hanya kalau ia ANGKA.

    Ini bug yang diperbaiki di sini, bukan penyederhanaan gaya. Gerbang sebelumnya membandingkan
    `spec["biaya_poin"] == "tidak diketahui"`, padahal tidak satu pun entri di ENDPOINTS berisi
    string itu — yang tertulis adalah "belum terukur" dan "cost_notes kosong saat dry-run ...".
    Akibatnya `submit(..., spend=True)` pada `text_to_model` lolos dari pagar dan benar-benar
    memakai poin. Sebuah pagar yang hanya cocok dengan nilai yang tidak pernah ada adalah pagar
    yang terbuka, dan tes teks-statik ("belum terukur" ada di pesan error) tidak bisa melihat itu
    karena pesannya memang berisi kata itu sejak awal.
    """
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def submit(cfg, kind, source, spend=False, cli_runner=None):
    """Dry-run kecuali `--spend` disebut. Yang dicek sebelum mengirim adalah apakah biayanya diketahui."""
    spec = ENDPOINTS.get(kind)
    if not spec:
        return {"ok": False, "why": f"jenis {kind!r} tidak ada di daftar — yang ada: "
                                    f"{sorted(ENDPOINTS)}"}
    is_cli = spec.get("kind") == "cli"
    target = (f"tripo {spec['path']}" if is_cli
              else f"{spec['method']} {cfg['base_url'].rstrip('/')}{spec['path']}")
    known = cost_is_known(spec["biaya_poin"])
    plan = {"would_call": target, "route": spec.get("kind"), "source": str(source),
            "cost_credits": spec["biaya_poin"], "cost_known": known, "model": cfg.get("model")}
    if not spend:
        return {"ok": True, "sent": False, "dry_run": plan,
                "why": "dry-run: tidak ada permintaan yang berangkat, tidak ada poin yang terpakai. "
                       "Tambahkan --spend untuk benar-benar mengirim."}
    if not known:
        return {"ok": False, "sent": False, "plan": plan,
                "why": f"DITOLAK: biaya {kind!r} belum terukur (tercatat {spec['biaya_poin']!r}, dan "
                       "yang boleh membayar hanya angka). Operator meminta test yang tidak mahal, "
                       "jadi mengirim tanpa tahu harganya adalah cara paling cepat melanggar "
                       "permintaan itu. Ukur lewat `tripo.py probe` (whoami, gratis), lalu isi "
                       "angkanya di ENDPOINTS."}
    if is_cli:
        run = cli_runner or (lambda argv: cli(argv, spend=True))
        r = run([spec["path"], "--json"])
        r["plan"] = plan
        r["sent"] = True
        return r
    body = {"type": spec.get("task_type", kind), "input": str(source)}
    r = call(cfg, spec["path"], spec["method"], body)
    r["plan"] = plan
    r["sent"] = True
    return r


def cli(argv, spend=False, timeout=600):
    """Jalankan tripo-cli resmi (VAST), dengan pagar biaya yang sama seperti modul ini.

    `tripo-cli` dipasang 2026-10-15 ke `~/runtime/npm-global` (bukan $HOME, sesuai §2) dan
    menjelaskan hal yang tidak bisa saya pastikan dari dokumen: ia bicara ke **API V3**
    (`--model tripo-v3.1 / tripo-p1 / tripo-p2`), punya `whoami` yang menampilkan saldo, dan
    `make --dry-run` yang menyusun rencana tanpa memakai poin. Karena itu tabel ENDPOINTS di
    atas jadi usang — jalurnya sekarang CLI, bukan tebakan path V2 saya.

    Yang TIDAK diambil alih dari CLI: `--yes`-nya otomatis aktif saat headless, artinya
    konfirmasi dilepas persis di lingkungan saya. Jadi fungsi ini memaksa `--dry-run` kecuali
    `spend=True` disebut, dan menambahkan `--no-open` supaya tidak ada browser yang terbuka di
    desktop operator saat dia tidur.
    """
    exe = os.environ.get("TRIPO_CLI", os.path.expanduser("~/runtime/npm-global/bin/tripo"))
    if not Path(exe).exists():
        return {"ok": False, "why": f"tripo-cli tidak ada di {exe} — npm install -g tripo-cli"}
    args = [exe, *argv]
    # Hanya perintah yang bisa memakai poin yang dipaksa --dry-run. Versi pertama menyisipkannya ke
    # SEMUA perintah, dan `tripo whoami --dry-run` langsung mati: "error: unknown option '--dry-run'"
    # — pagar yang menolak perintah baca gratis justru membuat orang menggugurkan pagarnya.
    if not spend and argv and argv[0] in SPEND_COMMANDS and "--dry-run" not in args:
        args.append("--dry-run")
    if "--no-open" not in args:
        args.append("--no-open")
    import subprocess
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"TRANSPORT {str(e)[:160]}"}
    out = {"ok": r.returncode == 0, "exit": r.returncode, "spent": bool(spend),
           "argv_safe": [a if not a.startswith("AIza") else "[KEY]" for a in args],
           "stdout": (r.stdout or "")[:1200], "stderr": (r.stderr or "")[:400]}
    return out


def selftest():
    """Semuanya diuji pada transport palsu — tidak ada satu byte pun yang keluar dari mesin."""
    cfg = {**DEFAULTS, "api_key": "TRIPO-KEY-PALSU-12345"}
    seen = []
    cli_seen = []

    def fake_http(c, path, method, body):
        seen.append((method, path, body))
        return {"ok": True, "status": 200, "json": {"data": {"available": 500}}}

    def fake_cli(argv, spend=False):
        cli_seen.append((list(argv), spend))
        return {"ok": True, "exit": 0, "stdout": '{"credits": 500}', "stderr": ""}

    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # `balance` adalah rute CLI, bukan path REST. Tes pertama dulu ditulis sebagai
    # `seen[0][1].endswith("balance")` dan merah karena probe() tidak lagi menyentuh HTTP sama
    # sekali — tetapi yang sebenarnya diuji di sini adalah hal yang lebih penting dari nama
    # endpoint: probe harus memakai rute GRATIS, dan harus TIDAK membangun URL dari string CLI.
    p = probe(cfg, transport=fake_http, cli_runner=fake_cli)
    check("probe memakai rute gratis (CLI whoami) dan TIDAK membangun satu pun URL HTTP "
          f"(http dipanggil: {len(seen)})",
          p["ok"] and p["kind"] == "cli" and not seen and cli_seen
          and cli_seen[0][0] == ["whoami", "--json"] and cli_seen[0][1] is False, str(cli_seen))
    check("probe menolak memfabrikasi URL: call() untuk rute non-path dijawab PATH_TIDAK_VALID",
          call(cfg, "whoami", "GET", None).get("ok") is False
          and "PATH_TIDAK_VALID" in call(cfg, "whoami", "GET", None)["why"],
          str(call(cfg, "whoami", "GET", None)))

    d = submit(cfg, "text_to_model", "a mossy rock", cli_runner=fake_cli)
    check("submit TANPA --spend tidak mengirim apa pun — HTTP maupun CLI diam",
          d.get("ok") and d.get("sent") is False and not seen and len(cli_seen) == 1,
          f"http={len(seen)} cli={len(cli_seen)}")

    # Ini tes yang menyelamatkan poin operator: biaya `text_to_model` adalah CALAT, bukan angka.
    # Versi sebelumnya membandingkan dengan string "tidak diketahui" yang tidak pernah ada di
    # tabel, jadi cabang ini tidak pernah terbuka dan --spend akan langsung memanggil API.
    s = submit(cfg, "text_to_model", "a mossy rock", spend=True, cli_runner=fake_cli)
    check("--spend pada operasi berbiaya-tak-diketahui DITOLAK dan tidak ada rute yang dipanggil "
          "(pagar lama dibandingkan dengan nilai yang tidak ada di tabel = pagar terbuka)",
          not s.get("ok") and s.get("sent") is False and s.get("plan", {}).get("cost_known") is False
          and not seen and len(cli_seen) == 1, str(s)[:200])
    sk = submit({**cfg}, "balance", "-", spend=True, cli_runner=fake_cli)
    check("--spend BOLEH lewat begitu biayanya angka 0 (rute balance), jadi pagar tidak menolak "
          "segalanya — yang diukur adalah bentuk biayanya, bukan ada/tidaknya pagar",
          sk.get("sent") is True and len(cli_seen) == 2, str(sk)[:160])

    st = status(cfg)
    check("status melapor panjang + sidik jari, tidak pernah menampilkan kunci",
          st["key_shown"] is False and st["key_present"]
          and st["key_length"] == len("TRIPO-KEY-PALSU-12345")
          and len(st["key_fingerprint"]) == 12, str(st))

    nokey = probe({**DEFAULTS}, transport=fake_http, cli_runner=fake_cli)
    check("tanpa kunci ia melapor NO_KEY lebih dulu, tanpa memanggil CLI atau HTTP",
          not nokey["ok"] and "NO_KEY" in nokey["why"] and len(cli_seen) == 2 and not seen,
          str(nokey)[:160])

    kind_box = {"kind": "cli"}

    def fake404(c, path, method, body):
        seen.append((method, path, body))
        return {"ok": False, "status": 404, "why": "not found"}
    real_spec = dict(ENDPOINTS["balance"])
    ENDPOINTS["balance"]["kind"] = "http"
    ENDPOINTS["balance"]["path"] = "/v2/account"
    try:
        p404 = probe(cfg, transport=fake404)
    finally:
        ENDPOINTS["balance"] = real_spec
    check("404 pada rute HTTP dibaca sebagai 'kemungkinan V2 dipensiunkan', bukan keberhasilan",
          "V2" in p404["verdict"] and seen[-1][1] == "/v2/account", str(p404)[:180])
    bad = dict(ENDPOINTS["balance"]); bad["kind"] = "carrier-pigeon"
    real_balance = ENDPOINTS["balance"]
    ENDPOINTS["balance"] = bad
    try:
        unk = probe(cfg, transport=fake_http, cli_runner=fake_cli)
    finally:
        ENDPOINTS["balance"] = real_balance
    check("kind yang tidak dikenal tidak boleh ditebak jadi HTTP: tidak ada panggilan, UNVERIFIED",
          not unk["ok"] and "KIND_TIDAK_DIKENAL" in unk["why"] and len(cli_seen) == 2, str(unk)[:160])

    # Pagarnya sendiri diuji tanpa memanggil CLI: yang diukur adalah apakah --dry-run dan --no-open
    # benar-benar disisipkan, dan apakah ia TIDAK menyisipkannya saat operator memang menyuruh bayar.
    import types
    _captured = {}

    def fake_run(args, **kw):
        _captured["args"] = list(args)
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    real_sub = __import__("subprocess").run
    __import__("subprocess").run = fake_run
    try:
        cli(["make", "a rock"], spend=False)
        dry = _captured.get("args", [])
        cli(["make", "a rock"], spend=True)
        paid = _captured.get("args", [])
    finally:
        __import__("subprocess").run = real_sub
    check("cli memaksa --dry-run + --no-open kecuali spend=True "
          f"(tanpa spend: {dry[-2:]} · dengan spend: {paid[-1:]})",
          "--dry-run" in dry and "--no-open" in dry and "--dry-run" not in paid)
    for name, cond, detail in checks:
        print(f"[{'ok' if cond else 'GAGAL'}] tripo: {name}" + (f" — {detail}" if not cond else ""))
    n_ok = sum(1 for _n, c, _d in checks if c)
    print(f"tripo selftest: {n_ok}/{len(checks)} sesuai")
    return 0 if n_ok == len(checks) else 1


def main(argv=None):
    a = argparse.ArgumentParser(prog="tripo.py")
    a.add_argument("cmd", choices=["status", "probe", "capabilities", "submit", "selftest", "cli"])
    # REMAINDER, bukan nargs="*": argparse harus TIDAK mencerna flag yang ditujukan ke tripo-cli.
    # nargs="*" terbukti salah: `tripo.py cli make x --for game-mobile` gagal di argparse saya
    # sendiri sebelum sempat menyentuh CLI-nya.
    a.add_argument("rest", nargs=argparse.REMAINDER, default=[])
    a.add_argument("--kind", default="text_to_model")
    a.add_argument("--input", default=None)
    a.add_argument("--spend", action="store_true")
    x = a.parse_args(argv)
    if x.cmd == "selftest":
        return selftest()
    if x.cmd == "cli":
        r = cli(x.rest, spend=x.spend)
        print(json.dumps(r, ensure_ascii=False)); return 0 if r.get("ok") else 1
    if x.cmd == "capabilities":
        print(json.dumps({
            "endpoints": ENDPOINTS, "defaults": DEFAULTS,
            "surfaces": {
                "studio": "studio.tripo3d.ai — aplikasi web; di sinilah 500 point operator terlihat",
                "api": "platform.tripo3d.ai / developers.tripo3d.ai — dokumen, billing, API key"},
            "unknown": "apakah poin Studio dan kredit API satu wallet yang sama — BELUM DIKETAHUI; "
                       "`tripo.py probe` menjawabnya tanpa memakai poin",
            "warning": "API V2 Tripo dinyatakan akan dipensiunkan; verifikasi lewat `tripo.py probe` "
                       "sebelum membangun apa pun di atas path ini"},
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
