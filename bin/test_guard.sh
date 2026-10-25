#!/usr/bin/env bash
# Test the commit guard by making REAL commits in a throwaway repository and asserting what git does.
#
# Why `git commit` and not a direct call to the script: the previous review round found that the
# message scan lived in `pre-commit`, which git invokes with NO arguments, so it never ran in
# production — and the old harness called the script by hand, handed it a path, and reported green.
# A guard tested by calling it the way only the test calls it is not tested. Every case here goes
# through `git commit` with both hooks installed, so the argument contract is part of the assertion.
#
# Runs in /tmp against its own repository. It never touches this repository's index, refs or hooks.
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/hooks" && pwd)"
# The hook directory lives OUTSIDE the worktree on purpose. It used to sit at $REPO/.githooks, and
# the per-case cleanup runs `git clean -fd`, which deleted the untracked hook directory after the
# first refusal -- so every later case committed with no guard installed and the harness reported
# those as "the commit went through". A harness that cleans away its own subject can only produce
# results that look like the subject's.
ROOT="$(mktemp -d /tmp/guard_test.XXXXXX)"
REPO="$ROOT/repo"
HOOKS="$ROOT/githooks"
mkdir -p "$REPO" "$HOOKS"
pass=0; fail=0
trap 'rm -rf "$ROOT"' EXIT

ok() { pass=$((pass+1)); printf '[ok    ] %s\n' "$*"; }
no() { fail=$((fail+1)); printf '[GAGAL ] %s\n' "$*"; }

# Built at runtime and never written as a literal. The first version kept a placeholder key in this
# very file, and the guard refused the commit -- correctly, because it matches the real shape and has
# no way to know a test does not mean it. Building it here keeps the production shape in the test and
# out of every file, log and diff in the repository.
KEY="AIza$(printf 'a%.0s' {1..35})"

cd "$REPO"
git init -q .
git config user.email t@t.invalid; git config user.name t
mkdir -p config engines/qoder
printf 'config/byok.json\nengines/\n' >> .gitignore
cp "$SRC/pre-commit" "$SRC/commit-msg" "$SRC/guard_rules.sh" "$HOOKS/"
chmod +x "$HOOKS"/*
git config core.hooksPath "$HOOKS"
printf 'init\n' > README.md
git add README.md .gitignore && git commit -qm init

# $1 label, $2 the token the refusal MUST carry, remaining args = the commit message. The token is the
# whole point: a non-zero exit only says something refused, and the guard refuses for several reasons.
# Round 10 found cases that stayed green when the guard failed to read a blob at all — the read failure
# is also a refusal, so an exit-code-only assertion cannot tell "caught the key" from "could not look".
must_refuse() {
  local label="$1" token="$2"; shift 2
  local out rc
  out=$(git commit -q -m "$*" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then no "$label — the commit went through"
  elif ! printf '%s' "$out" | grep -q "$token"; then no "$label — refused, but for a reason other than $token: ${out:0:170}"
  else ok "$label"; fi
}
must_accept() {
  local label="$1"; shift
  local out
  if out=$(git commit -q -m "$*" 2>&1); then ok "$label"
  else no "$label — refused: ${out:0:170}"; fi
}
unstage() { git reset -q HEAD -- . 2>/dev/null; git clean -qfd; }

# 1 exact denied path, reached the way an accident is: forced past the ignore rule
printf 'keys live here\n' > config/byok.json
git add -f config/byok.json
must_refuse "config/byok.json refused even with git add -f" "PATH" "add config"
unstage

# 2 suffix rule, ISOLATED from the blob net: named .pem, content innocent. The previous case appended
#   a key to the file, so the blob net answered on the suffix rule's behalf and a broken `*.pem`
#   pattern still reported green.
printf 'nothing secret about this file\n' > server.pem
git add server.pem
must_refuse "*.pem refused by the suffix rule alone (content is innocent)" "PATH" "add pem"
unstage

printf 'innocent\n' > deploy.key
git add deploy.key
must_refuse "*.key refused by the suffix rule" "PATH" "add key suffix"
unstage

printf 'innocent\n' > aws_credentials
git add aws_credentials
must_refuse "*credentials* refused by the suffix rule" "PATH" "add creds name"
unstage

# 3 rename: a denied path arriving under an innocent name (the staged filter has to include R)
printf 'keys live here\n' > config/byok.json
git add -f config/byok.json
git commit -q -m "bring in secret" --no-verify          # history the guard must then survive
git mv -f config/byok.json notes.txt
must_refuse "renaming a secret to an innocent name is still refused" "PATH" "move it away"
git reset -q --hard HEAD~1 >/dev/null 2>&1; git clean -qfd

# 4 blob net at an innocent path
printf 'readme\n%s\n' "$KEY" > NOTES.md
git add NOTES.md
must_refuse "a key pasted into an ordinary file is refused by the blob net" "BLOB" "add notes"
unstage

# 5 a key inside binary bytes. Two things this fixture had to get right, both found by mutating the hook:
#   * the NUL is written EXPLICITLY. `/dev/urandom` alone is a coin flip — 200 random bytes contain a NUL
#     about half the time, and on a run without one, grep calls the blob text and the case passes with or
#     without the `-a` it claims to test.
#   * the assertion demands a LINE NUMBER, not just a refusal. Without `-a`, GNU grep reports "binary file
#     matches" on stderr and prints nothing on stdout, so a refusal-only assertion would still be satisfied
#     by any other net. Requiring `line [0-9]+` is what makes this case fail when the flag is removed,
#     which it does: measured 2026-10-16, the mutated hook committed the keyed binary straight into history.
{ head -c 200 /dev/urandom; printf '\000'; printf '%s\n' "$KEY"; } > asset.bin
git add asset.bin
out=$(git commit -q -m "add asset" 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "BLOB  asset.bin line [0-9]+"; then
  ok "a key inside a binary blob is found and located (grep -a, scanned as text not skipped)"
else
  no "binary blob not audited properly (rc=$rc): ${out:0:180}"
fi
unstage

# 6 registration file
mkdir -p engines/qoder                    # case 1's cleanup can remove the directory
printf '{}\n' > engines/qoder/settings.json
git add -f engines/qoder/settings.json
must_refuse "an engine registration file is refused by name" "PATH" "add registration"
unstage

# 6b the pattern `.env*` — ROADMAP.md has always listed `.env*` in the path net while the list itself
#      carried only `.env` and `.env.local`, so `.env.production` was denied by a rule that did not exist.
#      Round 11 called it: the document promised more than the code delivered.
printf 'DB_HOST=internal-only\n' > .env.production
git add .env.production
must_refuse ".env* is denied by pattern, not only by two exact names" "PATH" "add env production file"
unstage

# 7 the message net, through a real commit whose tree is otherwise clean
printf 'harmless\n' > doc.md
git add doc.md
must_refuse "a key in the COMMIT MESSAGE is refused (git hands commit-msg the path)" "MSG" "docs: set key to $KEY"
must_accept "the same clean tree commits once the message is innocent" "docs: add doc"

# 8 a NEWLINE path that actually carries a secret. The previous version staged an innocent file and
# asserted acceptance, so it proved nothing about the case it named: a scanner that fails to read a
# weird path would still have reported green. Content first, then the odd name.
nlfile="$(printf 'weird\nname.md')"
if printf 'harmless\n%s\n' "$KEY" > "$nlfile" 2>/dev/null; then
  git add -- "$nlfile" 2>/dev/null
  must_refuse "a newline in the filename cannot hide a key (path read with -z, blob by OID)" "BLOB" "add odd"
else
  no "git refused to stage the newline filename, so the case could not run here"
fi
unstage

# 9 a COLON in the filename: `git show :pre:commit` parses the part before the colon as a revision
# and dies, which the old `2>/dev/null` turned into a silent pass.
colonfile='pre:commit.md'
if printf '%s\n' "$KEY" > "$colonfile" 2>/dev/null; then
  git add -- "$colonfile"
  must_refuse "a colon in the filename still gets its blob read (OID lookup, not :path)" "BLOB" "add colon"
else
  no "could not create the colon filename; case did not run"
fi
unstage

# 9b a MAGIC PATHSPEC filename. `git ls-files -- "$f"` treats `$f` as a pathspec, so a staged file named
#      `:(top)magic.md` pointed the scan at `magic.md` in the root instead of at itself: the guard read a
#      different blob, found nothing in it, and would have cleared a keyed file. The innocent twin must
#      exist for that misread to be possible, and the assertion names the magic file so a refusal coming
#      from any other entry does not count. Round 10 found this by reading the line, not by being told.
printf 'innocent twin\n' > magic.md && git add -- magic.md && git commit -q --no-verify -m "innocent twin"
printf '%s\n' "$KEY" > ':(top)magic.md'
# The fixture itself must use the literal rule: `git add` honours pathspec magic too, and without this the
# file is never staged and the case silently tests nothing -- the same false green the merge case had.
GIT_LITERAL_PATHSPECS=1 git add -- ':(top)magic.md'
out=$(git commit -q -m "add magic" 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qF 'BLOB  :(top)magic.md'; then
  ok "a magic-pathspec filename is scanned as itself (GIT_LITERAL_PATHSPECS), not as a pattern"
else
  no "magic pathspec not scanned as itself (rc=$rc): ${out:0:200}"
fi
git reset -q --hard HEAD~1 >/dev/null 2>&1; git clean -qfd

# 10 author metadata: the key never appears in the message or a file, only in the commit object
printf 'plain file\n' > meta.md && git add meta.md
out=$(git commit -q --author="$KEY person <p@example.invalid>" -m "add meta" 2>&1); rc=$?
if [ $rc -eq 0 ]; then
  no "a key placed in the AUTHOR field was committed anyway"
elif printf '%s' "$out" | grep -q "IDENT"; then
  ok "a key in the author identity is refused (commit object, not message body)"
else
  no "the author-identity key was refused for some other reason: ${out:0:170}"
fi
git reset -q; rm -f meta.md

# 11 a symlink: only the target path is stored, so the guard cannot see what it points at and says so
printf '%s\n' "$KEY" > /tmp/guard_target_outside_repo.txt
ln -sfn server.pem link-to-pem 2>/dev/null
git add link-to-pem
must_refuse "a symlink is refused rather than quietly scanned as its own text" "MODE" "add symlink"
git reset -q; rm -f link-to-pem /tmp/guard_target_outside_repo.txt

# 15 an index entry whose blob cannot be read. The old pipeline `git cat-file blob "$oid" 2>/dev/null |
#      grep ... || true` turned "this object is corrupt" into "no match", which is a clean bill of health
#      printed by a scanner that saw nothing. Round 10 named this in two places; the fix is an existence
#      check plus a grep status that distinguishes 1 (nothing matched) from anything else (could not work).
printf '%s\n' "$KEY" > unreadable.md && git add -- unreadable.md
oid=$(git ls-files --stage -z -- unreadable.md | tr '\0' '\n' | awk 'NR==1{print $2}')
objfile=".git/objects/${oid:0:2}/${oid:2}"
if [ -n "$oid" ] && [ -f "$objfile" ]; then
  cp "$objfile" "$ROOT/object.backup"
  chmod u+w "$objfile"        # git writes loose objects read-only on purpose (444); corruption needs a chmod first
  printf 'this is not zlib data anymore' > "$objfile"
  out=$(git commit -q -m "commit a blob git cannot read" 2>&1); rc=$?
  cp "$ROOT/object.backup" "$objfile"; rm -f "$ROOT/object.backup"   # leave the repository readable
  if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "READ"; then
    ok "an index entry whose blob cannot be read is refused, not reported clean"
  else
    no "a corrupt blob was treated as no match (rc=$rc): ${out:0:180}"
  fi
else
  no "the staged blob was not a loose object, so the corruption case could not run (oid=${oid:-none})"
fi
git reset -q HEAD -- . 2>/dev/null; rm -f unreadable.md

# 12 DELETING a denied file must be allowed. The guard refuses adding a secret; if it also refused
#    removing one, a leaked file could never be fixed by an ordinary commit. This case exists because
#    a previous round shipped exactly that regression while the file's own comment promised the
#    opposite — a promise in prose is not a test.
mkdir -p config                              # case 3's cleanup deletes the directory, not just the file
printf 'keys live here\n' > config/byok.json
git add -f config/byok.json
git commit -q -m "leak it" --no-verify
if ! git rev-parse -q --verify "HEAD:config/byok.json" >/dev/null 2>&1; then
  no "case 12 never got the secret committed, so the deletion it tests could not happen"
elif git rm -q config/byok.json && out=$(git commit -q -m "remove the leaked file" 2>&1); then
  ok "removing a previously committed secret is allowed (the guard must not block its own remedy)"
else
  # Diagnostics in plain variables, never nested `$( )` with escaped quotes inside a quoted
  # message: the previous version of this line could not be parsed, and a harness bash cannot
  # read proves nothing about the guard it tests.
  ident=$(git var GIT_AUTHOR_IDENT 2>&1)
  committer=$(git var GIT_COMMITTER_IDENT 2>&1)
  authors=$(git log --format='%an <%ae>' -3 2>&1 | tr '\n' ';')
  no "deleting a denied file was REFUSED: ${out:0:300} | IDENT=[$ident] COMMITTER=[$committer] LOG=[$authors]"
fi
git reset -q --hard HEAD >/dev/null 2>&1; git clean -qfd

# 13a a secret that arrives through an AUTOMATIC MERGE — the shape git never runs pre-commit for.
#     Measured 2026-10-16: an auto-merge fires prepare-commit-msg, commit-msg and post-merge, and not
#     pre-commit, so a key carried in from another branch walked past the staged-tree net and git wrote
#     the merge commit anyway. commit-msg now runs the same scan; this case is the proof, and it failed
#     against the version of the guard before that line existed.
#     The fixtures commit with --no-verify because they stand for a key that is already in history from
#     before the guard. Committing them through the guard would leave this case "passing" in a repository
#     where the secret never arrived -- which is exactly how the previous version of it read green.
git checkout -q -b side-a 2>/dev/null
printf 'line1\nline2\n' > shared.txt && git add shared.txt && git commit -q --no-verify -m "shared base"
git checkout -q -b side-b
printf 'line1\nline2\n%s\n' "$KEY" >> shared.txt && git add shared.txt && git commit -q --no-verify -m "b appends a key"
git checkout -q side-a 2>/dev/null
printf 'line1 edited\nline2\n' > shared.txt && git add shared.txt && git commit -q --no-verify -m "a edits line1"
before=$(git rev-parse HEAD)
out=$(git merge side-b -m "merge b into a" 2>&1); rc=$?
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ] && printf '%s' "$out" | grep -q "commit refused"; then
  ok "a key arriving through an automatic merge is refused and HEAD does not move"
else
  no "the merge was committed or the guard never spoke (rc=$rc moved=$([ "$before" = "$after" ] && echo no || echo yes)): ${out:0:200}"
fi
git merge --abort >/dev/null 2>&1 || true
git checkout -q side-a 2>/dev/null; git reset -q --hard "$before" >/dev/null 2>&1
git checkout -q master 2>/dev/null || git checkout -q main 2>/dev/null
git branch -D side-a side-b >/dev/null 2>&1; git reset -q --hard HEAD >/dev/null 2>&1; git clean -qfd

# 13b a CONFLICTED merge index: three stages for one path, ancestor first. git itself refuses to commit
#     while a path is unmerged (measured 2026-10-16: exit 128, before any hook runs), so this state is
#     not publishable through `git commit` today. It is asserted anyway, against pre-commit called the
#     way git calls it (no arguments), because the round-9 finding was precisely that `git diff --cached`
#     does not list unmerged paths at all: the guard read nothing and cleared a keyed index.
git checkout -q -b conf-a 2>/dev/null
printf 'base line\n' > shared.txt && git add shared.txt && git commit -q --no-verify -m "conf base"
git checkout -q -b conf-b
printf '%s\n' "$KEY" > shared.txt && git add shared.txt && git commit -q --no-verify -m "b replaces the line with a key"
git checkout -q conf-a 2>/dev/null
printf 'edited on a\n' > shared.txt && git add shared.txt && git commit -q --no-verify -m "a replaces the same line"
git merge conf-b >/dev/null 2>&1 || true
stages=$(git ls-files --unmerged | wc -l)
out=$(bash "$SRC/pre-commit" 2>&1); rc=$?
if [ "$stages" -ge 3 ] && [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "BLOB  shared.txt"; then
  ok "every stage of a conflicted path is read (stage 3's key refused although stage 1 sorts first)"
else
  no "conflicted index was not audited: stages=$stages rc=$rc out=${out:0:160}"
fi
git merge --abort >/dev/null 2>&1 || true
git checkout -q master 2>/dev/null || git checkout -q main 2>/dev/null
git branch -D conf-a conf-b >/dev/null 2>&1
git reset -q --hard HEAD >/dev/null 2>&1; git clean -qfd

# 13c one definition of "looks like a key". Both hook files carry the expression -- commit-msg reads
#     metadata with it, pre-commit reads blobs -- so if they drift a key can be clean to one hook and
#     secret-shaped to the other, and nobody reads the two files side by side. Equality is a test.
defs=$(grep -l '^KEY_RE=' "$SRC"/*.sh "$SRC"/pre-commit "$SRC"/commit-msg "$SRC"/pre-push 2>/dev/null | sort -u | wc -l)
if [ "$defs" = "1" ]; then
  ok "the key shape is defined in exactly one file that all three guards read (guard_rules.sh)"
else
  no "KEY_RE is defined in $defs files; two copies of a secret shape become two different guards"
fi
# And a guard with no rules must refuse rather than pass everything: an empty deny list is a clean bill
# of health issued by a scanner that had nothing to compare against.
mv "$HOOKS/guard_rules.sh" "$ROOT/rules.hidden"
out=$(git commit -q -m "commit while the guard is blind" 2>&1); rc=$?
mv "$ROOT/rules.hidden" "$HOOKS/guard_rules.sh"
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "RULES"; then
  ok "a guard that cannot read its own rules refuses the commit instead of clearing it"
else
  no "the guard ran blind and returned rc=$rc: ${out:0:170}"
fi

# 14 an ordinary commit must still pass, or the guard is only noise
printf 'clean work\n' > work.md
git add work.md
must_accept "an ordinary commit is not blocked" "add ordinary work file"

# --- the push net -------------------------------------------------------------------------------
# pre-push is the last thing between this history and a public remote, and the only net that sees the
# routes no commit hook sees: an automatic merge (measured above) and anything committed --no-verify.
# It needs its own repository and its own remote, because the ref list it audits arrives on stdin from
# a real push -- and the assertion must be that the CREDENTIAL net spoke, not merely that the push
# failed: the hook also refuses when it cannot find the push gate, and that would read as a pass too.
PUSH="$ROOT/pushrepo"
mkdir -p "$PUSH/tools" "$PUSH/hh"
cp "$SRC/pre-commit" "$SRC/commit-msg" "$SRC/pre-push" "$SRC/guard_rules.sh" "$PUSH/hh/"
chmod +x "$PUSH/hh"/*
cd "$PUSH"
git init -q .
git config user.email t@t.invalid; git config user.name t
git config core.hooksPath "$PUSH/hh"
# A stub that always clears, so any refusal below can only have come from the credential net. It has to
# be valid PYTHON because the hook runs `python3 tools/work_order.py`; the first version of this line
# was a bash shebang, the stub died on a SyntaxError, every push in this section was refused by the
# gate instead of by the net -- and three of them still reported "ok".
printf 'import sys\nsys.exit(0)\n' > tools/work_order.py
chmod +x tools/work_order.py
git init -q --bare "$ROOT/pushremote.git"
git remote add origin "$ROOT/pushremote.git"
printf 'clean\n' > ok.md && git add ok.md && git commit -qm "first clean commit"
if out=$(git push -q origin HEAD 2>&1); then ok "a clean range passes the push net"
else no "the push net blocked a clean range: ${out:0:220}"; fi

# a key in a FILE, committed past every commit hook
printf 'line\n%s\n' "$KEY" > leaky.md && git add leaky.md
git commit -q --no-verify -m "commit that skipped every commit hook"
out=$(git push origin HEAD 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "TREE.*leaky\.md"; then
  ok "a key committed past the commit hooks is stopped at the push, naming the file"
else
  no "the push net let a keyed range through (rc=$rc): ${out:0:220}"
fi

# a key in the commit IDENTITY, which no file and no message ever contains
printf 'nothing secret\n' > meta2.md && git add meta2.md
git commit -q --no-verify --author="$KEY person <p@example.invalid>" -m "keyed author"
out=$(git push origin HEAD 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "META"; then
  ok "a key in a pushed commit's identity is stopped at the push (metadata scan)"
else
  no "the push net let a keyed identity through (rc=$rc): ${out:0:220}"
fi

# a key inside BINARY bytes at the pushed revision. What this case can and cannot prove is stated rather
# than assumed: measured 2026-10-16, deleting `-a` from the hook's git grep does not change the outcome on
# git 2.43, so this is not a test of the flag -- it is a test that binary content is audited at push time
# at all. The flag stays because a git that skipped binaries would otherwise open the hole silently, and
# the label claims only what the assertion carries.
{ head -c 200 /dev/urandom; printf '\000'; printf '%s\n' "$KEY"; } > asset2.bin && git add asset2.bin
git commit -q --no-verify -m "binary with a key"
out=$(git push origin HEAD 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "TREE.*asset2\.bin"; then
  ok "a key inside a binary blob is audited by the push net, naming the file"
else
  no "the push net skipped a binary blob (rc=$rc): ${out:0:220}"
fi

# the override exempts the VERDICT requirement only. An operator saying "this push is reviewed" is not
# saying "this range is safe to publish a key into", so the credential net must fire even with
# IRSOFKA_PUSH_OVERRIDE set — the ordering that makes that true is invisible in the file and very visible
# here, which is why it is asserted against behaviour rather than against a grep of the source.
out=$(IRSOFKA_PUSH_OVERRIDE=1 REASON="override must not clear a key" git push origin HEAD 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "TREE|META"; then
  ok "an override push is still refused by the credential net (override clears the verdict gate only)"
else
  no "the override carried a keyed range to the remote (rc=$rc): ${out:0:220}"
fi

# a DENIED PATH whose content is innocent: no blob net can catch a key that is not credential-SHAPED, so
# the push net needs the path net too. Without it, `config/byok.json` or a `*.p12` committed past the
# commit hooks with --no-verify would be pushed by the same guard that refuses it on the way in.
printf 'not shaped like a key at all\n' > leak.pem && git add leak.pem
git commit -q --no-verify -m "denied path, innocent content, no commit hook could stop it"
out=$(git push origin HEAD 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "PATH.*leak\.pem"; then
  ok "a denied PATH reaching the push net is refused by name, not by content"
else
  no "the push net has no path net (rc=$rc): ${out:0:200}"
fi

# an ANNOTATED TAG: `git rev-list` peels a tag to its commit, so the tag object's own tagger line and
# message — both published by `git push --tags` — were never read by anything.
git tag -a keyed-tag -m "release $KEY" 2>/dev/null
out=$(git push origin keyed-tag 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "META.*tag|tag.*META"; then
  ok "a key inside an annotated tag's message is refused at the push"
else
  no "the tag object was not audited (rc=$rc): ${out:0:200}"
fi

# a `git diff-tree` that FAILS must be a refusal. Exit 1 means "no match" for grep, which the tree scan
# above correctly treats as clean, but for diff-tree 1 only ever means it could not do the job. The two
# rules must not be copied into each other, so the failure is produced on purpose by putting a stub
# `git` earlier on the PATH that fails only diff-tree and forwards everything else.
REALGIT=$(command -v git)
mkdir -p "$ROOT/gitstub"
printf '#!/usr/bin/env bash\nif [ "$1" = "diff-tree" ]; then exit 1; fi\nexec %s "$@"\n' "$REALGIT" > "$ROOT/gitstub/git"
chmod +x "$ROOT/gitstub/git"
# This case injects the failure by invoking the hook ITSELF, with git's hook contract respected: no
# arguments, the ref list on stdin, the repository as cwd. It cannot be done through a real `git push`,
# and that is worth knowing rather than working around blindly: measured 2026-10-16, git PREPENDS its own
# exec directory to PATH when it runs a hook, so a stub earlier on PATH is never reached and the push
# sails through with the stub's log still empty. A test that only looks like the real event proves only
# what the fake event was capable of.
old_sha=$(git rev-parse HEAD~1) && new_sha=$(git rev-parse HEAD)
out=$(printf 'refs/heads/master %s refs/heads/master %s\n' "$new_sha" "$old_sha" \
      | PATH="$ROOT/gitstub:$PATH" bash "$PUSH/hh/pre-push" origin "$ROOT/pushremote.git" 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "the file list could not be read"; then
  ok "a failed file-list read is refused, not reported as 'no denied names'"
else
  no "diff-tree failure was read as clean (rc=$rc): ${out:0:220}"
fi

# a DENIED NAME in a ROOT COMMIT. `git diff-tree` prints nothing for a commit without a parent unless
# `--root` is given, so the first commit of an orphan branch — a real way to start a published branch —
# handed the path net an empty list and cleared a `server.pem` sitting in it.
git checkout -q --orphan keyed-orphan
git rm -rqf --cached . >/dev/null 2>&1
printf 'nothing credential-shaped in here\n' > server.pem && git add server.pem
git commit -q --no-verify -m "root commit carrying a denied name"
out=$(git push origin keyed-orphan 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "PATH.*server\.pem"; then
  ok "a denied name in a root commit is seen by the path net (--root)"
else
  no "root commit cleared the path net (rc=$rc): ${out:0:200}"
fi
git checkout -q master 2>/dev/null; git branch -D keyed-orphan >/dev/null 2>&1

# what a NEW ref actually publishes. Counting `rev-list <sha>` for a brand-new branch counts the whole
# history, so past MAX_REVS every new branch and every tag would be refused forever — a guard that turns
# into an outage. This needs its OWN clean repository: the push repo above deliberately holds keyed
# commits that were never accepted, so "already on the remote" is false there and a case written against
# it would be measuring the fixture rather than the hook.
CLEAN="$ROOT/cleanrepo"; mkdir -p "$CLEAN/tools" "$CLEAN/hh"
cp "$SRC/pre-commit" "$SRC/commit-msg" "$SRC/pre-push" "$SRC/guard_rules.sh" "$CLEAN/hh/"
chmod +x "$CLEAN/hh"/*
cd "$CLEAN"
git init -q . && git config user.email t@t.invalid && git config user.name t
git config core.hooksPath "$CLEAN/hh"
printf 'import sys\nsys.exit(0)\n' > tools/work_order.py && chmod +x tools/work_order.py
git init -q --bare "$ROOT/cleanremote.git"
git remote add origin "$ROOT/cleanremote.git"
for i in 1 2 3 4 5 6; do printf 'line %s\n' "$i" >> notes.md && git add notes.md && git commit -q -m "clean note $i"; done
if ! git push -q origin HEAD >/dev/null 2>&1; then
  no "the clean repository could not publish its own history, so the new-ref case cannot run"
else
  git checkout -q -b feature-clean
  printf 'work\n' > feature.md && git add feature.md && git commit -q -m "one new commit on a new branch"
  out=$(GUARD_MAX_REVS=3 git push origin feature-clean 2>&1); rc=$?
  if [ $rc -eq 0 ]; then
    ok "a new branch with 1 unpublished commit passes a bound of 3 (5 published ones are excluded)"
  else
    no "the new-ref range counted published history and refused real work (rc=$rc): ${out:0:220}"
  fi
  git checkout -q master
  git checkout -q -b feature-heavy
  for i in 1 2 3 4 5; do printf 'x%s\n' "$i" > f$i.md && git add f$i.md && git commit -q -m "new commit $i"; done
  out=$(GUARD_MAX_REVS=3 git push origin feature-heavy 2>&1); rc=$?
  if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "RANGE"; then
    ok "the bound still refuses when 5 genuinely new revisions exceed it"
  else
    no "the bound stopped biting on new work (rc=$rc): ${out:0:220}"
  fi
  # and a key on a NEW branch is still caught, exclusion or no exclusion
  git checkout -q master
  git checkout -q -b feature-leaky
  printf 'readme\n%s\n' "$KEY" > leak3.md && git add leak3.md && git commit -q --no-verify -m "key on a new branch"
  out=$(git push origin feature-leaky 2>&1); rc=$?
  if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qE "TREE.*leak3\.md"; then
    ok "a key on a brand-new branch is caught by the range that is actually new"
  else
    no "new-branch exclusion hid a key (rc=$rc): ${out:0:220}"
  fi
fi

# and the push that was refused must not have reached the remote
sent=$(git --git-dir="$ROOT/pushremote.git" rev-list --all --count 2>/dev/null || echo "")
clean_only=$(git --git-dir="$ROOT/pushremote.git" log --format='%s' 2>/dev/null | head -1)
if [ "$sent" = "1" ] && [ "$clean_only" = "first clean commit" ]; then
  ok "the remote holds only the clean commit — nothing keyed left this machine"
else
  no "remote history is wrong (count=$sent head=${clean_only:-none})"
fi

printf '\nbin/test_guard.sh: %s/%s sesuai\n' "$pass" "$((pass+fail))"
[ "$fail" -eq 0 ]
