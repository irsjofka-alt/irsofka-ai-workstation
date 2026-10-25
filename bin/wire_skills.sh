#!/usr/bin/env bash
# Bangun ulang jembatan skill: satu pohon, dibaca semua engine.
#
#   wire_skills.sh            buat/perbaiki tautan
#   wire_skills.sh --check    laporkan saja, jangan menulis
#
# Kenapa perlu: brain/skills adalah satu-satunya tempat penyimpanan, tapi Qoder mencari
# ~/.agents/skills dan Antigravity mencari plugins/station-rules/skills. Kalau tautan ini
# dibuat manual sekali lalu lupa, engine lagi-lagi bikin salinan sendiri — persis kegagalan
# yang bikin tiap AI punya cerita berbeda. Pohonnya symlink, jadi isinya tidak pernah dua.
set -uo pipefail

STATION="$HOME/.ai-station"
B="$STATION/brain/skills"
TREE="$STATION/engines/agents/skills"
GEMINI="$STATION/engines/gemini_config/plugins/station-rules"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

# nama-pack:arah. Lima kategori workstation diberi awalan station- supaya tidak bertabrakan
# dengan skill builtin; pack legacy memakai namanya sendiri (sudah spesifik).
PACKS="
station-visual:$B/visual
station-coding:$B/coding
station-sysadmin:$B/sysadmin
station-analysis:$B/analysis
station-learned:$B/learned
ue5-station:$B/ue5-station
blender:$B/blender
comfyui:$B/comfyui
"
for p in "$B"/legacy/*/; do
  [ -f "$p/SKILL.md" ] || continue
  PACKS+="$(basename "$p"):$p
"
done

say() { printf "%s\n" "$*"; }
problem=0

link_one() { # target-pack <link> — wajib punya SKILL.md di dalamnya
  local src="$1" dst="$2"
  if [ ! -d "$src" ]; then say "  LEWAT  $dst (sumber tidak ada: $src)"; return; fi
  if [ ! -f "$src/SKILL.md" ]; then
    say "  SALAH  $src tidak punya SKILL.md — engine tidak akan menganggapnya skill"
    problem=$((problem + 1)); return
  fi
  link_tree "$src" "$dst"
}

link_tree() { # direktori-kontainer <link> — tidak menuntut SKILL.md
  local src="$1" dst="$2"
  # Kalau target sudah berupa direktori NYATA (bukan symlink), `ln -sfn` tidak menimpanya
  # melainkan MENYARANGKAN tautan ke dalamnya (skills/a/a) — sunyi dan merusak. Direktori
  # itu bisa berisi data, jadi tidak dihapus; minta manusia yang memutuskan.
  if [ -d "$dst" ] && [ ! -L "$dst" ]; then
    say "  BIARKAN $dst adalah direktori nyata, bukan tautan — tidak disentuh (berisi data)."
    say "          Kalau memang sampah: pindahkan sendiri, lalu jalankan ulang."
    problem=$((problem + 1)); return
  fi
  if [ "$CHECK" = "1" ]; then
    if [ "$(readlink -f "$dst" 2>/dev/null)" = "$(readlink -f "$src")" ]; then
      say "  ok     ${dst/#$HOME/\~}"
    else
      say "  PUTUS   ${dst/#$HOME/\~} -> $(readlink "$dst" 2>/dev/null || echo '(tidak ada)')"
      problem=$((problem + 1))
    fi
  else
    mkdir -p "$(dirname "$dst")"
    ln -sfn "$src" "$dst"
    say "  taut    ${dst/#$HOME/\~}"
  fi
}

say "== pohon skill bersama =="
while IFS= read -r entry; do
  [ -n "$entry" ] || continue
  link_one "${entry#*:}" "$TREE/${entry%%:*}"
done <<< "$PACKS"

# ~/.agents harus berupa symlink, bukan direktori data. Guard self_preservation hanya
# mengizinkan bentuk ini (nama terdaftar di config/home_shims.json + sumber di workstation).
link_tree "$STATION/engines/agents" "$HOME/.agents"

link_tree "$TREE" "$GEMINI/skills"

# ---- index debt: a document no index mentions is a document no engine will ever read ---------------
# This rule exists because it was measured, not because it sounds tidy. Two learned skills
# (`exit1_timeout_300_python3.md`, `exit1_timeout_90_python3.md`) sat in `brain/skills/learned/` while
# neither `learned/SKILL.md` nor the master `brain/skills/SKILLS.md` mentioned them — the incidents
# behind them had repeated 6x and 9x, `incident_log` said `resolved`, `skills_inventory` had the rows,
# and each file claimed "Auto-Mitigated ke Master Skills". Every part of that claim was true except the
# one part that decides whether the machine ever benefits: nobody indexed them. The recorder had been
# matching the master heading as a literal string ("### 5. Learned Skills …") after that section was
# renumbered to "### 8.", skipped the write, and printed nothing.
# An ENTRY, not a mention: a line that is a catalog row or list item naming the file. Round 14 finding 6
# caught the earlier `grep -qF` version, which was satisfied by the filename appearing in a sentence — the
# same defect the recorder was fixed for, transplanted into the check meant to catch it. No backtick is
# used inside this pattern: a backtick in a double-quoted bash string is a command substitution, and that
# bug has now attacked three different files in this repository.
entry_present() { # $1 filename, $2 index file
  local name="$1" file="$2"
  # `.*` and not `[^\n]`: inside a bracket expression that would mean "any character except backslash
  # and the letter n", which failed almost every real line — measured as "6 tanpa indeks master" on a
  # catalog that in fact names all six. An ENTRY is a line whose FIRST characters are a list bullet with
  # a link, or a table cell, and the name may appear later in it (the old rows label the entry with the
  # raw signature `exit1:timeout_60_python3.md` while the path carries `exit1_timeout_60_python3.md`;
  # matching the path is what survives that difference).
  # Two conditions on the SAME line, checked in two passes: the line must start like a catalog entry, and
  # it must carry the name. Anchoring the name right after `- [` fails on the older rows, which label the
  # entry with the raw signature (`- [exit1:python3_EOF_import.md](file:///…/exit1_python3_EOF_import.md)`)
  # and put the real filename in the URL part of the same line.
  grep -E -- "^[[:space:]]*(- \[|\|)" "$file" | grep -qF -- "$name"
}

index_debt=0
master_debt=0
debt_checked=0
# Levels, stated exactly as they are checked (a previous commit claimed "at all levels" and was wrong):
#   * EVERY pack: each of its documents must be named by that pack's own SKILL.md — that is the level an
#     engine reaches when it opens the pack.
#   * the `learned` pack only: its documents must ALSO be named by the master catalog, because the
#     recorder writes rows there and the operator reads that level. Measured 2026-10-16: the master
#     names 6 of 6 learned documents, 1 of 1 for the small packs, but 0 of 5 in `blender` and 0 of 9 in
#     `comfyui` — those are indexed by their own SKILL.md by design, so demanding per-file rows in the
#     master would be a rule no reader uses and would make the gate red forever.
MASTER_INDEX="$B/SKILLS.md"
for pack_dir in "$B"/*/ "$B"/legacy/*/; do
  [ -d "$pack_dir" ] || continue
  docs=$(find "$pack_dir" -maxdepth 1 -name '*.md' ! -name 'SKILL.md' -printf '%f\n' 2>/dev/null | sort)
  [ -n "$docs" ] || continue
  pidx="$pack_dir/SKILL.md"
  if [ ! -r "$pidx" ]; then
    say "  INDEBT  $pack_dir: $(printf '%s\n' "$docs" | wc -l) dokumen, indeksnya tidak terbaca"
    index_debt=$((index_debt + $(printf '%s\n' "$docs" | grep -c .)))
    continue
  fi
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    debt_checked=$((debt_checked + 1))
    stem="${d%.md}"
    if ! grep -qF -- "$d" "$pidx" && ! grep -qF -- "$stem" "$pidx"; then
      say "  INDEBT  ${d} tidak disebut indeksnya ($pidx) — tidak akan pernah dibaca engine"
      index_debt=$((index_debt + 1))
    elif [ "$pack_dir" = "$B/learned/" ] && [ -r "$MASTER_INDEX" ] \
         && ! entry_present "$d" "$MASTER_INDEX"; then
      # An ENTRY shape, not a mention. `grep -qF "$d"` (round 14, finding 6) let the filename pass when
      # it appeared anywhere in the master — in a heading, in a sentence, in the very comment explaining
      # this rule — which is the recorder bug again, transplanted into the check that was supposed to
      # catch it.
      say "  INDEBT  ${d} ada di indeks pack tapi tidak menjadi ENTRI katalog master ($MASTER_INDEX)"
      master_debt=$((master_debt + 1))
    fi
  done <<< "$docs"
done

count=$(ls -1 "$TREE" 2>/dev/null | wc -l)
say ""
say "pack tersedia: $count"
say "dokumen dicek indeksnya: $debt_checked · tanpa indeks pack: $index_debt · tanpa indeks master (learned): $master_debt"
if [ "$CHECK" = "1" ]; then
  if [ "$problem" -eq 0 ] && [ "$index_debt" -eq 0 ] && [ "$master_debt" -eq 0 ]; then
    say "semua tautan sehat, semua dokumen terindeks"
  else
    say "ADA $problem masalah tautan · $index_debt tanpa indeks pack · $master_debt tanpa indeks master"
  fi
  exit $([ "$problem" -eq 0 ] && [ "$index_debt" -eq 0 ] && [ "$master_debt" -eq 0 ] && echo 0 || echo 1)
fi
say "Qoder memuat ini tanpa restart; untuk engine lain butuh respawn tab."
# Mode tulis juga harus gagal dengan benar: kalau tidak, pemakai otomatis (CI, skrip
# setup) membaca exit 0 padahal ada tautan yang ditolak.
[ "$problem" -eq 0 ] || { say "ADA $problem masalah yang perlu keputusan manusia."; exit 1; }
[ "$index_debt" -eq 0 ] || { say "ADA $index_debt dokumen yang tidak disebut indeksnya — tulis barisnya."; exit 1; }
[ "$master_debt" -eq 0 ] || { say "ADA $master_debt dokumen learned yang hilang dari katalog master."; exit 1; }
