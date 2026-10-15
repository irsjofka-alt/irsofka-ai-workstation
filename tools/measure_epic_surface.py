"""Engine-wide Epic tool surface, excluding generated headers.

Two mistakes to avoid, both made once already: a grep whose glob silently skips a directory, and a
count that reads `Intermediate/Build/**.generated.h` instead of the source that declares the toolset.
"""
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import os

# Where the engine lives. Overridable because a hard-coded path to one person's install is a
# measurement nobody else can repeat, and the whole point of this file is that the number can be
# re-derived by whoever doubts it.
PLUGINS = Path(os.environ.get("UE_PLUGINS", str(Path.home() / "Apps/LinuxUnrealEngine5.8.3/Engine/Plugins")))

res = subprocess.run(
    ["grep", "-rn", "--include=*.h", "--exclude-dir=Intermediate", ": public UToolsetDefinition", "."],
    cwd=str(PLUGINS), capture_output=True, text=True).stdout
lines = [l for l in res.splitlines() if l.strip()]
print("declarations found (Intermediate excluded):", len(lines))

cls_file = {}
for l in lines:
    path, _, rest = l.partition(":")
    path = path[2:]
    m = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*public UToolsetDefinition", rest)
    if m:
        cls_file.setdefault(m.group(1), path)

def is_test(c, path):
    return "/Tests/" in path or "/Mocks/" in path or "Test.h" in path or c.startswith(("UFake", "UMock"))

real = {c: f for c, f in cls_file.items() if not is_test(c, f)}
tests = {c: f for c, f in cls_file.items() if is_test(c, f)}
print(f"shipped toolsets: {len(real)} | test/mock: {len(tests)}")

counts, unverified = {}, []
for c, f in sorted(real.items()):
    text = (PLUGINS / f).read_text(errors="replace")
    # The declaration reads `class API_MACRO ClassName : public UToolsetDefinition`, so match the
    # name and its colon rather than the literal "class Name" — the macro sits between them.
    m = re.search(rf"\b{re.escape(c)}\b\s*:", text)
    if not m:
        unverified.append(c)
        continue
    seg = text[m.start():]
    j = seg.find("\n};")
    if j > 0:
        seg = seg[:j]
    counts[c] = len(re.findall(r"UFUNCTION\s*\(", seg))

total = sum(counts.values())
print(f"UFUNCTION declarations inside those {len(counts)} class bodies: {total}")
for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
    print(f"  {c:42s} {n:3d}   {real[c].split('/')[1]}/{real[c].split('/')[2] if len(real[c].split('/'))>2 else ''}")
if unverified:
    print("could not locate class body for:", unverified)

by_owner = defaultdict(int)
for c, f in real.items():
    by_owner["/".join(f.split("/")[:2])] += counts.get(c, 0)
print("\ntools by plugin:")
for k, v in sorted(by_owner.items(), key=lambda kv: -kv[1]):
    print(f"  {k:42s} {v:4d}")

Path(os.environ.get("EPIC_SURFACE_OUT", "epic_surface.txt")).write_text(
    f"declarations={len(lines)} shipped={len(real)} test={len(tests)} ufunctions={total}\n"
    + "\n".join(f"{c}\t{n}\t{real[c]}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1])))
print("\nwrote", os.environ.get("EPIC_SURFACE_OUT", "epic_surface.txt"))
