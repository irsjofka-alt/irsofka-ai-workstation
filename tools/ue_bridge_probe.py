#!/usr/bin/env python3
"""Round-trip probe for the IRSofka Station bridge — the editor's own TCP socket.

Framing was READ from the plugin source, not guessed:
  IRSofkaDispatcher/Private/IRSofkaSocketServer.cpp
    - binds 127.0.0.1:9877 (`FIPv4Endpoint(FIPv4Address(127,0,0,1), ServerPort)`)
    - one request per connection, terminated by '\\n' (`ReceivedData.Last() == '\\n'`)
    - reply is one JSON object with '\\n' appended (`ResponseString.AppendChar('\\n')`)
    - request key `domain` is matched lowercased against registered handlers
    - handlers run synchronously on the GameThread via `FIRSofkaTaskRunner::ExecuteSync`
  IRSofkaWorld/Private/IRSofkaWorldSubsystem.cpp
    - status vocabulary is "SUCCESS" / "FAILED" with an `error` string
    - get_all_actors answers {status, count, actors:[{label,class,loc}]}
    - spawn answers {actor_label, class, location}; get_transform answers
      {location, rotation, scale}; set_transform answers {status} and nothing else.

What it measured on 2026-10-15, on a live editor, so the next reader starts from facts: the round
trip is CORRECT for a real class path — `/Script/Engine.StaticMeshActor` spawns as
class=StaticMeshActor and written location/rotation/scale read back exactly. A bare name
("StaticMeshActor") makes `LoadClass` return null, the handler falls back to
`AActor::StaticClass()`, and a bare AActor has no RootComponent, so every transform becomes a
silent no-op while all three calls answer SUCCESS. Both gaps are reported under `engine_defects`.

Mutation is opt-in. Spawning into the operator's open level dirties the package and enters the
editor's transaction buffer, and a probe killed between spawn and delete would leave an orphan,
so: `--allow-mutation` is required to create anything, a stale sweep removes leftover probe actors
from earlier runs before starting, and cleanup runs in a finally block. Without the flag the probe
measures reads only, and says so.

Exit codes, and each one is a different claim:
  0  every contract check passed AND no engine defect was found (read-only mode normally reports
     this, because a defect in the spawn path can only be found by spawning).
  1  a contract check failed, or --strict was given while engine defects were present.
  2  the bridge is unreachable — UNAVAILABLE. A fact about the editor being closed: not a pass,
     and not a defect in the bridge.
  3  every contract check passed but the engine IS defective, in non-strict mode. It is not 0,
     because a caller reading only `$?` must not be able to take a defect-bearing run as clean.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

HOST = os.environ.get("IRSOFKA_UE_HOST", "127.0.0.1")
PORT = int(os.environ.get("IRSOFKA_UE_PORT", "9877"))
EPSILON = 1e-2          # UE prints %f with six decimals through FString::Printf; 1e-2 is honest
PROBE_PREFIX = "IIRSofkaProbe"
PROBE_LABEL = f"{PROBE_PREFIX}{os.getpid()}"
PROBE_CLASS = "/Script/Engine.StaticMeshActor"
BARE_CLASS = "StaticMeshActor"
# Every spelling the engine's silent fallback can come back as. Comparing against the single
# literal "Actor" would let "/Script/Engine.Actor" or "AActor" hide the defect, and `--strict`
# would then exit 0 with the fallback still happening.
FALLBACK_NAMES = {"actor", "aactor", "/script/engine.actor"}

results: list[dict] = []
defects: list[dict] = []
# Cacat yang sudah diperbaiki dan TERBUKTI diperbaiki di run ini. Dibawah laporan, bukan dibuang:
# tanpa daftar ini, "tidak ada defect" dan "defect-nya sudah tidak ada karena plugin menolak"
# tercetak identik — dan itu persis cara perbaikan dikira keberuntungan antrean kosong.
fixed: list[dict] = []


def send(domain: str, action: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    payload = json.dumps({"domain": domain, "action": action, "params": params or {}}) + "\n"
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0] if buf else b""
    if not line.strip():
        return {"status": "NO_REPLY", "error": "the bridge answered nothing"}
    try:
        return json.loads(line.decode("utf-8", "replace"))
    except ValueError as exc:
        return {"status": "BAD_JSON", "error": f"{exc}: {line[:200]!r}"}


def check(name, cond, detail=""):
    results.append({"check": name, "ok": bool(cond), "detail": str(detail)[:400]})
    return bool(cond)


def triple(v):
    """Exactly three finite numbers, or None. A missing or empty list is NOT a match — `zip`
    against a short list plus `all([])` is how a check passes for no reason at all."""
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return None if any(x != x or x in (float("inf"), float("-inf")) for x in out) else out


def same(a, b) -> bool:
    ta, tb = triple(a), triple(b)
    if ta is None or tb is None:
        return False
    return all(abs(x - y) <= EPSILON for x, y in zip(ta, tb))


def class_is_fallback(name) -> bool:
    return str(name or "").strip().lower() in FALLBACK_NAMES


def classify_bare_spawn(reply: dict, readback_location) -> tuple[str, str]:
    """Apa arti balasan `spawn` dengan NAMA POLOS — cacat, atau perbaikan yang diharapkan?

    Dua-dua masuk akal dan harus dibedakan, karena versi pertama instrumen ini menganggap
    balasan FAILED sebagai kegagalan probe: setelah fallback AActor dihentikan (commit plugin
    a2be9b8), penolakan dengan nama kandidat adalah keadaan BENAR, dan menyebutnya cacat akan
    membuat perbaikan terlihat seperti regresi.

      refused_with_candidates — plugin sudah menolak: tidak ada aktor tanpa RootComponent
      fallback_success        — masih spawn diam-diam menjadi AActor: cacat
      silent_noop             — class terlihat benar tapi lokasi tidak mendarat: cacat lain
      unexpected              — balasan tidak bisa dibaca; dilaporkan, tidak ditebak
    """
    status = str(reply.get("status") or "")
    if status == "FAILED":
        err = str(reply.get("error") or "").lower()
        # Kata yang benar-benar dikeluarkan ResolveActorClass di commit a2be9b8. Probe dulu
        # mengenali hanya "refused"/"could not be loaded" dan menyebut penolakan sah sebagai
        # `unexpected` — instrumen yang salah baca kosakata akan menjadikan perbaikan sebagai
        # kegagalan, dan itu cara paling meyakinkan untuk menghapus sebuah perbaikan.
        markers = ("no loaded aactor class", "no aactor class at", "matches", "refus",
                   "could not be loaded", "empty actor_class", "no actor_class given",
                   "bare aactor")
        cand = reply.get("candidates")
        if isinstance(cand, list) or any(m in err for m in markers):
            return "refused_with_candidates", str(reply.get("error"))[:160]
        return "unexpected", f"FAILED tanpa alasan penolakan kelas: {str(reply)[:160]}"
    if status != "SUCCESS":
        return "unexpected", f"status={status!r} bukan SUCCESS/FAILED: {str(reply)[:120]}"
    if class_is_fallback(reply.get("class")):
        return "fallback_success", (f"spawn kelas={reply.get('class')!r} menjawab SUCCESS "
                                    f"dengan lokasi diminta {reply.get('location')}")
    if readback_location is not None and reply.get("location") and \
            not same(readback_location, reply.get("location")):
        return "silent_noop", (f"class={reply.get('class')!r} tapi readback "
                               f"{readback_location} != laporan spawn {reply.get('location')}")
    return "resolved", f"class={reply.get('class')!r} dan lokasi mendarat"


def classify_set_transform(reply: dict) -> tuple[str, str]:
    """SUCCESS tanpa apa pun yang mendarat adalah cacat yang diukur, bukan gaya balasan.

    Setelah a2be9b8 balasan membawa `landed` + `location_drift` + `has_root_component`. Keberadaan
    kunci itu yang membedakan tulisan yang bisa diaudit dari tulisan yang hanya声称 berhasil —
    dan itu bisa diperiksa bahkan ketika editor tertutup, karena bentuk balasannya kontraknya.
    """
    if str(reply.get("status")) != "SUCCESS":
        return "failed", str(reply.get("error") or "")[:160]
    landed = reply.get("landed")
    if isinstance(landed, dict) and "location_drift" in reply:
        return "landed", (f"landed={str(landed)[:90]} drift={reply.get('location_drift')} "
                          f"root={reply.get('has_root_component')}")
    if any(k in reply for k in ("location", "rotation", "scale", "actor_label")):
        # balasan lama masih menyebut angka; read-back membuktikan sesuatu, hanya tidak dari sini
        return "legacy_shape", f"kunci lama: {sorted(reply)[:6]}"
    return "silent_success", f"kunci balasan={sorted(reply)} — SUCCESS tanpa bukti tulisan"


def actor_count(reply: dict):
    """The integer the editor itself reports, or None if it did not report one."""
    if reply.get("status") != "SUCCESS":
        return None
    if isinstance(reply.get("count"), int):
        return reply["count"]
    actors = reply.get("actors")
    return len(actors) if isinstance(actors, list) else None


def labels_from(reply: dict):
    """Probe-owned labels in a get_all_actors reply — or None if the reply is not one.

    Returning `[]` on a failed read was the reviewer's finding: `[]` satisfies "no leftovers", so a
    socket error looked like a clean level, and the finally-block guard `if lbl in probe_labels()`
    then skipped the delete and left the test actor in the operator's session. Unreadable is now a
    distinct value, and the two callers must handle it rather than trust it.
    """
    if not isinstance(reply, dict) or reply.get("status") != "SUCCESS":
        return None
    actors = reply.get("actors")
    if not isinstance(actors, list):
        return None
    return [str(a.get("label")) for a in actors if isinstance(a, dict)
            and str(a.get("label") or "").startswith(PROBE_PREFIX)]


def probe_labels():
    return labels_from(send("world", "get_all_actors", {}))


def positively_gated(node) -> bool:
    """Is this control expression TRUE exactly when mutate is true?

    Substring matching on "mutate" was the bypass a reviewer named, and they were right twice over:
    `if not mutate:` contains the word and does the opposite of a gate, and so does a ternary whose
    dangerous call sits in the ELSE branch — `x() if mutate else delete()` reads as gated to a
    substring and deletes in read-only mode. So the AST is inspected instead: the name `mutate` must
    be the tested condition itself, or one ANDed operand of it, never a negation and never the
    fall-through side.
    """
    import ast
    if isinstance(node, ast.Name):
        return node.id == "mutate"
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return any(positively_gated(v) for v in node.values)
    if isinstance(node, ast.UnaryOp):          # `not mutate`
        return False
    if isinstance(node, ast.Compare):          # `mutate is False`, `mutate == 0`, ...
        return False
    return False


def _gating_expressions(node, tree) -> list:
    """Every control predicate that encloses `node`, innermost first.

    For a ternary the guard is its .test, and the branch that actually runs is only the body when the
    call sits inside it — that distinction is what the substring version got wrong.
    """
    import ast
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    out = []
    cur = node
    while cur is not None:
        par = parents.get(cur)
        if par is None:
            break
        # THE BRANCH IS THE WHOLE POINT. A reviewer found that reading `If.test` without asking
        # which side the call sits on made `if mutate: pass / else: delete()` look gated: the test
        # mentions mutate, the delete does not run because of it, and the next line of this file
        # would have trusted that. So a gate only counts when the call is on the side the test
        # selects — body for If and IfExp, iter-and-body for a For.
        if isinstance(par, ast.If):
            if cur in par.body:
                out.append(par.test)
        elif isinstance(par, ast.IfExp):
            if cur is par.body:
                out.append(par.test)
        elif isinstance(par, ast.For):
            if cur in par.body:
                it = par.iter
                if isinstance(it, ast.IfExp):
                    out.append(it.test)
                elif isinstance(it, ast.Name) and it.id == "mutate":
                    out.append(it)
        cur = par
    return out


def delete_sites_are_guarded(src: str) -> tuple[bool, list]:
    """Every actor delete in this file must sit inside the loop over `created`.

    The reviewer's finding was behavioural, not textual: the finally block used to delete two
    guessed labels unconditionally, so the read-only mode that the autopilot gate runs sent real
    mutation calls into the operator's live level. A test that only checks the happy path would
    not see that; this walks the AST and asks where each delete actually lives.
    """
    import ast
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = node.func.id if isinstance(node.func, ast.Name) else ""
        if fname not in ("timed", "send"):
            continue
        # Any literal "delete" anywhere in the call counts as a delete site. Positional-only
        # inspection was the bypass a reviewer named: `send(domain="world", action="delete")` and a
        # helper that forwards the verb both hide from an args[1] check, and the point of this
        # function is to catch the case nobody thought to write positionally.
        literals = [n.value for n in ast.walk(node) if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)]
        if "delete" not in literals:
            continue
        # A delete is legal only where the control expression itself decides on `mutate`: an `if
        # mutate` test, a loop iterable gated on mutate, or the mutate branch of a ternary. Checking
        # for one loop shape was both too weak and too brittle -- it passed a sweep that mutated in
        # read-only mode and failed the signal handler's cleanup, which is the opposite of a guard.
        if not any(positively_gated(expr) for expr in _gating_expressions(node, tree)):
            bad.append(f"line {node.lineno}: delete with no positive `mutate` gate")
    return (not bad), bad


def logic_selftest() -> int:
    """Pure-logic checks, no editor needed. Every case here is a way a check used to pass for the
    wrong reason: `all([])` on a short zip, `None or True`, and a literal comparison that a
    different spelling of the same defect slips past."""
    out = []

    def expect(name, got, want):
        out.append((name, got, want, got == want))

    expect("same([], [1,2,3]) is False, not all([])==True", same([], [1, 2, 3]), False)
    expect("same(None, [1,2,3]) is False", same(None, [1, 2, 3]), False)
    expect("same([1,2], [1,2,3]) is False (short list must not pass)", same([1, 2], [1, 2, 3]), False)
    expect("same([1,2,'x'], [1,2,3]) is False", same([1, 2, "x"], [1, 2, 3]), False)
    expect("same([1,2,float('nan')], [1,2,3]) is False",
           same([1, 2, float("nan")], [1, 2, 3]), False)
    expect("same([1,2,3],[1,2,3]) is True", same([1, 2, 3], [1, 2, 3]), True)
    expect("same([1,2,3],[1,2,3.005]) is True within 1e-2", same([1, 2, 3], [1, 2, 3.005]), True)
    expect("triple('abc') is None", triple("abc"), None)
    expect("triple([1,2,3]) returns floats", triple([1, 2, 3]), [1.0, 2.0, 3.0])
    expect("class_is_fallback('Actor')", class_is_fallback("Actor"), True)
    expect("class_is_fallback('AActor')", class_is_fallback("AActor"), True)
    expect("class_is_fallback('/Script/Engine.Actor')",
           class_is_fallback("/Script/Engine.Actor"), True)
    expect("class_is_fallback('StaticMeshActor') is False",
           class_is_fallback("StaticMeshActor"), False)
    expect("class_is_fallback(None) is False", class_is_fallback(None), False)
    expect("actor_count of a failed reply is None", actor_count({"status": "FAILED"}), None)
    expect("actor_count reads the editor's own count",
           actor_count({"status": "SUCCESS", "count": 9, "actors": []}), 9)
    expect("actor_count falls back to len(actors)",
           actor_count({"status": "SUCCESS", "actors": [{"label": "A"}]}), 1)
    expect("actor_count refuses a shape it cannot read",
           actor_count({"status": "SUCCESS", "actors": "not-a-list"}), None)
    # The finding-1 pair: an unreadable reply must not read as "nothing left behind".
    expect("labels_of a failed read is None, not []", labels_from({"status": "FAILED"}), None)
    expect("labels_of NO_REPLY is None", labels_from({"status": "NO_REPLY"}), None)
    expect("labels_of a non-list actors is None",
           labels_from({"status": "SUCCESS", "actors": "oops"}), None)
    expect("labels_of an empty level is []",
           labels_from({"status": "SUCCESS", "count": 0, "actors": []}), [])
    expect("labels_of keeps only probe-prefixed labels",
           labels_from({"status": "SUCCESS", "actors": [{"label": "Cube"},
                                                        {"label": PROBE_PREFIX + "1"}]}),
           [PROBE_PREFIX + "1"])
    expect("None is not equal to [] (the guard that skipped cleanup)",
           labels_from({"status": "FAILED"}) == [], False)

    import ast as _ast

    def _gate(src_fragment: str, position: int = 0):
        """Build the real AST for `if <fragment>: delete()` and hand back the enclosing test."""
        tree = _ast.parse(f"if {src_fragment}:\n    delete()\n")
        return tree.body[0].test

    def _gates_for_call(src: str) -> list:
        """Parse real code, find the `delete()` call, and return the gates that actually cover it."""
        tree = _ast.parse(src)
        call = next(n for n in _ast.walk(tree)
                    if isinstance(n, _ast.Call) and getattr(n.func, "id", "") == "delete")
        return _gating_expressions(call, tree)

    def _all_gated(src: str) -> bool:
        gs = _gates_for_call(src)
        return bool(gs) and all(positively_gated(g) for g in gs)

    expect("branch: `if mutate: delete()` is gated",
           _all_gated("if mutate:\n    delete()\n"), True)
    expect("branch: `if mutate: pass else: delete()` is NOT gated -- the finding the last version faked",
           _all_gated("if mutate:\n    pass\nelse:\n    delete()\n"), False)
    expect("branch: `x = skip() if mutate else delete()` is NOT gated (call sits in orelse)",
           _all_gated("x = (skip() if mutate else delete())\n"), False)
    expect("branch: `x = delete() if mutate else skip()` IS gated",
           _all_gated("x = (delete() if mutate else skip())\n"), True)
    expect("branch: `for lbl in (a if mutate else []): delete()` is gated by the ternary test",
           _all_gated("for lbl in (created if mutate else []):\n    delete()\n"), True)
    expect("gate: bare `mutate`", positively_gated(_gate("mutate")), True)
    expect("gate: `mutate and created`", positively_gated(_gate("mutate and created")), True)
    expect("gate: `not mutate` is REJECTED (the bypass a reviewer named)",
           positively_gated(_gate("not mutate")), False)
    expect("gate: `mutate is False` is REJECTED",
           positively_gated(_gate("mutate is False")), False)
    expect("gate: `other_flag` is REJECTED", positively_gated(_gate("other_flag")), False)
    expect("gate: For over `created if mutate else []` covers the delete inside it",
           _all_gated("for lbl in (created if mutate else []):\n    delete()\n"), True)
    expect("gate: `not mutate` as a test is rejected on its own too",
           positively_gated(_gate("not mutate")), False)

    src = Path(__file__).read_text(encoding="utf-8")
    guarded, offenders = delete_sites_are_guarded(src)
    expect("setiap delete dikendalikan ekspresi yang menyebut `mutate` (baca-saja tidak menyentuh editor)",
           (guarded, offenders), (True, []))

    # --- classifier: "ini cacat" vs "ini sudah diperbaiki", diukur tanpa editor.
    # Memakai expect(), bukan check() modul: check() mengumpulkan ke `results` milik jalur HIDUP,
    # jadi kalau dipakai di sini kasusnya berjalan tanpa pernah masuk hitungan dan bisa lulus
    # tanpa ada yang membacanya — bentuk lulus-karena-salah-tempat yang persis ingin dicegah file ini.
    expect("spawn nama polos yang DITOLAK dengan sebab = refused_with_candidates, bukan kegagalan",
           classify_bare_spawn({"status": "FAILED",
                                "error": "no loaded AActor class named 'StaticMeshActor' "
                                         "(tried /Script/Engine.StaticMeshActor)"}, None)[0],
           "refused_with_candidates")
    expect("FAILED tanpa alasan penolakan = unexpected, tidak boleh dianggap penolakan yang sah",
           classify_bare_spawn({"status": "FAILED", "error": "boom"}, None)[0], "unexpected")
    expect("spawn yang diam-diam jadi AActor tetap tertangkap sebagai fallback_success",
           classify_bare_spawn({"status": "SUCCESS", "class": "AActor", "location": [1, 2, 3]},
                               [0.0, 0.0, 0.0])[0], "fallback_success")
    expect("kelas benar dan lokasi mendarat = resolved",
           classify_bare_spawn({"status": "SUCCESS", "class": "StaticMeshActor",
                                "location": [10, 20, 30]}, [10.0, 20.0, 30.0])[0], "resolved")
    expect("kelas benar tapi lokasi tidak mendarat = silent_noop, bukan lolos",
           classify_bare_spawn({"status": "SUCCESS", "class": "StaticMeshActor",
                                "location": [10, 20, 30]}, [0.0, 0.0, 0.0])[0], "silent_noop")
    expect("FAILED karena hal lain (misalnya world hilang) TIDAK dihitung sebagai penolakan kelas",
           classify_bare_spawn({"status": "FAILED", "error": "No active editor world"}, None)[0],
           "unexpected")
    expect("balasan yang bukan SUCCESS/FAILED dilaporkan sebagai unexpected",
           classify_bare_spawn({"status": "WEIRD"}, None)[0], "unexpected")
    expect("set_transform SUCCESS tanpa isi = silent_success (cacat yang dulu ada)",
           classify_set_transform({"status": "SUCCESS"})[0], "silent_success")
    expect("set_transform yang membalas apa yang mendarat = landed",
           classify_set_transform({"status": "SUCCESS", "landed": {"location": [1, 2, 3]},
                                   "location_drift": 0.0, "has_root_component": True})[0], "landed")
    expect("bentuk lama yang masih menyebut angka TIDAK dikira perbaikan",
           classify_set_transform({"status": "SUCCESS", "location": [1, 2, 3]})[0], "legacy_shape")
    expect("penolakan informatif = failed, dan alasannya terbawa ke laporan",
           classify_set_transform({"status": "FAILED", "error": "no RootComponent"}),
           ("failed", "no RootComponent"))

    bad = [o for o in out if not o[3]]
    for name, got, want, okk in out:
        print(f"{'[ok    ]' if okk else '[GAGAL ]'} {name}: got={got!r} want={want!r}")
    print(f"\nue_bridge_probe logic selftest: {len(out) - len(bad)}/{len(out)} sesuai")
    return 0 if not bad else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return logic_selftest()
    started = time.time()
    try:
        socket.create_connection((HOST, PORT), timeout=3.0).close()
    except OSError as exc:
        print(json.dumps({"state": "UNAVAILABLE", "endpoint": f"{HOST}:{PORT}",
                          "error": f"{type(exc).__name__}: {exc}",
                          "meaning": "the editor's bridge is not listening; this is not a pass "
                                     "and not a defect in the bridge"}, indent=2))
        return 2

    mutate = "--allow-mutation" in sys.argv
    timings: dict[str, float] = {}
    created: list[str] = []

    def cleanup_best_effort():
        """Runs from a signal handler too. A SIGTERM does not execute `finally`, and a probe
        killed by `timeout 180` would otherwise leave its actor in the operator's level until
        the next run's sweep found it."""
        # The gate is on the loop itself, not on an early return above it: an early return is a
        # sibling statement, so a check that walks enclosing expressions cannot see it, and a rule
        # that cannot see its own exception is not a rule. Same behaviour, visible guard.
        for lbl in (dict.fromkeys(created) if mutate else []):
            try:
                send("world", "delete", {"actor_label": lbl}, timeout=5.0)
            except Exception:  # noqa: BLE001 — a dying process cannot report, only try
                pass

    import signal
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            def _on_signal(sig, _frame, _s=cleanup_best_effort):
                _s()
                # Re-raise with the default handler so the process still dies of the signal:
                # 143 under SIGTERM, 130 under SIGINT. Exiting 1 here would tell a supervisor
                # that a CONTRACT check failed when in fact the run was interrupted -- which is
                # exactly the misreading the rest of this file is about avoiding.
                signal.signal(sig, signal.SIG_DFL)
                os.kill(os.getpid(), sig)
            signal.signal(_sig, _on_signal)
        except (ValueError, OSError):
            pass  # not the main thread on this platform; the finally block still runs

    def timed(action, params, timeout=15.0):
        t0 = time.time()
        out = send("world", action, params, timeout=timeout)
        timings[action] = round((time.time() - t0) * 1000, 1)
        return out

    # 1. Is the editor reachable, in its own words?
    actors = timed("get_all_actors", {})
    n_before = actor_count(actors)
    check("get_all_actors menjawab SUCCESS", actors.get("status") == "SUCCESS", actors)
    check("get_all_actors mengembalikan count yang bisa dibaca", n_before is not None, actors)

    # 2. Sweep leftovers from earlier runs before measuring anything, so a previous crash cannot
    #    quietly become this run's baseline. An unreadable list is reported and does NOT pass.
    stale = probe_labels()
    if stale is None:
        check("sisa probe dapat dibaca sebelum mengukur", False,
              "get_all_actors tidak mengembalikan daftar actor; sweep tidak bisa membuktikan apa pun")
    else:
        if stale:
            # A leftover from an earlier run is a fact to report, not something read-only mode may
            # fix: deleting it is a mutation, and the mode the autopilot gate runs promises none.
            results.append({"check": "sisa probe run sebelumnya terlihat (read-only: dilaporkan, "
                                      "tidak dihapus)", "ok": True, "detail": str(stale)[:200]})
        for lbl in (stale if mutate else []):
            timed("delete", {"actor_label": lbl})
        left = probe_labels()
        if mutate:
            check("sisa probe dari run sebelumnya dibersihkan (atau memang tidak ada)",
                  left == [], f"sweep={stale} left={left}")
        else:
            # Read-only cannot clean, so it must not be scored for not cleaning: that check used to
            # fail the run whenever an older crashed run left something behind, which made the safe
            # mode the fragile one. It reports, and the actor is still counted in the final state.
            results.append({"check": "sisa probe run sebelumnya dilaporkan (read-only tidak menghapus)",
                            "ok": left is not None, "detail": f"stale={stale} left={left}"})

    n_swept = actor_count(timed("get_all_actors", {}))

    try:
        if mutate:
            # 3. PROOF OF CLASS: spawn a real class at a known location and read the numbers back.
            spawn_loc = [111.0, 222.0, 333.0]
            # Requested first, recorded second: if the spawn call itself dies mid-flight, the
            # label is already known and the finally block still tries the delete.
            created.append(PROBE_LABEL)
            sp = timed("spawn", {"actor_class": PROBE_CLASS, "location": spawn_loc,
                                 "rotation": [0, 0, 0], "actor_label": PROBE_LABEL})
            check("spawn dengan class path menjawab SUCCESS", sp.get("status") == "SUCCESS", sp)
            label = sp.get("actor_label") or PROBE_LABEL
            if label != PROBE_LABEL:
                created.append(label)   # the engine renamed it; that name must go too
            check("spawn mengembalikan actor_label yang bisa dibaca kembali",
                  bool(sp.get("actor_label")), sp)
            check("spawn melaporkan class yang diminta",
                  str(sp.get("class") or "").lower() == "staticmeshactor", sp)
            check("spawn melaporkan location == yang diminta", same(sp.get("location"), spawn_loc),
                  sp)

            gt = timed("get_transform", {"actor_label": label})
            check("get_transform menjawab SUCCESS untuk label yang baru di-spawn",
                  gt.get("status") == "SUCCESS", gt)
            check("get_transform mengembalikan lokasi yang benar-benar mendarat (tiga angka, "
                  "bukan list kosong)", same(gt.get("location"), spawn_loc),
                  f"spawn={spawn_loc} read={gt.get('location')}")

            new_loc = [-45.5, 67.25, -8.125]
            new_rot = [10.0, 20.0, 30.0]
            new_scale = [2.0, 1.5, 0.5]
            st = timed("set_transform", {"actor_label": label, "location": new_loc,
                                         "rotation": new_rot, "scale": new_scale})
            check("set_transform menjawab SUCCESS", st.get("status") == "SUCCESS", st)
            gt2 = timed("get_transform", {"actor_label": label})
            check("lokasi benar-benar berpindah: get setelah set == yang ditulis",
                  same(gt2.get("location"), new_loc), f"wrote={new_loc} read={gt2.get('location')}")
            check("rotasi benar-benar berpindah", same(gt2.get("rotation"), new_rot),
                  f"wrote={new_rot} read={gt2.get('rotation')}")
            check("skala benar-benar berubah", same(gt2.get("scale"), new_scale),
                  f"wrote={new_scale} read={gt2.get('scale')}")

            # 4. The measured defect: a bare class name falls back to a body without a root
            #    component, and every transform after that is a silent no-op.
            bare_lbl = PROBE_LABEL + "B"
            created.append(bare_lbl)
            bare = timed("spawn", {"actor_class": BARE_CLASS, "location": [10.0, 20.0, 30.0],
                                   "actor_label": bare_lbl})
            bare_gt = timed("get_transform", {"actor_label": bare_lbl})
            timed("set_transform", {"actor_label": bare_lbl, "location": [70.0, 80.0, 90.0]})
            bare_gt2 = timed("get_transform", {"actor_label": bare_lbl})
            timed("delete", {"actor_label": bare_lbl})
            bare_kind, bare_detail = classify_bare_spawn(bare, bare_gt.get("location"))
            if bare_kind == "refused_with_candidates":
                fixed.append({"id": "silent-AActor-fallback",
                              "measured": f"spawn kelas polos {BARE_CLASS!r} DITOLAK dengan nama: "
                                          f"{bare_detail}"})
                check("nama polos yang ditolak TIDAK menciptakan aktor (tidak ada yang tersisa)",
                      str(bare_gt.get("status")) == "FAILED", bare_gt)
            if bare_kind == "fallback_success":
                defects.append({
                    "id": "silent-AActor-fallback",
                    "measured": f"spawn class={BARE_CLASS!r} menjawab SUCCESS dengan class="
                                f"{bare.get('class')!r}; lokasi dilaporkan [10,20,30] tetapi "
                                f"get_transform membaca {bare_gt.get('location')}; setelah "
                                f"set_transform SUCCESS masih membaca {bare_gt2.get('location')}",
                    "cause": "LoadClass wants '/Script/Engine.StaticMeshActor'; a bare name yields "
                             "null and `if (!LoadedClass) LoadedClass = AActor::StaticClass()` "
                             "spawns a body with no RootComponent, so every transform is a no-op",
                    "why_it_matters": "an engine answers SUCCESS about an object that cannot be "
                                      "moved, and the spawn reply echoes the REQUESTED location "
                                      "rather than the applied one"})
            if bare_kind in ("resolved", "silent_noop"):
                check("spawn dengan nama polos yang RESOLVED harus membuktikan lokasi mendarat",
                      same(bare_gt.get("location"), [10.0, 20.0, 30.0]),
                      f"kind={bare_kind} class={bare.get('class')} read={bare_gt.get('location')}")
            elif bare_kind == "unexpected":
                check("balasan spawn nama polos bisa dibaca probe", False, bare_detail)

            # 4b. The second measured defect: a write that reports nothing. The engine's own
            #     ActionSetTransform returns the literal {"status":"SUCCESS"}, so a caller cannot
            #     audit a write from its reply and must issue a second read to learn anything.
            st_kind, st_detail = classify_set_transform(st)
            if st_kind in ("silent_success", "legacy_shape"):
                defects.append({
                    "id": "set-transform-returns-nothing",
                    "measured": f"set_transform reply keys={sorted(st)} — hanya status; readback "
                                f"menyusul: location={gt2.get('location')} "
                                f"rotation={gt2.get('rotation')} scale={gt2.get('scale')}",
                    "cause": 'ActionSetTransform returns the literal '
                             'TEXT("{\\"status\\":\\"SUCCESS\\"}") and never calls Modify() or a '
                             'transaction, so the level may not be marked dirty by the write',
                    "why_it_matters": "a write that reports nothing cannot be audited from its "
                                      "reply, which is exactly what an unattended run needs"})
        else:
            results.append({"check": "mode baca-saja: tidak ada actor yang dibuat",
                            "ok": True, "detail": "lewatkan spawn; pakai --allow-mutation untuk "
                                                  "membuktikan kelas spawn dan round-trip transform"})

        # 5. The negative path is a fact, not a crash: an unknown label.
        missing = timed("get_transform", {"actor_label": "NoSuchActor_zzz"})
        check("label yang tidak ada dijawab FAILED dengan error yang menyebut labelnya",
              missing.get("status") == "FAILED" and "NoSuchActor_zzz" in json.dumps(missing),
              missing)
    finally:
        # 6. Delete what this run may have created, unconditionally. The guard used to be
        #    `if lbl in probe_labels()`, and probe_labels() returns None when the editor did not
        #    answer -- so a read failure silently skipped the cleanup and left the test actor in
        #    the operator's level. A delete of something that is not there costs one FAILED reply;
        #    skipping a delete of something that IS there costs the operator their session state.
        #    The list is every label this run actually asked for OR the editor answered back: if
        #    the engine renames on spawn, the returned label is what must be deleted, not the one
        #    we requested. Duplicates cost one extra FAILED reply each.
        if mutate:
            for lbl in dict.fromkeys(created):
                timed("delete", {"actor_label": lbl})

    # 7. No leftovers, measured as a number the editor itself reports. `n_before is None or ...`
    #    would have called a failed read a success, so both ends must be real and equal.
    n_after = actor_count(timed("get_all_actors", {}))
    if mutate:
        check("jumlah actor kembali persis seperti sebelum probe (angka editor, bukan len(None))",
              n_swept is not None and n_after is not None and n_after == n_swept,
              f"baseline={n_swept} after={n_after}")
    leftover = probe_labels()
    if leftover is None:
        check("tidak ada actor probe yang tertinggal di level", False,
              "editor tidak menjawab daftar actor; 'bersih' tidak bisa disimpulkan dari kegagalan baca")
    elif mutate:
        # This run created actors, so this run must leave none behind.
        check("run ini tidak meninggalkan actor probe sama sekali", leftover == [],
              f"leftover={leftover}")
    else:
        # Read-only created nothing, so the honest claim is narrower: whatever probe actors exist
        # were already here. Asserting [] instead would fail a safe run for an earlier crashed one's
        # debris -- which is exactly how the safe mode becomes the fragile mode.
        newones = sorted(set(leftover) - set(stale or []))
        check("mode baca-saja tidak menambah actor probe apa pun", newones == [],
              f"pre-existing={stale} now={leftover} new={newones}")

    strict = "--strict" in sys.argv
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    fail = passed < total or (strict and bool(defects))
    # A green default with defects listed is not a clean engine, so it is not called "OK". Finding 4
    # of the correction round: `state:"OK"` over two measured bridge defects let a reader take the
    # gate's zero as a verdict about the bridge. DEFECTS_PRESENT exits 0 (the contract checks all
    # passed) but is never readable as clean; --strict makes it fail instead.
    clean = passed == total
    state = ("FAILED" if not clean else
             ("DEFECTS_PRESENT" if defects else "CLEAN"))
    print(json.dumps({"state": state,
                      "endpoint": f"{HOST}:{PORT}",
                      "checks": f"{passed}/{total}",
                      "mutation": mutate,
                      "strict": strict,
                      "exit_meaning": "0 = all checks passed AND no defect; 1 = a check failed or "
                                      "--strict with defects; 2 = bridge unreachable; 3 = all "
                                      "checks passed but the engine IS defective (see "
                                      "engine_defects)",
                      "elapsed_ms": int((time.time() - started) * 1000),
                      "latency_ms_by_action": timings,
                      "probe_class": PROBE_CLASS, "probe_label": PROBE_LABEL,
                      "level_saved": False,
                      "level_dirtied_by_mutation": mutate,
                      "engine_defects": defects,
                      "engine_defects_fixed_observed": fixed,
                      "failures": [r for r in results if not r["ok"]]},
                     indent=2, ensure_ascii=False))
    if fail:
        return 1
    # 3, not 0: every contract check passed but the engine is defective. A shell caller reading
    # only $? must not be able to take a defect-bearing run as a pass. (This line used to say
    # "DEFECTS_PRESENT exits 0", which contradicted the code below it and the docstring above.)
    return 3 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
