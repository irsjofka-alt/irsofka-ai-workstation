#!/usr/bin/env python3
"""project_context.py — resolusi @Project menjadi konteks dokumen.

Membaca registry yang sama dengan build.py dan mengumpulkan `.md` sesuai urutan
prioritas: `reference_files` dulu, lalu sisa `.md` bila `load_all_md` menyala.
Di atas anggaran (default 100.000 karakter untuk pemakaian langsung/CLI) berkas wajib tetap dikirim utuh,
sedangkan sisanya menjadi outline — judul bagian + cuplikan awal — supaya
konteks tidak membengkak tanpa kehilangan peta dokumennya.

Perintah:
    project_context.py --project meshcleanroom [--max-chars 100000]
    project_context.py --project "@MeshCleanRoom"

Mencetak satu objek JSON ke stdout. Teks dokumen diredaksi memakai pola
build.redact(); jumlah penggantian dilaporkan per berkas.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

DEFAULT_MAX_CHARS = 100_000


def resolve_docs(project: dict) -> list:
    """Daftar (label, path, wajib) dengan urutan yang sama seperti halaman project."""
    seen = set()
    rows = []

    def add(path: Path, base: Path, mandatory: bool):
        try:
            real = path.resolve()
        except OSError:
            return
        if real in seen or not path.is_file():
            return
        seen.add(real)
        label = f"{base.name}/{path.name}" if len(project["paths"]) > 1 else path.name
        rows.append({"label": label, "path": path, "mandatory": mandatory})

    for spec in project["reference_files"]:
        spec = spec.replace("\\", "/").strip()
        if not spec:
            continue
        found = False
        if "/" in spec:
            head, rest = spec.split("/", 1)
            for base in project["paths"]:
                if base.name == head or base.as_posix().endswith("/" + head):
                    candidate = base / rest
                    if candidate.is_file():
                        add(candidate, base, True)
                        found = True
        else:
            for base in project["paths"]:
                candidate = base / spec
                if candidate.is_file():
                    add(candidate, base, True)
                    found = True
        if not found:
            rows.append({"label": Path(spec).name, "path": None,
                         "mandatory": True, "missing": True})

    if project["load_all_md"]:
        for base in project["paths"]:
            if not base.is_dir():
                continue
            for path in sorted(base.glob("*.md")):
                add(path, base, False)
    return rows


def outline(text: str, limit: int = 1_400) -> str:
    headings = [line.strip() for line in text.splitlines()
                if re.match(r"^#{1,6}\s+\S", line)][:40]
    intro = re.sub(r"\s+", " ", text.strip())[:360]
    pieces = []
    if intro:
        pieces.append(intro)
    if headings:
        pieces.append("Bagian: " + " | ".join(headings))
    return ("\n\n".join(pieces))[:limit]


def build_context(project: dict, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    docs = resolve_docs(project)
    prepared = []
    total = 0
    missing = []
    for row in docs:
        if row.get("missing") or row["path"] is None:
            missing.append(row["label"])
            prepared.append({**row, "available": False})
            continue
        text = build.read_text(row["path"])
        safe, redactions = build.redact(text)
        chars = len(safe)
        total += chars
        prepared.append({
            **row,
            "available": True,
            "bytes": row["path"].stat().st_size,
            "chars": chars,
            "text": safe,
            "redactions": redactions,
        })

    over_budget = total > max_chars
    files = []
    returned = 0
    truncated = False
    for row in prepared:
        if not row.get("available"):
            files.append({
                "label": row["label"], "available": False,
                "bytes": 0, "chars": 0, "mode": "missing",
            })
            continue
        entry = {
            "label": row["label"],
            "path": str(row["path"].resolve()),
            "bytes": row["bytes"],
            "chars": row["chars"],
            "redactions": row["redactions"],
            "available": True,
        }
        if not over_budget or row["mandatory"]:
            entry["mode"] = "full"
            entry["content"] = row["text"]
            returned += len(row["text"])
        else:
            body = outline(row["text"])
            entry["mode"] = "outline"
            entry["outline"] = body
            returned += len(body)
            truncated = True
        files.append(entry)

    return {
        "ok": True,
        "project": {
            "slug": project["slug"],
            "codename": project["codename"],
            "name": project["name"],
            "mode": project["mode"],
            "workspaces": len(project["paths"]),
            "paths": [str(path) for path in project["paths"]],
        },
        "budget": {
            "max_chars": max_chars,
            "total_chars": total,
            "returned_chars": returned,
            "over_budget": over_budget,
            "truncated": truncated,
            "policy": "berkas wajib utuh; sisanya outline bila di atas anggaran",
        },
        "files": files,
        "missing": missing,
    }


def find_project(projects: list, wanted: str) -> dict:
    key = wanted.strip().lower().lstrip("@")
    for project in projects:
        if project["slug"].lower() == key or project["codename"].lower().lstrip("@") == key:
            return project
    raise SystemExit(json.dumps({
        "ok": False,
        "error": f"project tidak terdaftar: {wanted!r}",
        "available": [{"slug": p["slug"], "codename": p["codename"]} for p in projects],
    }, ensure_ascii=False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Konteks dokumen @Project.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args(argv)
    max_chars = max(1_000, min(args.max_chars, 2_000_000))
    project = find_project(build.load_registry(), args.project)
    print(json.dumps(build_context(project, max_chars), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
