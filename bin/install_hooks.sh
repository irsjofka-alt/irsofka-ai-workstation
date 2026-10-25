#!/usr/bin/env bash
# Link the repository's tracked hooks into .git/hooks, so a clone has the same guards as the author.
# Why a script and not a README line: .git/hooks is not versioned, so a guard that lives only in the
# repository is a suggestion. Verified afterwards by asking git itself which hooks are installed.
set -euo pipefail
STATION="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKDIR="$STATION/bin/hooks"
# A second target: the F11 MCP repositories that live OUTSIDE this one. Measured 2026-10-16, all three
# (`irsofka-comfyui-mcp`, `irsofka-blender-mcp`, `irsofka-workstation-mcp`) had no hook files at all, so
# the credential guard protected the workstation repository and said nothing about the repos that are the
# published surface of F11. `--repo <path>` points that repository's .git/hooks at THESE hook files by
# absolute path: one definition of the deny list and the key shape stays in bin/hooks/guard_rules.sh,
# rather than becoming a copy per repository that can drift (§12). A clone on another machine will not
# find the station and will refuse to run blind — which is the honest failure, not a silent pass.
TARGET="$STATION"
if [ "${1:-}" = "--repo" ]; then
  TARGET="$(cd "${2:?--repo needs a path}" && pwd)"
  [ -d "$TARGET/.git" ] || { echo "install_hooks: $TARGET has no .git — refusing to guard a non-repository" >&2; exit 1; }
fi
REPO="$TARGET"
mkdir -p "$REPO/.git/hooks"
# The shared rules file is SOURCED by the hooks, not executed by git, and the hooks find it through
# `readlink -f` of their own path — so it must never appear in .git/hooks as a would-be hook, and it
# must exist before any hook is installed. A guard whose rules are missing refuses every commit; that
# is the correct failure, but it is not a failure worth shipping.
[ -r "$HOOKDIR/guard_rules.sh" ] || { echo "install_hooks: $HOOKDIR/guard_rules.sh is missing; refusing to install guards with no rules" >&2; exit 1; }
for h in "$HOOKDIR"/*; do
  [ -f "$h" ] || continue
  name=$(basename "$h")
  [ "$name" = "guard_rules.sh" ] && continue
  if [ "$REPO" = "$STATION" ]; then
    ln -sf "../../bin/hooks/$name" "$REPO/.git/hooks/$name"
  else
    ln -sf "$HOOKDIR/$name" "$REPO/.git/hooks/$name"
  fi
  chmod +x "$h"
  echo "installed $name -> $(readlink "$REPO/.git/hooks/$name")"
done

# Another repository also needs something for the hook's gate half to read. `mcp/push_gate.py` is a thin
# runner: it takes the repository from the working directory and imports the station's OWN
# `work_order.push_gate`, so the rules about what a clearance is stay in one place (§12). The link is
# local to this machine — it points at an absolute path — so it is written to .git/info/exclude rather
# than committed: a clone on another machine has no gate here, and the hook says so loudly instead of
# judging the wrong repository.
if [ "$REPO" != "$STATION" ]; then
  mkdir -p "$REPO/tools"
  ln -sf "$STATION/mcp/push_gate.py" "$REPO/tools/work_order.py"
  excl="$REPO/.git/info/exclude"
  mkdir -p "$(dirname "$excl")"
  grep -qxF '/tools/work_order.py' "$excl" 2>/dev/null || printf '\n# local push-gate runner (a symlink into the station, meaningless on another machine)\n/tools/work_order.py\n' >> "$excl"
  echo "gate runner -> $(readlink "$REPO/tools/work_order.py") (excluded from the repository)"
fi
