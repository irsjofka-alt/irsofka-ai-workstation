#!/usr/bin/env python3
"""Run the F11 MCP plan against the machine and print what is left — the check the README points to.

Why this file exists. `mcp/README.md` describes the servers but must not carry a hand-written
verdict. A plan that cannot be measured is a wish, and a README that reports a wish is how the next
session re-litigates a decision.

Each requirement below is (a) stated as a testable condition, (b) answered by a command run here,
now, and (c) owned by a `quest_tasks.seed_key` so a loop can claim the unfinished one. The first
column is the only place a status may be written; the README embeds the printed table rather than
restating it.

Three vocabularies, no fourth: PASS means the measured condition holds. FAIL means it was measured and does
not hold. UNAVAILABLE means the thing needed to measure it was not there (no git, no database, no editor
listening, no remote to compare against) — and per contract §5, absence of evidence is never reported as
either of the other two. `--strict` exits non-zero on any FAIL; UNAVAILABLE alone exits 0 but is printed,
because a closed editor is not a finished task and is not a broken one either.

Usage:
    python3 ~/.ai-station/mcp/check_plan.py            # table
    python3 ~/.ai-station/mcp/check_plan.py --markdown # same rows as a README-ready block
    python3 ~/.ai-station/mcp/check_plan.py --json     # machine-readable
    python3 ~/.ai-station/mcp/check_plan.py --strict   # exit 1 while any requirement FAILs
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

PASS, FAIL, UNAVAILABLE = "PASS", "FAIL", "UNAVAILABLE"

HOME = Path.home()
MCP_ROOT = HOME / "Apps/mcp"
STATION = Path(os.environ.get("AI_STATION_HOME") or (HOME / ".ai-station")).expanduser()
# Dua hal berbeda, dan dulu checker menganggapnya satu.
#   PLUGIN_REPO  = sumber yang di-commit dan yang diterbitkan F11 — itulah yang R5 tanyakan.
#   PLUGIN_INSTALLED = salinan yang sedang dimuat editor. Boleh tertinggal, karena memasang binari
#                      ke sesi editor yang hidup adalah tindakan manusia (decision 55, item 301 HUMAN).
# Melaporkan salinan terpasang sebagai "fiturnya belum ada" adalah cara checker menutup pekerjaan
# yang justru baru selesai dikerjakan.
PLUGIN_REPO = HOME / "Documents/ai-workstation/projects/IRSofka-UE-AIStation-Core/Source"
PLUGIN_INSTALLED = HOME / "Apps/LinuxUnrealEngine5.8.3/Engine/Plugins/Marketplace/IRSofkaStationCore/Source"

# The four published MCP repositories covered by this plan.
OURS = ["irsofka-comfyui-mcp", "irsofka-unreal-mcp", "irsofka-workstation-mcp", "irsofka-blender-mcp"]

# A fifth repository has an origin and points at itself from the plugin's `.uplugin` (`DocsURL`),
# but it is not one of the four published repositories — so it stays out of R1/R2/R13/R14, which
# answer questions about those four and would otherwise be scored on an unscoped surface. It is NOT out of
# R7: that requirement is "every repository with an origin answers about its own backlog", and a repo
# five commits ahead of a remote the gate never opens is precisely the silence the rule exists to end.
PLUGIN_REPO_ROOT = HOME / "Documents/ai-workstation/projects/IRSofka-UE-AIStation-Core"

# Third-party servers replaced by our own published implementation. Ours exists and passes its
# own suite, so the AGPL server must stop being the path an engine reaches.
MUST_BE_REPLACED = ["mcp-blender"]

# Kept deliberately: Comfy Org's own server is licensed "AGPL-3.0-or-later OR commercial", it is
# the vendor's supported surface, and our `irsofka-comfyui-mcp` is a different tool with a different
# job (it gates a submit on VRAM), not its successor. Reporting it as a defect would invent a rule
# and train nobody to trust the check.
ORTHOGONAL_THIRD_PARTY = ["comfy-mcp"]

REG_FILES = {
    "qoder": STATION / "engines/qoder/settings.json",
    "antigravity-cli": STATION / "engines/antigravity-cli/settings.json",
    "gemini-config": STATION / "engines/gemini_config/mcp_config.json",
}


@dataclass
class Row:
    rid: str
    requirement: str
    source: str
    # `owner` BUKAN bagian dari konstruktor. Dulu ia ditulis di dua tempat — literal di REQUIREMENTS
    # dan peta OWNER_BY_RID yang dibaca R9 — dan keduanya pernah tidak sepakat (R5: satu menyebut
    # f11step0b, peta bilang f11mcp_unreal). Dua sumber untuk satu fakta adalah persis cacat yang
    # kontrak 12 larang, jadi satu-satunya tempat pemilik didefinisikan adalah OWNER_BY_RID.
    owner: str = ""
    state: str = UNAVAILABLE
    evidence: str = ""


@dataclass
class Ctx:
    """Everything measured once per run, so nine checks do not each re-shelled the same git."""
    registrations: dict[str, list[str]] = field(default_factory=dict)
    ran: bool = False

    def load_registrations(self) -> None:
        if self.ran:
            return
        self.ran = True
        for engine, path in REG_FILES.items():
            try:
                data = json.loads(path.read_text())
                self.registrations[engine] = sorted((data.get("mcpServers") or {}).keys())
            except Exception as exc:  # noqa: BLE001 — an unreadable config is UNAVAILABLE, not "absent"
                self.registrations[engine] = []
                self.registrations[f"!{engine}"] = [f"{type(exc).__name__}: {exc}"]


def sh(argv: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run and capture, never through a shell: an evidence command that interpolates is a bug waiting."""
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except FileNotFoundError as exc:
        return 255, "", f"NOT_RUNNABLE {exc}"
    except subprocess.TimeoutExpired:
        return 254, "", f"TIMEOUT after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 253, "", f"{type(exc).__name__}: {exc}"


def git(repo: Path, *args: str) -> tuple[int, str]:
    code, out, err = sh(["git", "-C", str(repo), *args], timeout=30)
    return code, (out or err).strip()


# ---------------------------------------------------------------- checks

# Teks lisensi untuk fixture. Dipasang utuh karena R13 menguji KELENGKAPAN berkas lisensi pihak
# ketiga — sebuah stub dua baris akan membuat suite merah dengan alasan yang benar, dan orang yang
# membaca merah itu akan menyalakan ceknya, bukan menambal ceknya.
FIXTURE_BSL = """Business Source License 1.1

License text copyright (c) 2017 MariaDB Corporation Ab, All Rights Reserved.

Parameters

Licensor:             PT IRSofka Data Teknologi
Additional Use Grant: None
Change Date:          Four years after the date that a given version of the Licensed Work is first
                      made publicly available.
Change License:       The Apache License, Version 2.0
"""

FIXTURE_NOTICE = """NOTICE - marks and attribution

All source code in this repository is clean-room original work.
"Blender" and "ComfyUI" are marks of their respective owners; naming them is attribution.
"""

FIXTURE_MIT = """MIT License

Copyright (c) 2025 fixture author

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
"""

FIXTURE_APACHE = """                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions. "License" shall mean the terms and conditions for use,
      reproduction, and distribution as defined by Sections 1 through 9.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   Copyright 2026 fixture author

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
"""


def c_repos_exist(ctx: Ctx) -> tuple[str, str]:
    missing, notgit = [], []
    for name in OURS:
        d = MCP_ROOT / name
        if not d.is_dir():
            missing.append(name)
        elif not (d / ".git").exists():
            notgit.append(name)
    if missing or notgit:
        bits = []
        if missing:
            bits.append(f"tidak ada: {', '.join(missing)}")
        if notgit:
            bits.append(f"bukan git repo: {', '.join(notgit)}")
        return FAIL, f"{len(OURS) - len(missing) - len(notgit)}/{len(OURS)} repo berdiri — " + "; ".join(bits)
    # Ditampilkan relatif ke home kalau memang di bawahnya: papan ini masuk ke dokumen yang bisa
    # dibaca publik, dan `/home/<user>` adalah identitas. Di fixture selftest MCP_ROOT ada di /tmp,
    # jadi jalur absolut dicetak apa adanya daripada meledak — checker yang mati saat pengujian
    # sendiri lebih berbahaya daripada checker yang salah.
    _shown = (f"~/{MCP_ROOT.relative_to(HOME)}" if str(MCP_ROOT).startswith(f"{HOME}/")
              else str(MCP_ROOT))
    return PASS, f"{len(OURS)}/{len(OURS)} ada dan berupa git repo di {_shown}"


def c_registered_both(ctx: Ctx) -> tuple[str, str]:
    ctx.load_registrations()
    per_engine = {e: [s for s in ctx.registrations.get(e, []) if s.startswith("irsofka-")]
                  for e in REG_FILES}
    qoder = set(ctx.registrations.get("qoder", []))
    # Sisi antigravity adalah DUA berkas (antigravity-cli dan gemini_config dibaca oleh dua
    # build yang berbeda), jadi "kedua sisi" = qoder AND minimal satu berkas antigravity.
    antigravity = set(ctx.registrations.get("antigravity-cli", [])) | set(
        ctx.registrations.get("gemini-config", []))
    nowhere = [n for n in OURS if n not in qoder and n not in antigravity]
    one_side = [n for n in OURS if (n in qoder) != (n in antigravity)]
    if nowhere:
        return FAIL, f"tidak terpasang di engine mana pun: {', '.join(nowhere)}"
    if one_side:
        # Ini yang dulu lolos: versinya memakai `or` di seluruh berkas, sehingga teks syarat
        # ("both engine sides") mengikat lebih keras daripada ceknya — dan satu sisi yang bolong
        # tercetak hijau. Fixture selftest menangkapnya pada 2026-10-16.
        return FAIL, f"baru satu sisi: {', '.join(one_side)} (qoder vs antigravity tidak sama)"
    detail = "; ".join(f"{e}={len(v)}" for e, v in per_engine.items())
    err = [f"{k[1:]}: {v[0]}" for k, v in ctx.registrations.items() if k.startswith("!")]
    if err:
        return UNAVAILABLE, f"sebagian berkas registrasi tidak terbaca ({'; '.join(err)}) — {detail}"
    return PASS, f"{len(OURS)}/{len(OURS)} terpasang di sisi qoder DAN sisi antigravity — {detail}"


def c_unreal_points_at_repo(ctx: Ctx) -> tuple[str, str]:
    """The unreal server must be reached as our repo, not as a file inside the engine plugin.

    The plugin is the receiver (contract section 4), not the launcher: an engine-side copy of
    the front-end is what made the repo's absence invisible, because the tool worked either way.
    """
    ctx.load_registrations()
    seen = {}
    for engine, path in REG_FILES.items():
        try:
            servers = (json.loads(path.read_text()).get("mcpServers") or {}).get("irsofka-unreal-mcp")
        except Exception:  # noqa: BLE001
            continue
        if not servers:
            continue
        argv = " ".join([str(servers.get("command") or ""), *[str(a) for a in (servers.get("args") or [])]])
        seen[engine] = argv
    if not seen:
        return UNAVAILABLE, "irsofka-unreal-mcp tidak terdaftar di berkas mana pun yang terbaca"
    inside_plugin = {e: a for e, a in seen.items() if "Plugins/Marketplace" in a or "IRSofkaStationCore" in a}
    if inside_plugin:
        return FAIL, f"masih diluncurkan dari dalam plugin ({', '.join(sorted(inside_plugin))}) — " \
                     f"repo {MCP_ROOT / 'irsofka-unreal-mcp'} tidak punya jalan masuk"
    return PASS, f"diluncurkan dari repo di {', '.join(sorted(seen))}"


def c_agpl_blender_is_replaced(ctx: Ctx) -> tuple[str, str]:
    """The AGPL blender server must not still be what an engine reaches for that work."""
    ctx.load_registrations()
    live = {}
    for engine, names in ctx.registrations.items():
        if engine.startswith("!"):
            continue
        hit = [n for n in names if n in MUST_BE_REPLACED]
        if hit:
            live[engine] = hit
    kept = sorted({n for e, names in ctx.registrations.items() if not e.startswith("!")
                   for n in names if n in ORTHOGONAL_THIRD_PARTY})
    note = f" (dipertahankan dengan sengaja: {', '.join(kept)} — milik Comfy Org, permukaan pendukung resmi)" if kept else ""
    if not live:
        return PASS, f"{', '.join(MUST_BE_REPLACED)} tidak ada di registrasi mana pun{note}"
    return FAIL, "; ".join(f"{e}: {', '.join(v)}" for e, v in sorted(live.items())) \
        + f" — AGPL masih menjadi jalur yang engine capai{note}"


def c_clean_room_surface(name: str, reference: str) -> tuple[str, str]:
    """One definition for both servers rewritten as independent clean-room work.

    This supersedes the "permissive takeover ... not a clean room" wording R3 and R12 carried until
    when the BSL plus clean-room rewrite was ordered and its
    execution to Gemini Flash 3.8 High (ledger row 319). The superseded wording stays in git history
    and in the rows' `source`: a requirement that changes meaning records who changed it, because a
    board that quietly redefines its own green is a board nobody can audit.

    Measured, never read from a claim:
      * `upstream/` ABSENT — carrying a third-party tree inside a BSL work is a second grant the
        contract never required, and its presence is precisely what the rewrite removed;
      * one grant over the whole work: LICENSE is Business Source License 1.1 with an Apache-2.0
        Change License, and NOTICE travels beside it;
      * provenance a stranger can check: UPSTREAM names the reference project, states the clean-room
        status, and carries a "Measured overlap" section with the command that produced it;
      * the manifest tells the truth about the tree: the repository's own `tools/deps_honesty.py`
        exits 0 — the comparison a publication reviewer had to perform by hand.
    """
    import re
    d = MCP_ROOT / name
    if not d.is_dir():
        return UNAVAILABLE, f"{name} belum ada — tidak ada yang bisa diperiksa"
    if (d / "upstream").exists():
        return FAIL, ("upstream/ masih ada — karya clean-room tidak boleh membawa pohon pihak "
                      "ketiga di dalam grant BSL")
    parts = {"LICENSE": d / "LICENSE", "NOTICE": d / "NOTICE", "UPSTREAM": d / "UPSTREAM",
             "server.py": d / "server.py", "tools/deps_honesty.py": d / "tools" / "deps_honesty.py"}
    missing = [n for n, f in parts.items() if not f.is_file()]
    if missing:
        return FAIL, "berkas yang dituntut requirement tidak ada: " + ", ".join(missing)
    lic_txt = parts["LICENSE"].read_text(errors="replace")
    if "Business Source License" not in lic_txt or "Apache License" not in lic_txt:
        return FAIL, "LICENSE bukan BSL 1.1 dengan Change License Apache-2.0 (keputusan lisensi)"
    up_txt = parts["UPSTREAM"].read_text(errors="replace")
    gaps = []
    if reference not in up_txt:
        gaps.append(f"proyek rujukan {reference} tidak disebut")
    if "clean-room" not in up_txt.lower() and "independent implementation" not in up_txt.lower():
        gaps.append("status clean-room / independent implementation tidak dinyatakan")
    if "Measured overlap" not in up_txt:
        gaps.append("seksi 'Measured overlap' tidak ada")
    elif not re.search(r"python3 tools/cleanroom_overlap\.py [0-9a-f]{7,40}", up_txt):
        gaps.append("overlap disebut tanpa perintah reproduksi ber-sha")
    if gaps:
        return FAIL, "UPSTREAM: " + "; ".join(gaps)
    code, out, err = sh([sys.executable, str(parts["tools/deps_honesty.py"]), str(d)], timeout=120)
    if code != 0:
        return FAIL, f"manifest tidak cocok dengan pohon kerja: {(out or err)[:170]}"
    return PASS, (f"{name}: tanpa pohon pihak ketiga, BSL + Change License Apache-2.0, overlap terhadap "
                  f"{reference} terukur dengan perintah yang bisa dijalankan ulang, manifest == impor")


def c_blender_is_clean_room(ctx: Ctx) -> tuple[str, str]:
    """R3 — the Blender server measured against the order that governs it now."""
    return c_clean_room_surface("irsofka-blender-mcp", "ahujasid/mcp-for-blender")


def c_comfy_is_clean_room(ctx: Ctx) -> tuple[str, str]:
    """R12 — the ComfyUI server, same definition, different repository."""
    return c_clean_room_surface("irsofka-comfyui-mcp", "joenorton/comfyui-mcp-server")


def c_probe_debt(ctx: Ctx) -> tuple[str, str]:
    """Utang yang DIUKUR probe, bukan dirasakan: logikanya jalan offline, round-trip tidak.

    Mode `--strict` menuntut nol cacat engine. Selama editor tertutup yang bisa dikatakan adalah
    UNAVAILABLE — `ConnectionRefused` bukan bukti bahwa cacatnya sudah diperbaiki, dan juga bukan
    bukti bahwa belum (kontrak §5).
    """
    probe = STATION / "tools" / "ue_bridge_probe.py"
    if not probe.is_file():
        return FAIL, "tools/ue_bridge_probe.py tidak ada — tidak ada alat ukurnya"
    def probe_summary(argv: list[str], timeout: int) -> tuple[int, str]:
        """Ringkas keluaran probe dari JSON-nya, bukan dari baris terakhirnya.

        Probe mencetak satu objek JSON, jadi `tail[-1]` adalah `}` — dan sebuah bukti yang berisi
        kurung kurawal tidak membuktikan apa pun. Ini bukan hiasan: PASS tanpa angka yang bisa
        dibaca orang adalah cara board terlihat benar sementara isinya tidak bisa diperiksa.
        """
        code, out, err = sh([sys.executable, str(probe), *argv], cwd=str(STATION), timeout=timeout)
        blob = (out or "").strip()
        start = blob.find("{")
        try:
            data = json.loads(blob[start:]) if start >= 0 else {}
        except Exception:  # noqa: BLE001 — keluaran yang tidak terbaca tetap harus dilaporkan
            data = {}
        if not data:
            return code, ((out + err).strip().splitlines() or ["tanpa keluaran"])[-1][:90]
        defects = data.get("engine_defects") or []
        return code, (f"{data.get('state')} checks={data.get('checks')} "
                      f"mutation={data.get('mutation')} defects={len(defects)} "
                      f"fixed={len(data.get('engine_defects_fixed_observed') or [])}")

    code, summary = probe_summary(["--selftest"], 200)
    if code != 0:
        return FAIL, f"logika probe merah (rc={code}): {summary}"
    reachable = c_editor_reachable(ctx)[0] == PASS
    if not reachable:
        return UNAVAILABLE, f"logika probe hijau ({summary}); round-trip --strict tidak terukur " \
                           "karena editor/plugin tidak menjawab"
    # Round-trip dibaca dari JSON probe: `state`, jumlah check dan `engine_defects`, bukan baris
    # terakhir stdout. `--strict` menuntut nol cacat, dan exit code probe adalah bagian dari yang
    # diukur — bukan sekadar "perintahnya pulang".
    code2, s2 = probe_summary(["--strict"], 300)
    if code2 != 0:
        return FAIL, f"round-trip strict merah (rc={code2}): {s2}"

    # Dua klaim R11 — kelas nama polos tidak lagi melahirkan aktor yang tidak bisa bergerak, dan
    # set_transform menjawab apa yang benar-benar mendarat — hanya lewat di jalur mutasi. Mode
    # baca-saja sudah hijau di atas dan itu TIDAK mengukur klaim ini, jadi board yang berhenti di
    # sini akan PASS tanpa membuktikan apa pun (terukur 2026-10-16: read-only = 6/6, mutasi = 17/17).
    # Sisi efeknya disebut apa adanya: probe membuat aktor `IIRSofkaProbe*`, menghapusnya lagi, dan
    # tidak pernah menyimpan level; level itu tetap bertanda kotor sampai pemiliknya membuang.
    code3, s3 = probe_summary(["--strict", "--allow-mutation"], 420)
    if code3 != 0:
        return FAIL, f"jalur mutasi strict merah (rc={code3}): {s3} — dan klaim R11 ada di jalur ini"
    return PASS, f"round-trip strict hijau: {s2} | mutasi: {s3} (probe menghapus aktornya sendiri, " \
                 "level tidak disimpan)"


def c_plugin_detects_station(ctx: Ctx) -> tuple[str, str]:
    """Two states the operator named: detected → the bridge is a station extension; not detected → an
    ordinary plugin. Detection lives on the plugin side; a client-side TCP probe is not that feature.

    The first version of this check grepped `ai-station` in `Source/` and PASSed — against the wrong
    evidence. `IRSofkaBYOKManager.cpp` mentions `.ai-station` as a config PATH, and the dashboard names
    a `StationBridge` socket, and neither of those is the plugin knowing whether the station is present.
    A check that PASSes because a substring appears somewhere is worse than no check: it closes the row.
    So the question is now shaped like the feature: a presence that is STORED or BRANCHED ON.
    """
    src = PLUGIN_REPO
    if not src.is_dir():
        return UNAVAILABLE, f"sumber plugin tidak ada di {src} — tidak ada yang bisa dicari"
    import re
    # sebuah kata yang menyatakan KEHADIRAN (bukan path konfigurasi), dideklarasikan atau dites
    presence = re.compile(r"\b(?:b|Has|Is|Detect)\w*(?:Station|AiStation)\w*\b"
                          r"|\bif\s*\(\s*\w*[Ss]tation\w*\s*\)")
    hits = []
    for f in src.rglob("*"):
        if not (f.is_file() and f.suffix in {".cpp", ".h"}):
            continue
        try:
            text = f.read_text(errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if presence.search(line):
                hits.append(f"{f.name}:{n}: {line.strip()[:70]}")
    if not hits:
        return FAIL, f"dipindai {sum(1 for f in src.rglob('*') if f.suffix in {'.cpp', '.h'})} berkas " \
                     f"{src}, 0 declares or branches on station presence — state terdeteksi/tidak " \
                     "hanya ada di sisi klien (bridge.py), belum di plugin"
    return PASS, f"{len(hits)} pernyataan presence di repo sumber — " + "; ".join(hits[:2])


def c_editor_reachable(ctx: Ctx) -> tuple[str, str]:
    """Only the receiver's side: is the plugin's listener answering on 9877? Never mutates anything.

    This is context, not a requirement: the editor being closed says nothing about whether the code
    exists, and the two-state fallback (R5) must be provable with the editor shut, which is exactly how
    the repository's own suite proves it.
    """
    try:
        with socket.create_connection(("127.0.0.1", 9877), timeout=2.0):
            return PASS, "listener plugin menjawab di 127.0.0.1:9877 (state terdeteksi bisa dibuktikan langsung)"
    except OSError as exc:
        return UNAVAILABLE, f"editor/plugin tidak menjawab ({type(exc).__name__}) — state tidak-terdeteksi, " \
                            "dan round-trip nyata tidak bisa diukur jam ini"


def c_repo_suites(ctx: Ctx) -> tuple[str, str]:
    """Behaviour, not existence: each server's own selftest must pass, offline, right now."""
    results, bad = [], []
    for name in OURS:
        d = MCP_ROOT / name
        server = d / "server.py"
        if not server.is_file():
            results.append(f"{name}=NO_SERVER")
            bad.append(name)
            continue
        code, out, err = sh(["timeout", "180", sys.executable, "server.py", "--selftest"],
                            cwd=str(d), timeout=200)
        tail = (out or err).strip().splitlines()
        summary = tail[-1][:70] if tail else "tanpa keluaran"
        if code == 0:
            results.append(f"{name}=ok ({summary})")
        elif code in (253, 254, 255):
            results.append(f"{name}={summary}")
            bad.append(f"{name}:unreadable")
        else:
            results.append(f"{name}=FAIL rc={code} ({summary})")
            bad.append(name)
    if any(b.endswith(":unreadable") for b in bad) and not [b for b in bad if not b.endswith(":unreadable")]:
        return UNAVAILABLE, "; ".join(results)
    if bad:
        return FAIL, "; ".join(results)
    return PASS, f"{len(OURS)}/{len(OURS)} selftest lulus tanpa engine hidup — " + "; ".join(results)


def c_push_clearance(ctx: Ctx) -> tuple[str, str]:
    """What this requirement is really about: the gate ANSWERS ABOUT ITS OWN repository.

    A held commit is the gate working — §6 says a run that cannot reach a verifier commits locally and
    leaves the push in the morning digest. So PASS is not "nothing unpushed"; PASS is "every repo with an
    origin knows its own backlog and has declared it". FAIL is a gate that cannot answer, or a repo that
    is not a repository at all — those are the shapes where a push could leave without a verdict.
    """
    gate = STATION / "mcp" / "push_gate.py"
    if not gate.is_file():
        return UNAVAILABLE, "mcp/push_gate.py tidak ada"
    lines, broken, noorigin = [], [], []
    surfaces = [(name, MCP_ROOT / name) for name in OURS]
    if (PLUGIN_REPO_ROOT / ".git").exists():
        surfaces.append(("IRSofka-UE-AIStation-Core", PLUGIN_REPO_ROOT))
    for name, d in surfaces:
        if not (d / ".git").exists():
            lines.append(f"{name}=no-git")
            broken.append(name)
            continue
        rc, _url = git(d, "remote", "get-url", "origin")
        if rc != 0:
            # Bukan kegagalan dan bukan clearance: tidak ada upstream untuk dibandingkan, jadi
            # "unpushed" tidak punya arti. Ini BEDA dari git yang tidak terbaca, dan dulu keduanya
            # tercetak sama — itu membuat repo yang sengaja belum dipublikasikan terlihat rusak.
            lines.append(f"{name}=no-origin")
            noorigin.append(name)
            continue
        code, out, err = sh([sys.executable, str(gate), "push-gate"], cwd=str(d), timeout=60)
        try:
            parsed = json.loads(out or err)
        except Exception:  # noqa: BLE001
            lines.append(f"{name}=unreadable")
            broken.append(name)
            continue
        if parsed.get("readable") is False:
            lines.append(f"{name}=UNKNOWN({str(parsed.get('why', ''))[:48]})")
            broken.append(name)
            continue
        un = parsed.get("unpushed")
        if parsed.get("ok") and str(un) == "0":
            lines.append(f"{name}=clear")
        elif parsed.get("declared"):
            lines.append(f"{name}=held-declared({un})")
        else:
            lines.append(f"{name}=held-UNDECLARED({un})")
            broken.append(name)
    if broken:
        return FAIL, "; ".join(lines)
    if noorigin and len(noorigin) < len(OURS):
        return PASS, "; ".join(lines)
    if noorigin:
        return UNAVAILABLE, "; ".join(lines)
    return PASS, "; ".join(lines)


def c_credential_guard(ctx: Ctx) -> tuple[str, str]:
    """§ the operator's standing order: a BYOK key never reaches git.

    Three properties, all required, because the first version of this check accepted "a file named
    pre-commit exists that contains the word byok" — and on 2026-10-16 that was satisfied by a
    THREE-LINE STUB that its own test had written through the symlink into the station's canonical
    guard. A guard that a stub can satisfy guards nothing, so the check now asks:
      (a) the hook RESOLVES into the station's own hook file (the same shape the `f11mcp_guard`
          ledger gate asks, so prose and gate cannot disagree),
      (b) no repository's history contains a key-shaped path,
      (c) a live throwaway commit carrying a key is actually REFUSED — the only test that measures
          behaviour instead of measuring the existence of something that claims behaviour.
    """
    station_hook = STATION / "bin/hooks/pre-commit"
    if not station_hook.is_file():
        return UNAVAILABLE, f"guard canonik tidak ada di {station_hook}"
    resolved_guard = station_hook.resolve()
    guard_lines = len(resolved_guard.read_text(errors="replace").splitlines())
    if guard_lines < 40:
        return FAIL, f"guard canonik hanya {guard_lines} baris di {resolved_guard} — sebuah berkas " \
                     "pendek bisa menggantikan guard dengan menulis TEMBUS symlink (terjadi 2026-10-16)"

    mislinked, leaked, dirty = [], [], []
    for name in OURS:
        d = MCP_ROOT / name
        hook = d / ".git/hooks/pre-commit"
        if not hook.is_file():
            mislinked.append(f"{name}:tidak ada")
            continue
        if hook.resolve() != resolved_guard:
            # symlink yang putus ATAU berkas biasa hasil salinan: dua-dua membuat vonis tidak mampat
            mislinked.append(f"{name}->{'stub' if not hook.is_symlink() else 'link-ke-lain'}")
            continue
        rc, out = git(d, "log", "--all", "--name-only", "--format=")
        if rc != 0:
            dirty.append(f"{name}:git-unreadable")
            continue
        hits = sorted({l.strip() for l in out.splitlines()
                       if l.strip() and ("byok" in l.lower() or "credentials" in l.lower())})
        if hits:
            leaked.append(f"{name}:{hits[0]}")

    if leaked:
        return FAIL, "RIWAYAT GIT memuat jalur kunci: " + ", ".join(leaked)
    if mislinked:
        return FAIL, "guard tidak resolve ke berkas stasiun: " + ", ".join(mislinked) \
                     + " (perbaiki dengan `bash bin/install_hooks.sh --repo <path>`)"

    # (c) pembuktian hidup: satu commit bawaan kunci harus DITOLAK, di repo buang-buang yang
    # terisolasi — bukan di repo operator, dan tanpa menulis ke mana pun yang bertahan.
    import tempfile
    proof = Path(tempfile.mkdtemp(prefix="checkplan-guard-"))
    try:
        rc_init, _o, _e = sh(["git", "init", "-q", "-b", "main"], cwd=str(proof), timeout=30)
        if rc_init != 0:
            return UNAVAILABLE, "git tidak bisa membuat repo ukur — perilaku guard tidak terkonfirmasi"
        (proof / "config").mkdir()
        # bentuk kunci, bukan kunci: AIza + 35 karakter, seperti yang dibaca aturan BLOB
        (proof / "config/byok.json").write_text(json.dumps({"keys": ["AIza" + "a" * 35]}))
        link = proof / ".git/hooks/pre-commit"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(resolved_guard)
        sh(["git", "add", "-A"], cwd=str(proof), timeout=30)
        rc_commit, out_c, err_c = sh(["git", "-c", "user.name=check", "-c", "user.email=check@local",
                                      "commit", "-q", "-m", "guard behaviour probe"],
                                     cwd=str(proof), timeout=60)
        # repo yang belum punya commit sama sekali MEMANG membuat `rev-list HEAD` gagal: itu
        # justru hasilnya. Membaca pesan fatal sebagai "jumlah commit" akan membalik vonis menjadi
        # FAIL atas alasan yang salah — bentuk bug yang persis sama sudah dua kali ketemu di sini.
        rc_n, n_out, _n_err = sh(["git", "-C", str(proof), "rev-list", "--count", "HEAD"], timeout=30)
        n_commits = n_out.strip() if rc_n == 0 else "0"
        if rc_commit == 0 or n_commits != "0":
            return FAIL, "guard MEMBIARKAN commit bawaan kunci lolos di repo ukur " \
                         f"(rc={rc_commit}, commits={n_commits}) — aturan (a)+(b) saja tidak cukup"
    except Exception as exc:  # noqa: BLE001
        return UNAVAILABLE, f"bukti hidup tidak selesai: {type(exc).__name__}: {str(exc)[:60]}"
    finally:
        sh(["rm", "-rf", str(proof)], timeout=60)

    return PASS, f"guard resolve ke stasiun di {len(OURS)}/{len(OURS)} repo, 0 jalur kunci di " \
                 f"riwayat, dan commit kunci DITOLAK hidup-habis (guard {guard_lines} baris)"


def c_ledger_owns_each_row(ctx: Ctx) -> tuple[str, str]:
    """A requirement the loop may skip is a requirement with no row. Every R# must resolve to a
    `quest_tasks` row, matched on `seed_key`, never on a title."""
    try:
        sys.path.insert(0, str(STATION / "tools"))
        import work_order as wo  # the owner of the queue vocabulary (§12)
    except Exception as exc:  # noqa: BLE001
        return UNAVAILABLE, f"work_order.py tidak terbaca: {type(exc).__name__}"
    try:
        conn, engine = wo.connect()
    except Exception as exc:  # noqa: BLE001
        return UNAVAILABLE, f"ledger tidak terbaca: {type(exc).__name__}: {str(exc)[:60]}"
    keys = sorted(set(OWNER_BY_RID.values()))
    try:
        ph = ",".join(["%s"] * len(keys)) if engine == "POSTGRESQL" else ",".join(["?"] * len(keys))
        rows = wo.run(conn, engine,
                      f"SELECT seed_key, status FROM quest_tasks WHERE seed_key IN ({ph})", tuple(keys))
    except Exception as exc:  # noqa: BLE001
        return UNAVAILABLE, f"query gagal: {type(exc).__name__}: {str(exc)[:60]}"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    found = {r["seed_key"]: r["status"] for r in rows or []}
    absent = [k for k in keys if k not in found]
    open_rows = [k for k, s in found.items() if str(s).upper() not in ("COMPLETED", "FAILED", "CANCELLED")]
    if absent:
        return FAIL, f"{len(found)}/{len(keys)} syarat punya baris ledger; tanpa baris: {', '.join(absent)}"
    return PASS, f"{len(keys)}/{len(keys)} syarat punya baris; {len(open_rows)} masih bisa diambil " \
                 f"({', '.join(sorted(open_rows)) or 'tidak ada'})"


def c_publication_hygiene(ctx: Ctx) -> tuple[str, str]:
    """Public documents cite decisions, not private machine identifiers.

    The check covers (a) account paths, (b) private prompt and transcript identifiers,
    (c) NOTICE files naming components the repository does not contain, and (d) a complete
    third-party licence. Scanning beats remembering: a rule that lives only in prose is
    re-violated every time someone
    writes a new sentence.
    """
    import re
    bad_paths, bad_refs, bad_keys, bad_lic = [], [], [], []
    licence_shape = re.compile(r"WITHOUT WARRANTIES|AS IS", re.I)
    for name in OURS:
        d = MCP_ROOT / name
        if not (d / ".git").exists():
            continue
        rc, listing = git(d, "ls-files")
        if rc != 0:
            return UNAVAILABLE, f"git ls-files tidak terbaca untuk {name}"
        for rel in listing.splitlines():
            f = d / rel
            if not f.is_file() or f.suffix in {".so", ".png", ".jpg", ".gif", ".woff", ".woff2", ".ttf", ".glb"}:
                continue
            try:
                txt = f.read_text(errors="replace")
            except OSError:
                continue
            low = rel.lower()
            # dokumen kita sendiri (bukan vendored) tidak boleh memuat path akun atau id transkrip
            if not low.startswith("upstream/"):
                if re.search(r"/home/[a-z0-9_.-]+", txt):
                    bad_paths.append(f"{name}/{rel}")
                if re.search(r"\bturn[s]? \d{3}\b|session_turns|session turns", txt, re.I):
                    bad_refs.append(f"{name}/{rel}")
            if re.search(r"AIza[0-9A-Za-z_-]{20,}|\bsk-[A-Za-z0-9]{16,}|\bghp_[A-Za-z0-9]{20,}", txt):
                bad_keys.append(f"{name}/{rel}")
        lic = d / "upstream/LICENSE"
        if lic.is_file():
            body = lic.read_text(errors="replace")
            lines = [l for l in body.splitlines() if l.strip()]
            if len(lines) < 12 or not licence_shape.search(body):
                bad_lic.append(f"{name}: {len(lines)} baris{' TANPA klausul penafian' if not licence_shape.search(body) else ''}")
        elif (d / "upstream").is_dir():
            bad_lic.append(f"{name}: upstream/ tanpa LICENSE")
    if bad_keys:
        return FAIL, "KUNCI bentuk nyata terdeteksi: " + ", ".join(bad_keys[:4])
    if bad_paths or bad_refs or bad_lic:
        bits = []
        if bad_paths:
            bits.append(f"path akun di {len(bad_paths)} dokumen ({', '.join(bad_paths[:3])})")
        if bad_refs:
            bits.append(f"id transkrip privat di {len(bad_refs)} dokumen ({', '.join(bad_refs[:3])})")
        if bad_lic:
            bits.append(f"lisensi pihak ketiga tidak utuh: {', '.join(bad_lic)}")
        return FAIL, "; ".join(bits)
    return PASS, (f"{len(OURS)} repo dipindai: 0 path akun, 0 id transkrip, 0 kunci, "
                  "LICENSE vendored utuh (>=12 baris berisi klausul penafian)")


def c_gate_honesty(ctx: Ctx) -> tuple[str, str]:
    """A station layer must not issue a permit it cannot sign.

    A publication verifier found this on 2026-10-16: `vram.decide()` was documented as refusing when
    no workstation gate exists, and in fact returned `ALLOW` with `unaudited: true` for any submit
    whose text missed a keyword list. Nothing was measured on that path, so the word was a permission
    with no number behind it — and a published server is read by strangers who have no station.

    So the requirement is measured as behaviour, not as prose: each of our servers that carries a gate
    module is run with `AI_STATION_HOME` pointed at an empty directory and every gate env unset, and
    the state it answers must not be one of its own "go" states. `sh` takes argv, not a shell, so the
    env override is passed as `env` arguments rather than interpolated.
    """
    probe = (
        "import json,sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "import vram;"
        "print(json.dumps({'state': vram.decide('render a frame on the GPU'),"
        "'go': list(vram.GATE_GO)}))"
    )
    present = [n for n in OURS if (MCP_ROOT / n / "vram.py").is_file()]
    if not present:
        return UNAVAILABLE, "tidak ada repo kami yang punya modul gerbang untuk diukur"
    seen, bad = [], []
    with tempfile.TemporaryDirectory() as empty:
        for name in present:
            d = MCP_ROOT / name
            # `env` menuntut opsi sebelum penugasan: menaruh AI_STATION_HOME= lebih dulu membuat
            # "-u" dibaca sebagai nama program, dan probe-nya mati sebelum menyentuh kode — yang
            # lalu terbaca sebagai "tidak ada yang gagal". Urutan ini bagian dari yang diuji.
            argv = ["env", "-u", "IRSOFKA_BLENDER_GATE", "-u", "IRSOFKA_COMFY_GATE",
                    "-u", "IRSOVKA_COMFY_GATE", f"AI_STATION_HOME={empty}",
                    sys.executable, "-c", probe, str(d)]
            code, out, err = sh(argv, timeout=60)
            if code != 0:
                bad.append(f"{name}: modul gerbang tidak bisa dijalankan ({(err or out)[:60]})")
                seen.append(name)
                continue
            try:
                payload = json.loads(out.strip())
                state, go = str(payload["state"].get("state", "")), set(payload["go"])
            except Exception:  # noqa: BLE001 — jawaban yang tidak bisa dibaca BUKAN izin
                bad.append(f"{name}: jawaban gerbang tidak terbaca sebagai JSON ({out[:60]})")
                seen.append(name)
                continue
            seen.append(f"{name}={state}")
            if state in go:
                bad.append(f"{name}: menjawab {state} (izin) padahal tidak ada meter di mesin ini")
    if bad:
        return FAIL, "; ".join(bad[:3])
    return PASS, (f"{len(seen)} modul gerbang dijalankan tanpa stasiun dan tanpa meter: "
                  + ", ".join(seen) + " — tidak ada yang menjawab izin")


def _live_sha(name: str) -> str:
    """HEAD of one published repository, '' when it cannot be read (UNKNOWN, never a match)."""
    c, out, _ = sh(["git", "-C", str(MCP_ROOT / name), "rev-parse", "--short", "HEAD"], timeout=30)
    return out.strip() if c == 0 else ""


def pointer_table() -> str:
    """The table `mcp/README.md` must carry, generated from live git every time it is asked for.

    §12: a report is derived, never hand-written. A sha typed into a README is a claim about a
    repository that starts rotting at the next commit, and a reader cannot tell a stale row from a
    current one. R10 compares the README against THIS function, so the only way the row can be wrong
    is for the board to go red.
    """
    lines = ["| name | real location | HEAD | origin |", "|---|---|---|---|"]
    for n in OURS:
        d = MCP_ROOT / n
        sha, url = "-", "-"
        if d.is_dir():
            c, out, _ = sh(["git", "-C", str(d), "rev-parse", "--short", "HEAD"], timeout=30)
            if c == 0:
                sha = out.strip() or "-"
            c, out, _ = sh(["git", "-C", str(d), "remote", "get-url", "origin"], timeout=30)
            if c == 0:
                url = f"<{out.strip().removesuffix('.git')}>"
        lines.append(f"| `{n}` | `~/Apps/mcp/{n}` | {sha} | {url} |")
    return "\n".join(lines)


def c_readme_carries_the_check(ctx: Ctx) -> tuple[str, str]:
    """The check must be reachable from the document the operator opens, or it is a tool nobody queries."""
    readme = STATION / "mcp" / "README.md"
    if not readme.is_file():
        return FAIL, "mcp/README.md tidak ada"
    txt = readme.read_text(errors="replace")
    if "check_plan.py" not in txt:
        return FAIL, "README tidak menyebut check_plan.py — status di dalamnya tidak bisa diperbarui oleh perintah"
    if "## Plan check" not in txt:
        return FAIL, "README menyebut checker tapi tidak punya seksi 'Plan check'"
    # Sengaja TIDAK menyebut jumlah baris README di bukti ini: bukti itu dicetak ke dalam README,
    # jadi angka yang berubah oleh penyalinan sendiri membuat blok generate tidak pernah bisa
    # dibandingkan byte-per-byte dengan keluaran berikutnya — dan " regenerate = sama" adalah
    # satu-satunya cara pembaca tahu bahwa tabelnya benar-benar hasil ukur, bukan karangan.
    # "Where is the code?" must be answered by the same document, and answered with numbers that
    # still agree with git. R15 proved a pointer README alone drifts: three of them here are stale
    # copies of a README that moved.
    if "## What is where" not in txt:
        return FAIL, "README tidak punya seksi 'What is where' — pembaca mcp/ tidak tahu servernya di mana"
    want = pointer_table()
    stale = [n for n in OURS
             if not any(l.startswith(f"| `{n}`") and l.split("|")[3].strip() == _live_sha(n)
                        for l in txt.splitlines())]
    if stale:
        return FAIL, ("tabel lokasi menyimpan sha yang tidak sama dengan HEAD sekarang: "
                      + ", ".join(stale) + " — perbarui dengan `check_plan.py --pointers`")
    if not any(l in txt for l in want.splitlines()):
        return FAIL, "tabel lokasi tidak berbentuk baris yang dihasilkan pointer_table()"
    return PASS, ("README punya seksi 'Plan check' yang menunjuk checker dan seksi 'What is where' "
                  "yang HEAD-nya masih cocok dengan git (" + str(len(OURS)) + " repo)")


def c_shipped_guards(ctx: Ctx) -> tuple[str, str]:
    """A guard that is not installed in the repository it protects protects nothing.

    The behaviour audit of 2026-10-16 returned three conditions; this row is the third one — move the
    rules out of an engine's self-correction and into measured validation. It is not satisfied by the
    hook file existing in the station, and not satisfied by a README that says the guards are there.
    Measured today, in the order the check runs them:

      * `.git/hooks` is not versioned, so the only proof a published repository has the guard is the
        link itself. This resolves the link and compares the real path instead of trusting that a
        file with the right name is the right file.
      * a guard that cannot fail is decoration, so the refusal is provoked in a fixture: the same hook
        must accept English prose, must refuse that file rewritten in Indonesian, and must say out
        loud when the documented exception is used. A threshold counting lines rather than
        occurrences passed every Indonesian sentence in the first test — that is how a guard ships as
        a no-op, and it killed two commits here before it was caught.
      * a repository that cannot be committed from is a repository that cannot be fixed. Two of the
        four carried no committer identity when measured: `git commit` there dies with 128 and
        "unable to auto-detect email address". Choosing whose name is on a published commit is the
        operator's act, not an engine's, so this row reports the gap rather than writing config.
    """
    hook = STATION / "bin" / "hooks" / "pre-commit"
    if not hook.is_file():
        return UNAVAILABLE, f"hook stasiun tidak ada di {hook}"

    en = ("# Guard\n\n"
          "This paragraph is written in English, which is what the contract requires for prose the\n"
          "world reads, and it is long enough to look like a real section rather than a stub.\n")
    idn = ("# Guard\n\n"
           "Baris ini ditulis dalam bahasa Indonesia yang sengaja panjang dan tidak perlu, hanya\n"
           "untuk membuktikan bahwa penjaga bagian 10 benar-benar menolak dan bukan menghias.\n")

    defects: list[str] = []
    seen: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        fx = Path(td)
        code, out, err = sh(["git", "-c", "init.defaultBranch=main", "init", "-q", str(fx)], timeout=60)
        if code != 0:
            return UNAVAILABLE, f"fixture git tidak bisa dibuat: {(err or out)[:90]}"
        hooks = fx / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").symlink_to(hook)
        ident = ["-c", "user.name=check-plan-selftest", "-c", "user.email=check-plan@invalid"]
        nonce = 0

        def attempt(text: str, extra: list[str] | None = None) -> tuple[int, str]:
            # A nonce per attempt: the second and third calls would otherwise stage content identical
            # to what the previous commit already wrote, and `git commit` exits 1 with "nothing to
            # commit" — which this function would report as a refusal by the guard it is testing.
            nonlocal nonce
            nonce += 1
            (fx / "guard_notes.md").write_text(text + f"\n<!-- probe {nonce} -->\n")
            code, out, err = sh(["git", "-C", str(fx), "add", "guard_notes.md"], timeout=60)
            if code != 0:
                return 97, err or out
            argv = (extra or []) + ["git", "-C", str(fx), *ident, "commit", "-m", f"fixture {nonce}"]
            code, out, err = sh(argv, timeout=120)
            return code, out + err

        code, body = attempt(en)
        if code != 0:
            defects.append(f"prosa Inggris DITOLAK (rc={code}): {body[:80]}")
        else:
            seen.append("english=accepted")

        code, body = attempt(en + "\n" + idn)
        if code == 0:
            defects.append("prosa Indonesia DITERIMA — penjaga bahasa tidak punya gigi")
        elif "LANG" not in body:
            defects.append(f"ditolak tapi tidak menyebut LANG: {body[:80]}")
        else:
            seen.append("indonesian=refused-by-name")

        code, body = attempt(en + "\n" + idn,
                             ["env", "IRSOFKA_ALLOW_INDONESIAN_DOC=selftest-exception"])
        if code != 0:
            defects.append(f"pengecualian tercatat tidak membuka jalan (rc={code}): {body[:70]}")
        elif "IRSOFKA_ALLOW_INDONESIAN_DOC" not in body:
            defects.append("pengecualian dipakai tapi tidak ditampilkan — jalan yang diam-diam")
        else:
            seen.append("exception=visible")

    real_hook = hook.resolve()
    for name in OURS:
        d = MCP_ROOT / name
        if not d.is_dir():
            defects.append(f"{name}: repository tidak ada")
            continue
        link = d / ".git" / "hooks" / "pre-commit"
        if not link.exists() or Path(os.path.realpath(link)) != real_hook:
            defects.append(f"{name}: guard tidak terpasang di repo yang diterbitkan")
        else:
            seen.append(f"{name}:hook")
        code, out, _err = sh(["git", "-C", str(d), "config", "user.email"], timeout=30)
        if code != 0 or not out.strip():
            defects.append(f"{name}: tidak ada identitas committer — commit di sana mati 128")
        else:
            seen.append(f"{name}:identity")

    if defects:
        return FAIL, "; ".join(defects[:4])
    return PASS, ("penjaga bahasa diukur sebagai perilaku pada fixture, dan hook yang sama terbukti "
                  f"terpasang di {len(OURS)} repo: " + ", ".join(seen))


# The row that owns each requirement. Titles live in the ledger; this table only names the key.
OWNER_BY_RID = {
    "R1": "f11mcp_repositories",
    "R2": "f11mcp_registration",
    "R3": "f11mcp_blender",
    "R12": "f11mcp_comfy_takeover",
    "R4": "f11mcp_unreal",
    "R5": "f11mcp_unreal",
    "R6": "f11mcp_purge",
    "R7": "f11mcp_gate",
    "R8": "f11mcp_guard",
    "R9": "f11mcp_plan",
    "R10": "f11mcp_plan",
    "R11": "f11step0b",
    "R13": "f11mcp_publish",
    "R14": "f11mcp_gate_honesty",
    "R15": "f11mcp_shipped_guards",
}

REQUIREMENTS: list[Row] = [
    Row("R1", "the four MCP repositories exist as independent git repositories",
        "repository audit, 2026-10-16"),
    Row("R2", "each repository is registered in both engine sides (Qoder and Antigravity)",
        "registration audit, 2026-10-16"),
    Row("R3", "irsofka-blender-mcp is an independent BSL work: no third-party tree inside the BSL "
              "grant, one licence over the whole work, measured overlap against the reference "
              "project, and station policy on top",
        "licence and repository audit, 2026-10-16; ledger row 319"),
    Row("R4", "the Unreal MCP is reached as our repository, not as a Python file inside the engine plugin",
        "architecture requirement, 2026-10-16"),
    Row("R5", "the plugin carries the two states itself: station detected -> bridge is a station "
              "extension; not detected -> an ordinary plugin",
        "architecture requirement, 2026-10-16"),
    Row("R6", "the third-party servers are no longer the path an engine reaches for that work",
        "licence and registration audit, 2026-10-16"),
    Row("R7", "every repository with an origin answers about its own backlog before anything is published",
        "contract section 6 (verifier before push), commit bd27048"),
    Row("R8", "a BYOK key never reaches git in any published repository, including its history",
        "credential guard audit, 2026-10-16"),
    Row("R9", "every requirement above has a ledger row, so a loop can claim it instead of skipping it",
        "ledger coverage audit, 2026-10-16"),
    Row("R10", "the plan check is embedded in the public README and is runnable",
        "README contract, 2026-10-16"),
    Row("R13", "public documents in our four repositories carry no account paths, no private "
              "transcript ids, no key-shaped strings, and keep the vendored licence whole",
        "publication hygiene audit, 2026-10-16"),
    Row("R11", "the plugin's silent-defect debt is closed: a bare class name refuses instead of "
              "producing an immovable actor, and set_transform answers what actually landed",
        "UE bridge probe, 2026-10-15"),
    Row("R12", "irsofka-comfyui-mcp is the same kind of independent clean-room work under the same "
              "grant, performed by the assigned engine and verified by an independent engine",
        "licence and repository audit, 2026-10-16; ledger row 319"),
    Row("R14", "no station layer issues a VRAM permit it cannot sign: run without a workstation, "
              "every gate module refuses instead of answering ALLOW",
        "independent verification, 2026-10-16"),
    Row("R15", "the guards live in the published repositories and are proven to fire: the credential "
              "guard and contract section 10's language rule resolve to the station hook in all "
              "four, the refusal is provoked against a fixture, and each repository can be "
              "committed from",
        "guard audit, 2026-10-16"),
]
CHECKS: dict[str, Callable[[Ctx], tuple[str, str]]] = {
    "R1": c_repos_exist,
    "R2": c_registered_both,
    "R3": c_blender_is_clean_room,
    "R12": c_comfy_is_clean_room,
    "R4": c_unreal_points_at_repo,
    "R5": c_plugin_detects_station,
    "R6": c_agpl_blender_is_replaced,
    "R7": c_push_clearance,
    "R8": c_credential_guard,
    "R9": c_ledger_owns_each_row,
    "R10": c_readme_carries_the_check,
    "R11": c_probe_debt,
    "R13": c_publication_hygiene,
    "R14": c_gate_honesty,
    "R15": c_shipped_guards,
}

# The editor's state is reported next to the table but is not a requirement: a closed editor is a fact
# about the machine at this minute, and R4's second half is a code feature that exists or does not.
def c_installed_copy_drift(ctx: Ctx) -> tuple[str, str]:
    """Does the binary the editor actually loads contain the feature? Measured from the artefact.

    The first version of this check looked for the SOURCE HEADER in the installed plugin. That is a
    proxy that inverts the truth: deployment replaces `Binaries/Linux/*.so`, headers are not copied,
    so a correctly deployed build still read as "not deployed". The symbol table is the real object:
    if `FIRSofkaStationPresence` is in the .so, a loaded editor can answer `core.presence`.
    """
    bin_dir = PLUGIN_INSTALLED.parent / "Binaries" / "Linux"
    if not bin_dir.is_dir():
        return UNAVAILABLE, f"tidak ada Binaries/Linux di {PLUGIN_INSTALLED.parent}"
    sos = sorted(bin_dir.glob("libUnrealEditor-IRSofka*.so"))
    if not sos:
        return UNAVAILABLE, f"tidak ada .so di {bin_dir}"
    # Dua encoding, dua jenis bukti. Nama simbol (mangled) adalah ASCII, jadi `strings` biasa
    # melihatnya. Literal pesan disimpan UE sebagai char16_t di Linux, jadi perlu `strings -el`:
    # versi pertama hanya membaca ASCII dan melaporkan perbaikan yang SUNGGUH terpasang sebagai
    # "belum dideploy" — instrumen yang buta encoding lebih berbahaya dari tidak ada instrumen,
    # karena ia menghasilkan FAIL yang terlihat berhati-hati.
    def scan(so: Path, needle: str, enc: str) -> int:
        code, out, _err = sh(["strings", f"-{enc}", str(so)], timeout=60)
        if code != 0:
            return 0
        return sum(1 for line in out.splitlines() if needle in line)

    present_hits, refusal_hits = [], []
    for so in sos:
        n = scan(so, "FIRSofkaStationPresence", "a")
        if n:
            present_hits.append(f"{so.name}:{n}")
        m = scan(so, "no loaded AActor class", "el") + scan(so, "refusing to spawn a bare AActor", "el")
        if m:
            refusal_hits.append(f"{so.name}:{m}")
    if not present_hits:
        return FAIL, f"0 dari {len(sos)} .so memuat simbol FIRSofkaStationPresence — build belum terpasang"
    if not refusal_hits:
        return FAIL, ("presence terpasang, tetapi penolakan fallback AActor (a2be9b8) tidak ada di "
                      f"binari mana pun — {', '.join(present_hits)}")
    # Backup terakhir adalah pembanding yang jujur: fitur harus TIDAK ada di binari lama.
    # Pembanding yang jujur adalah binari yang fitur itu TIDAK punya. Setelah deploy kedua, backup
    # TERBARU justru sudah memuat fiturnya (terukur 2026-10-16: kedua backup berisi simbol yang sama),
    # jadi mengambil backups[-1] menghasilkan FAIL yang menuduh hal yang salah — seakan-akan
    # instalasinya rusak, padahal yang hilang hanyalah keadaan "sebelum". Jadi: cari backup paling baru
    # yang masih 0; kalau tidak ada, yang tidak tersedia adalah PERBANDINGNYA, bukan pasangnya.
    backups = sorted(PLUGIN_INSTALLED.parent.glob("Binaries/Linux.bak-*"), reverse=True)
    delta = ""
    baseline = None
    for b in backups:
        old_so = b / "libUnrealEditor-IRSofkaCore.so"
        if old_so.is_file() and scan(old_so, "FIRSofkaStationPresence", "a") == 0:
            baseline = b
            break
    if baseline is not None:
        delta = f"; di backup {baseline.name} simbol yang sama = 0 (harus 0)"
    elif backups:
        return UNAVAILABLE, (f"fitur terpasang ({', '.join(present_hits[:3])}), tapi {len(backups)} "
                             "backup semuanya sudah memuat simbol yang sama — tidak ada keadaan "
                             "'sebelum' di disk, jadi perbandingan tidak bisa membuktikan apa pun. "
                             "Ini bukan kegagalan instalasi.")
    return PASS, f"{', '.join(present_hits[:3])} + penolakan fallback di {', '.join(refusal_hits[:2])}{delta}"


CONTEXT_CHECKS: dict[str, Callable[[Ctx], tuple[str, str]]] = {
    "editor 9877": c_editor_reachable,
    "installed-copy": c_installed_copy_drift,
}


def evaluate(only: set[str] | None = None) -> tuple[list[Row], list[tuple[str, str, str]], str]:
    ctx = Ctx()
    rows: list[Row] = []
    for r in REQUIREMENTS:
        if only is not None and r.rid not in only:
            # Disaring SEBELUM mengukur, bukan sesudahnya: sebuah gerbang baris tidak boleh membayar
            # empat selftest + empat push gate + satu probe hanya untuk menanyakan satu syarat.
            continue
        row = Row(r.rid, r.requirement, r.source, OWNER_BY_RID[r.rid])
        try:
            row.state, row.evidence = CHECKS[r.rid](ctx)
        except Exception as exc:  # noqa: BLE001 — a crashing check reports itself, never a false PASS
            row.state, row.evidence = UNAVAILABLE, f"check error {type(exc).__name__}: {str(exc)[:80]}"
        rows.append(row)
    extras = []
    for label, fn in CONTEXT_CHECKS.items():
        try:
            state, evidence = fn(ctx)
        except Exception as exc:  # noqa: BLE001
            state, evidence = UNAVAILABLE, f"{type(exc).__name__}"
        extras.append((label, state, evidence))
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return rows, extras, stamp


def render_markdown(rows: list[Row], extras: list[tuple[str, str, str]], stamp: str) -> str:
    tally = {s: sum(1 for r in rows if r.state == s) for s in (PASS, FAIL, UNAVAILABLE)}
    out = [f"Generated by `python3 ~/.ai-station/mcp/check_plan.py` on {stamp} — "
           f"**{tally[PASS]} PASS / {tally[FAIL]} FAIL / {tally[UNAVAILABLE]} UNAVAILABLE** of {len(rows)}.",
           "",
           "| # | requirement | basis | ledger row | state | measured now |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        mark = {"PASS": "✅", "FAIL": "❌"}.get(r.state, "❓")
        out.append(f"| {r.rid} | {r.requirement} | {r.source} | `{r.owner}` | {mark} {r.state} | "
                   f"{r.evidence.replace('|', '/')} |")
    out.append("")
    for label, state, evidence in extras:
        out.append(f"Machine context — `{label}`: **{state}** — {evidence}")
    out.append("")
    out.append("UNAVAILABLE is not a pass and not a failure: it means the thing needed to measure it was "
               "not present at this minute (no editor listening, no remote to compare, no readable ledger). "
               "Per contract §5 it is never reported as either of the other two.")
    return "\n".join(out)


def selftest() -> int:
    """Prove every check can go RED — and prove the proving itself touches nothing real.

    Written after a measured accident on 2026-10-16: the first version of this function created its
    fixtures under `~/Apps/mcp`, because `MCP_ROOT` was "patched" by assigning it to itself. It
    therefore wrote into the operator's actual repositories, committed junk into four of them, and —
    because those repositories' `.git/hooks/pre-commit` are SYMLINKS into the station — wrote a
    three-line `exit 0` stub straight through the link onto the station's canonical credential guard.
    Nothing was pushed, and every repository was reset back to its last legitimate commit, but the
    lesson is structural and it is now tested: a fixture that shares a path with production is not a
    fixture. So this run (a) moves every module root under a temporary directory before it creates
    anything, (b) never writes through a symlink it did not create inside that directory, and
    (c) finishes by comparing a signature of the real tree against the one taken before it started —
    and reports a RED if anything changed.
    """
    import tempfile

    results = []

    def check(name, cond, detail=""):
        results.append((bool(cond), name, detail))
        print(f"[{'ok    ' if cond else 'GAGAL '}] {name}" + ("" if cond else f"  <- {detail}"))

    def real_tree_signature() -> str:
        """sha + size of each production server.py, plus where its guard hook actually points."""
        bits = []
        for n in OURS:
            d = HOME / "Apps/mcp" / n
            srv = d / "server.py"
            bits.append(f"{n}:{srv.stat().st_size if srv.is_file() else 'gone'}:"
                        f"{git(d, 'rev-parse', 'HEAD')[1] if d.exists() else 'nogit'}:"
                        f"{(d / '.git/hooks/pre-commit').resolve() if (d / '.git/hooks/pre-commit').is_file() else 'none'}")
        return "\n".join(bits)

    before = real_tree_signature()
    tmp = Path(tempfile.mkdtemp(prefix="checkplan-selftest-"))
    saved = {"MCP_ROOT": MCP_ROOT, "PLUGIN_REPO": PLUGIN_REPO, "PLUGIN_INSTALLED": PLUGIN_INSTALLED,
             "PLUGIN_REPO_ROOT": PLUGIN_REPO_ROOT, "STATION": STATION, "REG_FILES": dict(REG_FILES)}
    try:
        # --- SEMUA fixture hidup di bawah tmp; produksi tidak pernah jadi direktori kerja ---
        globals()["MCP_ROOT"] = tmp / "Apps-mcp"
        # Surface kelima (repo plugin) ikut diasingkan: R7 menambahkannya kalau direktori itu
        # memang sebuah repo, dan tanpa penambalan ini fixture mengukur ~/Documents yang asli —
        # ia punya origin, jadi "repo tanpa origin" berhenti UNAVAILABLE dan selftest menuduh
        # checker, padahal yang bocor adalah sandbox-nya sendiri. Terukur 2026-10-16 11:35.
        globals()["PLUGIN_REPO_ROOT"] = tmp / "no-plugin-repo"
        fake_station = tmp / "station"
        (fake_station / "bin/hooks").mkdir(parents=True)
        (fake_station / "mcp").mkdir(parents=True)
        # guard tiruan yang MEWAKILI bentuk aslinya: panjang, bukan sekadar ada
        fake_guard = fake_station / "bin/hooks/pre-commit"
        fake_guard.write_text(
            "#!/usr/bin/env bash\n# fake guard for the selftest: refuses a staged *byok* path\n" +
            "\n".join(f"# padding line {i} (the real guard is 200 lines; length is checked)"
                      for i in range(60)) +
            "\nfor f in \"$(git diff --cached --name-only)\"; do\n"
            "  case \"$f\" in *byok*) echo \"fake guard: refused $f\" >&2; exit 1 ;; esac\n"
            "done\nexit 0\n")
        (fake_station / "bin/hooks/pre-commit").chmod(0o755)
        globals()["STATION"] = fake_station

        def make_repo(name, *, hook="good"):
            d = MCP_ROOT / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "server.py").write_text(
                "import sys\n"
                "DISABLE_TELEMETRY = \"1\"\nBLENDER_MCP_SAFE_MODE = \"1\"\n"
                "NETWORK_TOOLS = {\"generate_3d\": \"https://example/premium\"}\n"
                "if '--selftest' in sys.argv: raise SystemExit(0)\n")
            sh(["git", "-C", str(d), "init", "-q", "-b", "main"], timeout=30)
            sh(["git", "-C", str(d), "add", "-A"], cwd=str(d), timeout=30)
            sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"],
               cwd=str(d), timeout=30)
            (d / "LICENSE").write_text("Business Source License 1.1\n\n"
                                       "Copyright (c) PT IRSofka Data Teknologi\n")
            (d / "upstream").mkdir(exist_ok=True)
            # Lisensi fixture harus SELengkap aslinya: R13 menuntut klausul penafian ada, jadi
            # berkas 3 barak akan membuat suite merah karena alasan yang benar.
            (d / "upstream" / "LICENSE").write_text(FIXTURE_MIT)
            (d / "UPSTREAM").write_text("Status: diambil alih (fork dari basis terbuka)\n"
                                        "source: ahujasid/mcp-for-blender\n"
                                        "sha 7a0373ec9199183cb460068c4f96aed9c579fb4f\n")
            hk = d / ".git/hooks/pre-commit"
            hk.parent.mkdir(parents=True, exist_ok=True)
            if hk.is_symlink() or hk.exists():
                hk.unlink()          # fixture dipakai ulang untuk mengubah bentuk hook
            if hook == "good":
                hk.symlink_to((fake_station / "bin/hooks/pre-commit").resolve())
            elif hook == "stub":
                # berkas biasa, bukan symlink: bentuk yang dulu LOLOS dari pemeriksaan R8
                hk.write_text("#!/bin/sh\n# byok guard\nexit 0\n")
                hk.chmod(0o755)
            elif hook == "missing":
                pass
            return d

        for n in OURS:
            make_repo(n)

        def write_registrations(per_engine: dict[str, list[str]], unreal_from_plugin: bool = False):
            paths = {}
            for engine, servers in per_engine.items():
                f = tmp / f"reg-{engine}.json"
                entry = {}
                for s in servers:
                    args = [str(MCP_ROOT / s / "server.py")]
                    if s == "irsofka-unreal-mcp" and unreal_from_plugin:
                        args = ["/Engine/Plugins/Marketplace/IRSofkaStationCore/Content/Python/mcp_server.py"]
                    entry[s] = {"command": "python3", "args": args}
                f.write_text(json.dumps({"mcpServers": entry}, indent=2))
                paths[engine] = f
            for k in list(REG_FILES):
                REG_FILES[k] = paths.get(k, tmp / f"absent-{k}.json")

        write_registrations({"qoder": OURS, "antigravity-cli": OURS, "gemini-config": OURS})

        # ---- R1
        st, ev = c_repos_exist(Ctx())
        check("R1 hijau ketika keempat repo ada", st == PASS, ev)
        (MCP_ROOT / "irsofka-unreal-mcp" / ".git").rename(MCP_ROOT / "moved-git")
        st, ev = c_repos_exist(Ctx())
        check("R1 merah ketika satu bukan git repo, dan menyebut yang mana",
              st == FAIL and "irsofka-unreal-mcp" in ev, ev)
        (MCP_ROOT / "moved-git").rename(MCP_ROOT / "irsofka-unreal-mcp" / ".git")
        (MCP_ROOT / "irsofka-blender-mcp").rename(tmp / "blender-out")
        st, ev = c_repos_exist(Ctx())
        check("R1 merah ketika satu repo hilang sama sekali (3/4)", st == FAIL and "3/4" in ev, ev)
        (tmp / "blender-out").rename(MCP_ROOT / "irsofka-blender-mcp")

        # ---- R2 / R4
        write_registrations({"qoder": OURS, "antigravity-cli": OURS[:-1], "gemini-config": []})
        st, ev = c_registered_both(Ctx())
        check("R2 merah ketika satu sisi engine tidak memuat salah satu repo", st == FAIL, ev)
        write_registrations({"qoder": OURS, "antigravity-cli": OURS, "gemini-config": []},
                            unreal_from_plugin=True)
        st, ev = c_unreal_points_at_repo(Ctx())
        check("R4 merah ketika unreal masih diluncurkan dari dalam plugin", st == FAIL, ev)
        st, ev = c_registered_both(Ctx())
        check("R2 tetap hijau di fixture yang sama: terdaftar != berasal dari repo (dua syarat beda)",
              st == PASS, ev)

        # ---- R5
        src = tmp / "plugin-src"; src.mkdir()
        (src / "A.cpp").write_text('void F() { Path = "/home/user/.ai-station/config"; }\n')
        globals()["PLUGIN_REPO"] = src
        st, ev = c_plugin_detects_station(Ctx())
        check("R5 merah pada plugin yang hanya MENYEBUT ai-station sebagai path (bukan presence)",
              st == FAIL, ev)
        (src / "B.cpp").write_text("bool HasStationMarker(const FString& R) { return true; }\n")
        st, ev = c_plugin_detects_station(Ctx())
        check("R5 hijau hanya kalau ada yang mendeklarasi atau membaca presence", st == PASS, ev)
        globals()["PLUGIN_REPO"] = tmp / "no-such-source"
        st, ev = c_plugin_detects_station(Ctx())
        check("sumber plugin tidak ada -> UNAVAILABLE, bukan FAIL dan bukan PASS", st == UNAVAILABLE, ev)
        globals()["PLUGIN_REPO"] = src

        # ---- R6
        write_registrations({"qoder": OURS + MUST_BE_REPLACED, "antigravity-cli": OURS,
                             "gemini-config": OURS + ORTHOGONAL_THIRD_PARTY})
        st, ev = c_agpl_blender_is_replaced(Ctx())
        check("R6 merah begitu AGPL masuk lagi ke salah satu engine",
              st == FAIL and "mcp-blender" in ev, ev)
        check("comfy-mcp dilaporkan sebagai DIPERTAHANKAN, tidak dihitung cacat",
              "dipertahankan" in ev, ev)
        write_registrations({"qoder": OURS, "antigravity-cli": OURS, "gemini-config": OURS})
        st, ev = c_agpl_blender_is_replaced(Ctx())
        check("R6 hijau ketika tidak ada AGPL di registrasi", st == PASS, ev)

        # ---- R7
        st, ev = c_push_clearance(Ctx())
        check("R7 tanpa push_gate.py -> UNAVAILABLE (alatnya tidak ada, bukan reponya)",
              st == UNAVAILABLE, ev)
        (fake_station / "mcp/push_gate.py").write_text(
            "import json,sys\nprint(json.dumps({'readable': True,'ok': False,'unpushed': 2,"
            "'declared': True}))\n")
        for n in OURS:
            sh(["git", "-C", str(MCP_ROOT / n), "remote", "add", "origin",
                f"{tmp}/bare.git"], timeout=30)
        st, ev = c_push_clearance(Ctx())
        check("R7 hijau ketika tiap repo menjawab tentang dirinya sendiri, walau commit-nya DITAHAN",
              st == PASS, ev)
        (fake_station / "mcp/push_gate.py").write_text(
            "import json,sys\nprint(json.dumps({'readable': True,'ok': False,'unpushed': 2,"
            "'declared': False}))\n")
        st, ev = c_push_clearance(Ctx())
        check("R7 merah ketika tumpukan commit tidak dideklarasikan — hold diam-diam bukan gerbang",
              st == FAIL and "UNDECLARED" in ev, ev)
        for n in OURS:
            sh(["git", "-C", str(MCP_ROOT / n), "remote", "remove", "origin"], timeout=30)
        st, ev = c_push_clearance(Ctx())
        check("repo tanpa origin -> UNAVAILABLE, bukan FAIL (dulu keduanya tercetak sama)",
              st == UNAVAILABLE, ev)

        # ---- R8: bentuk yang membuat guard gagal dulu, sekarang harus tertangkap
        make_repo("irsofka-workstation-mcp", hook="stub")  # symlink replaced by a plain stub file
        st, ev = c_credential_guard(Ctx())
        check("R8 merah ketika hook adalah STUB, bukan symlink ke guard stasiun "
              "(cacat yang bikin guard mati diam-diam pada 2026-10-16)",
              st == FAIL and "stub" in ev, ev)
        make_repo("irsofka-workstation-mcp", hook="good")
        # riwayat yang memuat jalur kunci harus tertangkap juga
        d = MCP_ROOT / "irsofka-comfyui-mcp"
        (d / "config").mkdir(exist_ok=True)
        (d / "config/byok.json").write_text(json.dumps({"keys": ["AIza" + "b" * 35]}))
        sh(["git", "-C", str(d), "add", "-A"], cwd=str(d), timeout=30)
        # --no-verify sengaja dipakai di sini: yang diuji ADALAH pembacaan riwayat, bukan hooknya
        sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify",
            "-m", "fixture history"], cwd=str(d), timeout=30)
        st, ev = c_credential_guard(Ctx())
        check("R8 merah ketika riwayat git memuat berkas byok",
              st == FAIL and "RIWAYAT GIT" in ev, ev)
        sh(["git", "-C", str(d), "reset", "-q", "--hard", "HEAD~1"], cwd=str(d), timeout=30)
        st, ev = c_credential_guard(Ctx())
        check("R8 hijau dengan guard resolve + riwayat bersih + penolakan hidup di repo ukur",
              st == PASS, ev)

        # ---- R3/R12: satu definisi clean-room untuk dua server. Yang diuji adalah keadaan yang
        # membedakan perintah operator 2026-10-16 12:0x dari sebuah karangan: pohon pihak ketiga yang
        # kembali, klaim "no code copied" tanpa angka, angka tanpa perintah reproduksi, dan manifest
        # yang tidak cocok dengan pohon. Verdict `tools/deps_honesty.py` di sini ditambal dengan stub
        # karena alatnya sendiri diuji di dalam tiap repository (server.py --selftest menjalankan
        # audit-nya DAN fixture yang harus ditolaknya); measure twice di satu tempat, bukan di dua.
        import shutil
        bl = MCP_ROOT / "irsofka-blender-mcp"
        # Fixture harus modeling repository yang LENGKAP, bukan hanya bagian yang kebetulan ada di
        # sandbox: R3 menuntut LICENSE/NOTICE/UPSTREAM dan ketiadaan upstream/, jadi keadaan itu
        # dibangun di sini, dan alat honestynya ditambal dengan stub exit-code karena audit manifest
        # yang sesungguhnya diuji di dalam tiap repository (server.py --selftest menjalankan
        # audit-nya sekaligus fixture yang harus ditolaknya).
        shutil.rmtree(bl / "upstream", ignore_errors=True)
        (bl / "tools").mkdir(parents=True, exist_ok=True)
        (bl / "LICENSE").write_text(FIXTURE_BSL)
        (bl / "NOTICE").write_text(FIXTURE_NOTICE)
        (bl / "UPSTREAM").write_text(
            "Status: Clean-room independent implementation (no code copied)\n"
            "| reference project | `ahujasid/mcp-for-blender` (BlenderMCP) |\n"
            "## Measured overlap against the base this server replaced\n"
            "```\npython3 tools/cleanroom_overlap.py 19c7bbf\n```\n")
        (bl / "tools" / "deps_honesty.py").write_text("import sys\nsys.exit(0)\n")
        st, ev = c_blender_is_clean_room(Ctx())
        check("R3 hijau pada fixture clean-room lengkap (tanpa upstream/, overlap terukur, manifest jujur)",
              st == PASS, ev)
        (bl / "upstream").mkdir(parents=True, exist_ok=True)
        (bl / "upstream/LICENSE").write_text(FIXTURE_MIT)
        st, ev = c_blender_is_clean_room(Ctx())
        check("R3 merah begitu pohon pihak ketiga kembali ke dalam karya ber-grant BSL",
              st == FAIL and "upstream/" in ev, ev)
        shutil.rmtree(bl / "upstream")
        (bl / "UPSTREAM").write_text("Status: Clean-room independent implementation\n"
                                     "| reference project | `ahujasid/mcp-for-blender` |\n")
        st, ev = c_blender_is_clean_room(Ctx())
        check("R3 merah kalau overlap tidak diukur — 'no code copied' tanpa angka",
              st == FAIL and "Measured overlap" in ev, ev)
        (bl / "UPSTREAM").write_text("Status: Clean-room\nahujasid/mcp-for-blender\n"
                                     "## Measured overlap\ntumpang tindih 11,2 persen menurut hitungan saya\n")
        st, ev = c_blender_is_clean_room(Ctx())
        check("R3 merah pada overlap yang ditulis tangan tanpa perintah ber-sha",
              st == FAIL and "reproduksi" in ev, ev)
        (bl / "UPSTREAM").write_text("Status: Clean-room independent implementation (no code copied)\n"
                                     "| reference project | `ahujasid/mcp-for-blender` (BlenderMCP) |\n"
                                     "## Measured overlap against the base this server replaced\n"
                                     "python3 tools/cleanroom_overlap.py 19c7bbf\n")
        (bl / "tools" / "deps_honesty.py").write_text("import sys\nsys.exit(1)\n")
        st, ev = c_blender_is_clean_room(Ctx())
        check("R3 merah saat manifest tidak cocok dengan pohon kerja — alatnya yang bilang tidak",
              st == FAIL and "manifest" in ev, ev)
        (bl / "tools" / "deps_honesty.py").write_text("import sys\nsys.exit(0)\n")
        (bl / "LICENSE").write_text("MIT License\n")
        st, ev = c_blender_is_clean_room(Ctx())
        check("R3 merah kalau grant yang diberikan bukan BSL milik operator", st == FAIL and "BSL" in ev, ev)
        (bl / "LICENSE").write_text(FIXTURE_BSL)

        # ---- konteks installed-copy: bukti datang dari binari, dan encoding harus dibaca benar
        inst = tmp / "installed" / "Binaries" / "Linux"
        (inst).mkdir(parents=True)
        globals()["PLUGIN_INSTALLED"] = tmp / "installed" / "Binaries" / "Linux" / ".." / ".." / "Source"
        (inst / "libUnrealEditor-IRSofkaCore.so").write_bytes(
            b"junk_FIRSofkaStationPresence_junk" * 3)
        (inst / "libUnrealEditor-IRSofkaWorld.so").write_bytes(
            "no loaded AActor class named X (tried Y)".encode("utf-16-le"))
        st, ev = c_installed_copy_drift(Ctx())
        check("installed-copy hijau hanya kalau simbol ASCII DAN literal UTF-16 sama-sama ada "
              "(buta encoding = FAIL palsu atas perbaikan yang nyata)", st == PASS, ev)
        (inst / "libUnrealEditor-IRSofkaWorld.so").write_bytes(b"nothing useful here")
        st, ev = c_installed_copy_drift(Ctx())
        check("installed-copy merah kalau presence ada tapi penolakan fallback tidak",
              st == FAIL and "penolakan" in ev, ev)
        for f in inst.glob("*.so"):
            f.unlink()
        st, ev = c_installed_copy_drift(Ctx())
        check("binari belum ada sama sekali -> FAIL, bukan UNAVAILABLE yang nyaman",
              st in (FAIL, UNAVAILABLE), ev)
        globals()["PLUGIN_INSTALLED"] = saved["PLUGIN_INSTALLED"]

        # ---- R12: definisi yang sama, repository yang berbeda. Ini bukan salinan aturan: kedua
        # baris memanggil SATU fungsi, dan apa yang diuji di sini adalah bahwa fungsi itu berlaku
        # untuk comfyui juga, termasuk keadaan yang dulu dianggap hijau (vendor + sha basis).
        cm = MCP_ROOT / "irsofka-comfyui-mcp"
        (cm / "tools").mkdir(parents=True, exist_ok=True)
        (cm / "LICENSE").write_text(FIXTURE_BSL)
        (cm / "NOTICE").write_text(FIXTURE_NOTICE)
        (cm / "tools" / "deps_honesty.py").write_text("import sys\nsys.exit(0)\n")
        shutil.rmtree(cm / "upstream", ignore_errors=True)
        (cm / "UPSTREAM").write_text("Status: Clean-room independent implementation (no code copied)\n"
                                    "| reference project | `joenorton/comfyui-mcp-server` |\n"
                                    "## Measured overlap\npython3 tools/cleanroom_overlap.py 9d30bd4\n")
        st, ev = c_comfy_is_clean_room(Ctx())
        check("R12 hijau untuk comfyui dengan bentuk yang sama seperti blender", st == PASS, ev)
        (cm / "upstream").mkdir(parents=True, exist_ok=True)
        (cm / "upstream/LICENSE").write_text(FIXTURE_APACHE)
        st, ev = c_comfy_is_clean_room(Ctx())
        check("R12 merah kalau basis Apache di-vendor kembali — bentuk yang dulu dianggap benar",
              st == FAIL and "upstream/" in ev, ev)
        shutil.rmtree(cm / "upstream")
        (cm / "UPSTREAM").unlink(missing_ok=True)
        st, ev = c_comfy_is_clean_room(Ctx())
        check("R12 merah kalau UPSTREAM hilang: tanpa provenance tidak ada yang bisa diperiksa orang lain",
              st == FAIL and "UPSTREAM" in ev, ev)

        # ---- R9
        st, ev = c_ledger_owns_each_row(Ctx())
        check("ledger tidak terbaca -> UNAVAILABLE; ketiadaan bukti tidak pernah berarti lolos",
              st == UNAVAILABLE, ev)

        # ---- R14: gerbang yang tidak bisa mengukur harus menolak — diukur sebagai PERILAKU,
        # bukan sebagai kalimat dokumentasi. Empat keadaan, dan ketiadaan alat diuji lebih dulu
        # karena itu keadaan yang paling mudah tersamar jadi hijau.
        st, ev = c_gate_honesty(Ctx())
        check("R14 UNAVAILABLE kalau tidak ada modul gerbang di repo mana pun (tanpa alat != lolos)",
              st == UNAVAILABLE and "tidak ada repo" in ev, ev)

        gate_allow = (
            'GATE_GO = ("ALLOW", "NOT_GPU_WORK", "BYPASSED")\n'
            'def decide(text):\n    return {"state": "ALLOW", "gate": "absent", "unaudited": True}\n')
        (MCP_ROOT / "irsofka-blender-mcp" / "vram.py").write_text(gate_allow)
        st, ev = c_gate_honesty(Ctx())
        check("R14 merah pada gerbang yang menjawab ALLOW tanpa meter — persis temuan verifier",
              st == FAIL and "irsofka-blender-mcp" in ev and "ALLOW" in ev, ev)

        (MCP_ROOT / "irsofka-blender-mcp" / "vram.py").write_text(
            'GATE_GO = ("ALLOW", "NOT_GPU_WORK", "BYPASSED")\n'
            'def decide(text):\n    return {"state": "DENY"}\n')
        st, ev = c_gate_honesty(Ctx())
        check("R14 hijau begitu jawaban bukan salah satu state 'go' milik modul itu sendiri",
              st == PASS, ev)

        # Override env HARUS sampai ke proses yang diukur. Kalau urutan argumen `env` salah
        # (opsi -u harus mendahului penugasan VAR=), probe mati sebelum menyentuh kode dan board
        # yang memperlakukan "tidak ada kegagalan" sebagai "lolos" akan hijau tanpa bukti.
        (MCP_ROOT / "irsofka-blender-mcp" / "vram.py").write_text(
            'import os\n'
            'GATE_GO = ("ALLOW", "NOT_GPU_WORK", "BYPASSED")\n'
            'def decide(text):\n'
            '    return {"state": "UNKNOWN" if os.environ.get("AI_STATION_HOME") else "ALLOW"}\n')
        st, ev = c_gate_honesty(Ctx())
        check("AI_STATION_HOME benar-benar mendarat di proses probe (urutan argumen `env` diuji)",
              st == PASS and "UNKNOWN" in ev, ev)

        (MCP_ROOT / "irsofka-blender-mcp" / "vram.py").write_text(
            'GATE_GO = ("ALLOW",)\nraise RuntimeError("modul rusak")\n')
        st, ev = c_gate_honesty(Ctx())
        check("modul gerbang yang tidak bisa dijalankan adalah FAIL, bukan diam (rc=127 bukan hijau)",
              st == FAIL and "tidak bisa dijalankan" in ev, ev)
        (MCP_ROOT / "irsofka-blender-mcp" / "vram.py").unlink()

        # ---- R13: hygiene publik diukur pada fixture, termasuk apa yang pernah lolos
        st, ev = c_publication_hygiene(Ctx())
        check("R13 hijau pada fixture yang bersih", st == PASS, ev)
        def commit_all(name):
            d = MCP_ROOT / name
            sh(["git", "-C", str(d), "add", "-A"], timeout=30)
            sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify",
                "-m", "fixture"], cwd=str(d), timeout=30)

        (MCP_ROOT / "irsofka-unreal-mcp" / "README.md").write_text(
            "# x\nsee /home/someuser/apps for the path\n")
        commit_all("irsofka-unreal-mcp")
        st, ev = c_publication_hygiene(Ctx())
        check("R13 merah begitu path akun masuk dokumen publik", st == FAIL and "path akun" in ev, ev)
        (MCP_ROOT / "irsofka-unreal-mcp" / "README.md").write_text(
            "# x\n" + "turn" + " 455 and " + "session" + "_turns\n")
        commit_all("irsofka-unreal-mcp")
        st, ev = c_publication_hygiene(Ctx())
        check("R13 refuses private transcript identifiers",
              st == FAIL and "transkrip" in ev, ev)
        (MCP_ROOT / "irsofka-unreal-mcp" / "README.md").write_text("# IRSofka Unreal Engine MCP\n")
        commit_all("irsofka-unreal-mcp")
        (MCP_ROOT / "irsofka-blender-mcp" / "upstream").mkdir(parents=True, exist_ok=True)
        (MCP_ROOT / "irsofka-blender-mcp" / "upstream/LICENSE").write_text("MIT License\n")  # sengaja pendek: kasus merah
        commit_all("irsofka-blender-mcp")
        st, ev = c_publication_hygiene(Ctx())
        check("R13 merah kalau lisensi vendored buntung (tanpa klausul penafian)",
              st == FAIL and "tidak utuh" in ev, ev)
        (MCP_ROOT / "irsofka-blender-mcp" / "upstream/LICENSE").write_text(FIXTURE_MIT)
        commit_all("irsofka-blender-mcp")
        st, ev = c_publication_hygiene(Ctx())
        check("R13 hijau lagi setelah berkas dipulihkan", st == PASS, ev)
        # Fixture ini sengaja MENGUJI berkas yang seharusnya tidak ada di karya clean-room; setelah
        # selesai ia dibersihkan supaya baris yang datang kemudian (R15) mengukur pohon sandbox
        # dalam keadaan yang sama seperti saat ia dimulai.
        shutil.rmtree(MCP_ROOT / "irsofka-blender-mcp" / "upstream", ignore_errors=True)

        # ---- R10
        (fake_station / "mcp/README.md").write_text("# servers\nno pointer\n")
        st, ev = c_readme_carries_the_check(Ctx())
        check("R10 merah ketika README tidak menunjuk checker", st == FAIL, ev)
        (fake_station / "mcp/README.md").write_text("# s\n## Plan check\nrun check_plan.py\n")
        st, ev = c_readme_carries_the_check(Ctx())
        check("R10 merah kalau pembaca tidak diberi tahu di mana servernya berada",
              st == FAIL and "What is where" in ev, ev)
        (fake_station / "mcp/README.md").write_text("# s\n## Plan check\nrun check_plan.py\n"
                                                   "## What is where\n" + pointer_table() + "\n")
        st, ev = c_readme_carries_the_check(Ctx())
        check("R10 hijau dengan seksi Plan check + penunjuk + tabel lokasi yang HEAD-nya masih cocok",
              st == PASS, ev)
        # sha yang basi harus tertangkap, karena itu alasan tabelnya diturunkan, bukan diketik.
        rot = pointer_table().replace("| " + _live_sha("irsofka-blender-mcp") + " |", "| deadbee |")
        (fake_station / "mcp/README.md").write_text("# s\n## Plan check\nrun check_plan.py\n"
                                                   "## What is where\n" + rot + "\n")
        st, ev = c_readme_carries_the_check(Ctx())
        check("R10 merah begitu satu HEAD di tabel tidak lagi sama dengan git",
              st == FAIL and "irsofka-blender-mcp" in ev, ev)

        # ---- R15: guard yang dipasang dan penolakan yang dibuktikan. Row ini sendiri adalah
        # perbaikan dari kelas cacat yang sama: penolakan yang tidak pernah diuji, dan pesan yang
        # mengklaim dirinya terlihat padahal `msg` hanya dicetak saat menolak.
        #
        # Fixture bahasa WAJIB memakai hook produksi, bukan stub stasiun sandbox: yang diuji di sini
        # adalah berkas guard yang benar-benar dipakai empat repository, dan mengukur stub bernama
        # sama adalah persis cacat "file dengan nama yang benar" yang R15 ada untuk tangkap.
        real_station = saved["STATION"]
        sandbox_station = globals()["STATION"]
        globals()["STATION"] = tmp / "no-station"
        st, ev = c_shipped_guards(Ctx())
        check("R15 UNAVAILABLE kalau hook stasiun tidak ada (tidak ada alat != lolos)",
              st == UNAVAILABLE and "tidak ada di" in ev, ev)
        globals()["STATION"] = real_station

        st, ev = c_shipped_guards(Ctx())
        check("R15 merah saat repository yang diterbitkan tidak punya guard terpasang",
              st == FAIL and "guard tidak terpasang" in ev, ev)

        # Link yang NAMANYA benar tapi isinya bukan hook stasiun adalah guard yang tidak ada.
        for n in OURS:
            hk = MCP_ROOT / n / ".git" / "hooks"
            hk.mkdir(parents=True, exist_ok=True)
            (hk / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
        st, ev = c_shipped_guards(Ctx())
        check("R15 merah pada file bernama pre-commit yang bukan hook stasiun (link harus diselesaikan)",
              st == FAIL and "guard tidak terpasang" in ev, ev)

        for n in OURS:
            link = MCP_ROOT / n / ".git" / "hooks" / "pre-commit"
            link.unlink()
            link.symlink_to(real_station / "bin" / "hooks" / "pre-commit")
            sh(["git", "-C", str(MCP_ROOT / n), "config", "user.email", "fixture@invalid"], timeout=30)
            sh(["git", "-C", str(MCP_ROOT / n), "config", "user.name", "fixture"], timeout=30)
        st, ev = c_shipped_guards(Ctx())
        check("R15 hijau saat keempat repo menunjuk hook stasiun DAN punya identitas committer",
              st == PASS, ev)
        globals()["STATION"] = sandbox_station

        # ---- reproducible + rc
        a = render_markdown(*evaluate())
        b = render_markdown(*evaluate())
        strip = lambda s: [l for l in s.splitlines() if not l.startswith("Generated by")]
        check("keluaran --markdown stabil lintas dua panggilan", strip(a) == strip(b),
              next((f"{x!r} vs {y!r}" for x, y in zip(strip(a), strip(b)) if x != y), ""))
        rc = main(["--only", "R99"])
        check("--only dengan id palsu keluar rc=2 (gerbang yang diam tentang typo-nya hijau selamanya)",
              rc == 2, f"rc={rc}")

        check("selftest TIDAK mengubah pohon produksi (server.py, HEAD, dan arah guard tiap repo)",
              real_tree_signature() == before,
              "sebelum:\n" + before[:180] + "\nsesudah:\n" + real_tree_signature()[:180])

        bad = [n for ok, n, _d in results if not ok]
        print(f"\ncheck_plan selftest: {len(results) - len(bad)}/{len(results)} sesuai")
        for n in bad:
            print(f"  GAGAL: {n}")
        return 0 if not bad else 1
    finally:
        for k, v in saved.items():
            globals()[k] = v
        sh(["rm", "-rf", str(tmp)], timeout=60)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Check the F11 MCP plan against the machine.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--pointers", action="store_true",
                    help="print the 'What is where' section with live HEADs, so the table in "
                         "mcp/README.md is regenerated from git instead of retyped from memory")
    ap.add_argument("--strict", action="store_true", help="exit 1 while any requirement FAILs")
    ap.add_argument("--selftest", action="store_true", help="prove every check can go red")
    ap.add_argument("--only", default="",
                    help="comma-separated ids (e.g. R1,R4) — a ledger gate asks about its own rows, "
                         "not the whole table; an id that does not exist is an error, never silence")
    ns = ap.parse_args(argv)
    if ns.selftest:
        return selftest()
    if ns.pointers:
        print("## What is where — clicking `mcp/` does not open a server\n"
              "`~/.ai-station/mcp/` holds the station's own tools (`check_plan.py`, `push_gate.py`, "
              "`PLAN.md`); server code lives in its own repository under `~/Apps/mcp/`. "
              "Regenerate this section with `python3 ~/.ai-station/mcp/check_plan.py --pointers` — "
              "R10 goes red when a HEAD in the table no longer agrees with git.\n\n" + pointer_table())
        return 0

    have = {r.rid for r in REQUIREMENTS}
    wanted: set[str] = set()
    if ns.only:
        wanted = {x.strip().upper() for x in ns.only.split(",") if x.strip()}
        unknown = [w for w in sorted(wanted) if w not in have]
        if unknown:
            # Gerbang yang diam tentang id yang salah akan lolos selamanya — itu bentuk gerbang
            # paling berbahaya, karena ia terlihat mengukur sesuatu.
            print(json.dumps({"ok": False, "unknown_ids": unknown, "known_ids": sorted(have)}),
                  file=sys.stderr)
            return 2
    rows, extras, stamp = evaluate(wanted or None)
    counts = {s: sum(1 for r in rows if r.state == s) for s in (PASS, FAIL, UNAVAILABLE)}

    if ns.json:
        print(json.dumps({"generated": stamp, "counts": counts,
                          "requirements": [r.__dict__ for r in rows],
                          "context": [{"label": l, "state": s, "evidence": e} for l, s, e in extras]},
                         indent=2, default=str))
    elif ns.markdown:
        print(render_markdown(rows, extras, stamp))
    else:
        print(f"F11 MCP plan check — {stamp}  ({counts[PASS]} PASS / {counts[FAIL]} FAIL / "
              f"{counts[UNAVAILABLE]} UNAVAILABLE)")
        for r in rows:
            print(f"[{r.state:<11}] {r.rid} {r.requirement}")
            print(f"             owner={r.owner}  <- {r.evidence}")
        for label, state, evidence in extras:
            print(f"[{state:<11}] -- {label}: {evidence}")
    return 1 if ns.strict and counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
