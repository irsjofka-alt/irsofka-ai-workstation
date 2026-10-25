#!/usr/bin/env python3
"""Push gate for a repository the Station ledger does not own — one definition, reached by link.

Why this file exists. Measured 2026-10-16: `bin/install_hooks.sh --repo` wired the credential guard into
the three F11 MCP repositories, and their `pre-push` then demanded `<repo>/tools/work_order.py`, which
only the station has. Result: every push out of all three was refused — the published surface of F11 made
un-pushable by the guard meant to protect it. Skipping the gate loudly is the stopgap (the hook does
that); this is the repair, because "no gate here" is a state to fix, not a state to document.

Why it is a THIN script instead of a second implementation. The rules about what counts as a clearance —
order of events per sha, a refusal revoking an older grant, an operator's hold that no verdict can lift,
a sha normalised to 12 characters, unreadable git meaning "closed" — live in `work_order.push_gate`, and
they survived six review rounds to get there. A copy in another file would be a second definition of one
word (§12) and the copy would be the one nobody reviews. So the repository is taken from
`git rev-parse --show-toplevel` (the working directory the hook runs in), and every judgement is made by
importing the station's own module and calling it with `repo=`.

Deliberate limits, stated because a gate that hides its edges gets trusted in the wrong places:
  * it answers `push-gate` and nothing else; unknown verbs refuse rather than pretend to be the ledger;
  * it never touches `quest_tasks`, decisions, or any table: there is no work order in a repository whose
    only job is to be published, and a half-populated ledger row is worse than none;
  * it reads the station's event log, which is where verdicts are recorded, so a verdict for an MCP
    commit has to be recorded with `--repo` pointing at that repository — see `record_push_verified`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def station_home() -> Path:
    """The station, from the environment the daemon already sets, never from a remembered path."""
    env = (os.environ.get("AI_STATION_HOME") or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".ai-station"


def current_repo() -> str | None:
    """The repository this command was invoked IN — from the working directory, not from __file__.

    `__file__` would resolve through the symlink each MCP repository carries and land back at the
    station, which would judge the station's range and hand an MCP commit a clearance it never had.
    """
    try:
        r = subprocess.run(["git", "-C", os.getcwd(), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001 — unreadable is not empty, and is never "clean"
        return None
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else None


def main(argv: list[str]) -> int:
    verb = argv[0] if argv else ""
    st = station_home()
    if not (st / "tools" / "work_order.py").is_file():
        print(json.dumps({"ok": False, "why": f"no station ledger at {st} — this gate cannot judge "
                                             "anything without the rules that own the word 'clearance'"},
                         indent=2), file=sys.stderr)
        return 1
    sys.path.insert(0, str(st / "tools"))
    try:
        import work_order as wo  # the one definition of what a clearance is
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "why": f"work_order.py tidak terbaca: {type(exc).__name__}: {exc}"},
                         indent=2), file=sys.stderr)
        return 1
    repo = current_repo()
    if repo is None:
        print(json.dumps({"ok": False, "why": "git tidak terbaca dari direktori kerja — UNKNOWN, dan "
                                             "UNKNOWN tidak pernah berarti boleh mendorong"}, indent=2),
              file=sys.stderr)
        return 1
    if verb == "push-gate":
        gate = wo.push_gate(repo=repo)
        gate["repo"] = repo                      # which repository this verdict is about, always visible
        print(json.dumps(gate, default=str, indent=2))
        return 0 if gate.get("ok") else 1
    if verb == "hold":
        # The other half of the §6 promise: a run that cannot reach a verifier commits locally and
        # leaves the push in the morning digest — which needs a place to SAY so, in the repository the
        # commits are actually sitting in. Same rules, same event log, same function.
        args, reason, by, release = argv[1:], "", "machine", False
        i = 0
        while i < len(args):
            if args[i] == "--reason" and i + 1 < len(args):
                reason = args[i + 1]; i += 2
            elif args[i] == "--by" and i + 1 < len(args):
                by = args[i + 1]; i += 2
            elif args[i] == "--release":
                release = True; i += 1
            else:
                i += 1
        if not reason.strip():
            print(json.dumps({"ok": False, "why": "hold without --reason is not a declaration"},
                             indent=2), file=sys.stderr)
            return 1
        if release:
            raw = wo._git("rev-list", "@{u}..HEAD", repo=repo)
            un = [x for x in (raw or "").splitlines() if x.strip()]
            rows = wo.release_push_hold(un, reason, by=by) if raw is not None else []
            print(json.dumps({"ok": bool(rows), "released": len(rows), "by": by, "repo": repo},
                             indent=2))
            return 0 if rows else 1
        payload, code = wo.hold_unpushed(reason, by=by, repo=repo)
        print(json.dumps(payload, indent=2))
        return code
    print(json.dumps({"ok": False, "repo": repo,
                      "why": f"verb {verb!r} not served here. This repository has a push gate, not a "
                             "work order: supported verbs are push-gate and hold. A verdict is recorded "
                             "in one place only — `python3 ~/.ai-station/tools/work_order.py "
                             "push-verified --model … --sha … --repo <this repo>` — because a clearance "
                             "is a claim about a ledger, not about a file."},
                     indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
