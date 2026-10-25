#!/usr/bin/env python3
"""Publisher deterministik untuk rencana Auto Pilot.

Alur: commit berkas yang disetujui → minta vonis verifier → catat push-verified
per sha → push. Tidak ada model yang menjalankan shell, jadi putaran ini tidak
bergantung pada classifier Qoder. Rahasia dipindai sebelum commit; push tetap
melewati credential net di hook pre-push.

Penggunaan:
    tools/auto_publish.py --config data/publish/<task>.json [--dry-run]
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATION = Path("/home/irsofka/.ai-station")
WORK_ORDER = STATION / "tools" / "work_order.py"
CROSS_VERIFY = STATION / "tools" / "cross_verify.py"
SECRET_RE = re.compile(
    r"(nvapi-|sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xoxb-|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|api[_-]?key\s*=\s*['\"]?[A-Za-z0-9_\-]{16,})",
    re.IGNORECASE,
)


def run(cmd, cwd=None, timeout=120):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def git(repo, *args, timeout=120):
    return run(["git", "-C", str(repo), *args], timeout=timeout)


def scan_secrets(repo, files):
    findings = []
    for name in files:
        path = Path(repo) / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in SECRET_RE.finditer(text):
            findings.append(f"{name}: pola rahasia '{match.group(0)[:12]}…'")
    return findings


def commit_repo(repo, files, message):
    status, out, err = git(repo, "status", "--porcelain", "--", *files)
    if status != 0:
        return None, f"git status gagal: {err.strip() or out.strip()}"
    changed = [line[3:].strip() for line in out.splitlines() if line.strip()]
    if not changed:
        # Retry setelah vonis gagal: commit sudah ada, pakai sha itu lagi alih-alih
        # membuat commit kosong.
        code, subject, _ = git(repo, "log", "-1", "--format=%s")
        if code == 0 and subject.strip() == message:
            code, head, _ = git(repo, "rev-parse", "HEAD")
            if code == 0:
                return head.strip(), None
        return None, "tidak ada perubahan pada berkas yang disetujui"
    unexpected = [f for f in changed if f not in files]
    if unexpected:
        return None, f"berkas berubah di luar daftar: {unexpected}"
    code, out, err = git(repo, "add", "--", *files)
    if code != 0:
        return None, f"git add gagal: {err.strip()}"
    code, out, err = git(repo, "commit", "-m", message)
    if code != 0:
        return None, f"git commit gagal: {err.strip() or out.strip()}"
    code, out, _ = git(repo, "rev-parse", "HEAD")
    return out.strip(), None


def build_evidence(path, entries, claim):
    # Tanpa timestamp: isi berkas ini ikut di-hash sebagai subject_fingerprint.
    # Waktu yang berubah tiap retry membuat gerbang handover tidak pernah cocok.
    lines = ["# Publication evidence", ""]
    for entry in entries:
        lines.append(f"# {entry['sha']}")
    lines += ["", claim, ""]
    for entry in entries:
        lines.append(f"## {entry['repo']} — {entry['sha']}")
        code, out, _ = git(entry["repo"], "show", "--stat", "--patch", entry["sha"])
        lines.append("```diff")
        lines.append(out if code == 0 else "(diff tidak terbaca)")
        lines.append("```")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(path, 0o600)


def verify(evidence, verifier, claim):
    code, out, err = run(
        [
            sys.executable,
            str(CROSS_VERIFY),
            verifier,
            claim,
            "--file",
            str(evidence),
            "--focus",
            "publication wording: licence correct, no clean-room label, no secrets, no private paths",
        ],
        timeout=1200,
    )
    text = f"{out}\n{err}"
    verdict = ""
    # `cross_verify.py` mencetak "vonis : SETUJU"; JSON model memakai "VERDICT:".
    # Keduanya harus terbaca, tanpa bergantung pada kapitalisasi.
    match = re.search(r"(?:verdict|vonis)\s*:\s*([A-Za-z_]+)", text, re.IGNORECASE)
    if match:
        verdict = match.group(1).upper()
    return code, verdict, text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    repos = config.get("repos") or []
    verifier = config.get("verifier") or "gemini-verify"
    claim = config.get("claim") or "Perubahan wording siap dipublikasikan."
    row = config.get("row")
    plan = Path(config.get("plan") or "")
    evidence = Path(config.get("evidence") or (STATION / "data" / "publish" / "evidence.md"))

    lock_path = STATION / "data" / "publish" / ".auto_publish.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("publisher lain sedang berjalan; keluar")
            return 0

        for repo in repos:
            findings = scan_secrets(repo["path"], repo["files"])
            if findings:
                print("DITOLAK: pola rahasia ditemukan:")
                for item in findings:
                    print(" -", item)
                return 2
            print(f"aman: {repo['path']} ({len(repo['files'])} berkas)")

        if args.dry_run:
            print("dry-run: tidak ada commit/push")
            return 0

        entries = []
        for repo in repos:
            sha, error = commit_repo(Path(repo["path"]), repo["files"], repo["message"])
            if not sha:
                print(f"GAGAL {repo['path']}: {error}")
                return 3
            entries.append({"repo": repo["path"], "sha": sha, "message": repo["message"]})
            print(f"commit {sha[:12]} di {repo['path']}")

        build_evidence(evidence, entries, claim)
        print(f"bukti: {evidence}")
        code, verdict, text = verify(evidence, verifier, claim)
        print(f"verifikasi: exit={code} verdict={verdict or 'TIDAK TERBACA'}")
        if verdict != "SETUJU" or code != 0:
            print(text[-1500:])
            print("TIDAK DIPUSH: vonis bukan SETUJU")
            return 4

        for entry in entries:
            code, out, err = run(
                [
                    sys.executable,
                    str(WORK_ORDER),
                    "push-verified",
                    "--model",
                    verifier,
                    "--sha",
                    entry["sha"],
                    "--repo",
                    entry["repo"],
                ],
                timeout=120,
            )
            print(f"push-verified {entry['sha'][:12]}: exit={code} {out.strip()[:160]}")
            if code != 0:
                print(err.strip()[:400])
                return 5

        pushed = []
        for entry in entries:
            code, out, err = git(entry["repo"], "push", timeout=300)
            text = (out + err).strip()
            print(f"push {entry['repo']}: exit={code}")
            if code != 0:
                print(text[-800:])
                return 6
            pushed.append(entry)

        evidence_text = "\n".join(
            f"- {e['repo']}: {e['sha']} — {e['message']}" for e in pushed
        )
        print("PUBLISHED")
        print(evidence_text)

        if row:
            sys.path.insert(0, str(STATION / "tools"))
            import work_order as wo

            conn, engine = wo.connect()
            ok, message = wo.finish(
                conn,
                engine,
                int(row),
                "COMPLETED",
                evidence=f"Published by tools/auto_publish.py. {evidence_text} verifier={verifier} verdict={verdict}",
            )
            print(f"ledger row {row}: {ok} {message}")
        if plan.is_file():
            text = plan.read_text(encoding="utf-8")
            text = text.replace(
                "- [ ] Commit and publish the approved wording",
                "- [x] Commit and publish the approved wording",
                1,
            )
            text += (
                "\n\n### Publication evidence (auto_publish)\n\n"
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} — verifier {verifier} = {verdict}\n\n"
                f"{evidence_text}\n"
            )
            plan.write_text(text, encoding="utf-8")
        return 0


if __name__ == "__main__":
    sys.exit(main())
