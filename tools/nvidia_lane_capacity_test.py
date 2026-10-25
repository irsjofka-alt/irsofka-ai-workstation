#!/usr/bin/env python3
"""Verify the configured Nvidia lanes can sustain their effective RPM.

Reads config/nvidia.json locally (mode 0600, gitignored). Keys are never printed.
By default it reads `rpm_per_key` from config/engines.json (35) and schedules
that many requests per lane over about 60 seconds, interleaving the lanes at
half the per-lane interval. It is a real API capacity test: a 70/RPM run sends
70 requests and reports every 429/503 instead of hiding it.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

STATION = Path(os.environ.get("AI_STATION_HOME", Path(__file__).resolve().parents[1]))


def load_lanes() -> list[dict]:
    store = json.loads((STATION / "config/nvidia.json").read_text(encoding="utf-8"))
    lanes = []
    for label, key_field, base_field in (
        ("lane-1", "api_key", "base_url"),
        ("lane-2", "api_key_2", "base_url_2"),
    ):
        key = (store.get(key_field) or "").strip()
        if not key:
            continue
        base = (store.get(base_field) or "https://integrate.api.nvidia.com/v1").strip()
        lanes.append({"label": label, "key": key, "base": base})
    return lanes


def engine_model() -> str:
    try:
        data = json.loads((STATION / "config/engines.json").read_text(encoding="utf-8"))
        return data["engines"]["nvidia"]["model"]
    except (OSError, KeyError, ValueError, TypeError):
        return "nvidia/nemotron-3-ultra-550b-a55b"


def effective_rpm() -> int:
    try:
        data = json.loads((STATION / "config/engines.json").read_text(encoding="utf-8"))
        return max(1, int(data["engines"]["nvidia"]["rpm_per_key"]))
    except (OSError, KeyError, ValueError, TypeError):
        return 35


def call(lane: dict, model: str, prompt: str, max_tokens: int, timeout: float) -> dict:
    url = f"{lane['base'].rstrip('/')}/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {lane['key']}",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
        elapsed = time.time() - started
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        if payload.get("choices"):
            return {"lane": lane["label"], "ok": True, "status": status, "elapsed": elapsed}
        error = payload.get("error") or payload.get("detail") or "no choices"
        return {
            "lane": lane["label"],
            "ok": False,
            "status": status,
            "elapsed": elapsed,
            "error": str(error)[:240],
        }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "lane": lane["label"],
            "ok": False,
            "status": exc.code,
            "elapsed": time.time() - started,
            "error": raw[:240],
        }
    except Exception as exc:  # noqa: BLE001 - report any transport failure
        return {
            "lane": lane["label"],
            "ok": False,
            "status": None,
            "elapsed": time.time() - started,
            "error": str(exc)[:240],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests-per-lane", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--prompt", default="ping")
    parser.add_argument("--only-lane", choices=["lane-1", "lane-2"], default=None)
    args = parser.parse_args()

    lanes = load_lanes()
    if args.only_lane:
        lanes = [lane for lane in lanes if lane["label"] == args.only_lane]
    if not lanes:
        print("tidak ada lane yang dikonfigurasi untuk pengujian ini")
        return 2

    per_lane = args.requests_per_lane or effective_rpm()
    interval = 60.0 / per_lane
    model = engine_model()
    total = per_lane * len(lanes)
    print(
        f"lanes={len(lanes)} per_lane={per_lane} total={total} "
        f"model={model} interval_per_lane={interval:.2f}s"
    )

    results: list[dict] = []
    lock = threading.Lock()
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = []
        for round_index in range(per_lane):
            for lane_index, lane in enumerate(lanes):
                target = started + round_index * interval + lane_index * (interval / 2)
                delay = target - time.time()
                if delay > 0:
                    time.sleep(delay)
                futures.append(
                    pool.submit(call, lane, model, args.prompt, args.max_tokens, args.timeout)
                )
        for future in as_completed(futures):
            result = future.result()
            with lock:
                results.append(result)
                if len(results) % 10 == 0:
                    ok = sum(1 for item in results if item["ok"])
                    print(f"progress {len(results)}/{total} ok={ok} failed={len(results)-ok}")

    wall = time.time() - started
    by_lane: dict[str, list[dict]] = {}
    for result in results:
        by_lane.setdefault(result["lane"], []).append(result)

    print("\n=== SUMMARY ===")
    print(
        f"wall={wall:.1f}s total={len(results)} "
        f"ok={sum(1 for r in results if r['ok'])} "
        f"failed={sum(1 for r in results if not r['ok'])}"
    )
    for label, items in sorted(by_lane.items()):
        ok = sum(1 for item in items if item["ok"])
        latencies = [item["elapsed"] for item in items]
        statuses = sorted({str(item["status"]) for item in items})
        print(
            f"{label}: ok={ok}/{len(items)} avg={sum(latencies)/len(latencies):.2f}s "
            f"max={max(latencies):.2f}s status={','.join(statuses)}"
        )
    failures = [item for item in results if not item["ok"]]
    if failures:
        print("\n=== FAILURES (first 12) ===")
        for item in failures[:12]:
            print(f"{item['lane']} status={item['status']} error={item['error']}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
