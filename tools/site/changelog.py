#!/usr/bin/env python3
"""changelog.py — post changelog dari `git log` AI Station.

Membaca N commit terakhir, menjalankan `build.py --check` sebagai angka uji, lalu
menerbitkan satu post `changelog` lewat `publish.py`. Commit yang sudah tercatat
disimpan di `data/site-index/changelog_state.json`; menjalankan ulang tanpa
commit baru tidak menduplikasi post.

Perintah:
    changelog.py [--limit 3] [--force] [--dry-run]

Jalankan setelah deploy/push, atau dari Auto Pilot.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import publish  # noqa: E402

ROOT = build.ROOT
STATE_PATH = build.INDEX_CACHE / "changelog_state.json"
PROJECT = "ai-station"


def git(*args: str, timeout: int = 20) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git gagal").strip()[:400])
    return proc.stdout


def commits(limit: int) -> list:
    raw = git("log", f"-n{limit}", "--date=short",
              "--pretty=format:%h%x09%ad%x09%s")
    rows = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"hash": parts[0], "date": parts[1], "subject": "\t".join(parts[2:])})
    return rows


def diff_totals(limit: int) -> dict:
    try:
        raw = git("log", f"-n{limit}", "--numstat", "--format=")
    except RuntimeError:
        return {"files": 0, "added": 0, "deleted": 0}
    files = added = deleted = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            deleted += int(parts[1])
    return {"files": files, "added": added, "deleted": deleted}


def build_check() -> dict:
    proc = subprocess.run(
        [sys.executable, str(HERE / "build.py"), "--check"],
        capture_output=True, text=True, timeout=120,
    )
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    summary = next((line for line in lines if line.startswith("check:")), "")
    return {"exit": proc.returncode, "summary": summary}


def load_state() -> dict:
    try:
        data = json.loads(build.read_text(STATE_PATH) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_state(state: dict):
    build.write_if_changed(
        STATE_PATH, json.dumps(state, indent=1, sort_keys=True),
        {"written": 0, "unchanged": 0},
    )


def compose(rows: list, totals: dict, check: dict) -> dict:
    newest = rows[0]["hash"] if rows else "HEAD"
    oldest = rows[-1]["hash"] if rows else "HEAD"
    title = f"Changelog AI Station — {oldest}..{newest}"
    commit_lines = "\n".join(
        f"- `{row['hash']}` — {row['subject']} ({row['date']})" for row in rows
    ) or "- (tidak ada commit terbaca)"
    body = (
        "## Commit\n\n"
        f"{commit_lines}\n\n"
        "## Uji\n\n"
        f"- `build.py --check`: {check['summary'] or '(tanpa ringkasan)'} "
        f"(exit {check['exit']})\n\n"
        "## Angka\n\n"
        f"- {len(rows)} commit · {totals['files']} berkas tersentuh · "
        f"+{totals['added']}/−{totals['deleted']} baris\n"
    )
    return {
        "type": "changelog",
        "project": PROJECT,
        "title": title,
        "slug": f"changelog-{newest}",
        "worker_id": "wk_station",
        "worker_name": "AI Station",
        "summary": f"{len(rows)} commit ({oldest}..{newest}); "
                   f"check exit {check['exit']}; +{totals['added']}/−{totals['deleted']} baris.",
        "tags": ["changelog", "git"],
        "body_md": body,
        "visibility": "local",
        "source": {"path": "git log", "sha256": newest},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Post changelog dari git log.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="terbitkan walau HEAD sudah tercatat")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    limit = max(1, min(args.limit, 20))
    try:
        rows = commits(limit)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": f"git log gagal: {exc}"}, ensure_ascii=False))
        return 1
    if not rows:
        print(json.dumps({"ok": False, "error": "tidak ada commit terbaca"}, ensure_ascii=False))
        return 1

    state = load_state()
    if rows[0]["hash"] == state.get("last_commit") and not args.force:
        print(json.dumps({
            "ok": True,
            "skipped": "HEAD sudah tercatat",
            "last_commit": rows[0]["hash"],
            "last_post": state.get("last_post"),
        }, ensure_ascii=False))
        return 0

    totals = diff_totals(limit)
    check = build_check()
    post = compose(rows, totals, check)
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "post": post, "check": check},
                         ensure_ascii=False, indent=2))
        return 0

    try:
        result = publish.publish_post({"post": post, "public": False})
    except publish.Rejected as exc:
        print(json.dumps({"ok": False, "error": str(exc), "secrets": exc.hits}, ensure_ascii=False))
        return 2
    state.update({
        "last_commit": rows[0]["hash"],
        "last_post": result["post"]["id"],
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    save_state(state)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
