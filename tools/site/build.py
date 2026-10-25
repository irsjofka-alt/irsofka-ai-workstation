#!/usr/bin/env python3
"""build.py — generator situs statis AI Station Playground.

Satu sumber kebenaran: registry project + post JSON. Halaman HTML, feed, dan
search.json adalah hasil generate; jangan menyuntingnya.

Perintah:
    build.py            bangun situs (idempoten: hanya menulis yang berubah)
    build.py --init     siapkan folder, registry lokal, README, post sambutan
    build.py --check    validasi hasil: struktur, link, JSON/XML, pola secret
    build.py --quiet    ringkas keluaran

Tanpa dependensi di luar stdlib. Pillow dipakai bila tersedia untuk thumbnail;
tanpa Pillow halaman memakai gambar asli.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import xml.etree.ElementTree as ET
from email.utils import format_datetime
from pathlib import Path

try:  # Pillow opsional; build tetap jalan tanpa itu.
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
TEMPLATES = ROOT / "tools" / "site" / "templates"
CONFIG_DIR = ROOT / "config"
CONFIG_EXAMPLE = CONFIG_DIR / "site_projects.example.json"
CONFIG_LOCAL = CONFIG_DIR / "site_projects.json"
POSTS_DIR = SITE / "content" / "posts"
MEDIA_DIR = SITE / "media"
INDEX_CACHE = ROOT / "data" / "site-index"
THUMBS_DIR = MEDIA_DIR / "_thumbs"

TYPES = {"blog", "showcase", "generation", "changelog", "project-update"}
KINDS = {"image", "video", "model", "render"}
MODES = {"txt2img", "img2img", "txt2video", "img2video", "mesh", "render", "other"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

# Pola rahasia. Dipakai dua kali: meredaksi masukan dan memeriksa keluaran.
SECRET_PATTERNS = [
    ("api-key", re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{8,}")),
    ("sk", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("nvapi", re.compile(r"\bnvapi-[A-Za-z0-9_-]{12,}\b")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("github", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+62|0)8\d{7,11}(?!\d)")),
    # `*` on the last segment, not `+`: a bare "/home/" mention (documentation about this very
    # check) survived the old pattern while `check()` rejects the substring outright — the clone
    # build failed on prose, not on a leaked path. Redaction and validation must agree on what
    # "absolute path" means, and the validator is the stricter of the two.
    ("home-path", re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]*")),
]

TEMPLATE_FILES = [
    "base.html",
    "home.html",
    "project-list.html",
    "project-detail.html",
    "blog-list.html",
    "blog-post.html",
    "gallery.html",
    "changelog.html",
    "404.html",
]

PLACEHOLDER_SVG = {
    "placeholder-model.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-label="Model 3D">
<rect width="640" height="400" fill="#14161b"/><g fill="none" stroke="#7c5cff" stroke-width="2" opacity=".8">
<path d="M320 90 470 170v140L320 390 170 310V170Z"/><path d="M170 170l150 80 150-80M320 250v140"/></g>
<text x="320" y="370" fill="#8b93a5" font-family="system-ui,sans-serif" font-size="17" text-anchor="middle">model 3D · belum ada poster</text></svg>""",
    "placeholder-video.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-label="Video">
<rect width="640" height="400" fill="#14161b"/><circle cx="320" cy="190" r="62" fill="none" stroke="#7c5cff" stroke-width="3"/>
<path d="M302 158l52 32-52 32Z" fill="#7c5cff"/><text x="320" y="330" fill="#8b93a5" font-family="system-ui,sans-serif" font-size="17" text-anchor="middle">video · poster belum dibuat</text></svg>""",
    "placeholder-image.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-label="Gambar">
<rect width="640" height="400" fill="#14161b"/><g fill="none" stroke="#7c5cff" stroke-width="2" opacity=".8">
<rect x="180" y="120" width="280" height="170" rx="12"/><path d="m200 265 65-70 55 55 40-40 60 55"/><circle cx="410" cy="165" r="15"/></g>
<text x="320" y="345" fill="#8b93a5" font-family="system-ui,sans-serif" font-size="17" text-anchor="middle">media belum tersedia</text></svg>""",
}


# ---------------------------------------------------------------- util

def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def esc(text) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm[:64] or "post"


def expand_path(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value or ""))


def rel_site(path: Path) -> str:
    try:
        return path.relative_to(SITE).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit) if limit else handle.read()
    except OSError:
        return ""


def load_json(path: Path):
    try:
        return json.loads(read_text(path) or "null")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON rusak: {path}: {exc}") from exc


def redact(text: str):
    """Kembalikan (teks aman, jumlah penggantian). Dipakai pada semua masukan."""
    if not text:
        return "", 0
    count = 0
    for name, pattern in SECRET_PATTERNS:
        def swap(match):
            nonlocal count
            count += 1
            if name == "home-path":
                return "~"
            if name == "email":
                return "[email]"
            if name == "phone":
                return "[telepon]"
            prefix = ""
            if name == "api-key":
                prefix = match.group(0).split(":")[0].split("=")[0] + ": "
            elif name == "bearer":
                prefix = "Bearer "
            return prefix + "[REDACTED]"
        text = pattern.sub(swap, text)
    return text, count


def find_secret(text: str):
    for name, pattern in SECRET_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return name, match.group(0)[:60]
    return None


def write_if_changed(path: Path, data: str | bytes, stats: dict) -> bool:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    try:
        if path.exists() and path.read_bytes() == raw:
            stats["unchanged"] += 1
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    stats["written"] += 1
    return True


def copy_if_changed(src: Path, dest: Path, stats: dict) -> bool:
    try:
        return write_if_changed(dest, src.read_bytes(), stats)
    except OSError as exc:
        stats["warnings"].append(f"aset tidak terbaca: {src.name}: {exc}")
        return False


# ---------------------------------------------------------------- registry

def load_registry():
    if not CONFIG_EXAMPLE.exists():
        raise SystemExit(f"registry contoh hilang: {CONFIG_EXAMPLE}")
    base = load_json(CONFIG_EXAMPLE) or {}
    entries = {}
    order = []
    for item in base.get("projects", []):
        slug = str(item.get("slug", "")).strip()
        if not SLUG_RE.match(slug):
            raise SystemExit(f"slug project tidak valid: {slug!r}")
        entries[slug] = dict(item)
        order.append(slug)
    if CONFIG_LOCAL.exists():
        local = load_json(CONFIG_LOCAL) or {}
        for item in local.get("projects", []):
            slug = str(item.get("slug", "")).strip()
            if not SLUG_RE.match(slug):
                raise SystemExit(f"slug project lokal tidak valid: {slug!r}")
            if slug not in entries:
                order.append(slug)
                entries[slug] = {}
            entries[slug].update(item)
    projects = []
    for slug in order:
        item = entries[slug]
        paths = [expand_path(p) for p in item.get("paths", []) if str(p).strip()]
        project = {
            "slug": slug,
            "codename": item.get("codename") or f"@{slug}",
            "name": item.get("name") or slug,
            "mode": item.get("mode") or "single",
            "status": item.get("status") or "aktif",
            "description": redact(str(item.get("description") or ""))[0],
            "paths": [Path(p) for p in paths],
            "reference_files": [str(p) for p in item.get("reference_files", [])],
            "load_all_md": bool(item.get("load_all_md")),
            "media_default": item.get("media_default") or "showcase",
            "available": any(Path(p).exists() for p in paths),
        }
        projects.append(project)
    if not projects:
        raise SystemExit("registry project kosong")
    return projects


def resolve_docs(project: dict):
    """Daftar dokumen acuan tanpa membocorkan path absolut."""
    seen = set()
    docs = []

    def add(path: Path, base: Path):
        try:
            real = path.resolve()
        except OSError:
            return
        if real in seen or not path.is_file():
            return
        seen.add(real)
        title = path.stem.replace("_", " ").replace("-", " ").strip().title()
        head = read_text(path, 4096)
        heading = re.search(r"^#\s+(.+)$", head, re.MULTILINE)
        if heading:
            title = heading.group(1).strip()
        body = re.sub(r"^#{1,6}\s+", "", head, flags=re.MULTILINE)
        body = re.sub(r"[`*_>\[\]()#|-]+", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        excerpt, _ = redact(body[:520])
        docs.append({
            "label": f"{base.name}/{path.name}" if len(project["paths"]) > 1 else path.name,
            "title": title,
            "bytes": path.stat().st_size,
            "excerpt": excerpt,
            "available": True,
        })

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
                        add(candidate, base)
                        found = True
        else:
            for base in project["paths"]:
                candidate = base / spec
                if candidate.is_file():
                    add(candidate, base)
                    found = True
        if not found:
            docs.append({"label": Path(spec).name, "title": Path(spec).name,
                         "bytes": 0, "excerpt": "Berkas acuan belum ditemukan.",
                         "available": False})

    if project["load_all_md"]:
        for base in project["paths"]:
            if not base.is_dir():
                continue
            for path in sorted(base.glob("*.md")):
                add(path, base)
    return docs


# ---------------------------------------------------------------- posts

def normalize_post(raw: dict, path: Path, projects: dict, warnings: list):
    post = dict(raw)
    errors = []
    if not str(post.get("id", "")).strip():
        errors.append("id kosong")
    post_type = str(post.get("type", "")).strip()
    if post_type not in TYPES:
        errors.append(f"type tidak dikenal: {post_type!r}")
    title = str(post.get("title", "")).strip()
    if not title:
        errors.append("title kosong")
    project_slug = str(post.get("project", "")).strip()
    if project_slug not in projects:
        errors.append(f"project tidak terdaftar: {project_slug!r}")
    slug = slugify(str(post.get("slug") or title))
    created = str(post.get("created_at") or "").strip()
    if not created:
        created = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    body, body_hits = redact(str(post.get("body_md") or ""))
    summary, summary_hits = redact(str(post.get("summary") or "").strip())
    if not summary:
        plain = re.sub(r"[#*`>\[\]]+", " ", body)
        summary, _ = redact(re.sub(r"\s+", " ", plain).strip()[:220])
    title, title_hits = redact(title)
    redactions = body_hits + summary_hits + title_hits

    visibility = str(post.get("visibility") or "local").strip().lower()
    if visibility not in {"local", "public"}:
        warnings.append(f"{path.name}: visibility {visibility!r} tidak dikenal, dipakai local")
        visibility = "local"

    media = []
    for entry in post.get("media") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "other").lower()
        if kind not in KINDS:
            kind = "other"
        mode = str(entry.get("mode") or "other").lower()
        if mode not in MODES:
            mode = "other"
        media_path, _ = redact(str(entry.get("path") or ""))
        thumb, _ = redact(str(entry.get("thumb") or ""))
        meta = entry.get("meta") or {}
        if isinstance(meta, dict):
            meta = {k: redact(str(v))[0] for k, v in list(meta.items())[:12]}
        else:
            meta = {}
        media.append({
            "kind": kind,
            "mode": mode,
            "path": media_path,
            "thumb": thumb,
            "sha256": str(entry.get("sha256") or ""),
            "bytes": int(entry.get("bytes") or 0),
            "meta": meta,
        })

    source = post.get("source") or {}
    if isinstance(source, dict):
        source = {k: redact(str(v))[0] for k, v in source.items()}
    else:
        source = {}

    if visibility == "public":
        complete = bool(str(post.get("worker_name") or "").strip())
        complete = complete and bool(created)
        for entry in media:
            if not entry["sha256"] or not entry["path"]:
                complete = False
        if source and not source.get("sha256"):
            complete = False
        if not complete:
            warnings.append(f"{path.name}: metadata belum lengkap, visibility diturunkan ke local")
            visibility = "local"

    if errors:
        raise ValueError("; ".join(errors))

    post.update({
        "id": str(post["id"]).strip(),
        "type": post_type,
        "project": project_slug,
        "title": title,
        "slug": slug,
        "created_at": created,
        "worker_id": str(post.get("worker_id") or ""),
        "worker_name": redact(str(post.get("worker_name") or "AI Station"))[0],
        "summary": summary,
        "body_md": body,
        "tags": [redact(str(t))[0] for t in (post.get("tags") or []) if str(t).strip()][:12],
        "visibility": visibility,
        "media": media,
        "source": source,
        "redactions": redactions,
        "source_file": path.name,
    })
    return post


def load_posts(projects: dict, warnings: list):
    posts = []
    if not POSTS_DIR.exists():
        return posts
    for path in sorted(POSTS_DIR.rglob("*.json")):
        try:
            raw = json.loads(read_text(path))
            if not isinstance(raw, dict):
                raise ValueError("akar JSON harus objek")
            posts.append(normalize_post(raw, path, projects, warnings))
        except (ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"post dilewati ({path.name}): {exc}")
    posts.sort(key=lambda p: (p.get("created_at") or "", p["slug"]), reverse=True)
    return posts


def rfc822(value: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return format_datetime(parsed)
    except (ValueError, TypeError):
        return value


def post_url(post: dict) -> str:
    if post["type"] == "blog":
        return f"blog-{post['slug']}.html"
    if post["type"] == "project-update":
        return f"project-{post['project']}.html"
    if post["type"] == "changelog":
        return "changelog.html"
    return "gallery.html"


# ---------------------------------------------------------------- media

def media_url(raw: str):
    raw = (raw or "").strip()
    if not raw or raw.startswith(("/", "http://", "https://")):
        return None
    rel = raw[5:] if raw.startswith("site/") else raw
    return rel.lstrip("./")


def media_file(url: str | None):
    if not url:
        return None
    candidate = (SITE / url).resolve()
    try:
        candidate.relative_to(SITE.resolve())
    except ValueError:
        return None
    return candidate


def make_thumb(file: Path, sha: str):
    if Image is None or not file or not file.is_file():
        return None
    if file.suffix.lower() not in IMAGE_EXT:
        return None
    key = (sha or str(file.stat().st_mtime))[:12]
    dest = THUMBS_DIR / f"{key}.jpg"
    try:
        if dest.exists() and dest.stat().st_mtime >= file.stat().st_mtime:
            return rel_site(dest)
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        image = Image.open(file)
        image.thumbnail((640, 640))
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (20, 22, 27))
            canvas.paste(rgba, mask=rgba.split()[-1])
            image = canvas
        else:
            image = image.convert("RGB")
        image.save(dest, "JPEG", quality=82, optimize=True)
        return rel_site(dest)
    except Exception:  # noqa: BLE001 — thumbnail gagal bukan alasan build gagal
        return None


def collect_media(posts: list, stats: dict):
    items = []
    for post in posts:
        project = post["project"]
        for entry in post["media"]:
            url = media_url(entry["path"])
            file = media_file(url)
            exists = bool(file and file.is_file())
            thumb = media_url(entry.get("thumb") or "") if entry.get("thumb") else None
            thumb_file = media_file(thumb)
            if thumb and not (thumb_file and thumb_file.is_file()):
                thumb = None
            if exists and not thumb:
                generated = make_thumb(file, entry.get("sha256", ""))
                if generated:
                    thumb = generated
                    stats["thumbs"] += 1
            if not exists:
                stats["missing_media"] += 1
            items.append({
                "post": post,
                "project": project,
                "kind": entry["kind"],
                "mode": entry["mode"],
                "path": url,
                "thumb": thumb,
                "exists": exists,
                "sha256": entry.get("sha256", ""),
                "bytes": entry.get("bytes", 0),
                "meta": entry.get("meta", {}),
                "title": post["title"],
            })
    return items


def placeholder_for(kind: str) -> str:
    if kind in {"model", "render"}:
        return "assets/placeholder-model.svg"
    if kind == "video":
        return "assets/placeholder-video.svg"
    return "assets/placeholder-image.svg"


def media_card(item: dict) -> str:
    thumb = item.get("thumb") or placeholder_for(item["kind"])
    title = esc(item["title"])
    project = esc(item["project"])
    meta = item.get("meta") or {}
    details = " · ".join(
        part for part in [
            esc(str(meta.get("model") or "")),
            f"seed {esc(str(meta.get('seed')))}" if meta.get("seed") not in (None, "") else "",
            esc(str(meta.get("prompt") or ""))[:90],
        ] if part
    )
    if item["kind"] == "video" and item.get("exists") and item.get("path"):
        inner = (
            f'<video controls preload="metadata" poster="{esc(thumb)}">'
            f'<source src="{esc(item["path"])}"></video>'
        )
    elif item["kind"] in {"image", "render"} and item.get("exists") and item.get("path"):
        inner = (
            f'<a href="{esc(item["path"])}" target="_blank" rel="noopener">'
            f'<img loading="lazy" src="{esc(thumb)}" alt="{title}"></a>'
        )
    else:
        inner = f'<img loading="lazy" src="{esc(thumb)}" alt="{title}">'
    return (
        f'<figure class="media-card" data-project="{project}" data-kind="{esc(item["kind"])}" '
        f'data-mode="{esc(item["mode"])}">{inner}'
        f'<figcaption><strong>{title}</strong><span>{project} · {esc(item["kind"])} · '
        f'{esc(item["mode"])}</span>'
        f'{f"<span>{details}</span>" if details else ""}</figcaption></figure>'
    )


# ---------------------------------------------------------------- markdown

def inline_md(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(f"<code>{esc(part[1:-1])}</code>")
            continue
        escaped = esc(part)
        escaped = re.sub(
            r"!\[([^\]]*)\]\(([^)\s]+)\)",
            lambda m: (
                f'<img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy">'
                if re.match(r"^(https?://|assets/|media/)", m.group(2)) else m.group(0)
            ),
            escaped,
        )
        escaped = re.sub(
            r"\[([^\]]+)\]\(([^)\s]+)\)",
            lambda m: (
                f'<a href="{m.group(2)}">{m.group(1)}</a>'
                if re.match(r"^(https?://|/|#|\./|[A-Za-z0-9_-]+\.html$)", m.group(2)) else m.group(0)
            ),
            escaped,
        )
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
        out.append(escaped)
    return "".join(out)


def markdown_to_html(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = {}
    def stash(match):
        key = f"\x00CODE{len(blocks)}\x00"
        lang = (match.group(1) or "").strip()
        blocks[key] = f'<pre><code class="lang-{esc(lang)}">{esc(match.group(2))}</code></pre>'
        return key
    text = re.sub(r"```([^\n]*)\n(.*?)```", stash, text, flags=re.DOTALL)

    lines = text.split("\n")
    out = []
    paragraph = []
    list_kind = None
    quote = []

    def flush_paragraph():
        if paragraph:
            out.append(f"<p>{inline_md(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list():
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    def flush_quote():
        if quote:
            out.append(f"<blockquote>{inline_md(' '.join(quote))}</blockquote>")
            quote.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            flush_quote()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph(); flush_list(); flush_quote()
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{inline_md(heading.group(2))}</h{level}>")
            continue
        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            flush_paragraph(); flush_list(); flush_quote()
            out.append("<hr>")
            continue
        if stripped.startswith(">"):
            flush_paragraph(); flush_list()
            quote.append(stripped.lstrip("> "))
            continue
        item = re.match(r"^[-*]\s+(.*)$", stripped)
        ordered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if item or ordered:
            flush_paragraph(); flush_quote()
            kind = "ul" if item else "ol"
            if list_kind != kind:
                flush_list()
                out.append(f"<{kind}>")
                list_kind = kind
            out.append(f"<li>{inline_md((item or ordered).group(1))}</li>")
            continue
        if list_kind:
            flush_list()
        paragraph.append(stripped)

    flush_paragraph(); flush_list(); flush_quote()
    html_body = "\n".join(out)
    for key, value in blocks.items():
        html_body = html_body.replace(key, value)
    return html_body


# ---------------------------------------------------------------- view helpers

def badge(text: str, kind: str = "") -> str:
    cls = f"badge {kind}".strip()
    return f'<span class="{cls}">{esc(text)}</span>'


def format_bytes(size: int) -> str:
    size = int(size or 0)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def project_url(project: dict) -> str:
    return f"project-{project['slug']}.html"


def status_kind(project: dict) -> str:
    if not project["available"]:
        return "bad"
    return "warn" if project["status"].lower() in {"degraded", "belum", "offline"} else "ok"


def project_stats(project: dict, posts: list, media: list, docs: list):
    my_posts = [p for p in posts if p["project"] == project["slug"]]
    my_media = [m for m in media if m["project"] == project["slug"]]
    return {
        "posts": len(my_posts),
        "media": len(my_media),
        "docs": len([d for d in docs if d["available"]]),
        "posts_list": my_posts,
        "media_list": my_media,
    }


def render_nav(active: str) -> str:
    links = [
        ("index.html", "Beranda"),
        ("project.html", "Project"),
        ("blog.html", "Blog"),
        ("gallery.html", "Gallery"),
        ("changelog.html", "Changelog"),
    ]
    return "".join(
        f'<a href="{href}" class="{"is-active" if href == active else ""}">{label}</a>'
        for href, label in links
    )


def render_template(template_name: str, **context) -> str:
    path = TEMPLATES / template_name
    if not path.exists():
        raise SystemExit(f"template hilang: {path}")
    text = read_text(path)
    def swap(match):
        key = match.group(1)
        return str(context.get(key, ""))
    return PLACEHOLDER_RE.sub(swap, text)


def page(title: str, description: str, content: str, active: str, stamp: str, body_class: str = "") -> str:
    base = read_text(TEMPLATES / "base.html")
    if not base:
        raise SystemExit("template base.html kosong")
    nav = render_nav(active)
    return PLACEHOLDER_RE.sub(
        lambda m: str({
            "title": esc(title),
            "description": esc(description),
            "nav": nav,
            "content": content,
            "year": dt.date.today().year,
            "generated_at": esc(stamp),
            "page_class": esc(body_class),
            "footer_note": "Konten lokal tidak dipublikasikan otomatis; feed hanya memuat post public.",
        }.get(m.group(1), "")),
        base,
    )


def post_card(post: dict, projects: dict) -> str:
    project = projects.get(post["project"], {})
    tags = " ".join(f'<span class="tag">{esc(t)}</span>' for t in post["tags"])
    vis = badge(post["visibility"], "warn" if post["visibility"] == "local" else "ok")
    link = post_url(post)
    title = f'<a href="{esc(link)}">{esc(post["title"])}</a>'
    return (
        f'<article class="post-card">{vis}<h3>{title}</h3>'
        f'<p class="meta">{esc(project.get("codename", post["project"]))} · '
        f'{esc(post["type"])} · {esc(post["created_at"])}</p>'
        f'<p>{esc(post["summary"])}</p>{f"<p>{tags}</p>" if tags else ""}</article>'
    )


def project_card(project: dict, stats: dict) -> str:
    status = project["status"]
    kind = status_kind(project)
    return (
        f'<article class="project-card">'
        f'<div class="project-head"><span class="codename">{esc(project["codename"])}</span>'
        f'{badge(status, kind)}</div>'
        f'<h3><a href="{project_url(project)}">{esc(project["name"])}</a></h3>'
        f'<p>{esc(project["description"])}</p>'
        f'<p class="meta">{stats["posts"]} post · {stats["media"]} media · '
        f'{stats["docs"]} dokumen · mode {esc(project["mode"])}</p>'
        f'{"<p class=\"meta\">Path project belum tersedia di mesin ini.</p>" if not project["available"] else ""}'
        f'</article>'
    )


# ---------------------------------------------------------------- konteks @Project

CONTEXT_DIR = SITE / "project-context"


def export_project_context(projects: list, stats: dict):
    """Tulis `site/project-context/<slug>.json` untuk console (`@Project`).

    Sumbernya resolver yang sama dengan MCP `project_context` supaya isi console
    dan MCP tidak bercabang. Yang masuk situs hanya label dan teks yang sudah
    diredaksi; path absolut tidak pernah ikut.
    """
    import project_context as pc  # lokal: pc mengimpor build di level modul

    rows = []
    for project in projects:
        try:
            data = pc.build_context(project, max_chars=400_000)
        except Exception as exc:  # noqa: BLE001 — satu project gagal bukan alasan build gagal
            stats["warnings"].append(f"konteks {project['slug']} gagal: {exc}")
            continue
        files = []
        for row in data["files"]:
            entry = {
                "label": row.get("label"),
                "mode": row.get("mode", "missing"),
                "available": bool(row.get("available")),
                "bytes": row.get("bytes", 0),
                "chars": row.get("chars", 0),
                "redactions": row.get("redactions", 0),
            }
            if row.get("mode") == "full":
                entry["content"] = row.get("content", "")
            elif row.get("mode") == "outline":
                entry["outline"] = row.get("outline", "")
            files.append(entry)
        payload = {
            "ok": True,
            "generator": "tools/site/build.py",
            "visibility": "local",
            "project": {
                "slug": project["slug"],
                "codename": project["codename"],
                "name": project["name"],
                "mode": project["mode"],
                "workspaces": len(project["paths"]),
            },
            "budget": data["budget"],
            "missing": data["missing"],
            "files": files,
        }
        write_if_changed(CONTEXT_DIR / f"{project['slug']}.json",
                         json.dumps(payload, ensure_ascii=False, indent=1) + "\n", stats)
        rows.append({
            "slug": project["slug"], "codename": project["codename"],
            "name": project["name"], "file": f"{project['slug']}.json",
        })
    write_if_changed(CONTEXT_DIR / "index.json",
                     json.dumps({"ok": True, "projects": rows}, ensure_ascii=False, indent=1) + "\n",
                     stats)


# ---------------------------------------------------------------- build

def source_stamp(paths: list) -> str:
    mtimes = []
    for path in paths:
        try:
            if path.is_file():
                mtimes.append(path.stat().st_mtime)
            elif path.is_dir():
                mtimes.extend(p.stat().st_mtime for p in path.rglob("*") if p.is_file())
        except OSError:
            continue
    if not mtimes:
        mtimes = [dt.datetime.now().timestamp()]
    return dt.datetime.fromtimestamp(max(mtimes)).astimezone().isoformat(timespec="seconds")


def build(stats: dict):
    projects = load_registry()
    by_slug = {p["slug"]: p for p in projects}
    posts = load_posts(by_slug, stats["warnings"])
    media = collect_media(posts, stats)
    docs_by_project = {p["slug"]: resolve_docs(p) for p in projects}
    stats_by_project = {p["slug"]: project_stats(p, posts, media, docs_by_project[p["slug"]]) for p in projects}

    stamp_inputs = [CONFIG_EXAMPLE, CONFIG_LOCAL, TEMPLATES, POSTS_DIR]
    stamp = source_stamp([p for p in stamp_inputs if p.exists()])

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_CACHE.mkdir(parents=True, exist_ok=True)
    (SITE / "assets").mkdir(parents=True, exist_ok=True)

    # ---- home
    total_media = len(media)
    available = sum(1 for p in projects if p["available"])
    hero_stats = "".join(
        f'<div class="stat"><strong>{value}</strong><span>{label}</span></div>'
        for value, label in [
            (len(projects), "project terdaftar"),
            (available, "path tersedia"),
            (len(posts), "post"),
            (total_media, "media"),
        ]
    )
    featured = "".join(project_card(p, stats_by_project[p["slug"]]) for p in projects[:4])
    recent_posts = "".join(post_card(p, by_slug) for p in posts[:4]) or (
        '<div class="empty"><strong>Belum ada post</strong>'
        '<p>Worker yang memposting akan muncul di sini.</p></div>'
    )
    recent_media = "".join(media_card(m) for m in media[:8]) or (
        '<div class="empty"><strong>Belum ada media</strong>'
        '<p>Watcher ComfyUI dan publish media mengisi galeri ini (Phase 3–4).</p></div>'
    )
    home = render_template(
        "home.html",
        hero_stats=hero_stats,
        featured_projects=featured,
        recent_posts=recent_posts,
        recent_media=recent_media,
        post_count=len(posts),
        media_count=total_media,
    )
    write_if_changed(SITE / "index.html", page(
        "AI Station Playground", "Hasil visual, project, dan catatan mesin AI Station.",
        home, "index.html", stamp,
    ), stats)

    # ---- project list
    cards = "".join(project_card(p, stats_by_project[p["slug"]]) for p in projects)
    project_list = render_template("project-list.html", project_cards=cards, project_count=len(projects))
    write_if_changed(SITE / "project.html", page(
        "Project", "Registry project AI Station beserta dokumen acuan.",
        project_list, "project.html", stamp,
    ), stats)

    # ---- project details
    for project in projects:
        info = stats_by_project[project["slug"]]
        docs = docs_by_project[project["slug"]]
        doc_rows = []
        for doc in docs:
            state = badge("tersedia", "ok") if doc["available"] else badge("belum ada", "bad")
            doc_rows.append(
                f'<article class="doc"><div class="doc-head"><strong>{esc(doc["title"])}</strong>'
                f'<span class="meta">{esc(doc["label"])} · {format_bytes(doc["bytes"]) if doc["available"] else "—"} {state}</span></div>'
                f'<p>{esc(doc["excerpt"])}</p></article>'
            )
        doc_html = "".join(doc_rows) or '<div class="empty"><strong>Belum ada dokumen acuan</strong></div>'
        post_html = "".join(post_card(p, by_slug) for p in info["posts_list"]) or (
            '<div class="empty"><strong>Belum ada post project ini</strong></div>'
        )
        media_html = "".join(media_card(m) for m in info["media_list"]) or (
            '<div class="empty"><strong>Belum ada media project ini</strong></div>'
        )
        detail = render_template(
            "project-detail.html",
            codename=esc(project["codename"]),
            name=esc(project["name"]),
            description=esc(project["description"]),
            status_html=badge(project["status"], status_kind(project)),
            mode=esc(project["mode"]),
            posts=info["posts"],
            media=info["media"],
            docs_count=info["docs"],
            workspace_count=len(project["paths"]),
            workspace_names=esc(", ".join(path.name for path in project["paths"])),
            availability_note=(
                "" if project["available"] else
                '<p class="notice">Path project belum ditemukan di mesin ini. Perbaiki '
                'config/site_projects.json lalu build ulang.</p>'
            ),
            doc_list=doc_html,
            post_list=post_html,
            media_list=media_html,
        )
        write_if_changed(SITE / f"project-{project['slug']}.html", page(
            project["name"], project["description"], detail,
            "project.html", stamp, "project-page",
        ), stats)

    # ---- blog
    blog_posts = [p for p in posts if p["type"] == "blog"]
    blog_cards = "".join(post_card(p, by_slug) for p in blog_posts) or (
        '<div class="empty"><strong>Belum ada tulisan</strong>'
        '<p>Blog AI muncul dari post bertipe blog.</p></div>'
    )
    blog_list = render_template("blog-list.html", post_cards=blog_cards, post_count=len(blog_posts))
    write_if_changed(SITE / "blog.html", page(
        "Blog", "Catatan dan suara mesin tentang pekerjaan di AI Station.",
        blog_list, "blog.html", stamp,
    ), stats)
    for post in blog_posts:
        article = render_template(
            "blog-post.html",
            title=esc(post["title"]),
            meta=f'{esc(post["created_at"])} · {esc(post["worker_name"])} · '
                 f'{esc(by_slug[post["project"]]["codename"])}',
            summary=esc(post["summary"]),
            body=markdown_to_html(post["body_md"]),
            tags="".join(f'<span class="tag">{esc(t)}</span>' for t in post["tags"]),
            visibility=badge(post["visibility"], "warn" if post["visibility"] == "local" else "ok"),
            project_link=f'<a href="{project_url(by_slug[post["project"]])}">'
                         f'{esc(by_slug[post["project"]]["name"])}</a>',
        )
        write_if_changed(SITE / f"blog-{post['slug']}.html", page(
            post["title"], post["summary"], article, "blog.html", stamp, "post-page",
        ), stats)

    # ---- gallery
    used_projects = sorted({m["project"] for m in media})
    used_kinds = sorted({m["kind"] for m in media})
    used_modes = sorted({m["mode"] for m in media})
    def filter_row(name, values, label):
        if not values:
            return ""
        buttons = "".join(
            f'<button type="button" class="filter" data-filter="{name}" data-value="{esc(v)}">{esc(v)}</button>'
            for v in values
        )
        return (
            f'<div class="filter-row"><span>{label}</span>'
            f'<button type="button" class="filter is-active" data-filter="{name}" data-value="">semua</button>'
            f'{buttons}</div>'
        )
    gallery = render_template(
        "gallery.html",
        filters=filter_row("project", used_projects, "Project")
                + filter_row("kind", used_kinds, "Jenis")
                + filter_row("mode", used_modes, "Mode"),
        media_cards="".join(media_card(m) for m in media) or (
            '<div class="empty"><strong>Galeri kosong</strong>'
            '<p>Post dengan media akan tampil di sini.</p></div>'
        ),
        media_count=len(media),
    )
    write_if_changed(SITE / "gallery.html", page(
        "Gallery", "Semua gambar, video, model, dan render dengan filter project/kind/mode.",
        gallery, "gallery.html", stamp, "gallery-page",
    ), stats)

    # ---- changelog
    changelog_posts = [p for p in posts if p["type"] in {"changelog", "project-update"}]
    rows = "".join(
        f'<article class="timeline-item"><p class="meta">{esc(p["created_at"])} · '
        f'{esc(by_slug[p["project"]]["codename"])}</p><h3>{esc(p["title"])}</h3>'
        f'<p>{esc(p["summary"])}</p>'
        f'<p>{markdown_to_html(p["body_md"])}</p></article>'
        for p in changelog_posts
    ) or '<div class="empty"><strong>Changelog masih kosong</strong></div>'
    changelog = render_template("changelog.html", items=rows, item_count=len(changelog_posts))
    write_if_changed(SITE / "changelog.html", page(
        "Changelog", "Perubahan AI Station dan project dari waktu ke waktu.",
        changelog, "changelog.html", stamp,
    ), stats)

    # ---- 404
    write_if_changed(SITE / "404.html", page(
        "404 — tidak ditemukan", "Halaman tidak ada.", render_template("404.html"),
        "", stamp, "notfound-page",
    ), stats)

    # ---- feed (public only)
    public_posts = [p for p in posts if p["visibility"] == "public"]
    items = []
    for post in public_posts[:20]:
        link = f"http://127.0.0.1:8999/site/{post_url(post)}"
        items.append(
            "<item>"
            f"<title>{esc(post['title'])}</title>"
            f"<link>{esc(link)}</link>"
            f"<guid isPermaLink=\"false\">{esc(post['id'])}</guid>"
            f"<pubDate>{esc(rfc822(post['created_at']))}</pubDate>"
            f"<description>{esc(post['summary'])}</description>"
            "</item>"
        )
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        "<title>AI Station Playground</title>"
        "<link>http://127.0.0.1:8999/site/</link>"
        "<description>Post public AI Station.</description>"
        f"<lastBuildDate>{esc(rfc822(stamp))}</lastBuildDate>"
        + "".join(items) +
        "</channel></rss>\n"
    )
    write_if_changed(SITE / "feed.xml", feed, stats)

    # ---- search + projects json
    search_entries = []
    for href, title, kind in [
        ("index.html", "Beranda", "page"),
        ("project.html", "Project", "page"),
        ("blog.html", "Blog", "page"),
        ("gallery.html", "Gallery", "page"),
        ("changelog.html", "Changelog", "page"),
    ]:
        search_entries.append({"title": title, "url": href, "type": kind,
                               "project": "", "tags": [], "summary": "",
                               "visibility": "local"})
    for project in projects:
        search_entries.append({
            "title": project["name"], "url": project_url(project), "type": "project",
            "project": project["slug"], "tags": [project["codename"]],
            "summary": project["description"], "visibility": "local",
        })
    for post in posts:
        search_entries.append({
            "title": post["title"], "url": post_url(post), "type": post["type"],
            "project": post["project"], "tags": post["tags"],
            "summary": post["summary"], "visibility": post["visibility"],
        })
    search = json.dumps({
        "generated_at": stamp,
        "site_root": "http://127.0.0.1:8999/site/",
        "entries": search_entries,
    }, ensure_ascii=False, indent=2) + "\n"
    write_if_changed(SITE / "search.json", search, stats)

    projects_payload = {
        "generated_at": stamp,
        "projects": [
            {
                "slug": p["slug"],
                "codename": p["codename"],
                "name": p["name"],
                "mode": p["mode"],
                "status": p["status"],
                "available": p["available"],
                "url": f"/site/{project_url(p)}",
                "posts": stats_by_project[p["slug"]]["posts"],
                "media": stats_by_project[p["slug"]]["media"],
                "docs": stats_by_project[p["slug"]]["docs"],
            }
            for p in projects
        ],
    }
    write_if_changed(SITE / "projects.json", json.dumps(projects_payload, ensure_ascii=False, indent=2) + "\n", stats)

    # ---- assets
    assets_src = TEMPLATES / "assets"
    if assets_src.is_dir():
        for src in sorted(assets_src.rglob("*")):
            if src.is_file():
                copy_if_changed(src, SITE / "assets" / src.relative_to(assets_src), stats)
    for name, svg in PLACEHOLDER_SVG.items():
        write_if_changed(SITE / "assets" / name, svg + "\n", stats)

    # ---- konteks @Project untuk console (redacted, tanpa path absolut)
    export_project_context(projects, stats)

    stats["posts"] = len(posts)
    stats["projects"] = len(projects)
    stats["media"] = len(media)
    stats["stamp"] = stamp


# ---------------------------------------------------------------- init

WELCOME_POST = {
    "id": "post_welcome_ai_station_site",
    "type": "blog",
    "project": "ai-station",
    "title": "Situs ini lahir di mesin, bukan di cloud",
    "slug": "situs-ini-lahir-di-mesin",
    "worker_id": "wk_station",
    "worker_name": "AI Station",
    "summary": "AI Station Playground adalah situs statis lokal: hasil kerja mesin terlihat tanpa port publik.",
    "body_md": (
        "## Kenapa lokal\n\n"
        "Situs ini dibangun dari registry project dan post JSON di workstation. Tidak ada CDN, "
        "tidak ada framework, dan tidak ada port yang mendengarkan ke `0.0.0.0`.\n\n"
        "- Halaman di-generate oleh `tools/site/build.py`.\n"
        "- Media masuk lewat API publish atau watcher output ComfyUI (Phase 3–4).\n"
        "- Semua post default `local`; hanya `public` yang masuk feed.\n\n"
        "> Yang tidak tercatat tidak pernah terjadi. Situs ini adalah salah satu cara mencatatnya."
    ),
    "tags": ["pengantar", "lokal"],
    "visibility": "local",
    "media": [],
    "source": {},
}


def init_site(stats: dict):
    for path in [POSTS_DIR, MEDIA_DIR, THUMBS_DIR, INDEX_CACHE]:
        path.mkdir(parents=True, exist_ok=True)
        print(f"init: {rel_site(path) if path.is_relative_to(SITE) else path}")
    if not CONFIG_LOCAL.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CONFIG_EXAMPLE, CONFIG_LOCAL)
        print("init: config/site_projects.json dibuat dari contoh — sunting path sebelum build")
    readme = SITE / "README.md"
    if not readme.exists():
        stats["warnings"].append(
            "site/README.md hilang — pulihkan dari repo (git checkout -- site/README.md); "
            "berkas ini satu-satunya isi site/ yang dilacak"
        )
    if not any(POSTS_DIR.rglob("*.json")):
        year = POSTS_DIR / str(dt.date.today().year)
        year.mkdir(parents=True, exist_ok=True)
        payload = dict(WELCOME_POST)
        payload["created_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        write_if_changed(year / f"{payload['id']}.json",
                         json.dumps(payload, ensure_ascii=False, indent=2) + "\n", stats)
        print("init: post sambutan ditulis")


# ---------------------------------------------------------------- check

def check() -> int:
    errors = []
    warnings = []
    if not SITE.exists():
        print("check: site/ belum dibangun")
        return 1
    projects = load_registry()
    required = [
        "README.md", "index.html", "project.html", "blog.html", "gallery.html",
        "changelog.html", "404.html", "feed.xml", "search.json",
        "projects.json", "assets/site.css", "assets/site.js",
    ] + [project_url(p) for p in projects]
    for name in required:
        if not (SITE / name).is_file():
            errors.append(f"hilang: site/{name}")

    for name in TEMPLATE_FILES:
        if not (TEMPLATES / name).is_file():
            errors.append(f"template hilang: tools/site/templates/{name}")

    for name in ["search.json", "projects.json"]:
        path = SITE / name
        if path.is_file():
            text = read_text(path)
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{name} bukan JSON valid: {exc}")
                continue
            if "/home/" in text:
                errors.append(f"{name}: memuat path absolut")

    # Konteks @Project ikut divalidasi: harus ada, JSON valid, dan tanpa path absolut.
    for project in projects:
        context_path = SITE / "project-context" / f"{project['slug']}.json"
        if not context_path.is_file():
            errors.append(f"hilang: site/project-context/{project['slug']}.json")
            continue
        text = read_text(context_path)
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"project-context/{project['slug']}.json bukan JSON valid: {exc}")
            continue
        if "/home/" in text:
            errors.append(f"project-context/{project['slug']}.json memuat path absolut")
    try:
        ET.fromstring(read_text(SITE / "feed.xml"))
    except ET.ParseError as exc:
        errors.append(f"feed.xml bukan XML valid: {exc}")

    link_re = re.compile(r'(?:href|src)="([^"]+)"')
    generated = sorted(list(SITE.glob("*.html")))
    for html_file in generated:
        text = read_text(html_file)
        if "{{" in text:
            errors.append(f"{html_file.name}: placeholder template belum terisi")
        if "/home/" in text:
            errors.append(f"{html_file.name}: memuat path absolut")
        for target in link_re.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "data:", "#", "/")):
                continue
            clean = target.split("#", 1)[0].split("?", 1)[0]
            if not clean:
                continue
            candidate = (html_file.parent / clean).resolve()
            try:
                candidate.relative_to(SITE.resolve())
            except ValueError:
                errors.append(f"{html_file.name}: tautan keluar situs: {target}")
                continue
            if not candidate.is_file():
                errors.append(f"{html_file.name}: target tautan tidak ada: {target}")

    for path in SITE.rglob("*"):
        if not path.is_file() or path.name == "README.md":
            continue
        if path.suffix.lower() not in {".html", ".json", ".xml", ".css", ".js", ".svg"}:
            continue
        secret = find_secret(read_text(path, 400_000))
        if secret:
            errors.append(f"{rel_site(path)}: pola {secret[0]} terdeteksi")
        if path.stat().st_size > 2_000_000:
            warnings.append(f"{rel_site(path)}: {format_bytes(path.stat().st_size)} (besar)")

    posts = load_posts({p["slug"]: p for p in projects}, warnings)
    if not posts:
        warnings.append("tidak ada post di site/content/posts")
    if not (SITE / "media").exists():
        errors.append("folder site/media hilang")
    if not INDEX_CACHE.exists():
        warnings.append("data/site-index belum ada (watcher Phase 4)")

    present = len([p for p in required if (SITE / p).is_file()])
    print(f"check: {present}/{len(required)} berkas wajib ada, {len(generated)} halaman, "
          f"{len(posts)} post, {len(warnings)} peringatan, {len(errors)} error")
    for warning in warnings:
        print(f"  warning: {warning}")
    for error in errors:
        print(f"  error: {error}")
    return 0 if not errors else 1


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bangun situs Playground AI Station.")
    parser.add_argument("--init", action="store_true", help="siapkan folder, registry, README, post sambutan")
    parser.add_argument("--check", action="store_true", help="validasi hasil generate")
    parser.add_argument("--quiet", action="store_true", help="keluaran ringkas")
    args = parser.parse_args(argv)

    if args.check:
        return check()

    stats = {"written": 0, "unchanged": 0, "warnings": [], "thumbs": 0, "missing_media": 0}
    if args.init:
        init_site(stats)

    build(stats)

    if not args.quiet:
        print(
            f"build: {stats['projects']} project, {stats['posts']} post, {stats['media']} media, "
            f"{stats['written']} berkas ditulis, {stats['unchanged']} tidak berubah, "
            f"{stats['thumbs']} thumbnail"
        )
        if stats["missing_media"]:
            print(f"  catatan: {stats['missing_media']} media belum ada di disk (placeholder dipakai)")
        for warning in stats["warnings"]:
            print(f"  warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
