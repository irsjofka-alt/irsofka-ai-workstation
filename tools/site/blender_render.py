#!/usr/bin/env python3
"""blender_render.py — batch render ReadyAsset lewat Blender CLI headless.

Menemukan seluruh `.blend` di IRSofka-ReadyAsset (18 mesh + 1 complete
assembly), memeriksa VRAM bebas, lalu merender satu per satu dengan
`blender -b <file> --python blender_render_one.py` — bukan lewat MCP socket.

Setiap hasil render dipublikasikan sebagai post showcase lewat publish.py,
sehingga `project-readyasset.html` dan gallery terisi otomatis.

Perintah:
    blender_render.py [--dry-run] [--force] [--resume] [--only SUBSTR ...]
                      [--max N] [--no-publish] [--res 1280] [--samples 32]
                      [--min-free-mib 2048] [--wait-gpu SECONDS]

Exit code: 0 sukses, 1 ada kegagalan, 3 diblokir (lock/VRAM).
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import publish  # noqa: E402

ROOT = build.ROOT
INDEX = build.INDEX_CACHE
STATE_PATH = INDEX / "render_state.json"
STAGING = INDEX / "renders" / "readyasset"
LOCK_PATH = INDEX / "render.lock"
ONE_SCRIPT = HERE / "blender_render_one.py"
ASSET_ROOT = Path(os.environ.get(
    "READYASSET_ROOT", "/home/irsofka/Documents/ai-workstation/projects/IRSofka-ReadyAsset"))
BLENDER = os.environ.get("BLENDER_BIN", "/home/irsofka/runtime/local/bin/blender")
PROJECT = "readyasset"
RENDER_TIMEOUT = 1800


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def progress(message: str):
    print(f"[blender_render] {message}", file=sys.stderr, flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover() -> list:
    if not ASSET_ROOT.is_dir():
        raise SystemExit(f"asset root tidak ada: {ASSET_ROOT}")
    items = []
    for path in sorted(ASSET_ROOT.glob("**/*.blend")):
        if ".git" in path.parts:
            continue
        kind = "assembly" if "complete" in path.stem.lower() else "mesh"
        items.append({
            "blend": path,
            "rel": str(path.relative_to(ASSET_ROOT)),
            "stem": path.stem,
            "kind": kind,
        })
    return items


def title_for(stem: str) -> str:
    if stem.lower().endswith("_complete"):
        return "SP Training Hall — Complete Assembly"
    if stem.startswith("SP_TrainingHall_"):
        return "SP Training Hall — " + stem[len("SP_TrainingHall_"):].replace("_", " ")
    return stem.replace("_", " ")


def vram_free_mib():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.check_output(
            [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True, timeout=10)
        values = [int(line.strip()) for line in out.splitlines() if line.strip()]
        return min(values) if values else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def wait_for_vram(min_free_mib: int, wait_seconds: int):
    """Kembalikan (ok, info). Menunggu bila VRAM sedang dipakai ComfyUI/UE."""
    deadline = time.time() + max(wait_seconds, 0)
    while True:
        free = vram_free_mib()
        if free is None:
            return True, {"free_mib": None, "note": "nvidia-smi tidak terbaca; cek VRAM dilewati"}
        if free >= min_free_mib:
            return True, {"free_mib": free, "required_mib": min_free_mib}
        if time.time() >= deadline:
            return False, {"free_mib": free, "required_mib": min_free_mib,
                           "note": "VRAM kurang; tunggu ComfyUI/UE selesai atau pakai --wait-gpu"}
        progress(f"VRAM {free} MiB < {min_free_mib} MiB, tunggu 5 dtk…")
        time.sleep(5)


def acquire_lock():
    INDEX.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def load_state() -> dict:
    try:
        data = json.loads(build.read_text(STATE_PATH) or "{}")
        if isinstance(data, dict) and isinstance(data.get("renders"), dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"version": 1, "renders": {}}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def staging_paths(item: dict) -> tuple:
    STAGING.mkdir(parents=True, exist_ok=True)
    png = STAGING / f"{item['stem']}.png"
    meta = STAGING / f"{item['stem']}.json"
    return png, meta


def entry_published(entry: dict) -> bool:
    post_file = entry.get("post_file")
    return bool(entry.get("post_id") and post_file and (ROOT / post_file).is_file())


def render_one(item: dict, args) -> dict:
    png, meta_path = staging_paths(item)
    if meta_path.exists():
        meta_path.unlink()
    cmd = [
        BLENDER, "-b", str(item["blend"]), "--python", str(ONE_SCRIPT), "--",
        "--out", str(png), "--meta", str(meta_path),
        "--res", str(args.res), "--samples", str(args.samples), "--engine", args.engine,
    ]
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"blender timeout > {RENDER_TIMEOUT}s"}
    result = None
    for line in proc.stdout.splitlines():
        if line.startswith("RENDER_RESULT "):
            try:
                result = json.loads(line[len("RENDER_RESULT "):])
            except json.JSONDecodeError:
                result = None
    if result is None:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-8:]
        return {"ok": False, "error": f"blender exit {proc.returncode} tanpa RENDER_RESULT",
                "tail": tail}
    if not result.get("ok") or not png.is_file() or not meta_path.is_file():
        return {"ok": False, "error": result.get("error") or "PNG/metadata tidak tertulis"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {"ok": True, "png": png, "meta_path": meta_path, "meta": meta,
            "seconds": round(time.time() - started, 2)}


def publish_item(item: dict, rendered: dict, blend_sha: str) -> dict:
    meta = rendered["meta"]
    dims = meta.get("bbox_size") or [0, 0, 0]
    seconds = meta.get("render_seconds", 0)
    body = "\n".join([
        f"Render studio otomatis dari `{item['rel']}`.",
        "",
        f"- Objek: {meta.get('object_count')} mesh ({meta.get('scene')})",
        f"- Bounding box: {dims[0]:.2f} × {dims[1]:.2f} × {dims[2]:.2f} m",
        f"- Engine: {meta.get('engine')} · {meta.get('samples')} sampel · "
        f"{meta.get('resolution')[0]}×{meta.get('resolution')[1]}",
        f"- Waktu render: {seconds} dtk",
    ])
    request = {
        "source_path": str(rendered["png"]),
        "project": PROJECT,
        "kind": "render",
        "mode": "render",
        "post_type": "showcase",
        "title": title_for(item["stem"]),
        "summary": (f"Render {meta.get('resolution')[0]}×{meta.get('resolution')[1]} · "
                    f"{meta.get('object_count')} objek · bbox "
                    f"{dims[0]:.1f}×{dims[1]:.1f}×{dims[2]:.1f} m · {seconds} dtk"),
        "body_md": body,
        "worker_name": "Blender",
        "tags": ["readyasset", "blender", "render", "eevee"],
        "visibility": "local",
        "public": False,
        "model": f"Blender {meta.get('blender_version')} · {meta.get('engine')}",
        "source": {
            "path": item["rel"],
            "sha256": blend_sha,
            "model": f"Blender {meta.get('blender_version')}",
        },
    }
    return publish.publish_media(request)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Batch render ReadyAsset lewat Blender CLI.")
    parser.add_argument("--dry-run", action="store_true", help="tampilkan rencana tanpa render")
    parser.add_argument("--force", action="store_true", help="render ulang walau sudah tercatat")
    parser.add_argument("--resume", action="store_true",
                        help="jangan render; publikasikan hasil staging yang sudah ada")
    parser.add_argument("--only", action="append", default=[],
                        help="filter substring nama berkas (boleh berulang)")
    parser.add_argument("--max", type=int, default=0, help="batasi jumlah aset diproses")
    parser.add_argument("--no-publish", action="store_true", help="render tanpa menulis post")
    parser.add_argument("--res", type=int, default=1280)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--engine", default="BLENDER_EEVEE")
    parser.add_argument("--min-free-mib", type=int, default=2048)
    parser.add_argument("--wait-gpu", type=int, default=0,
                        help="detik menunggu VRAM bebas sebelum menyerah")
    args = parser.parse_args(argv)

    if not Path(BLENDER).is_file():
        return emit({"ok": False, "error": f"blender tidak ada: {BLENDER}"}, 1)

    items = discover()
    meshes = [item for item in items if item["kind"] == "mesh"]
    assemblies = [item for item in items if item["kind"] == "assembly"]
    if args.only:
        needles = [needle.lower() for needle in args.only]
        items = [item for item in items
                 if any(needle in item["stem"].lower() or needle in item["rel"].lower()
                        for needle in needles)]

    state = load_state()
    renders = state.setdefault("renders", {})
    plan = []
    for item in items:
        entry = renders.get(item["rel"], {})
        png, meta_path = staging_paths(item)
        if entry_published(entry) and not args.force:
            action = "skip-published"
        elif entry.get("png_sha256") and png.is_file() \
                and sha256_file(png) == entry.get("png_sha256") and not args.force:
            action = "publish-existing" if not args.no_publish else "skip-rendered"
        elif args.resume:
            action = "skip-resume-missing"
        else:
            action = "render"
        plan.append({"item": item, "action": action, "entry": entry})
    if args.max > 0:
        counted = 0
        limited = []
        for row in plan:
            if counted >= args.max:
                row = {**row, "action": "skip-max"}
            elif row["action"] != "skip-published":
                counted += 1
            limited.append(row)
        plan = limited

    if args.dry_run:
        return emit({
            "ok": True, "dry_run": True,
            "asset_root": str(ASSET_ROOT),
            "found": {"total": len(items), "mesh": len(meshes), "assembly": len(assemblies)},
            "plan": [{
                "rel": row["item"]["rel"], "kind": row["item"]["kind"],
                "action": row["action"],
            } for row in plan],
        })

    todo_render = [row for row in plan if row["action"] == "render"]
    if todo_render:
        ok, info = wait_for_vram(args.min_free_mib, args.wait_gpu)
        if not ok:
            return emit({"ok": False, "blocked": "vram", "gpu": info}, 3)
        progress(f"VRAM check: {info}")

    lock = acquire_lock()
    if lock is None:
        return emit({"ok": False, "blocked": "lock",
                     "error": f"batch lain sedang berjalan ({LOCK_PATH})"}, 3)

    summary = {"ok": True, "rendered": 0, "published": 0, "skipped": 0, "failed": [],
               "posts": [], "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        for row in plan:
            item, action = row["item"], row["action"]
            if action.startswith("skip"):
                summary["skipped"] += 1
                progress(f"{action}: {item['rel']}")
                continue

            rendered = None
            if action == "publish-existing":
                png, meta_path = staging_paths(item)
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                rendered = {"png": png, "meta_path": meta_path, "meta": meta}
                progress(f"pakai hasil render lama: {item['rel']}")
            else:
                progress(f"render: {item['rel']} ({args.res}px, {args.samples} sampel)")
                rendered = render_one(item, args)
                if not rendered.get("ok"):
                    summary["failed"].append({"rel": item["rel"], "error": rendered.get("error"),
                                              "tail": rendered.get("tail")})
                    progress(f"GAGAL: {rendered.get('error')}")
                    continue

            png_sha = sha256_file(rendered["png"])
            entry = renders.setdefault(item["rel"], {})
            entry.update({
                "kind": item["kind"],
                "rendered_at": rendered["meta"].get("rendered_at"),
                "png": str(rendered["png"].relative_to(ROOT)),
                "png_sha256": png_sha,
                "meta": str(rendered["meta_path"].relative_to(ROOT)),
                "object_count": rendered["meta"].get("object_count"),
                "bbox_size": rendered["meta"].get("bbox_size"),
                "render_seconds": rendered["meta"].get("render_seconds"),
                "engine": rendered["meta"].get("engine"),
                "resolution": rendered["meta"].get("resolution"),
            })
            save_state(state)

            if rendered["meta"].get("render_seconds"):
                summary["rendered"] += 1

            if args.no_publish:
                continue
            try:
                published = publish_item(item, rendered, sha256_file(item["blend"]))
            except publish.Rejected as exc:
                summary["failed"].append({"rel": item["rel"], "error": f"publish ditolak: {exc}"})
                progress(f"publish ditolak: {exc}")
                continue
            post = published.get("post") or {}
            entry.update({
                "post_id": post.get("id"), "post_file": post.get("file"),
                "post_url": post.get("url"), "visibility": post.get("visibility"),
            })
            save_state(state)
            summary["published"] += 1
            summary["posts"].append({"rel": item["rel"], "post_id": post.get("id"),
                                     "url": post.get("url")})
            progress(f"post {post.get('id')} → {post.get('url')}")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    summary["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    summary["state"] = str(STATE_PATH.relative_to(ROOT))
    return emit(summary, 1 if summary["failed"] else 0)


if __name__ == "__main__":
    sys.exit(main())
