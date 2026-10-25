#!/usr/bin/env python3
"""
cline_rate_limiter.py — Client-side rate limiter for Cline providers.

Enforces max requests per minute per provider using token bucket stored in
~/.ai-station/engines/cline/data/rate_limit_state.json

Integrates with ai-station: reads/writes state in workstation data dir.
"""

import argparse
import json
import os
import sys
import time
import subprocess
from pathlib import Path
from filelock import FileLock

AI_STATION = Path.home() / ".ai-station"
CLINE_DATA = AI_STATION / "engines" / "cline" / "data"
STATE_FILE = CLINE_DATA / "rate_limit_state.json"
LOCK_FILE = STATE_FILE.with_suffix(".lock")

# Default limits per provider (RPM = requests per minute)
DEFAULT_LIMITS = {
    "nvidia": 40,      # 1 call per 1.5 seconds
    "cline": 60,       # ClinePass tier
    "cline-pass": 60,
    "usage-billing": 100,
}


def load_state() -> dict:
    """Load rate limit state from JSON."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    """Save rate limit state atomically."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def wait_for_slot(provider: str, rpm: int):
    """Block until a request slot is available for provider."""
    min_interval = 60.0 / rpm  # seconds between requests
    
    with FileLock(LOCK_FILE, timeout=60):
        state = load_state()
        now = time.time()
        
        provider_state = state.get(provider, {"last_request": 0, "requests": []})
        
        # Clean old requests (older than 60 seconds)
        cutoff = now - 60
        recent = [t for t in provider_state.get("requests", []) if t > cutoff]
        
        if len(recent) >= rpm:
            # Wait until oldest request expires
            oldest = min(recent)
            wait_time = (oldest + 60) - now
            if wait_time > 0:
                print(f"[rate-limiter] {provider}: limit reached ({len(recent)}/{rpm} RPM), waiting {wait_time:.1f}s", file=sys.stderr)
                time.sleep(wait_time)
                now = time.time()
                recent = [t for t in recent if t > now - 60]
        
        # Also enforce minimum interval from last request
        last = provider_state.get("last_request", 0)
        elapsed = now - last
        if elapsed < min_interval:
            wait_time = min_interval - elapsed
            print(f"[rate-limiter] {provider}: min interval {min_interval:.1f}s, waiting {wait_time:.1f}s", file=sys.stderr)
            time.sleep(wait_time)
            now = time.time()
        
        # Record this request
        recent.append(now)
        state[provider] = {"last_request": now, "requests": recent}
        save_state(state)


def main():
    parser = argparse.ArgumentParser(description="Rate-limited Cline wrapper")
    parser.add_argument("--provider", required=True, help="Provider ID (nvidia, cline, etc.)")
    parser.add_argument("--rpm", type=int, help="Override RPM limit")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to cline")
    
    args = parser.parse_args()
    
    if not args.args or args.args[0] != "--":
        print("Usage: cline_rate_limiter.py --provider <id> [--rpm N] -- <cline args...>", file=sys.stderr)
        sys.exit(1)
    
    cline_args = args.args[1:]  # skip '--'
    
    rpm = args.rpm or DEFAULT_LIMITS.get(args.provider, 60)
    
    print(f"[rate-limiter] {args.provider}: enforcing {rpm} RPM (1 req per {60/rpm:.1f}s)", file=sys.stderr)
    
    wait_for_slot(args.provider, rpm)
    
    # Execute cline with the same environment
    cline_bin = os.environ.get("CLINE_BIN", "cline")
    cmd = [cline_bin] + cline_args
    
    # `execvp` menggantikan proses ini dengan cline: tidak ada anak proses yang bisa
    # tertinggal saat wrapper mati, dan stdout/stderr tetap interaktif seperti tanpa
    # pembungkus. subprocess.run di sini justru memutus terminal dan meninggalkan
    # cucu proses yang tidak ikut kelompok pemanggil.
    try:
        os.execvp(cline_bin, cmd)
    except FileNotFoundError:
        print(f"[rate-limiter] cline binary not found: {cline_bin}", file=sys.stderr)
        sys.exit(127)
    except KeyboardInterrupt:
        sys.exit(130)

if __name__ == "__main__":
    main()
