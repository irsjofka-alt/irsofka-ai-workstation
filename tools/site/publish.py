#!/usr/bin/env python3
"""publish.py — pintu tulis tunggal untuk situs AI Station Playground.

Urutan tetap dan tidak boleh dibalik: pindai secret → validasi → salin media +
sha256 → tulis JSON post → build ulang. Post yang ditolak tidak pernah menyentuh
disk, dan yang ditulis hanya berisi teks yang sudah diredaksi.

Perintah:
    publish.py post --request <file.json>
    publish.py media --request <file.json>
    publish.py rebuild
    publish.py projects

`post` menerima objek post langsung, atau {"post": {...}, "public": true}.
Publikasi `public` hanya terjadi bila flag eksplisit itu ada; tanpa itu semua
post ditulis `visibility=local`.

Semua perintah mencetak tepat satu objek JSON ke stdout. Aturan isi (tipe, slug,
redaksi, penurunan visibility) tetap milik build.py; berkas ini tidak punya
salinannya sendiri.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

ROOT = build.ROOT
MEDIA_INDEX = build.INDEX_CACHE / "media_sha.json"
INBOX = build.INDEX_CACHE / "inbox"
MAX_MEDIA_BYTES = 2 * 1024**3

# Pola yang menolak post. Email/telepon/home-path tidak ada di sini: itu PII,
# diredaksi otomatis oleh build.redact(). Kunci, token, dan password ditolak.
HARD_SECRETS = {"api-key", "sk", "nvapi", "google", "github", "slack", "bearer"}

STORE_FIELDS = (
    "id", "type", "project", "title", "slug", "created_at", "worker_id",
    "worker_name", "summary", "body_md", "tags", "visibility", "media", "source",
)


class Rejected(Exception):
    def __init__(self, message: str, hits: list | None = None):
        super().__init__(message)
        self.hits = hits or []


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def iter_strings(value, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_strings(item, field)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{prefix}[{index}]")
    elif isinstance(value, str):
        yield prefix, value


def scan_hard(payload) -> list:
    hits = []
    for field, text in iter_strings(payload):
        for name, pattern in build.SECRET_PATTERNS:
            if name in HARD_SECRETS and pattern.search(text):
                hits.append({"kind": name, "field": field})
    return hits


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_media_index() -> dict:
    try:
        data = json.loads(build.read_text(MEDIA_INDEX) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_media_index(index: dict):
    build.write_if_changed(
        MEDIA_INDEX, json.dumps(index, indent=1, sort_keys=True),
        {"written": 0, "unchanged": 0},
    )


def import_media_file(source: Path, project: str, kind: str) -> dict:
    """Salin satu berkas ke site/media/<project>/YYYY-MM/ dengan sha256.

    Berkas dengan sha yang sama tidak disalin dua kali: indeks di
    data/site-index/media_sha.json mengingat tempat pertama.
    """
    if not source.is_file():
        raise Rejected(f"media tidak ditemukan: {source.name}")
    size = source.stat().st_size
    if size > MAX_MEDIA_BYTES:
        raise Rejected(f"media terlalu besar: {size} byte (batas {MAX_MEDIA_BYTES})")
    digest = sha256_file(source)
    index = load_media_index()
    rel = index.get(digest)
    target = build.SITE / rel if rel else None
    deduped = bool(target and target.is_file())
    if not deduped:
        month = dt.datetime.now().strftime("%Y-%m")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-")[:80] or "media"
        rel = f"media/{project}/{month}/{digest[:12]}-{safe}"
        target = build.SITE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or sha256_file(target) != digest:
            tmp = target.with_name(target.name + ".tmp")
            shutil.copyfile(source, tmp)
            tmp.replace(target)
        index[digest] = rel
        save_media_index(index)
    return {
        "path": f"site/{rel}",
        "sha256": digest,
        "bytes": size,
        "deduped": deduped,
    }


def prepare_media(entries: list, project: str) -> tuple[list, list]:
    media = []
    notes = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        source = entry.pop("source_path", None) or entry.pop("source", None)
        raw_path = str(entry.get("path") or "").strip()
        candidate = None
        if source:
            candidate = Path(os.path.expanduser(str(source)))
        elif raw_path and raw_path.startswith(("/", "~")):
            candidate = Path(os.path.expanduser(raw_path))
        if candidate is not None:
            kind = str(entry.get("kind") or infer_kind(candidate)).lower()
            info = import_media_file(candidate.resolve(), project, kind)
            entry.update({"kind": kind, "path": info["path"],
                          "sha256": info["sha256"], "bytes": info["bytes"]})
            if info["deduped"]:
                notes.append(f"media {candidate.name}: sha256 sudah ada, dipakai ulang")
        else:
            entry.setdefault("kind", "image")
            file = build.media_file(build.media_url(raw_path))
            if file and file.is_file():
                entry["sha256"] = entry.get("sha256") or sha256_file(file)
                entry["bytes"] = entry.get("bytes") or file.stat().st_size
        media.append(entry)
    return media, notes


def infer_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in build.VIDEO_EXT:
        return "video"
    if suffix in build.IMAGE_EXT:
        return "image"
    if suffix in {".blend", ".fbx", ".obj", ".glb", ".gltf", ".usd", ".usdz"}:
        return "model"
    return "image"


def publish_post(request: dict) -> dict:
    hits = scan_hard(request)
    if hits:
        raise Rejected(
            "post ditolak: mengandung pola secret "
            + ", ".join(sorted({h['kind'] for h in hits})),
            hits,
        )
    if "post" in request and isinstance(request["post"], dict):
        post = dict(request["post"])
        explicit_public = bool(request.get("public"))
    else:
        post = dict(request)
        explicit_public = bool(post.pop("public", False))

    projects = build.load_registry()
    by_slug = {p["slug"]: p for p in projects}
    project_slug = str(post.get("project") or "").strip()
    if project_slug not in by_slug:
        raise Rejected(f"project tidak terdaftar: {project_slug!r} (pilihan: {', '.join(sorted(by_slug))})")

    notes = []
    now = dt.datetime.now().astimezone()
    if not str(post.get("created_at") or "").strip():
        post["created_at"] = now.isoformat(timespec="seconds")
    if not str(post.get("id") or "").strip():
        post["id"] = "post_" + now.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(2)

    requested = str(post.get("visibility") or "local").strip().lower()
    if requested not in {"local", "public"}:
        notes.append(f"visibility {requested!r} tidak dikenal, dipakai local")
    post["visibility"] = "public" if explicit_public else "local"
    if requested == "public" and not explicit_public:
        notes.append("visibility=public diturunkan ke local: publikasi butuh flag eksplisit")

    media, media_notes = prepare_media(post.get("media") or [], project_slug)
    post["media"] = media
    notes.extend(media_notes)

    warnings = []
    try:
        normalized = build.normalize_post(post, Path(str(post["id"]) + ".json"), by_slug, warnings)
    except ValueError as exc:
        raise Rejected(f"post tidak valid: {exc}") from exc

    if normalized["redactions"]:
        notes.append(f"{normalized['redactions']} pola PII diredaksi saat menulis")

    year = str(dt.datetime.fromisoformat(normalized["created_at"]).year) \
        if _iso_ok(normalized["created_at"]) else now.strftime("%Y")
    target = build.POSTS_DIR / year / f"{_safe_id(normalized['id'])}.json"
    stored = {field: normalized[field] for field in STORE_FIELDS}
    build.write_if_changed(
        target, json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
        {"written": 0, "unchanged": 0},
    )

    built = rebuild()
    build_warnings = list(warnings) + built.pop("warnings", [])
    return {
        "ok": True,
        "post": {
            "id": stored["id"],
            "type": stored["type"],
            "project": stored["project"],
            "slug": stored["slug"],
            "title": stored["title"],
            "visibility": stored["visibility"],
            "url": build.post_url(normalized),
            "file": str(target.relative_to(ROOT)),
            "media": len(stored["media"]),
            "redactions": normalized["redactions"],
        },
        "notes": notes,
        "warnings": build_warnings,
        "build": built,
    }


def _iso_ok(value: str) -> bool:
    try:
        dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-")[:80]
    return safe or "post"


def publish_media(request: dict) -> dict:
    hits = scan_hard(request)
    if hits:
        raise Rejected(
            "media ditolak: mengandung pola secret "
            + ", ".join(sorted({h['kind'] for h in hits})),
            hits,
        )
    source = request.get("source_path")
    project = str(request.get("project") or "").strip()
    if not source:
        raise Rejected("source_path wajib diisi")
    if project not in {p["slug"] for p in build.load_registry()}:
        raise Rejected(f"project tidak terdaftar: {project!r}")
    src = Path(os.path.expanduser(str(source)))
    if not src.is_file():
        raise Rejected(f"media tidak ditemukan: {src}")
    kind = str(request.get("kind") or infer_kind(src)).lower()
    if kind not in build.KINDS:
        raise Rejected(f"kind tidak dikenal: {kind!r} ({', '.join(sorted(build.KINDS))})")
    mode = str(request.get("mode") or "other").lower()
    if mode not in build.MODES:
        mode = "other"

    info = import_media_file(src.resolve(), project, kind)
    result = {
        "ok": True,
        "media": {
            "project": project,
            "kind": kind,
            "mode": mode,
            "path": info["path"],
            "sha256": info["sha256"],
            "bytes": info["bytes"],
            "deduped": info["deduped"],
            "source": str(src.resolve()),
        },
    }
    if request.get("create_post", True) is False:
        result["build"] = rebuild()
        result["post"] = None
        return result

    meta = {key: request[key] for key in ("model", "prompt", "seed")
            if request.get(key) not in (None, "")}
    post_request = {
        "post": {
            "type": request.get("post_type") or (
                "showcase" if kind in {"render", "model"} else "generation"),
            "project": project,
            "title": request.get("title") or src.stem.replace("_", " ").replace("-", " "),
            "summary": request.get("summary") or "",
            "body_md": request.get("body_md") or "",
            "worker_id": request.get("worker_id") or "",
            "worker_name": request.get("worker_name") or "AI Station",
            "tags": request.get("tags") or [],
            "visibility": request.get("visibility") or "local",
            "media": [{
                "kind": kind, "mode": mode, "path": info["path"],
                "sha256": info["sha256"], "bytes": info["bytes"], "meta": meta,
            }],
            "source": request.get("source") or {
                key: value for key, value in (
                    ("path", str(src.resolve())), ("sha256", info["sha256"]),
                    ("prompt", request.get("prompt")), ("model", request.get("model")),
                    ("seed", request.get("seed")),
                ) if value not in (None, "")
            },
        },
        "public": bool(request.get("public")),
    }
    published = publish_post(post_request)
    result["post"] = published["post"]
    result["notes"] = published.get("notes", [])
    result["warnings"] = published.get("warnings", [])
    result["build"] = published["build"]
    return result


def rebuild() -> dict:
    stats = {"written": 0, "unchanged": 0, "warnings": [], "thumbs": 0, "missing_media": 0}
    build.build(stats)
    return {
        "written": stats["written"],
        "unchanged": stats["unchanged"],
        "thumbs": stats["thumbs"],
        "missing_media": stats["missing_media"],
        "warnings": list(stats["warnings"])[:20],
    }


def list_projects() -> dict:
    projects = build.load_registry()
    warnings = []
    posts = build.load_posts({p["slug"]: p for p in projects}, warnings)
    rows = []
    for project in projects:
        docs = build.resolve_docs(project)
        rows.append({
            "slug": project["slug"],
            "codename": project["codename"],
            "name": project["name"],
            "mode": project["mode"],
            "status": project["status"],
            "available": project["available"],
            "paths": [str(path) for path in project["paths"]],
            "docs": [
                {"label": doc["label"], "bytes": doc["bytes"], "available": doc["available"]}
                for doc in docs
            ],
            "posts": sum(1 for post in posts if post["project"] == project["slug"]),
            "url": "site/" + build.project_url(project),
        })
    return {"ok": True, "projects": rows, "warnings": warnings}


def load_request(path: str) -> dict:
    raw = build.read_text(Path(path))
    if not raw.strip():
        raise Rejected("request kosong")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Rejected(f"request bukan JSON valid: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected("request harus objek JSON")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pintu tulis situs AI Station Playground.")
    sub = parser.add_subparsers(dest="command", required=True)
    post = sub.add_parser("post", help="tulis satu post dari berkas request JSON")
    post.add_argument("--request", required=True)
    media = sub.add_parser("media", help="salin media + buat post dari berkas request JSON")
    media.add_argument("--request", required=True)
    sub.add_parser("rebuild", help="bangun ulang situs")
    sub.add_parser("projects", help="daftar project, dokumen, dan jumlah post")
    args = parser.parse_args(argv)

    try:
        if args.command == "post":
            return emit(publish_post(load_request(args.request)))
        if args.command == "media":
            return emit(publish_media(load_request(args.request)))
        if args.command == "rebuild":
            return emit({"ok": True, "build": rebuild()})
        return emit(list_projects())
    except Rejected as exc:
        return emit({"ok": False, "error": str(exc), "secrets": exc.hits}, 2)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 1)


if __name__ == "__main__":
    sys.exit(main())
