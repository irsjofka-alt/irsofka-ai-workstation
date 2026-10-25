#!/usr/bin/env python3
"""ingest.py — watcher direktori output ComfyUI menjadi post gallery.

Satu putaran (`--once`) untuk systemd timer, atau loop (`--loop`). Berkas baru
yang sudah stabil dibaca metadatanya (PNG `prompt`/`workflow`, sidecar JSON,
lalu nama berkas), diklasifikasi mode-nya, lalu dipublikasikan lewat
`publish.py` — satu pintu yang sama dengan worker dan API.

State di `data/site-index/ingest_state.json`:
  - `initialized`  : run pertama mencatat berkas lama TANPA memposting (baseline),
                     supaya gallery tidak dibanjiri riwayat. `--backfill` memaksa
                     berkas lama ikut dipublikasikan.
  - `seen`         : sha256 → jejak publikasi (idempoten; sha sama tidak dua kali).
  - `files`        : path → mtime/size/sha untuk melewati berkas yang tak berubah.

Perintah:
    ingest.py --once [--backfill] [--dry-run]
    ingest.py --loop [--interval 20]
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import publish  # noqa: E402

ROOT = build.ROOT
CONFIG_EXAMPLE = ROOT / "config" / "site_ingest.example.json"
CONFIG_LOCAL = ROOT / "config" / "site_ingest.json"
STATE_PATH = build.INDEX_CACHE / "ingest_state.json"
LOCK_PATH = build.INDEX_CACHE / "ingest.lock"

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"}
VIDEO_EXT = {".mp4", ".webm", ".mov"}
VIDEO_NODES = {"VHS_VideoCombine", "SaveVideo", "SaveAnimatedWEBP", "SaveWEBM", "CreateVideo"}
LOAD_IMAGE_NODES = {"LoadImage", "LoadImageMask", "LoadImageOutput"}
MODEL_NODES = {"CheckpointLoaderSimple", "CheckpointLoader", "UNETLoader", "UnetLoaderGGUF"}
DEFAULT_WATCH = [
    {"path": "~/ComfyUI-Shared/output", "project": "comfyui"},
    {"path": "~/runtime/comfyui/output", "project": "comfyui"},
    {"path": "~/ComfyUI-Installs/ComfyUI/ComfyUI/output", "project": "comfyui"},
]


def load_config() -> dict:
    config = {"interval_seconds": 20, "stability_seconds": 4, "watch": list(DEFAULT_WATCH)}
    for path in (CONFIG_EXAMPLE, CONFIG_LOCAL):
        if not path.is_file():
            continue
        try:
            data = json.loads(build.read_text(path) or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            config.update({k: v for k, v in data.items() if k in {"interval_seconds", "stability_seconds", "watch"}})
    config["interval_seconds"] = max(5, int(config.get("interval_seconds") or 20))
    config["stability_seconds"] = max(0, int(config.get("stability_seconds") or 4))
    return config


def load_state() -> dict:
    try:
        data = json.loads(build.read_text(STATE_PATH) or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("initialized", False)
    data.setdefault("seen", {})
    data.setdefault("files", {})
    return data


def save_state(state: dict):
    build.write_if_changed(
        STATE_PATH, json.dumps(state, indent=1, sort_keys=True),
        {"written": 0, "unchanged": 0},
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_info(path: Path) -> dict:
    if path.suffix.lower() != ".png":
        return {}
    try:
        from PIL import Image
        with Image.open(path) as image:
            return {key: image.info[key] for key in ("prompt", "workflow") if key in image.info}
    except Exception:  # noqa: BLE001 — metadata hilang bukan alasan gagal
        return {}


def sidecar_info(path: Path) -> dict:
    sidecar = path.with_suffix(".json")
    if not sidecar.is_file():
        return {}
    try:
        data = json.loads(build.read_text(sidecar) or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: json.dumps(data[key]) if not isinstance(data[key], str) else data[key]
            for key in ("prompt", "workflow") if key in data}


def api_graph(raw: str | None):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    nodes = {key: value for key, value in data.items()
             if isinstance(value, dict) and "class_type" in value}
    return nodes or None


def linked_text(nodes: dict, node_id: str | None) -> str:
    if not node_id or node_id not in nodes:
        return ""
    inputs = nodes[node_id].get("inputs") or {}
    for key in ("text", "text_g", "string"):
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def describe_graph(nodes: dict | None) -> dict:
    if not nodes:
        return {}
    classes = {str(node.get("class_type")) for node in nodes.values()}
    model = ""
    for node in nodes.values():
        if str(node.get("class_type")) in MODEL_NODES:
            inputs = node.get("inputs") or {}
            model = str(inputs.get("ckpt_name") or inputs.get("unet_name") or "")
            if model:
                break
    seed = None
    positive = negative = ""
    for node in nodes.values():
        inputs = node.get("inputs") or {}
        if "seed" in inputs and seed is None:
            seed = inputs.get("seed")
        if "noise_seed" in inputs and seed is None:
            seed = inputs.get("noise_seed")
        if str(node.get("class_type")).startswith("KSampler"):
            pos = inputs.get("positive")
            neg = inputs.get("negative")
            if isinstance(pos, list) and pos:
                positive = positive or linked_text(nodes, str(pos[0]))
            if isinstance(neg, list) and neg:
                negative = negative or linked_text(nodes, str(neg[0]))
    if not positive:
        texts = [linked_text(nodes, key) for key in nodes]
        texts = [text for text in texts if text]
        positive = max(texts, key=len) if texts else ""
    return {
        "classes": classes,
        "model": model,
        "seed": seed,
        "positive": positive,
        "negative": negative,
    }


def classify(path: Path, info: dict) -> tuple[str, str, dict]:
    """(kind, mode, meta) dari metadata graph atau nama berkas."""
    kind = "video" if path.suffix.lower() in VIDEO_EXT else "image"
    graph = api_graph(info.get("prompt")) or api_graph(info.get("workflow"))
    meta = describe_graph(graph)
    classes = meta.get("classes") or set()
    has_load_image = bool(classes & LOAD_IMAGE_NODES)
    has_sampler = any(str(name).startswith("KSampler") for name in classes)
    if classes & VIDEO_NODES or kind == "video":
        kind = "video"
        mode = "img2video" if has_load_image else "txt2video"
    elif has_load_image and has_sampler:
        mode = "img2img"
    elif has_sampler:
        mode = "txt2img"
    else:
        mode = "other"
    meta.pop("classes", None)
    return kind, mode, {key: value for key, value in meta.items() if value not in (None, "")}


def title_for(path: Path, meta: dict) -> str:
    prompt = str(meta.get("positive") or "").strip()
    if prompt:
        clean = " ".join(prompt.split())
        return clean[:72] + ("…" if len(clean) > 72 else "")
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def candidates(watch: list) -> list:
    rows = []
    for entry in watch:
        base = Path(os.path.expanduser(str(entry.get("path") or "")))
        project = str(entry.get("project") or "comfyui")
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.is_file() and path.suffix.lower() in SUPPORTED:
                rows.append((path, project))
    return rows


def run_once(config: dict, backfill: bool = False, dry_run: bool = False) -> dict:
    state = load_state()
    baseline = not state["initialized"] and not backfill
    now = dt.datetime.now().timestamp()
    published, seen_existing, skipped = [], 0, 0

    for path, project in candidates(config["watch"]):
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime < config["stability_seconds"]:
            skipped += 1
            continue
        key = str(path.resolve())
        known = state["files"].get(key)
        if known and known.get("size") == stat.st_size and known.get("mtime") == int(stat.st_mtime):
            skipped += 1
            continue
        digest = sha256_file(path)
        state["files"][key] = {"size": stat.st_size, "mtime": int(stat.st_mtime), "sha256": digest}
        if baseline:
            state["seen"].setdefault(digest, {"path": key, "published": False, "baseline": True})
            seen_existing += 1
            continue
        if digest in state["seen"]:
            skipped += 1
            continue

        info = png_info(path) or sidecar_info(path)
        kind, mode, meta = classify(path, info)
        request = {
            "source_path": key,
            "project": project,
            "kind": kind,
            "mode": mode,
            "post_type": "generation",
            "title": title_for(path, meta),
            "summary": f"ComfyUI {mode} · {kind} · {path.name}",
            "worker_id": "wk_comfyui",
            "worker_name": "ComfyUI",
            "tags": ["comfyui", mode],
            "visibility": "local",
            "model": meta.get("model") or "",
            "prompt": meta.get("positive") or "",
            "seed": meta.get("seed"),
        }
        if dry_run:
            published.append({"dry_run": True, "path": key, "project": project,
                              "kind": kind, "mode": mode, "title": request["title"]})
            continue
        try:
            result = publish.publish_media(request)
        except publish.Rejected as exc:
            state["seen"][digest] = {"path": key, "published": False, "rejected": str(exc)}
            skipped += 1
            continue
        state["seen"][digest] = {
            "path": key,
            "published": True,
            "post_id": (result.get("post") or {}).get("id"),
            "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        published.append({
            "path": key,
            "project": project,
            "kind": kind,
            "mode": mode,
            "post_id": (result.get("post") or {}).get("id"),
        })

    state["initialized"] = True
    state["cursor_ts"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if not dry_run:
        save_state(state)
    return {
        "ok": True,
        "baseline": baseline,
        "published": published,
        "baseline_recorded": seen_existing,
        "skipped": skipped,
        "initialized": state["initialized"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Watcher output ComfyUI → situs.")
    parser.add_argument("--once", action="store_true", help="satu putaran (untuk systemd timer)")
    parser.add_argument("--loop", action="store_true", help="loop terus dengan interval")
    parser.add_argument("--interval", type=int, default=None, help="detik per putaran")
    parser.add_argument("--backfill", action="store_true", help="publikasikan berkas lama juga")
    parser.add_argument("--dry-run", action="store_true", help="hanya laporkan, tidak menulis")
    args = parser.parse_args(argv)
    config = load_config()
    if args.interval:
        config["interval_seconds"] = max(5, args.interval)

    build.INDEX_CACHE.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": True, "skipped": "watcher lain sedang berjalan"}, ensure_ascii=False))
            return 0
        if args.loop:
            while True:
                print(json.dumps(run_once(config, args.backfill, args.dry_run), ensure_ascii=False), flush=True)
                time.sleep(config["interval_seconds"])
        print(json.dumps(run_once(config, args.backfill, args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
