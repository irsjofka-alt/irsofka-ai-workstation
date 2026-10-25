# Public writing contract

This repository is public. Tracked files and commit messages are a product surface, not a
record of the conversation that produced them.

## Rules

1. **Describe the system, not the conversation.** State what was built, why, and how it is
   measured. Do not narrate who said what, when, or in which row.
2. **No transcript archaeology.** Do not cite `session_turns`, turn numbers, transcript files,
   private message ids, or ledger rows as attribution.
3. **No operator quotes or personal pronouns.** Do not reproduce instructions, complaints, or
   chat text. A decision is written as a decision with its rationale.
4. **No AI self-reference.** Do not write "this assistant", "the assistant's own", "the machine
   invented", or similar. The document speaks for the project.
5. **No blame, defence, or emotion.** Do not explain who was wrong, what was embarrassing, or
   why a previous session failed. Record the correction and move on.
6. **Evidence is executable.** Prefer a command, a test count, a commit SHA, or a file path.
   A claim that cannot be measured is labelled as a goal, not as a fact.
7. **One language per surface.** English is canonical for public docs, code, commits, API
   fields, and logs. Operator-facing UI may use the ID/EN selector defined in `ROADMAP.md`.
8. **Internal notes stay internal.** Live checklists, raw transcripts, recovery notes, and
   historical narrative belong under `data/` (gitignored) or `~/runtime`, never in git.

## Examples

| Do not write | Write |
|---|---|
| "The operator asked three times because the assistant forgot." | "The requirement is projected into the ledger so a loop can claim it." |
| "He said the licence should be BSL." | "Published family licence: BSL 1.1, Change License Apache-2.0." |
| "This assistant chose the base." | "The base is MIT (`ahujasid/mcp-for-blender`); provenance is in `UPSTREAM`." |
| "The row that proves it is 461." | "Decision record: `ROADMAP.md` F11 and the repository `NOTICE`." |
| "The previous session was useless." | "Superseded by the current implementation; the regression test is …" |

## Tone

Write like an engineer handing over a system: calm, specific, and brief. Do not flatter, do not
perform certainty, do not argue with the reader, and do not add emotional commentary. If a
statement would embarrass the project when read by a stranger, it does not belong in git.

## Enforcement

`bin/hooks/pre-commit` scans added lines in tracked prose, and `bin/hooks/commit-msg` scans the
commit message. Both refuse the patterns defined in `bin/hooks/guard_rules.sh`
(`PUBLIC_WRITING_RE`). The guard reports a file and a count; it does not quote the offending
line. Fix the text or move it to `data/`; do not bypass the hook.

Historical commits may still contain older wording. The current tree and every new commit are
covered; rewriting published history is a separate, destructive operation and is not performed
automatically.
