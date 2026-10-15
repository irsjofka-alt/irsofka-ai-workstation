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

Exit codes: 0 all checks passed (defects reported, not fatal), 1 a contract check failed, or
`--strict` found engine defects, 2 the bridge is unreachable — UNAVAILABLE, which is a fact about
the editor being closed, not a pass and not a defect in the bridge.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time

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


def actor_count(reply: dict):
    """The integer the editor itself reports, or None if it did not report one."""
    if reply.get("status") != "SUCCESS":
        return None
    if isinstance(reply.get("count"), int):
        return reply["count"]
    actors = reply.get("actors")
    return len(actors) if isinstance(actors, list) else None


def probe_labels() -> list[str]:
    r = send("world", "get_all_actors", {})
    actors = r.get("actors")
    if not isinstance(actors, list):
        return []
    return [str(a.get("label")) for a in actors if isinstance(a, dict)
            and str(a.get("label") or "").startswith(PROBE_PREFIX)]


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
    #    quietly become this run's baseline.
    stale = probe_labels()
    for lbl in stale:
        timed("delete", {"actor_label": lbl})
    check("sisa probe dari run sebelumnya dibersihkan (atau memang tidak ada)",
          len(probe_labels()) == 0, f"sweep={stale} left={probe_labels()}")

    n_swept = actor_count(timed("get_all_actors", {}))

    try:
        if mutate:
            # 3. PROOF OF CLASS: spawn a real class at a known location and read the numbers back.
            spawn_loc = [111.0, 222.0, 333.0]
            sp = timed("spawn", {"actor_class": PROBE_CLASS, "location": spawn_loc,
                                 "rotation": [0, 0, 0], "actor_label": PROBE_LABEL})
            check("spawn dengan class path menjawab SUCCESS", sp.get("status") == "SUCCESS", sp)
            label = sp.get("actor_label") or PROBE_LABEL
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
            bare = timed("spawn", {"actor_class": BARE_CLASS, "location": [10.0, 20.0, 30.0],
                                   "actor_label": bare_lbl})
            bare_gt = timed("get_transform", {"actor_label": bare_lbl})
            timed("set_transform", {"actor_label": bare_lbl, "location": [70.0, 80.0, 90.0]})
            bare_gt2 = timed("get_transform", {"actor_label": bare_lbl})
            timed("delete", {"actor_label": bare_lbl})
            if bare.get("status") == "SUCCESS" and class_is_fallback(bare.get("class")):
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
            if not class_is_fallback(bare.get("class")) and bare.get("status") == "SUCCESS":
                check("spawn dengan nama polos tetap harus membuktikan class yang mendarat",
                      same(bare_gt.get("location"), [10.0, 20.0, 30.0]),
                      f"class={bare.get('class')} read={bare_gt.get('location')}")

            # 4b. The second measured defect: a write that reports nothing. The engine's own
            #     ActionSetTransform returns the literal {"status":"SUCCESS"}, so a caller cannot
            #     audit a write from its reply and must issue a second read to learn anything.
            if st.get("status") == "SUCCESS" and not any(
                    k in st for k in ("location", "rotation", "scale", "actor_label")):
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
        # 6. Clean up whatever this process created, including on the paths that raised.
        if mutate:
            for lbl in [PROBE_LABEL, PROBE_LABEL + "B"]:
                if lbl in probe_labels():
                    timed("delete", {"actor_label": lbl})

    # 7. No leftovers, measured as a number the editor itself reports. `n_before is None or ...`
    #    would have called a failed read a success, so both ends must be real and equal.
    n_after = actor_count(timed("get_all_actors", {}))
    if mutate:
        check("jumlah actor kembali persis seperti sebelum probe (angka editor, bukan len(None))",
              n_swept is not None and n_after is not None and n_after == n_swept,
              f"baseline={n_swept} after={n_after}")
    check("tidak ada actor probe yang tertinggal di level", probe_labels() == [],
          probe_labels())

    strict = "--strict" in sys.argv
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    fail = passed < total or (strict and bool(defects))
    print(json.dumps({"state": "FAILED" if fail else "OK",
                      "endpoint": f"{HOST}:{PORT}",
                      "checks": f"{passed}/{total}",
                      "mutation": mutate,
                      "strict": strict,
                      "elapsed_ms": int((time.time() - started) * 1000),
                      "latency_ms_by_action": timings,
                      "probe_class": PROBE_CLASS, "probe_label": PROBE_LABEL,
                      "level_saved": False,
                      "level_dirtied_by_mutation": mutate,
                      "engine_defects": defects,
                      "failures": [r for r in results if not r["ok"]]},
                     indent=2, ensure_ascii=False))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
