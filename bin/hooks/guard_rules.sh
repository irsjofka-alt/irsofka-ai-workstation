#!/usr/bin/env bash
# One definition of "this path holds a secret" and "this string looks like a key", read by every guard.
#
# Why a shared file rather than a copy in each hook: the guard now has three entry points (pre-commit
# reads the staged index, commit-msg reads the message and identity, pre-push reads the whole range that
# is leaving the machine). Two copies of a deny list become two different guards, and the operator's rule
# is that one word has one definition. Measured cost of not doing this: round 10 found the push net
# missing the path net entirely, so `config/byok.json` committed with --no-verify would have been pushed
# by a guard that refuses it on the way in.
#
# A hook that cannot read this file must refuse rather than carry on with nothing: an empty deny list is
# a clean bill of health handed out by a scanner that found no rules to check.

# Shapes measured from this machine's own file. The length floor is deliberately high: a run of 30+
# key characters after a known prefix is not something prose produces by accident.
KEY_RE='(AIza[0-9A-Za-z_-]{30,}|AQ\.A[0-9A-Za-z_.\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'

# Measured before the list was written; denying a legitimately tracked path would block the operator's
# own edits and teach him to route around the guard:
#   .env*  (all of them)               env files are written by tools, not by this project; the pattern
#                                        is here because ROADMAP.md always claimed `.env*` in the path net
#                                        while the list only held `.env` and `.env.local` — round 11 found
#                                        the document promising more than the code delivered, which is the
#                                        same defect class as an override name nobody reads.
#   config/byok.json                   untracked, ignored .gitignore:10  -> denied by name
#   engines/*/settings.json etc        untracked, ignored .gitignore:73  -> the three real
#                                        registration files by NAME, so `git add -f` (which walks
#                                        straight past an ignore rule) is refused too
#   config/engines.json                TRACKED, registry metadata only, 0 credential-shaped strings
#                                        -> NOT denied. Refusing a file the Station UI edits buys
#                                        nothing and teaches bypass.
DENY_EXACT=(
  "config/byok.json"
  ".env"
  ".env.local"
  "engines/qoder/settings.json"
  "engines/antigravity-cli/settings.json"
  "engines/gemini_config/mcp_config.json"
)
DENY_PATTERNS=(
  ".env*"
  "*.pem"
  "*.key"
  "*credentials*"
  "*id_rsa*"
  "*.p12"
  "*.pfx"
)

# Public writing. A public repository is a product surface, not a transcript. These patterns are
# refused in tracked prose and commit messages: attribution archaeology, operator quotes, AI
# self-reference, and blame/defence narrative. Internal notes belong under data/ (ignored).
PUBLIC_WRITING_RE="(who said it|who decided what|the row that proves|row that proves it|this assistant|assistant's own|assistant transcripts|assistant's replies|operator's own typing|operator row|the operator never|operator never|he (said|asked|wrote|quotes|repeated)|his (instruction|refusal|main task|loop|words|answer)|echoed back|quoted, not remembered|recovered rather than retold|the machine's own invention|whose command was that|proposed and promised|sungguh memalukan|tidak professional|tidak profesional|session_turns [0-9]+|\\bturn [0-9]{3,4}\\b|transcript 20[0-9]{2})"

path_is_denied() {
  local f="$1" d
  for d in "${DENY_EXACT[@]}"; do
    # [[ ]] with a QUOTED right-hand side is literal equality — the correct half for exact paths.
    if [[ "$f" == "$d" || "$f" == */"$d" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  for d in "${DENY_PATTERNS[@]}"; do
    # UNQUOTED right-hand side here: in [[ ]] that is a glob pattern, which is what a suffix rule
    # needs. Quoting it would compare the asterisk as a character and match nothing — the bug that
    # lived here for a whole review round, where `*.pem` never matched a real .pem file.
    if [[ "$f" == $d || "$f" == */$d ]]; then printf '%s\n' "$d"; return 0; fi
  done
  return 1
}

# The rules file that a hook is about to source. Resolved from the hook's own directory so a symlinked
# install (.git/hooks/pre-commit -> ../../bin/hooks/pre-commit) still finds it; `BASH_SOURCE[0]` inside a
# sourced file is the sourced file itself, which is the only path in this hook that is not ambiguous.
guard_rules_path() {
  local dir="${1:-}"
  [ -n "$dir" ] || dir="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
  printf '%s/guard_rules.sh' "$dir"
}
