#!/usr/bin/env python3
"""Round-trip probe for the IRSofka Station bridge — the UE editor's own TCP socket.

Framing was READ from the plugin source, not guessed:
  IRSofkaDispatcher/Private/IRSofkaSocketServer.cpp
    - binds 127.0.0.1:9877 (`FIPv4Endpoint(FIPv4Address(127,0,0,1), ServerPort)`, line ~30)
    - one request per connection, terminated by '\\n' (`if (ReceivedData.Last() == '\\n') break`)
    - reply is one JSON object with '\\n' appended (`ResponseString.AppendChar('\\n')`)
    - request key `domain` is matched lowercased against registered handlers
    - handlers run synchronously on the GameThread via `FIRSofkaTaskRunner::ExecuteSync`
  IRSofkaWorld/Private/IRSofkaWorldSubsystem.cpp
    - status vocabulary is "SUCCESS" / "FAILED" with an `error` string
    - spawn answers {actor_label, class, location}; get_transform answers
      {location, rotation, scale}; set_transform answers {status} and NOTHING else.

Why this file exists: item 297 says write the probe first, then fix `set_transform`/
`get_transform` and prove the `spawn` class. A fix proposed without a measurement is a guess
about someone else's editor, so the probe answers the question "what actually happens on a round
trip" before any change is made. It is read-mostly and self-cleaning: it spawns one actor, moves
it, reads it back, deletes it, and never saves the level.

What it measured on 2026-10-15, on a live editor (so the next reader starts from facts):
the round trip is CORRECT for a real class path — spawn `/Script/Engine.StaticMeshActor` reports
`class=StaticMeshActor`, and set_transform's location, rotation and scale all read back exactly.
The pair item 297 named is therefore not broken in itself; what was broken is the class argument.
A bare name ("StaticMeshActor") makes `LoadClass` return null, the handler falls back to
`AActor::StaticClass()`, and a bare AActor has no RootComponent — every transform becomes a
silent no-op while all three calls answer SUCCESS. That, plus `set_transform` replying with
nothing but a status, is reported under `engine_defects`; `--strict` makes either one a failure.

Exit codes: 0 all checks passed (defects reported, not fatal), 1 a contract check failed or
`--strict` found engine defects, 2 the bridge is unreachable (UNAVAILABLE — which is a fact about
the editor being closed, not a pass and not a defect in the bridge).
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
PROBE_LABEL = f"IIRSofkaProbe{os.getpid()}"
# The full class path, not the bare name. Measured 2026-10-15: `LoadClass<AActor>(nullptr,
# "StaticMeshActor")` returns null because UE wants "/Script/Engine.StaticMeshActor", and the
# handler then falls back to `AActor::StaticClass()` — a bare AActor has NO RootComponent, so
# SetActorLocation/SetActorRotation/SetActorScale3D all silently do nothing while every call
# answers SUCCESS. The bare-name case is still exercised below, as a measured defect report, so
# the fallback cannot hide behind this constant.
PROBE_CLASS = "/Script/Engine.StaticMeshActor"
BARE_CLASS = "StaticMeshActor"

results: list[dict] = []


def send(domain: str, action: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    """One command, one line, one connection. Connect-per-call matches the server's own model:
    `HandleClient` reads until a newline, answers once, and does not loop."""
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
        return {"status": "NO_REPLY", "error": "the bridge accepted the connection and answered nothing"}
    try:
        return json.loads(line.decode("utf-8", "replace"))
    except ValueError as exc:
        return {"status": "BAD_JSON", "error": f"{exc}: {line[:200]!r}"}


def check(name, cond, detail=""):
    results.append({"check": name, "ok": bool(cond), "detail": str(detail)[:400]})
    return bool(cond)


def close_enough(a, b) -> bool:
    try:
        return all(abs(float(x) - float(y)) <= EPSILON for x, y in zip(a, b))
    except (TypeError, ValueError):
        return False


def main() -> int:
    started = time.time()
    try:
        socket.create_connection((HOST, PORT), timeout=3.0).close()
    except OSError as exc:
        print(json.dumps({"state": "UNAVAILABLE", "endpoint": f"{HOST}:{PORT}",
                          "error": f"{type(exc).__name__}: {exc}",
                          "meaning": "the editor's bridge is not listening; this is not a pass "
                                     "and not a defect in the bridge — open UE or it stays UNAVAILABLE"},
                         indent=2))
        return 2

    timings: dict[str, float] = {}

    def timed(action, params, timeout=15.0):
        t0 = time.time()
        out = send("world", action, params, timeout=timeout)
        timings[action] = round((time.time() - t0) * 1000, 1)
        return out

    # 1. Can the editor be reached at all, in its own words?
    actors = timed("get_all_actors", {})
    n_before = len(actors.get("actors") or []) if isinstance(actors.get("actors"), list) else None
    check("get_all_actors menjawab SUCCESS", actors.get("status") == "SUCCESS", actors)

    # 2. PROOF OF CLASS: spawn a named class at a known location.
    spawn_loc = [111.0, 222.0, 333.0]
    sp = timed("spawn", {"actor_class": PROBE_CLASS, "location": spawn_loc,
                         "rotation": [0, 0, 0], "actor_label": PROBE_LABEL})
    check(f"spawn {PROBE_CLASS} menjawab SUCCESS", sp.get("status") == "SUCCESS", sp)
    label = sp.get("actor_label") or PROBE_LABEL
    check("spawn mengembalikan actor_label yang bisa dibaca kembali", bool(sp.get("actor_label")), sp)
    check("spawn melaporkan class yang diminta",
          str(sp.get("class") or "").lower() == "staticmeshactor", sp)
    check("spawn melaporkan location == yang diminta",
          close_enough(sp.get("location") or [], spawn_loc), sp)

    # 3. get_transform round-trip: the location the editor reports must be the one it was given.
    gt = timed("get_transform", {"actor_label": label})
    check("get_transform menjawab SUCCESS untuk label yang baru di-spawn",
          gt.get("status") == "SUCCESS", gt)
    check("get_transform mengembalikan location == location spawn (round-trip angka, bukan status)",
          close_enough(gt.get("location") or [], spawn_loc),
          f"spawn={spawn_loc} read={gt.get('location')}")

    # 4. set_transform then read back. This is the pair item 297 names.
    new_loc = [-45.5, 67.25, -8.125]
    new_rot = [10.0, 20.0, 30.0]
    new_scale = [2.0, 1.5, 0.5]
    st = timed("set_transform", {"actor_label": label, "location": new_loc,
                                 "rotation": new_rot, "scale": new_scale})
    check("set_transform menjawab SUCCESS", st.get("status") == "SUCCESS", st)
    gt2 = timed("get_transform", {"actor_label": label})
    check("lokasi benar-benar berpindah: get setelah set == yang ditulis",
          close_enough(gt2.get("location") or [], new_loc),
          f"wrote={new_loc} read={gt2.get('location')}")
    check("rotasi benar-benar berpindah", close_enough(gt2.get("rotation") or [], new_rot),
          f"wrote={new_rot} read={gt2.get('rotation')}")
    check("skala benar-benar berubah", close_enough(gt2.get("scale") or [], new_scale),
          f"wrote={new_scale} read={gt2.get('scale')}")

    # 5. The negative path must be a fact, not a crash: unknown label.
    missing = timed("get_transform", {"actor_label": "NoSuchActor_zzz"})
    check("label yang tidak ada dijawab FAILED dengan error yang menyebut labelnya",
          missing.get("status") == "FAILED" and "NoSuchActor_zzz" in json.dumps(missing), missing)

    # 6. Clean up: the probe never leaves a body, and never saves the level.
    dl = timed("delete", {"actor_label": label})
    check("probe menghapus actor yang ia buat sendiri", dl.get("status") == "SUCCESS", dl)
    after = timed("get_transform", {"actor_label": label})
    check("setelah delete, get_transform menjawab FAILED (actor sungguh hilang)",
          after.get("status") == "FAILED", after)
    actors_after = timed("get_all_actors", {})
    n_after = len(actors_after.get("actors") or []) if isinstance(actors_after.get("actors"), list) else None
    check("jumlah actor kembali seperti sebelum probe (tidak ada sisa)",
          n_before is None or n_after is None or n_after == n_before,
          f"before={n_before} after={n_after}")

    # 7. MEASURED ENGINE DEFECTS, reported rather than folded into the pass count.
    #    These are gaps in the operator's UE plugin, not violations of what the bridge claims to
    #    do, so lowering a contract check to hide them would be the wrong move. They are measured
    #    here so the number exists, and `--strict` turns each one into a failure when someone is
    #    ready to be held to it (which needs a C++ rebuild of IRSofkaStationCore, and that needs
    #    either an editor restart or Live Coding keystrokes — neither is something an unattended
    #    run may do to the operator's open session).
    defects: list[dict] = []

    bare = timed("spawn", {"actor_class": BARE_CLASS, "location": [10.0, 20.0, 30.0],
                           "actor_label": PROBE_LABEL + "B"})
    bare_gt = timed("get_transform", {"actor_label": PROBE_LABEL + "B"})
    bare_set = timed("set_transform", {"actor_label": PROBE_LABEL + "B",
                                       "location": [70.0, 80.0, 90.0]})
    bare_gt2 = timed("get_transform", {"actor_label": PROBE_LABEL + "B"})
    timed("delete", {"actor_label": PROBE_LABEL + "B"})
    if bare.get("status") == "SUCCESS" and str(bare.get("class")) == "Actor":
        defects.append({
            "id": "silent-AActor-fallback",
            "measured": f"spawn class={BARE_CLASS!r} menjawab SUCCESS dengan class="
                        f"{bare.get('class')!r}; location dilaporkan {[10.,20.,30.]} tetapi "
                        f"get_transform membaca {bare_gt.get('location')}; setelah set_transform "
                        f"SUCCESS masih membaca {bare_gt2.get('location')}",
            "cause": "IRSofkaWorldSubsystem.cpp: LoadClass wants '/Script/Engine.StaticMeshActor'; "
                     "a bare name yields null and `if (!LoadedClass) LoadedClass = AActor::StaticClass()` "
                     "spawns a body with no RootComponent, so every transform is a no-op",
            "why_it_matters": "an engine answers SUCCESS about an object that cannot be moved, and "
                              "the spawn reply echoes the REQUESTED location, not the applied one",
        })

    if st.get("status") == "SUCCESS" and not any(
            k in st for k in ("location", "rotation", "scale", "actor_label")):
        defects.append({
            "id": "set-transform-returns-nothing",
            "measured": f"set_transform reply keys={sorted(st)} — hanya status; caller harus "
                        f"memanggil get_transform lagi untuk tahu apa yang mendarat "
                        f"(readback hari ini cocok: {gt2.get('location')})",
            "cause": "ActionSetTransform returns the literal TEXT(\"{\\\"status\\\":\\\"SUCCESS\\\"}\")",
            "why_it_matters": "a write that reports nothing cannot be audited from the reply, and "
                              "it also skips Modify()/transaction so the editor may not mark the "
                              "level dirty",
        })

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    strict = "--strict" in sys.argv
    fail_count = passed < total or (strict and bool(defects))
    out = {"state": "FAILED" if fail_count else "OK",
           "endpoint": f"{HOST}:{PORT}",
           "checks": f"{passed}/{total}",
           "strict": strict,
           "elapsed_ms": int((time.time() - started) * 1000),
           "latency_ms_by_action": timings,
           "probe_class": PROBE_CLASS, "probe_label": PROBE_LABEL,
           "level_saved": False,
           "engine_defects": defects,
           "failures": [r for r in results if not r["ok"]]}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
