# 🗺️ Roadmap — Irsofka AI Workstation

Latest status: **October 8, 2026.** Every verified milestone includes reproducible evidence;
untested components are explicitly labeled. This document consolidates and supersedes
`ai_workstation_master_plan.md` and `PROJECT_SUMMARY_IRSOFKA_AI_WORKSTATION.md` along with `README.md`.

## Phases

```
F1 Brain & Rules Foundation       ✅ Completed
F2 Rust PTY Terminal Engine       ✅ Completed
F3 Telemetry & SQL State          ✅ Completed
F4 Native Rust Window GUI         ✅ Completed
F5 Memory & Session Integrity     ✅ Completed (Oct 6)
F6 Cross-Verification Ecosystem   🟡 In Progress
F7 Creative Studio (ComfyUI/UE5)  ✅ Completed (scope: studio wired + producing; see the scope note — UE handoff is not in it)
F8 Station — System of Record     🟡 In Progress (F8.2, F8.3 shipped)
F9 Refleks — Closed Sense-Act Loop ⬜ Planned
F10 Autopilot — Improvement Unattended ⬜ Planned
F11 Unreal bridge & MCP ownership  🟨 Partly shipped (mcp/ map shipped; **IRSofka ComfyUI MCP written independently — no code copied: 61/61 selftest + live DENY and live ALLOW**; UE plugin: BYOK path fix + new Blueprint->C++ exporter **compile verified** via RunUAT BuildPlugin (runtime still unverified; deployment is an open decision — see "The rebrand collision" above the ordered-work list); license chosen: BSL 1.1)
```

## F1–F4 — Foundation (Completed & Re-verified Oct 6)

Dual-mode Rust daemon `irsofka-station-core` (`--headless` + native tao/wry desktop window), three
interactive terminal tabs, RTX 3060/RAM/disk telemetry, PostgreSQL 16 with automated SQLite fallback,
hardware MCP server, zero external browser runtime dependency.

## F5 — Memory & Session Integrity ✅ (Completed & Verified Oct 6)

Resolves historical issues where sessions terminated upon daemon restarts and multiline prompts were
prematurely sent upon pressing Enter. Root cause resolved: release binaries were built but not deployed.

Verified improvements:

| Improvement | Evidence |
|---|---|
| Prompt Composer: Enter = new line, Ctrl+Enter = send, Ctrl+Shift+Enter = line-by-line | `curl :8999/` serves `<textarea>` + `handleKey()` with `e.ctrlKey` |
| Tabs hosted in tmux outside daemon cgroup | `/api/workspace` tabs report `backend:"tmux"`; daemon adopts panes (`tmux_adopt`, stable `pane_pid`) |
| Restart-proof sessions | 5× daemon restarts on Oct 6 maintained session `63313f58`; shell cgroup isolated in `irsofka-tabs.service` |
| `--continue` fail-safe | `cli_profiles.json` `continue_session:true` → `run_tab.sh` applies flag automatically |
| Enter dispatched as `\r` instead of `\n` | Fixed legacy bug where prompts were populated into input fields but not submitted |
| Automated handoffs | Hooks `SessionEnd` + `PreCompact` → recorded to `world_memory` & `brain/memory/projects/handoff_auto_*.md` |
| Self-preservation guardrails | `hooks/self_preservation.py` passes 57/57 test cases |
| Verified self-healing | `incident_log` tracks real failures; recurring failures trigger `auto_learned=TRUE` skill creation |
| Honest status dispatcher | Offline engines report `UNAVAILABLE` rather than false `COMPLETED`; writes go through PostgreSQL adapter |
| Backups & restore test | Hourly timer; `station_restore.sh --check` verifies clean restore to temporary DB (2,973 action records) |
| Credentials removed from code | `db_local.json` (gitignored) + environment variables; 0 secrets in git history |
| Clean `$HOME` enforcement | 4.7 GB moved to `~/runtime`; safety guard blocks unapproved dotfiles in `$HOME` |

## F6 — Cross-Verification Ecosystem 🟡

Completed:
- `ai-station verify` — Bidirectional verification between Qoder and Gemini. Verified: Gemini rejects invalid claims and validates sound logic referencing exact source lines (`main.rs:1384`, `1426-1431` — line numbers predate the Oct 7 source split).
- `config/engines.json` — Configuration-based engine registry allowing new engines without code modifications.
- Message box `ai_message` + MCP tools `ask_peer` / `check_messages` / `resolve_message`.
- `local_llm.py` — Tri-guard GPU resource management with VRAM safety gates and CPU/RAM fallback.

Pending:
- ⬜ **Tier `heavy` deployment:** `phi4:14b` evaluation under concurrent GPU workloads.
- ⬜ Real-world cross-engine stress testing with simultaneous ComfyUI / graphical workloads.
- ⬜ Antigravity quota telemetry widget in the GUI (data available via `/api/usage`).

### Verified Milestones (Oct 6)
- ✅ `parallel_tri_engine.py` dynamically loads `config/engines.json`. Legacy hardcoded models removed; `never_load_if` policy enforced: if available VRAM is insufficient, local execution reports `UNAVAILABLE` to PostgreSQL and identifies current GPU processes. Ollama requests pass configured `keep_alive` values.
- ✅ **Privacy redaction in daemon:** `/api/stats` and `/api/usage` sanitize personal identification (emails, real names, user avatars) before exposing metrics.
- ✅ **Toolset catalog updated:** `tools/TOOLSET_CATALOG.md` accurately documents the Rust daemon rather than deprecated Python scripts.
- ✅ **Stale SQLite databases archived:** Stale database copies moved to backups to avoid divergence from PostgreSQL.
- ✅ **Local engines evaluated:** `qwen3.5:4b` and `qwen3.5:9b` benchmarked with `model_check.py` (43.1 tokens/sec, full VRAM deallocation verified).
- ✅ **Zero-response bug resolved:** Set `num_ctx=12288`, `num_predict=1200`, and `think=false` for local verification models to prevent reasoning token starvation.
- ✅ **Symlink guard verified:** Fixed symlink nesting bug (`ln -sfn`) with directory safety guards.
- ✅ **Handoff git tracking corrected:** Accurate repository detection relative to `~/.ai-station` root directory.

### Verified Milestones (Oct 7)

- ✅ **Daemon source split by domain:** `main.rs` reduced from 2,261 to 234 lines. Startup, window and
  the router table are all that remains; behaviour moved to `paths`, `profile`, `terminal`, `spool`,
  `probe`, `engineinfo`, `save_state`, `procinfo` and `api/{workspace,engine,stats,terminal,desktop,daemon,static_files}`.
  Release build: zero warnings. Verified live after deploy — `/api/cli/config` 200, `/api/screenshot/latest`
  772 KB, `/api/term/history` 512 KB, GPU/RAM/disk populated, three tabs `alive`, `db_engine: POSTGRESQL`.
- ✅ **Two portability defects closed during the move:** the PostgreSQL default user was a compiled-in
  constant (`"irsofka"`) that made foreign clones attempt login with an account that is not theirs; and
  `count()` interpolated table names into SQL without validation.
- ✅ **Generated architecture map:** `bin/arch_map.sh` writes `brain/memory/projects/ARCHITECTURE.md`
  (folder → file → purpose), reading each purpose from the file's own header comment — no second copy to
  drift. 68 files mapped, 0 undocumented; refreshed at the end of every deploy; `--check` exits non-zero
  on undocumented files. Contract §11 records the rule for new files.
- ✅ **Memory restore briefing corrected:** duplicate `handoff_auto_*` rows no longer crowd out real
  memory; empty handoffs are neither written nor displayed; session id recovered from the transcript
  filename when the hook payload omits it; budget raised so the briefing no longer truncates its own
  pointer lines.
- ✅ **Binary backup rotation:** `deploy_engine.sh` keeps the 8 newest backups; `bin/` dropped from
  114 MB to 50 MB.
- ✅ **Call workers are cut, not kept (contract §4).** Any process that speaks to a model outside the
  operator's tmux panes is a *call worker*: it exists for one action and must die with its whole
  process group once its answer has been read. `tools/call_workers.py` decides what is persistent by
  asking — panes from the tmux socket, engines from `config/engines.json`, at the moment of use — and
  never from a written-down PID. Python dispatchers go through `run_grouped`, Rust probes through
  `probe::run_bounded`; a deploy now fails while an engine binary is spawned outside them, or while a
  stray worker is alive. Six defects were found by measurement rather than by reading:
  `subprocess.run(timeout=…)` kills the child and leaves the grandchild holding its RSS;
  `/bin/kill -KILL -PGID` exits 0 having killed nothing, because procps reads the leading negative
  number as a signal spec (`kill -KILL -- -PGID` is the form that works) — a Rust test asserts it, and
  the test was only proven to have teeth by deleting `--` and watching it fail; `tmux window_name` is
  `bash` in all four sessions, so the pane probe found one persistent process where three exist;
  `st_atime` of `/proc/<pid>/stat` is touched by every read and can never report a process's age;
  the two Claude entries share one `binary` value, so a dict keyed by binary collapsed the engine list
  to two entries; and identity by name alone classified the Qoder desktop IDE's 15 Electron processes
  as 2.18 GB of leaked workers — one `--cut` would have closed the operator's editor instead of a leak.
  Identity is now the resolved executable directory plus the name prefix, which still recognises a CLI
  that self-updated under it (`agy` runs from a file marked `(deleted)`) and rejects its neighbours.
  End-to-end proof on a real leak: an orphaned `qoder -p` at 259.6 MB was flagged, cut by group, the
  three workspace panes survived, audit back to zero.
- ✅ **The same file audited by an engine that is not the one that wrote it.** Asked independently,
  per contract §4: `claude-opus` returned nothing usable — headless `agy -p` tried to call a tool it
  cannot approve and was auto-denied, and `cross_verify` recorded `FAILED` rather than a pass, which
  is the documented limit of that tier. The `gemini` reviewer answered (the harness reported `TIMEOUT`
  because it answered after the window closed; the pane content was read separately, so the dispatch
  record stays honest and the verdict still had to be checked by hand) and returned **FAIL** with four
  findings. Three were real, and all three were catastrophic rather than cosmetic:
  1. `pane_roots()` returned `{}` when tmux could not be read. Empty and unknown are not the same
     fact: an empty persistent set marks every pane process as a stray. Demonstrated live with the
     kill path neutralised — with a bogus socket the old code reported `ok: true` and named
     **pid 498148 (`agy`, 271.3 MB) and pid 727828 (`qoder`, 827.6 MB)**, the operator's own two
     sessions, as things to cut. `pane_roots` now returns `None` for unreadable, the audit answers
     `ok: false`, and `--cut` refuses to act on an unknown set.
  2. `cut_strays()` signalled every PGID it found, and `killpg` reaches the whole group. A worker
     that escaped its pane by re-parenting still carries the pane's PGID, so the cut could kill the
     operator's CLI while killing the leak. A group is now killed whole only when no persistent PID
     stands in it; otherwise the cut narrows to the individual stray PIDs and says so. A stray whose
     own PID is in the persistent set is skipped outright.
  3. The group was swept only on timeout. A CLI that exits 0 after daemonising a worker leaves that
     group leaderless and resident — the same leak with a friendlier exit code. Both `run_grouped`
     and `probe::run_bounded` now sweep on the success path as well.
  Finding 4 — `setsid()`/double-fork escapes any PGID signal — is a real boundary that cannot be
  fixed by this mechanism; it is closed later, by identity, at the next audit or deploy gate, and the
  contract now says a green audit is not proof that no such worker exists.
  Each of the three fixes was proven to have teeth the same way as before: remove the fix, watch the
  test fail, restore. Removing the success sweep leaves one grandchild alive in the Rust test and one
  in the Python selftest. `cargo test` is now 3 tests and is a deploy gate.

## F7 — Creative Studio ✅

**Catatan cakupan (kenapa ✅ ini bukan berarti "selesai segalanya").** Ditutup atas keputusan operator
2026-10-15 10:4x, setelah core ComfyUI mencapai v0.40.0 dan studio menghasilkan berkas nyata untuk
pertama kalinya. Yang ✅ ini klaim adalah: **integrasi terpasang dan menghasilkan**.

| Masuk cakupan ✅ (terukur) | TIDAK masuk cakupan ✅ |
|---|---|
| ComfyUI core v0.40.0, `outdated: false`; comfy-cli 1.22.0; pack Trellis2 1.0.27, Manager 3.42, BiRefNet | Satu objek pun yang **masuk Unreal Engine** — rantai UE belum pernah dijalankan |
| Bobot nyata di disk: 3 diffusion + 2 text encoder + 3 VAE, satu root bersama CLI dan Desktop | Retopologi/UV yang **di-review secara visual** dan dinyatakan layak game |
| Gambar pertama: `17d7b8e8_000.png` 1.468.916 B, PNG 832×1216, Z-Image-Turbo int8, 8 langkah cfg 1, nol kredit | Gerbang VRAM yang **ditegakkan penjadwal** — masih aturan yang harus dibaca engine (`free_memory` melepas 7.627 MiB sebelum run 3D) |
| Model pertama: `9c23b91f_008.glb` 23.136.068 B, glTF 2.0, 99.810 wajah (diukur ulang di Blender headless) | Jalur video/audio/musik (Veo 3.1, Nano Banana 2.1, native-audio, Lyra) — **belum satu pun dipanggil**; lihat `brain/memory/reference-byok-design-capabilities.md` |

Angka wajah di baris terakhir itu sendiri adalah pelajaran: laporan node menulis 5.406.248, berkasnya
berisi 99.810. Yang dipercaya adalah hitungan ulang, bukan teks node.

### Tripo3D via the official CLI — prepared, and it corrected my guesses

`tripo-cli` 0.6.1 (repo `vast-enterprise/Tripo-API-CLI`, no install lifecycle scripts) is installed to
`~/runtime/npm-global`, not to `$HOME`; `~/runtime/shell/runtime.sh` gained `NPM_CONFIG_PREFIX` and the
matching PATH entry so the bin resolves.

What it settled that documentation could not: **the API is V3**. `tripo make --dry-run` runs *without an
API key and without spending credits* and returns the real plan, which exposed my hand-written endpoint
table as wrong on every line:

| | my guess (V2) | measured from the CLI (V3) |
|---|---|---|
| text→3D | `/v2/generate/text-to-3d` | `/v3/generation/text-to-model` |
| texture | — | `/v3/models/texture` |
| convert | — | `/v3/models/convert` |

`tools/tripo.py` now shells to the CLI instead of to my guesses, and keeps the cost guard the CLI does
not have: `--dry-run` is forced for `make`/`ai`/`redo` unless `--spend` is passed, and `--no-open` is
always added so nothing pops a browser on the operator's desktop. Two bugs were caught by testing this
against the live CLI: the guard appended `--dry-run` to read-only commands too (`tripo whoami` rejects
that flag), and argparse was eating flags meant for the CLI until `REMAINDER`.

**Deliberately NOT registered in `config/engines.json`.** That file is the registry of models the
workstation dispatches *decisions and verification* to; a 3D asset generator is not a decision-maker,
and putting it there invites the resolver ladder to ask a mesh generator for a verdict. It belongs to
the F7 toolchain, which is where it is recorded.

Next step needs the operator, not me: `tripo login` (browser authorises, key arrives automatically —
so no key is pasted into a chat log), then `python3 tools/tripo.py cli whoami --json` to read the
balance and whether Studio points and API credits share a wallet.

### Gemini BYOK as a collaboration surface — measured, not assumed

The operator's own Google AI Studio pool (5 slots, 3 real keys, PRO accounts) is now a first-class
engine in the workstation rather than a file only an agent edits. `tools/byok.py` owns the key file,
`/api/byok` serves it, and Station renders a **Gemini BYOK** view showing each slot's length, shape
and 12-character fingerprint — never the key itself. Keys do not pass through process arguments
(`ps` would show them to every user on the machine): a POST writes the body to a `0600` file and
hands the child only its path, which the child deletes on read.

Rotation follows the rule the Unreal plugin already implements: **429 rotates the key, a capacity
signal (503) escalates the model on the same key** — because Google meters quota per model, not per
key. Measured on this pool:

| Capability | Result |
|---|---|
| `gemini-3.8-flash` | ✅ works — used to audit the held diff |
| `gemini-4-argon` | ❌ 429 on **all three keys** including PRO, even after the plan was renewed; `-latest`/`-preview` are 404, so the base name is right |
| `veo-3.1` (+fast/+lite) | ⚠️ route exists — `predictLongRunning` answers 429, not 404 — but this pool's `generateContent` path cannot call it. Video needs its own operation-polling client. |
| `nano-banana-2.1`, `nano-banana-pro`, native-audio | ❌ image generation **called and refused** 2026-10-15: four image models (`nano-banana-2.1`, `3.1-flash-lite-image`, `3.1-flash-image`, `2.5-flash-image`) on two keys — non-PRO first, then PRO — all 429 `RESOURCE_EXHAUSTED` at `generate_content_free_tier_requests` / `..._input_token_count` with **limit 0**. The same PRO key answers 200 for `gemini-3.8-flash` seconds later, so this is a per-model entitlement wall, not a dead key. 0 images, 0 points spent. `ask()` has no image modality anyway, so a working image route here would be a new feature, not a new key. |
| **music / Lyra** | ❌ 404 on every name tried, on non-PRO **and** PRO keys, and no `lyra`/`music` entry in the model list. The operator states it is available on the model side; that is recorded as a statement, not as a route this workstation can use. |

Original plan: ComfyUI HTTP API (`:8188`) integration via a `comfy_workflow` MCP tool sharing the same
VRAM gate, to prevent resource contention with local LLMs and graphical viewports.

Measured on Oct 14, 2026: the **integration layer is done**, the **asset layer is not**.
Amended Oct 15, 2026 04:3x: the sentence "there is no generated image yet" stopped being true. Z-Image-Turbo
int8 produced `~/runtime/f7-bukti/17d7b8e8_000.png` — 1,468,916 bytes, PNG 832×1216 RGB, md5
`29fd1c67edc64757f0e5743b7e960df9`, prompt_id `17d7b8e8-5e34-4520-b534-de198518ec71`, `status: completed`
with `error: null`, and `spends_credits: false`. Amended again the same hour: the 3D half produced
`~/runtime/f7-bukti/9c23b91f_008.glb` — 23,136,068 bytes, glTF 2.0 (magic verified, declared length
equals actual length), 1 mesh / 1 primitive / 3 images / 1 material, prompt_id
`9c23b91f-f7ea-4476-a55f-f2e5953e4629`, `error: null`. The node report for that run reads
2,783,400 vertices / 5,406,248 faces — **and that is not what the file holds.** Importing the same
GLB into headless Blender measures **80,847 vertices / 99,810 faces**, i.e. exactly under the
100,000 target that was set: decimation did bound the saved mesh, and the 23 MB is three texture
maps, not geometry. The claim written an hour earlier on this line ("the decimate stage did not
bound what got saved") was inferred from node text without opening the file, and it was wrong.
A headless decimate to 50,000 faces left `boundary_edges=0`, `nonmanifold=1`, `degenerate=0`
unchanged, so the blanket rule that decimation worsens AI meshes does not apply to a watertight
source — measure `boundary_pct` first. **Amended 2026-10-15 12:3x: that sentence is retracted, and
the reason is the same trap this project has now hit twice.** `boundary_edges=0` was read from the
mesh in memory, before export. Measured on the files, after positional welding (the only read that
distinguishes a hole from a split vertex): the source rock is **0.02% open**, decimate to 0.05 is
**65.88% open**, and my own 04:47 retopo-50k is **16.25% open** — worse than the thing it came
from. Decimate does damage it; the in-memory read simply could not see the damage because it never
left Blender. The order that works, measured with its cost: voxel remesh 0.02 (99,810 faces to
3,950, **watertight at 0.00% open**, 0.10 s), then UV smart-project (remesh deletes the UV layer,
0.29 s), then export (0.23 s) — and the 17.66 MB is two 4096² textures, not topology: geometry
alone is 0.44 MB, and `image.scale(1024,1024)` takes the asset to **2.97 MB in 0.25 s**. Full
numbers in `brain/skills/comfyui/06-game-ready-mesh.md`. F7 stays 🟡 for one thing that is still
actually missing: **nothing has entered UE.** The VRAM half of the gap closed on 2026-10-15 12:4x —
`tools/gpu_gate.py` is now called by the drainer inside `hand_new_work`, before any claim and before
a single keystroke, and refuses by name (`VRAM_DENIED` / `VRAM_UNKNOWN`) stating the shortfall and
the process holding the memory. Thresholds live in `config/gpu_budget.json` as numbers with their
measurement cited (3D 9,966 MiB; image diffusion 5,919 MiB; vendor-cloud 0), re-read at use instead
of remembered, and an unreadable `nvidia-smi` refuses rather than assuming. Measured against the
live card the moment it shipped: 9,532 MiB free against 9,966 needed, so a TRELLIS.2 item is refused
by 434 MiB right now — while ComfyUI itself holds 1,624 MiB, which is exactly the situation the gate
was written for. `gpu_gate selftest 19/19`, `drainer 172/172` (four new checks: deny names the
holder and leaves the row PENDING; non-GPU work still passes a full GPU; allow records its
headroom; an unreadable meter refuses).

The gate's own first version failed open, and the audit that found it was aimed at exactly that:
an unreadable `config/gpu_budget.json` produced zero rules, so a TRELLIS item classified as
"not GPU work" and dispatched; a rule with a non-integer threshold was skipped with the same
result; and raw substring matching meant the keyword `checkpoint` would have held ordinary git
items behind a 5,919 MiB floor whenever the GPU was busy. Two of those three were written down as
*intended behaviour* by selftests I had written fifteen minutes earlier - `check_("config tidak
ada = kerja dianggap bukan GPU, BUKAN GPU yang dianggap aman")` asserts the fail-open in its own
name. A test written from the same assumption as the function it checks cannot catch that
assumption. Now: a missing or malformed config uses in-code fallback rules that cover **both** GPU
classes and marks the decision `degraded`; an unreadable threshold keeps its rule and answers
UNKNOWN (refuse); matching is word-bounded on `_` and alphanumerics, while `.` deliberately stays a
boundary because the real product name in this queue is `TRELLIS.2`. A third round found the fix
itself was shadowed: editing the file had left a **second `def rules()`** below the guarded one,
Python binds the last definition, and the suite reported green while testing unreachable code -
plus a parse loop outside its own guard, where one `{"match": 123}` could kill the tick. `status()` also crashed on a
broken threshold (`int >= None`), which is how a reporting function silences a gate at the moment it
is most needed; it takes an injected sampler now, so its test cannot go quietly vacuous.

### Verified

| Component | State | Evidence (reproducible) |
|---|---|---|
| MCP server registered for the CLI | ✅ | `engines/qoder/settings.json` → `mcpServers: [local-workstation, irsofka-unreal-mcp, mcp-blender, comfy-mcp]` |
| comfy-cli | ✅ 1.22.0 | `server_info` → `compatibility.comfy_cli_version` |
| ComfyUI core | ✅ v0.40.0 (commit 3541246a), `outdated: false` | `server_info` → `freshness.core` — re-measured 2026-10-15; the row above previously read "v0.39.2, v0.40.0 available", which had since stopped being true |
| Node packs | ✅ ComfyUI-Manager 3.42, BiRefNet-ZHO, Trellis2 1.0.27 | `server_info` → `freshness.packs` |
| One shared model root for CLI **and** Desktop | ✅ | `extra_model_paths.yaml` (`is_default: true`) and the ComfyUI Desktop app's own `settings.json` resolve to the **same** `models`/`input`/`output` directories — the "which root is actually used" trap is closed by config, not by guessing |
| Hardware routing decision | ✅ RTX 3060 12 GB → 8–24 GB band: images local, video not | `server_info` → `hardware.gpu` |
| Skill library | ✅ `SKILL.md` + 9 measured documents under `brain/skills/comfyui/01–09` (untracked, per the skills policy in the contract) | `~/.agents/skills/comfyui` → symlink to that directory |
| Model weights | ✅ 3 diffusion + 2 text encoders + 3 VAE on disk | `search_models`: `diffusion_models/{lumina_2_model_bf16, z_image_turbo_int8_convrot, trellis_2_int8_convrot}.safetensors`, `text_encoders/{qwen_3_4b_fp8_mixed, gemma_2_2b_fp16}.safetensors`, `vae/{ae, trellis_2_shape_vae_bf16, trellis_2_texture_vae_bf16}.safetensors` |
| **First real generation** | ✅ Z-Image-Turbo int8, 8 steps, cfg 1, 832×1216 | prompt_id `17d7b8e8-5e34-4520-b534-de198518ec71` → `status: completed`, `error: null`; `fetch_outputs` wrote 1,468,916 bytes to `~/runtime/f7-bukti/17d7b8e8_000.png`; `file` confirms `PNG 832x1216 8-bit RGB`; md5 `29fd1c67edc64757f0e5743b7e960df9`. Route: `fetch_template(image_z_image_turbo_int8)` → `local_check.runnable: true, error_count: 0` → `set_workflow_slot` (5 slots) → `validate_workflow` (`valid: true`, `spends_credits: false`, 10 nodes) → `run_workflow(wait=false)`. Free local graph: no partner node, no credit spent. |

### Still blocking F7

- ⬜ **No model weights exist on this machine.** `checkpoints/`, `unet/`, `diffusion_models/`, `vae/` and
  `text_encoders/` are empty in both roots; a bounded search of the home directory
  (`-maxdepth 4 -name '*.safetensors'`) returns 0. Nothing can generate until at least one checkpoint
  is downloaded.
- ⬜ **Server is down and has never produced output here.** `server_info` → `server.running: false`,
  port 8188 unowned, and `output/` holds only the placeholder file.
- ⬜ **The VRAM gate is not shared.** `comfy-mcp` and `local_llm.py` each decide on their own; nothing
  prevents a diffusion run and a 14 B local model from colliding on the same 12 GB. This is the actual
  engineering work left in F7, not the MCP wiring.
- ⬜ **The UE5 half of "Creative Studio" is not in this repository.** See *Ecosystem — three
  repositories, one pipeline* in `README.md`: the editor bridge is private.

### Acceptance path (in order, no step skippable)

Download one licence-verified image checkpoint into the shared models root resolved by
`extra_model_paths.yaml`
(contract §7: check free VRAM first), start with `comfy launch --background`, then run
`search_templates` → `fetch_template` → `set_workflow_slot` → `run_workflow` and confirm
`local_check` cleared before the run. F7 becomes ✅ only when a generated file exists on disk and the
same run coexisted with a local LLM without either seizing VRAM the other was holding.

## F8 — Station: System of Record 🟡

**What it is.** The Workstation (this cockpit) is where the operator *gives orders*; it must stay
open and is terminal-centric. The Station is where results are *seen* — a human-facing, modern web
view of inputs, outputs, reports and settings, modelled on an ERP: one system of record that every
other tool writes into.

Body and brain already exist (PostgreSQL, hooks, engines); the hands are still being built; the face
is not there yet. F8 is the face.

### Two invariants (the cheap-to-skip, expensive-to-revert part)

1. **One vocabulary.** `job`, `artifact`, `cost`, `role` are defined exactly once, in the schema.
   A second definition anywhere guarantees two reports that disagree, and nothing identifies which
   one is true. The cost of an ERP is paid in schema design, not in UI.
2. **Reports are derived, never editable.** No field on a report screen may be written by hand.
   One manual correction is enough for the whole ledger to lose its authority.

Consequence for build order: the ledger is finished before the dashboard. A clean interface on top
of a partial ledger displays wrong numbers confidently — and the operator trusts the neatness.

### Modules and current state

| ERP concept | Station equivalent | Today |
|---|---|---|
| Master data | teams, roles, verifier per role | `config/slots.yaml` + `config/engines.json` exist; no face |
| Transactions | prompts, commands, responses, failures | `action_log`, `session_turns`, `incident_log`; no face |
| Warehouse | images, models, commits, deploys | **no table exists** |
| Work order | project → phases → DONE | `quest_tasks` only, no phases, no team binding |
| Reporting | daily digest, weekly progress | none |
| Treasury | credits and quota left, cost per engine | none — raw data is already in transactions |

### Build order (agreed with the operator, Oct 7)

- ⬜ **F8.1 Warehouse.** `artifacts` table (path, kind, producer engine, originating action id,
  workspace, checksum, created_at) written by the ingestor, not by the AI that made the file.
  *Acceptance:* generate an image through ComfyUI and a git commit; both appear in the Station
  within one ingest cycle, with the command that produced them linked.
- ✅ **F8.2 Treasury.** Daily cost and remaining quota per engine, read from `action_log` plus
  CLI-reported usage. *Acceptance:* the numbers match what each CLI itself reports for the same
  day, or the row is marked `UNAVAILABLE` — never estimated.
  Shipped: `engine_quota` + a probe loop in the ingestor (`agy -p /usage`, `agy -p /credits`,
  and Qoder's `/usage` panel read from an isolated tmux socket), `/api/treasury`, and the
  Treasury view on the Station page. Measured, not assumed: Antigravity has no daily cost
  window at all (5h + weekly only) and Qoder exposes no per-turn credit field, so both are
  reported as `UNAVAILABLE` with the reason the data itself gives. Local tiers are
  `UNSUPPORTED`, which is a different statement and stays a different word.
- ✅ **F8.3 Master data face.** Editor for slots and engine registry: pick model per role, save.
  Writes go through schema validation, keep a timestamped backup, and refuse to save a registry
  that no engine can start from. *Acceptance:* change the verifier to another model, and the next
  `ai-station verify` run resolves the new one from config — never from memory.
  Shipped: `tools/master_data.py` owns the rules — it validates against `agy models`,
  `qoder --list-models`, the pulled ollama catalogue and the meter's own window names, and it
  refuses a role that restates the model its registry key already defines — `/api/master` owns
  the files (whitelist of writable paths, timestamped backup, tmp+rename, re-read and re-validate
  from disk after writing, rollback if the read is bad, one `action_log` row per save), and the
  Station's "Roles & verifier" view is the editor. 30 validator selftest cases pass.
  `slots.yaml` is validated but never written: rewriting YAML without `ruamel` would delete the
  contract comment inside it, so the editor refuses a changed `slots` instead of silently eating it.
  Measured, not assumed: the acceptance run set `claude-sonnet` to `claude-sonnet-5-5-medium`,
  `ai-station verify --list` resolved the new id out of config, and the value was set back.
  Two defects were only visible in the diff. `serde_json::Value` sorts object keys, so the first
  version of the endpoint rewrote all 104 lines of `engines.json` without changing a single value
  — fixed by passing raw bytes in both directions, and `deploy_engine.sh` now fails a deploy if
  the daemon ever reorders the registry again. And a candidate that placed an engine key *beside*
  `engines` instead of inside it saved as `200 OK` while changing nothing at all, so the registry
  document now has a declared shape and anything outside it is refused.
- 🟡 **F8.4 Work order.** Project entity with phases (intake → plan → build → debug → done),
  bound team and leader. *Open decision:* whether each phase requires the operator's approval
  before the next one runs. Recommendation on record: yes — with a thin wallet, an unattended
  pipeline is a meter running.
  Measured precondition (Oct 8): `quest_tasks` has exactly eight columns —
  `id, title, status, model_assigned, cli_engine, summary, created_at, completed_at` — and holds
  18 `COMPLETED`, 4 `FAILED`, 4 `UNAVAILABLE` rows. It cannot express a check command, a phase, a
  dependency or who is holding the task, so no engine can pick work from it. F8.4 is therefore the
  enabler for F10, not a sibling: an autopilot that cannot read the queue would have to invent one,
  and §12 forbids a second definition of `job`. This migration must add `phase`, `check_command`,
  `depends_on`, `claimed_by`, `lease_expires_at`, `evidence` — and `evidence` is what makes
  "COMPLETED" mean something other than "someone felt like it".
  Shipped the same night, as one unit with F10.2: `tools/work_order.py` is the only module that
  owns the words `job`, `claim`, `lease` and `evidence`, and it creates `quest_tasks` — which no
  code did before, so the eight-column table had been produced by hand all along. `claim()` is a
  conditional `UPDATE` with a read-back, not a `SELECT` then an `UPDATE`, and the lease is stored as
  an integer epoch because SQLite writes UTC where PostgreSQL writes local time — a `TIMESTAMP`
  there is a seven-hour silent drift. `COMPLETED` is refused without evidence, and legacy rows keep
  the status they were written with: `IN_PROGRESS` reads as `CLAIMED` through one `ALIAS` map and is
  never rewritten, because a report edited by hand stops being a report (§12).
  Two of its own bugs came from the live backend, not the test: `psycopg2` returns tuples, so every
  `dict(r)` in the module raised in production while the SQLite selftest stayed green — fixed in
  `run()`, and permanently watched by `selftest --live`, which asserts the same things against the
  backend actually in use and cleans up rows it made under its own marker.
  Remaining, and named so it is not mistaken for done: the Station work-order *view* (this table is
  written and read from the CLI only), and the open decision above about per-phase approval.
  *Acceptance:* `ai-station work-order next` offers an item whose dependencies are complete; the same
  `claim` from two CLIs admits exactly one; `complete` without evidence fails.
  Measured (Oct 8, 01:00): `selftest` 30/30, `selftest --live` on `POSTGRESQL` 11/11, and `next`
  returns item 27 — the seeded roadmap queue, not a legacy row.
- ⬜ **F8.5 Daily digest.** `/api/day` grouping the day's transactions per session, summarised by
  the local model, raw rows always one click below the summary.

**Open question (F8.1):** record metadata only (path + provenance), or store a small copy of each
result (thumbnail, diff, screenshot) so the Station renders it without touching the producing app?
Metadata-only is cheaper and cannot go stale silently; copies look better and survive deletion of
the original. Not yet decided.

## F9 — Refleks: Closing the Sense–Act Loop ⬜

**What it is.** The operator's own decomposition of this machine: head (`brain/`), eyes (Wayland
screenshot), hands (ydotool + tmux), body (one PostgreSQL). Head and body are built. Eyes and hands
are **open-loop**: an engine moves the mouse and then *assumes* the screen followed, presses Enter
and *assumes* the pane answered. F9 replaces each assumption with a measurement, so the cheapest
local model behaves like a careful one — it is not smarter, it is fed facts it cannot hallucinate.

Every item below is a re-graded survivor of `~/Documents/Ide Nyeleneh` 1–6 (reviewed Oct 8 against
measurements, not against enthusiasm). The refused ideas are recorded with a **numeric re-open
threshold**: a refusal without a threshold is a preference, and preferences rot.

- ⬜ **F9.1 Deterministic environment state.** On every command exit the Rust side writes one
  structured row — exit code, cwd, the process tree still alive, and the pane delta — and the
  SessionStart/PreToolUse briefing injects that row instead of letting the model recall what it ran.
  This is the single highest-value item in all six notebooks because it attacks the known failure:
  a model guessing terminal state. *Acceptance:* ask an engine "what is running in this pane" with
  no tools available; it answers from the row, and its answer is byte-checkable against
  `ps`/`tmux list-panes`. A wrong answer is a `DISPUTED`, not a retry.
- ⬜ **F9.2 Action ↔ outcome graph.** Link `action_log` to its verdict (success / FAILED / DISPUTED)
  keyed by command *shape*, then answer one question before an engine types: has this shape failed on
  this path before? Partially exists already — `incident_recorder` promotes an error signature after
  5 repeats (52 incident rows → 2 learned skills). F9.2 lowers the latency from "5 disasters" to
  "1, remembered per path" and turns the answer into injected prompt text rather than a note nobody
  opens. *Acceptance:* reproduce a recorded failure command; the briefing must warn about it before
  the command runs, naming the incident id.
- ⬜ **F9.3 Reflex proof — did the screen actually change.** After any `ydotool` act, diff a tiny
  region (64×64 around the target is enough) before and after, in Rust, and report changed/not-changed
  with the delta. Today the `station-visual` skill can only say "belum terverifikasi visual" when
  capture fails; this converts *unknown* into *known*, which is the whole point of §5.
  *Acceptance:* move the mouse over a window that exists and one that does not; the tool returns
  CHANGED for the first and NO_CHANGE for the second, without an LLM in the loop.
- ⬜ **F9.4 Wake on event instead of asking.** Two cheap event paths replace two poll loops: `epoll`
  on the PTY master fd (a character arriving wakes the reader — the value is *no polling*, not the
  fantasy of beating tmux's measured ~3 ms capture) and PostgreSQL `LISTEN`/`NOTIFY` so a new
  `quest_tasks` row pushes to the daemon instead of being re-selected. Both keep the current API;
  neither changes a number the operator sees. *Acceptance:* a row inserted from `psql` reaches the
  daemon with no timer in between, proven by a log line carrying the row id, and `ai-station workers`
  shows one fewer busy loop.

### F9 — refused, with the threshold that would un-refuse it

| idea (source) | why refused on this machine | re-open when |
|---|---|---|
| `/dev/shm` zero-copy IPC (4.1) | the path it would speed up is measured at **0.88 ms** per recall query and ~3 ms per pane capture; SHM shaves microseconds and adds a concurrency-bug class PostgreSQL cannot see | a measured tick of the ingest loop exceeds **20 ms** |
| time-partitioned memory tables (4.3) | whole DB is **17 MB**, `action_log` 12 599 rows / 7.7 MB; a seq scan already finishes sub-millisecond | `action_log` passes **2 M rows** or DB passes **2 GB** |
| Grimoire in `pgvector` (6.3, 4) | `vector` is **not installed** (`pg_available_extensions` empty for it), and the brain is 42 skill + 24 memory files, which the selective-loading rule already ranks by hand | the index stops ranking right: a needed file exists but grep misses it **twice in a week** |
| `synchronous_commit = off` (4.2) | the contract's premise is "unrecorded history never happened"; this trades the body's memory for throughput the body does not need | never — this one is refused on principle, not on budget |
| Java core: Loom, ZGC, Panama SIMD, R2DBC (2) | the SIMD claim rests on AVX-512 and **`/proc/cpuinfo` shows no `avx512*` flag** on the i7-12700F; a JVM is a second runtime that can call a model, which §1 forbids; and our ceiling is tokens/sec, not thread count | never as a core; a JVM may exist only as a *tool* behind a gate |
| CXL unified memory, kernel module pinning weights into GPU registers, neuromorphic co-processor, NVMe-oF (3.1) | no CXL device in `lspci`; registers are per-thread and hold kilobytes, not 6.6 GB of weights; the NVIDIA driver is binary; a custom kernel module is the one idea here that can stop the box booting | new hardware that actually enumerates, and a reason that survives a benchmark |
| PL/Rust in triggers, `pgai`/`pgvectorscale` in-DB RAG (5.1, 5.2) | neither extension is installed; more importantly a model call born *inside* PostgreSQL bypasses `run_grouped`/`probe::run_bounded`, so `ai-station workers` cannot see it and cannot cut it, and the DB — the body — becomes an engine | never; the database does not speak to models |
| pg_cron as the scheduler (5.4) | `pg_cron` is not installed and would need `shared_preload_libraries` plus a restart of the DB every engine shares; a user timer already exists and is proven (`irsofka-memory-backup.timer`) | never — right tool exists |
| genetic prompt evolver rewriting `brain/**` (1.1) | §12 rule 2: reports are derived. A machine rewriting the contract ends the contract | only as **proposals** into the F10.4 queue, approved by the operator |

## F10 — Autopilot: Improving Without Being Told ⬜

**What it is.** The operator asked (Oct 8): how do you keep improving with no order, forever, without
stopping — and should every choice you offer me be thrown at Antigravity/Gemini instead, since that
pool has longer breath while Qoder's is cheap to spend here.

Measured before designing, so this section is not aspiration:

- Qoder hooks **already fire unattended** — `PreToolUse`, `SessionStart`, `SessionEnd`, `PreCompact`,
  `PostCompact` are configured in `engines/qoder/settings.json`.
- The "AI failed → AI learns" wire **already runs**: `session_ingestor.py` calls `incident_recorder`
  on sweep, and 2 learned skills exist out of 52 incident rows.
- A user-level systemd timer **already works on this box** (`irsofka-memory-backup.timer`, hourly).
- Nothing was scheduled by the assistant: `CronList` → no jobs. So the missing piece is not a
  trigger. It is a **queue with a check command**, a **lease**, and a **stop condition** — and those
  are F8.4, which is why F10 is written after it and not before it.

### Four invariants (the cheap-to-skip, expensive-to-revert part)

1. **Autonomy in selection, never in scope.** The loop may decide *which* queued item to do next; it
   may not decide what is allowed to exist. Scope stays in the contract and in the deploy gate.
   Without this, "always improving" is a process with write access to the repo and no owner.
2. **A tick ends with a check command passing, or a row saying why it did not.** Silence is the one
   output an unattended loop must never produce; an empty tick that invents work is indistinguishable
   from a healthy one. Idle with an empty queue means *stop*, not *find something*.
3. **Unattended never touches:** git history (force push, reset, branch deletion), PostgreSQL
   durability parameters, contract/`§` text, files outside the claimed item's paths, `pkill -f` or
   `tmux kill-server` (§6). Those become `ESCALATED` rows that wait for the operator.
4. **`UNAVAILABLE` stops the tick; it never switches engine silently.** A quota gate that cannot read
   a meter refuses the dispatch (§4). An autopilot that responds to a closed pool by quietly using a
   different one produces work whose cost is attributed to nobody — and F8.2 exists precisely so cost
   has an owner.
5. **Unknown is never idle.** The mirror of the invariant that made the call-worker cut safe
   ("unknown is never empty"): a watchdog that cannot read the pane must not conclude the engine
   stopped. Measured reason — a `gemini` verification dispatch was recorded `TIMEOUT` by the harness
   while the tmux pane had already answered. Absence of a signal is not evidence of rest, and acting
   on that absence resumes a session that was mid-sentence.
6. **The off switch does not require the AI's cooperation.** If the only way to stop an unattended
   loop is to ask it politely, it is not a switch. See F10.8.

### Build order — set by review, not by numbering (Oct 8)

The modules keep their numbers, because the queue rows and `tools/work_order.py` already cite them.
The order they are *built* in does not follow those numbers. The ordering question went to the
`resolver` role (per §4: `gemini`, with every measurement above inside the prompt — a headless worker
cannot go looking), and the answer was `PERLU_KOREKSI` on three points. All three are accepted:

1. **F10.8 before F10.3.** A drainer with no switch is a loop with no off. Building F10.3 first would
   have meant one night of testing the drainer by hand, which is exactly the night nothing goes wrong,
   and then bolting the toggle on afterwards — invariant 6 says the switch is not an accessory.
2. **F8.4 and F10.2 are one unit, not a dependency chain.** Split, they produce a schema someone
   migrates and a lease someone else forgets. Merged, one module owns the row and the claim on it, so
   `claimed_by` cannot exist in a table that `claim()` does not know about.
3. **The circuit breaker was missing entirely.** The design had a resume budget (3 nudges) and nothing
   for the faster failure: an item whose `check_command` exits 1 in 0.2 s is claimed, released, retried,
   and retried — the loop looks extremely busy while it drains the weekly window, and a dirty working
   tree from the crashed run then poisons the *next* item too. Shipped tonight in `work_order.py`:
   `NIGHT_MAX_FAILURES = 3` and `NIGHT_MAX_ITEMS = 25` are counted over the current ON window,
   `claim()` refuses with the figure that tripped, and a working tree that is dirty — or unreadable,
   which is `UNKNOWN`, not clean — holds the queue until a person names the files.

Two of its suggestions were **refused**, and the refusals are written down because a review that is
only agreed with is not a review:

- It proposed `git checkout -- .` to clear a dirty tree before the next item. Refused: that discards
  whatever another engine was mid-way through writing, and invariant 3 puts uncommitted work of someone
  else beyond any tick's reach. The correct behaviour is to refuse the claim and escalate the filenames.
  (The same reasoning rejected keystroke injection on the first night the operator was asleep: F10.7's
  detector shipped at 02:10 and its `CONTINUE` is designed and deferred, not shipped, because a resume
  path that has never been watched over a shoulder is the same class of mistake as an off switch that
  has not been built yet.)
- It deferred F9.1 until F10 lands. Refused: F9.1 reads exit codes, process trees and pane deltas —
  deterministic, read-only, no dependency on the queue — and F10.7 needs exactly those measurements to
  exist. Parking it costs the one thing F10 is built on. F9.1 stays unblocked and parallel.

Order as it will be built: **F8.4 + F10.2** (✅ 01:00) → **F10.8** (🟡 01:30, switch shipped; expiry
and drainer still owed) → **F10.1** (🟡 01:44, broker shipped; nothing forces an engine to call it yet)
→ **F10.7** (🟡 02:10, the sense shipped; the hand — `CONTINUE` — deliberately not) →
**F10.3** → F10.4 / F10.5 / F10.6 / F10.9. The seeded queue encodes this through `depends_on`, so an engine
that skips the order cannot claim.

### Modules

- 🟡 **F10.1 Decision broker.** When an engine would ask the operator "option 1 or 2", it writes a
  `decision` row — question, options, evidence *per option*, and cost per option — then dispatches the
  resolver **role** resolved from `config/slots.yaml` at the moment of use. The answer is recorded in
  the same row, with the model id that actually answered.
  *Why the operator's instinct is right:* `slots.yaml` already names `architect` as a long-breath
  engine (`gemini-3.1-pro`) and `auditor` as Qoder, so routing a choice to the other pool is not a new
  direction — it is the team as configured, finally used for decisions instead of only for code.
  Quota pools are separate (measured in §4), so a decision spent there does not shorten the working
  agent's breath. Decisions are also the cheapest thing to delegate: a few hundred tokens.
  *Where it must not go:* independence. `decider`/`verifier` stay on the Claude tier for exactly the
  reason a peer review is worth something — the reviewer is not the reviewed. A resolver that both
  picks the direction and grades the result is not a reviewer. And invariant 3 outranks the broker:
  irreversible calls are never delegated to any engine, however much quota it has.
  *Honest constraint from measurement:* headless `agy -p` cannot approve tool permissions, so the
  decision must arrive fully evidenced in the prompt — the broker sends the file, not a pointer. One
  `gemini` dispatch was also measured as `TIMEOUT` by the harness while the pane had already answered:
  a broker that reads only the pipe loses real answers, so it reads the message table first.
  *Acceptance:* pose a two-option question with an empty queue; `ai-station decisions` shows the row
  resolved by a model id taken from config, the working engine's own quota untouched, and — if the
  resolver's pool reads `UNAVAILABLE` — the row lands `ESCALATED` rather than answered by someone else.

  **Standing order from the operator (Oct 8, 00:30), now contract §4:** a preference-level choice —
  "option 1 or 2", which of two designs, which queued item next — is **never** put to the operator.
  It is resolved by the `resolver` role and the answer is recorded. Only an item that crosses a named
  threshold waits for a human (F10.8 queue). The operator's stated reason is that the engine choices
  would come out the same anyway, and the point of the notebook is not to be consulted twice.

  The pool the operator named is `gemini` (`gemini-3.8-flash-high`, registered as `reviewer` in
  `cross_verify --list`, and it has its own tools so it can read code before answering). And on the
  fresh meter it is the right call — measured 00:33 the same night, read rather than believed:

  | pool | 5h left | weekly left | whose breath it competes with |
  |---|---|---|---|
  | Gemini Models | 0.9802 | **0.9792** | the `architect` role — the engine writing the code |
  | Claude and GPT models | 1.0000 | **0.6224** | nobody's; 38 % of it went to yesterday's audit dispatches |
  | Qoder add-on credits | — | 451 / 1500 used | the working agent (this engine) |

  So `gemini` has more weekly air than the verify tier does tonight, which is the operator's whole
  point. The one caveat worth keeping in writing: that air is the *architect's* air, so a long Gemini
  implementation session and a long Gemini decision queue draw from the same meter — which is why the
  order is a **ladder of three roles in `config/slots.yaml`**, not a hard-coded name: `resolver` →
  `gemini`, `resolver-alt` → `claude-sonnet` (a pool the architect does not touch), `resolver-local` →
  `local` (`qwen3.5:9b`: no quota at all, only VRAM, and the §7 gate still has to clear it). A meter
  that reads 98 % free tonight is not the meter that is free next week, and §12 forbids picking a model
  from memory — so the rung order is config, editable from the F8.3 face, and the contract text names
  roles only. Each rung skipped must be skipped as `UNAVAILABLE` with its own number, never silently.
  Verified as shipped (Oct 8): the three rungs parse under `active_team`, `master_data.py selftest`
  still passes 30/30, and `cross_verify --list` still resolves the registry — the rungs are role ids
  pointing at existing keys, so nothing was restated.
  The row half of this module is shipped with F8.4: a `decisions` table (question, options, weight,
  rung used, engine key, **model id that answered**, state) plus `work_order.py decide|resolve`, and
  `resolve` refuses `RESOLVED` without a model id — a decision attributed to nobody is the F8.2 failure
  shape again.
  Shipped 2026-10-08 01:44 (`33fee41`): the dispatch itself — `work_order.py broker`, reached by
  `decide --route` / `route <id>`. It stays thin: registry, quota gate and the pane reader already
  belong to `cross_verify.py`, so the broker adds only the ladder (read from `config/slots.yaml` per
  call), the answer contract, and the rule that an exhausted ladder lands `ESCALATED` instead of being
  answered by a substitute. `route --dry-run` prints the ladder and the exact prompt without sending
  anything, which is how the ladder gets checked before quota is spent.
  Three real defects, each found by running it:
  - **The pane echoes the prompt.** The parser read its own instructions: `ALASAN` captured the
    template line, and — far worse — the rules text contains ``PILIHAN: TIDAK``, so a pane that had
    only echoed the prompt would have been recorded as a resolver that *refused*. A conclusion
    invented from our own sentence. `answer_tail()` cuts at the last occurrence of the prompt's final
    line, and a regression check asserts the old reading was wrong.
  - **`resolve_decision` sliced `answer` unconditionally**, and the escalation path passes `None`.
    The first question no machine could settle would have crashed instead of reaching a person —
    precisely the failure this module exists to handle.
  - **`decisions.weight` is `VARCHAR(10)` in PostgreSQL**; `irreversible` is 12 characters. SQLite
    does not check lengths, so the fixture stayed green for the third time on a production-only bug.
    The DDL says 20 and `ensure_schema` widens a narrow existing column, reading the current width
    first so it never rewrites one that is already right.
  Measured: selftest 48/48, live 14/14 on `POSTGRESQL`; decision 3 (two options, evidence and cost per
  option) resolved in 30 s by `gemini-3.8-flash-high` through the `resolver` rung, model id read from
  config; decision 6 born `ESCALATED` under invariant 3 and visible in the Human Decide tab; deploy
  gates green.
  Remaining, and named so it is not mistaken for done:
  - Only rung 1 has ever been exercised for real. The skip-and-fall path is proven by test, not by
    quota.
  - **Nothing forces an engine to use this.** The standing order is honoured by convention plus
    contract text; the enforcement point would be a hook that turns an "option 1 or 2?" into a
    `decide --route`, and it is not built. Until then F10.1 is a door, not a corridor.
  - "Working engine's quota untouched" is evidenced by the dispatch *path* (pane → antigravity, zero
    Qoder calls), not by a credit delta: the Qoder credit meter returned no number tonight, and
    writing 0 there would have been fabrication.
- ✅ **F10.2 Task lease — the actual sync primitive** *(built inside F8.4, one unit — see the build
  order above)*. Two engines coordinating by chatting is two
  engines racing to read the same prose. Add `claimed_by` + `lease_expires_at` (F8.4 columns): one
  `UPDATE ... WHERE claimed_by IS NULL` claims the item, an expired lease is reclaimable, and the
  Station's team view shows who holds what. This is what "bekerja saling sinkron" has to mean, or it
  means duplicate work with nicer vocabulary. *Acceptance:* start the same item from both CLIs;
  exactly one proceeds, the other prints the holder's id and leaves no half-written file.
  Shipped (Oct 8): the race is won by the write, not by reading first — `claim()` is a conditional
  `UPDATE` followed by a read-back, so two CLIs claiming in the same instant cannot both pass a check
  they performed before the other landed. Lease is 900 s without a renewed lease and an expired lease is
  reclaimable by design. Measured against the live backend, not a fixture: `selftest --live` claims
  `selftest-live-<epoch>-A` as `qoder`, then `antigravity`, and the second is refused with the holder
  named in its own message. Remaining here: the Station's *team view* of who holds what — the lease is
  readable from the CLI today and invisible on the page until F8's reporting rows are built.
- 🟡 **F10.3 The drainer.** A systemd user timer runs a deterministic script — no LLM — which picks
  the next unclaimed `PENDING` item, runs its `check_command` before and after, and only then calls an
  engine to do the work. The gate stays the same one that blocks a human: `cargo test -q` and
  `call_workers.py --guard`. *Acceptance:* the timer's log for a whole night contains item ids, exit
  codes, and at least one item it refused to touch because its scope hit invariant 3.
  Two things about its place in the queue, both from the resolver's review: it is built **after F10.8**,
  because a timer that can start this loop needs a switch that can stop it first, and after **F10.7**,
  because a drainer that cannot tell a stalled engine from a finished one re-claims held work. That
  judgement is now owned: `heartbeat()` returns `WORKING` / `AT_REST` / `UNKNOWN` per open claim, and the
  drainer is the only caller allowed to turn `AT_REST` into a keystroke — it must not re-derive the two
  signals in the timer, because a second definition of "resting" is how a machine ends up typing into a
  session that was mid-sentence (§12). `resume_count` is incremented there, never in the sense.
  Its circuit breaker is already written and green, though — counted per ON window, enforced inside
  `claim()` rather than in the timer, so a human running the same commands tonight trips the same
  number. The queue also refuses to re-offer a concluded row: `FAILED` and `UNAVAILABLE` are skipped by
  `next_item()`, which was caught by `next` handing back legacy row 15 (`UNAVAILABLE`, an engine saying
  it could not run the item) as if it were fresh work.
  *Shipped (Oct 8, 02:58), hand deliberately closed.* `tools/drainer.py` is one deterministic tick:
  it reads the ladder (`drain_level`), the switch (`autopilot`) and the breaker (`night_state`) — all
  three derived, none re-implemented — then behaves at the level a human raised it to. `observe` reads
  and logs only; `dispatch` additionally runs an item's `check_command` through `run_grouped`
  (`CHECK_TIMEOUT` 240 s, its own process group, swept on success too) and writes the ledger from the
  measured exit code; `resume` additionally types `CONTINUE` to a claim that `heartbeat()` proved
  `AT_REST`. **Raising the ladder requires a controlling terminal**, and that was measured from inside
  the timer's own environment, not argued: `systemd-run --user` running `work_order.py arm resume`
  exits 1 with `/dev/tty tidak terbuka dari proses ini` and changes nothing. Lowering it never needs
  permission (invariant 6), so `disarm` works from a headless process and is now one of the proven
  `OFF_PATHS`. `MAX_RESUME` is enforced at the write, inside `nudge()`, following the breaker's
  precedent in `claim()`. Invariant 3 is a refusal *before* a process exists: a gate containing an
  undoable verb escalates the item to `HUMAN` and the selftest proves non-execution with a canary file
  (`touch … && rm -rf …`) that is asserted never to appear.
  Measured: `work_order selftest 98/98`, `selftest --live 31/31` on PostgreSQL, `drainer selftest
  43/43` (injected `beats`/`runner`/`sender`, zero processes, zero keystrokes, zero daemon calls, on a
  temp SQLite file — it never touches the live ledger). One real tick from `systemctl --user start
  irsofka-autopilot.service` wrote `{"action":"CLOSED_OFF","level":"observe","mode":"OFF"}` to
  `logs/drainer-2026-10-08.jsonl`; the units are installed but `is-enabled` reads `disabled`, because a
  timer that can start this loop is switched on by a person who is awake to read its log.
  Two defects found by measuring, both fixed: `items_done` was read from a column nothing ever
  incremented, so both surfaces displayed a fabricated 0 forever — it is now derived from the ledger
  through the ON window, and the window filter was wrong in *both* backends (`strftime('%s',…)` returns
  TEXT, and bare `EXTRACT(EPOCH FROM completed_at)` reads naive values as UTC, off by 25 200 s = the
  Asia/Jakarta offset; both now CAST/`::timestamptz` and asserted on the live backend). And F10.3's own
  seeded gate was `systemctl --user list-timers <unit>`, which exits **0 with an empty table for a unit
  that does not exist** — a gate that is always true is an invitation to mark work COMPLETED without
  doing it, so it now demands the installed file *and* an enabled timer, and `selftest` refuses to
  seed any bare `list-timers` gate again.
  Still owed, and it is not code: the acceptance is *a whole night's log*. That needs the operator to
  run `systemctl --user enable --now irsofka-autopilot.timer` and `arm dispatch` from a terminal, then
  read the log in the morning. Until then this bullet stays 🟡 and the hand stays at `observe`.
- ⬜ **F10.4 Proposal queue — the safe form of "improve forever".** The loop may *author* new roadmap
  items, never *adopt* them: proposals land as `PROPOSED` rows carrying the observation, the proposed
  check command, and the files it would touch. Bro approves by editing one column in the Station.
  This is also where the notebook's prompt-evolver idea becomes legitimate: it mutates candidates in a
  queue, and the contract stays written by hand. *Acceptance:* a proposal produced from a repeated
  failure shows up in `/api/decisions` with its evidence, and nothing it suggested has run.
- ⬜ **F10.5 Dream phase.** On idle ≥30 min (the notebook's file 1 idea, on a user timer instead of
  `pg_cron`): re-rank `world_memory`, compact stale session rows into handoffs, run
  `arch_map.sh --check` for documentation debt, and re-scan `incident_log` for a signature at 4
  repeats so the 5th one is already answered. *Acceptance:* one night of dreaming emits a digest row
  and changes no tracked file without naming it in the digest.
- ⬜ **F10.6 Whose breath is spent.** The operator's premise — "Gemini has long breath, Qoder's points
  are plenty and you barely cost anything" — must be enforced, not believed. F8.2 measured that
  Qoder exposes **no per-turn credit field**, so my own cost reads `UNAVAILABLE` to me; the panel Bro
  reads is the source, and I may not reason as if I had measured it. So the budget is expressed as
  *pool + window*, resolved from `config/engines.json` per role, and the autopilot states on every
  tick which pool it is spending. *Acceptance:* a tick that would exceed a configured window fraction
  goes `ESCALATED` with the meter's own numbers.
- 🟡 **F10.7 Heartbeat — is that engine working or resting?** This is the piece the operator described
  directly (Oct 8): when a CLI stops because a session got long, Rust and PostgreSQL must tell
  *"mid-task"* from *"at rest"*, and only the second one gets a `continue`. The daemon already owns the
  state it needs — `action_log` rows arrive per action, the pane is readable in ~3 ms, and
  `quest_tasks.claimed_by` (F10.2) says what is held — so the detector is a join, not a new sense.
  It requires **two independent signals agreeing** before it may send anything: an open claim, no
  `action_log` row for T minutes, and a pane whose text is not changing. Invariant 5 is the whole
  design: if the pane cannot be read, the state is `UNKNOWN`, the tick is logged as `UNKNOWN`, and no
  keystroke is sent. A resume budget per item (default 3) then parks it, because a task that needs
  four nudges is not asleep — it is stuck, and nudging it again is noise that looks like progress.
  *Acceptance:* stop a session mid-item on purpose and the daemon resumes it once, visibly, with the
  item id in the log; then make the pane unreadable and prove it sends nothing at all.
  Shipped as data only (Oct 8, earlier slice): `resume_count` and `due_epoch` columns, and `stalled()`,
  which lists candidates and says in its own docstring that a list is not a decision to type.
  Shipped (Oct 8, 02:10): `heartbeat()` — the sense. It takes two independent readings per open claim
  (pane text sampled twice, and the age of the engine's last `action_log` row) and returns
  `WORKING` / `AT_REST` / `UNKNOWN` **per claim**, with the reason string, both readability flags, the
  lease state and the remaining resume budget. `AT_REST` requires both signals to agree; `UNKNOWN` is
  what any single dead signal produces, and there is no path through the function that sends anything.
  Read live, not from a fixture: with item 32 held by this session it answered `WORKING`, "pane
  menampilkan spinner/esc-to-cancel", `pane_readable: true`, `quiet_seconds: 2`; run with the tmux
  socket pointed at a name that does not exist — the acceptance's "make the pane unreadable" — it
  answered `UNKNOWN`, "pane tidak terbaca — bukan berarti diam", and sent nothing, because there is
  nothing in it to send.
  Three defects found while building it, all of them the kind that only exists in production:
  **the word `heartbeat` had two owners** — lease renewal and this detector shared one name, and adding
  the second definition silently shadowed the first: for a few minutes of working tree the CLI's
  `work_order.py heartbeat <id>` was calling the *detector* with an item id as its `quiet_seconds`
  argument (§12's warning made concrete: two definitions for one word, and the file would have shipped
  that way had the collision not been noticed before the commit). Renewal is now `renew_lease()` /
  `work_order.py lease <id>`; no caller outside the file used the old name — checked across
  `tools/`, `engine-rust/src/`, `systemd/`, `bin/` and the docs before renaming.
  **`action_log`'s time column is `ts` on both backends**, but the SQLite branch of the stalled query
  read `created_at`, which is a `quest_tasks` column. Measured, not assumed: this machine has no
  fallback DB file (`brain/workstation.db` does not exist, PostgreSQL is primary), so a throwaway SQLite
  file was built from `session_ingestor.DDL_SQLITE` itself — the old query raises
  `OperationalError: no such column: created_at` there, the new one returns `(epoch, True)`, and because
  SQLite's `CURRENT_TIMESTAMP` is UTC the parsed epoch matched `time.time()` exactly; read as local time
  it would have been 25 200 s off. **A missing row and an unreadable table are not the same fact**:
  `_last_action()` now returns `(epoch, readable)`, because the old single value made "cannot read the
  ledger" look like "this engine has done nothing", which is the exact wrong conclusion — the wrong
  direction of the one error this module is not allowed to make.
  The independent audit (`cross_verify` → `claude-sonnet-5-5-high`, 02:05, verdict `PERLU_KOREKSI`)
  found two more of that same direction, both of which my own green selftest had *blessed* rather
  than guarded: a readable ledger with **no row under the claim holder's name** was treated as "quiet"
  (`claimed_by` and `action_log.engine` are two naming spaces with nothing guaranteeing they agree, so
  that None can equally mean "recorded as something else"), and two pane reads taken with `sample_gap=0`
  make "the text did not change" a tautology rather than an observation. Both now return `UNKNOWN`;
  `MIN_SAMPLE_GAP = 1` floors the production path while the injected test seam keeps its zero-delay
  reads; a `capture` that *raises* is now a dead reader (`UNKNOWN`) instead of a crash; a timestamp
  that parses to no number is reported as unreadable, not as "no action". The audit's claim that the
  no-keyskeystroke check was too narrow was also right — it read one function's source — so the check
  now reads the source of every function on the read path (`heartbeat`, `_pane_text`, `_last_action`,
  `stalled`) and separately asserts that the only names borrowed from `cross_verify` are the two
  readers, `capture_pane` and `pane_is_busy`. Its `InFailedSqlTransaction` worry was checked and does
  not apply: `db_state.py:71` sets `conn.autocommit = True`, and that reason is now a comment at the
  `except`, not a memory. `selftest` 65/65 offline — 18 checks
  that drive the pane through injected fakes, replacing the one placeholder that only asserted the
  function existed, so no test can touch a live terminal — and 19/19
  against live PostgreSQL, including an invariant check that reads the function's own source and fails
  if a keystroke path (`send-keys`, `send_to_terminal`, `ydotool`, `subprocess`) is ever added to it.
  What is **not** shipped: the hand. `CONTINUE` injection is deliberately absent — **No keystroke is
  injected yet, deliberately, on the first night this design existed** — the resume path has never been
  watched over an operator's shoulder, and F10.7 is the one module whose failure mode is a machine
  typing into a person's terminal. Built after F10.8 for that reason: a thing that sends `CONTINUE` must
  have a switch that stops it before it has users. Remaining: (a) the drainer (F10.3) is the only place
  allowed to act on `AT_REST`, and it does not exist yet; (b) `resume_count` is never incremented by
  anything, so the budget is reported but unenforced until the hand exists; (c) the acceptance's first
  half — "stop a session mid-item on purpose and the daemon resumes it once, visibly" — cannot be
  demonstrated until (a) lands, and it must be watched with the operator awake, not proved at 03:00.
- 🟡 **F10.8 Autopilot switch — ON means the machine is entrusted, not that it is unowned.** A toggle on
  the Workstation cockpit (`gui.html` is the control surface; the *state* of the toggle is recorded as
  a row so the Station can show who had the machine and when). The operator's framing is the spec:
  ON = this computer belongs to the AI and its ecosystem for the night; OFF = a human is at the desk.
  Four things make that survivable, and they are the non-negotiable part of this module:
  1. **Three off paths, none of which asks permission.** The GUI toggle, `ai-station autopilot off`
     from any shell, and `systemctl --user stop` of the drainer timer — the last one only once F10.3
     exists, which it did not when this was first written down. Invariant 6: if the loop is
     misbehaving at 03:00, waking it to negotiate is not an off switch.
  2. **Self-expiry.** ON carries a wall-clock boundary and an item count; either one reached ends the
     run and emits the morning digest. A switch that stays ON because everyone forgot is a background
     process running with the operator's identity.
  3. **The hands floor.** Autopilot ON grants tmux panes and headless APIs. It does **not** grant
     `ydotool`: moving the real mouse and typing real keys steals the desktop of a sleeping person,
     and a misclick into whatever window is frontmost at 03:00 is the one failure this ledger cannot
     undo — no `action_log` row restores a deleted file from an app that has no CLI. GUI control is
     allowed only for an item that explicitly claims the `visual_operator` team, and — with autopilot
     ON and nobody at the desk — such an item enters the human queue instead of clicking.
  4. **The human queue is for facts, not preferences.** The operator's own example, checked instead of
     assumed: "OS disk left 10 GB but must download `phi4:14b`". Measured tonight: **389 G free** on
     `/` (449 G total, 9 % used), VRAM **928 MiB used of 12 288**, and `phi4:14b` genuinely not pulled
     (`cross_verify --list`: server alive, catalogue holds `qwen3.5:4b`, `qwen3.5:9b`, `tev1:0.8b`).
     So that item does not wait for a human tonight — and that is precisely why the threshold is a
     measured number evaluated when the item runs, never a remembered number.
     *Acceptance:* with the toggle ON, walk away; the morning digest lists every item attempted, every
     item skipped and why, which pool each cost came from, and the queue contains only items whose
     measured resource line actually crossed its threshold.
  Shipped 2026-10-08 01:30 (`a7726cf`, `35f0c80`), split the way F8.3 split: `work_order.py` owns the
  vocabulary and every breaker number, `api/autopilot.rs` owns HTTP and the process boundary and copies
  no policy, and neither HTML page restates a rule — each figure on screen is read from
  `work_order.py status`. The Station's Human Decide tab shipped read-only at first, on the argument that
  F8's two-surfaces rule forbids controls there. That argument was retired the same night, because it was
  wrong in a specific way: a queue that shows a question with no answer column is not a record of a
  decision, it is a transcript of one nobody can make. The tab now carries the option list, the answer
  column and the switch as control, and each of them writes through `work_order.py`, which stays the only
  owner of every number and every rule — the boundary is now stated as §12.3.
  Remaining, and named so nobody mistakes the switch for the loop:
  - **Self-expiry is checked at claim time, not by a timer.** A window that lapses at 02:00 stops
    nothing until the next `claim`. Tolerable while no drainer exists to make claims unattended;
    F10.3 must re-read the window per item, not once per run.
  - **ON currently governs nothing.** The switch is honest — it says who held the machine, and for how
    long — but the drainer that acts on it is F10.3. The order was set the other way round on purpose:
    a loop with no off is the failure this module exists to prevent.
  - **`CONTINUE` injection is deliberately not shipped.** An engine that looks idle may be thinking.
  - The morning digest (F8.5) reads the same table but does not emit yet.
  Measured: `selftest` 32/32, `selftest --live` on `POSTGRESQL` 12/12; deploy gates green (cargo test 3
  passed, call-worker guard "0 left behind / 3 pane processes protected", arch map 75 files / 0
  undocumented); endpoint round-trip ON→OFF with both 400 paths refused; the 503 body forced by hiding
  `work_order.py` names only routes proven to exist; `ai-station autopilot` verified without the daemon;
  4 `autopilot_switch` rows reached `action_log`; both surfaces confirmed by screenshot.
  Later the same night, the answer path — where F10.1's queue and F10.3's hand meet. `decisions.item_id`
  links a question to the quest waiting on it and `decisions.answered_by` names the person who answered;
  `work_order.py answer` writes `ANSWERED` with `rung_used='human'` and refuses to overwrite an answer
  already given; and `finish()` on a `HUMAN` escalation now poses a linked question of its own, so an item
  that crosses a threshold arrives with something to answer rather than only a title. `POST /api/decide`
  runs that command and then `drainer.py deliver --decision N`, recording **before** sending: the answer
  survives any failure of the keystroke, and the reply carries the drainer's reason verbatim.
  `deliver_answer` is the only function on this machine allowed to type a person's answer into an engine's
  pane, it is reachable only from a click, and the drainer selftest enforces that by scanning the module
  function by function instead of banning tokens file-wide.
  Measured: `work_order.py selftest` 109/109 offline and 31/31 on `POSTGRESQL`, `drainer.py selftest`
  59/59; a live `POST /api/decide` returned 200 with the row read back `ANSWERED` / `answered_by`, and its
  delivery stage reported honestly that it was deferred because the target claim still read `WORKING`
  ("jawaban sudah tercatat, pengiriman ditunda — bukan digagalkan"); a second save on the same row
  returned 409 quoting the first answer's timestamp; an empty answer returned 400 and left the question
  open; `POST /api/autopilot` with `mode:"maybe"` and with `minutes:0` each returned 400 and left
  `mode OFF` untouched, while `mode:"off"` returned 200 and wrote `changed_by=operator-gui` plus the
  reason into the ledger. The click layer was then proven by the only hand allowed to use it: the operator
  answered three rows from the page at 07:00, each landing in PostgreSQL as `ANSWERED` /
  `answered_by=operator-gui`, and each producing a `drainer-2026-10-08.jsonl` line that refused to send —
  once because the question carried no `item_id` ("tidak ada pekerjaan yang bisa dibangunkan") and twice
  because the target claim still read `WORKING`. No answer was lost to a refused keystroke.
  Two of those answers are decisions, not tests, and they are binding: **ON may commit and push, but only
  after a verifier clears the work** (decision 6), and **the drainer's timer stays off tonight because the
  operator is back at the desk** (decision 19) — so F10.3's acceptance half, one night of unattended log,
  remains owed by explicit choice rather than by oversight.

- ✅ **F10.10 Dashboard — the Station becomes a per-project command center, and SEMI stops being
  a word without a mechanism.** Shipped 2026-10-08 10:50, commits `4df4a08`…`0d9df2e` + the verb fix.
  The operator's complaint was usable as written: *berantakan, tidak eye catching, harus scroll ke
  bawah sangat banyak* — and the cause was mechanical, not aesthetic: `.card` was constructed at eight
  places in `station.html` and had **no CSS rule at all**, so every card was a transparent div with
  browser-default headings on a dark background, inside a container that was `flex-direction:column`
  by construction. The file also had **zero `@media` blocks** and no spacing scale. It now has three
  columns, a real card, and a fold for the eight stacked diagnostic sections.
  Renamed from *Human Decide*, moved to the top of the nav, with `#decide` kept as a live alias so an
  old bookmark does not die because a tab was renamed; hash is now written back and listened to.

  **SEMI is a mode, not a level** — a third answer to *who owns the machine*, stored in
  `autopilot_state.mode` beside the switch, never in a second table. Its whole content is the basket:
  `origin='operator'` rows only, enforced twice — in `next_item(origin=…)` and again inside `claim()`,
  because a limit that only filters the queue is a limit the caller can skip. The night breaker and
  the dirty-tree rule apply unchanged; a selftest check proves a roadmap row is refused *by name*
  while an operator row is accepted in the same queue.

  **The trigger is the engine's own turn boundary.** Measured in `qodercli-1.1.65` before designing
  on it: `Stop` is a real hook event, `decision` accepts `"block"` (normalised to `"deny"`), and a
  blocking Stop hook returns `{blocked, inject, retryAsStopHook}` — so `hooks/semi_wake.py` can hand a
  queued command to the session that is already running without touching a pane, `ydotool`, or a
  timer. Blocking is only ever the consequence of a successful claim on a PENDING row, so an empty
  queue injects nothing and **SEMI cannot run away**: the queue is the loop guard, which is why
  `stop_hook_active` is deliberately *not* used to cap it (that would limit the operator to one
  command per turn). Proven live at 10:50: item 85 → `{"decision":"block"}`, a 4138-character prompt,
  `claimed_by=qoder`, and `logs/semi_wake.jsonl` naming the item.

  **Project identity got exactly one new word.** Three notions of "where work happens" already lived
  here — `REPO` (the only execution use being `drainer.py:133 cd {REPO} && gate`), `TabProfile.workspace`,
  and the `current_project` symlink Rust never reads — which is precisely the *confusing identitas* the
  operator warned about. `quest_tasks.ws_path` is the new one, NULL meaning the old behaviour exactly,
  and the gate now runs in the row's own directory: an engine working on one tree while its verdict is
  read from another is a report about someone else. `workspaces` is a registry that **never claims the
  focus** — `is_focus` is derived from the symlink at read time, and a selftest check asserts the table
  has no `active` column to drift out of agreement.

  Three defects were caught by looking at real data rather than by testing: `log(why, **rest)`
  collided with call sites passing `why=` so the hook's *refusal* path raised `TypeError`, and the
  crash handler had the same defect — a trigger that crashes while reporting a refusal is worse than
  one that stays quiet; guideline dedupe keyed on the *relative* path, so this project's `AGENTS.md`
  silently deleted the workstation's from the list; and the Dashboard's word `guidelines` did not
  match the CLI verb `guides`, so the first real write failed with argparse exit 2. All three fixed at
  the source, not papered over — the last by renaming the verb rather than adding a mapping table,
  because a mapping is where two words for one thing hide. Selftests: work_order 110 → 135, drainer 59 → 83.
  **The loop was proven on Antigravity first, at the operator's instruction, and it needed neither
  the timer nor a raised hand.** The recipe is documented by the CLI itself, in
  `engines/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md`: a `Stop` hook returning
  `{"decision":"continue","reason":…}` "blocks the stop and re-enters the loop". Same mechanism as
  qodercli's `block`, different keyword — and writing the wrong keyword produces a hook that runs,
  returns something, and continues nothing, which is a failure that looks like success. Antigravity
  also *reports* two things that used to be inferred from pane text, and both are now used:
  `fullyIdle` (honours "if it is Working, wait until it finishes" in the agent's own words) and
  `workspacePaths` (the project the agent is in, which is not the same as the shell's cwd).
  Measured 2026-10-08 11:27 with one real `agy -p` turn through `run_grouped`: the CLI fired the hook
  twice — first `SEMI_NOT_IDLE`, then `SEMI_INJECTED` for item 87 with `decision=continue` and 62
  lines of locked guidelines — while item 88, addressed to qoder, correctly stayed `PENDING`. Engine
  isolation is `next_item(for_engine=…)`, in the module that owns "what is next".
- ✅ **F10.11 The loop must not die quietly, and OFF stops being a mode.** The operator's boundary for
  tonight was explicit: an unattended run that stops the moment the machine goes idle wastes the
  window it was given, and he will not be at the desk to restart it. Four defects matched that sentence, and all
  four are now closed by measurement rather than by intent.
  1. **The breaker was miscounted, not mislabelled.** `night_state` reported *"kegagalan berturut-turut"*
     while its SQL summed **every** `FAILED`/`PARKED` row in twelve hours, so over a long night three
     unrelated failures (A fails, B and C finish, D fails) closed the loop permanently and the machine sat
     idle until morning. `consecutive_failures()` counts a *run* since the last success instead. Two
     boundaries it had to get right, both found by test rather than argument: `completed_at` resolves to
     one second, so ordering is `(timestamp, id)` — `>` loses a failure written after the last success in
     the same second, `>=` drags in every failure written before it; and `ORDER BY … DESC` puts **NULL
     first** in both backends, so one legacy `COMPLETED` row without `completed_at` became "the last
     success" with `e = NULL`, which Python read as `0` and turned into a breaker that closed on a machine
     that was working fine. The PostgreSQL branch of that query is only exercised by `selftest --live`, and
     it was the live run that caught it.
  2. **Nothing reached the operator.** Every tick ran, logged to a file nobody opened, and said nothing on
     the desktop. `tools/notify.py` is now the single owner of the banner and fires on exactly three
     things — work that actually arrived at a CLI, a question that needs a human, and an error or refusal
     that stops the loop — because a banner every five minutes trains a person to ignore the banners that
     matter. `drainer`'s selftest derives the emitted vocabulary from its own AST and fails while any name
     has no basket, so a new event cannot silently default to "safe"; it found `PANE_WRONG_DIR` unfiled on
     the first run. What it took to notice: `notify-send` **does not exist on this machine** (`which`
     empty, no `/usr/bin/notify-send`), and all three call sites swallowed the error — `let _ =
     Command::new(...)`, `except Exception: pass` — so the workstation's entire notification surface had
     been dead since it was written, and every test of it had passed. The path is now DBus
     `org.freedesktop.Notifications` via `gdbus`, the daemon's returned id is read back as the proof, and
     the banner was confirmed by screenshot.
  3. **A command waited for the timer.** *"begitu perintah masuk, backend harus langsung mengirim ke
     CLI."* `queue_command` now calls `drainer.deliver_now()`, which hands the row to the same
     `hand_new_work` gates the tick uses — no second rulebook — and when the hand is still `observe` the
     row stays PENDING and the refusal comes back named (`HAND_FLAT`) instead of as a successful-looking
     "queued". Proven by a test that never calls `tick()` at all.
  4. **OFF was a mode nobody used.** Two positions, not three: `MODES = ("SEMI","ON")`, default `SEMI`,
     and `off`/`stop` demoted to *words that lower the hand and close the window* (`STOP_WORDS`) — the
     brake survives, the third value does not, so §12 keeps one word per state. Boot stays inert by
     structure, not by a mode value: the timer is `start`ed and never `enable`d, and `level` is born
     `observe`. The Rust route stopped carrying its own copy of the word list, which is the copy that
     would have gone on rejecting `off` for weeks after the machine stopped knowing it; it validates shape
     and forwards, and a refusal now exits non-zero so no caller can read a rejected write as success.
  Measured acceptance: `work_order selftest 178/178`, `selftest --live 34/34` (PostgreSQL), `drainer
  selftest 138/138`, `notify selftest 24/24`, `soak_watch selftest 10/10`, `arch_map --check` clean, and a
  30-round tick soak against the live ledger with injected hands — 0 rounds silent, 0 exceptions, 0
  keystrokes. Live proof of the delivery path: item 143 queued to SQL, refused by the dirty-tree brake,
  then `drainer wake --item 143` handed it to the Antigravity pane with `level` still `observe`; it
  answered, ran five commands and closed the item.
- 🔶 **F10.9 Push gate — the answer to decision 6 has to stop being a sentence.** The operator allowed an
  unattended run to commit and push *on condition* that a verifier clears the work first. The mechanism now
  exists and is bound per commit: `work_order push-gate` refuses unless every unpushed commit is
  *declared* — cleared by a `push_verified` event naming its sha with a passing verdict, or held by a
  `push_held` event with a reason. `record_push_verified` no longer takes a verdict as an argument; it
  reads one from the `cross_verify` rows in `action_log`, so an engine cannot write its own clearance.
  `record_push_held` supplies the other half, which had no emitter at all.
  Two defects were found by independent auditors and closed: the gate was two global `grep -q` over the
  event file (permanently satisfied once both kinds existed) and it never read the *value* of `verdict`.
  Historical commits are reported as a count, not as failures — a permanently red gate is a gate people
  walk past.
  *Acceptance still owed:* one commit reaching `origin` with a verifier row naming the model that cleared
  it, and one commit deliberately unverifiable and still local at dawn, both visible in the event log and
  neither produced by a human hand.

## F11 — Unreal bridge, MCP ownership, and what to publish ⬜

Opened 2026-10-15 by the operator, on two questions put to him about the UE 5.8 plugin: whether it
should be shared at all, and which licence satisfies public demonstrability, protection against a
re-attribution of his work, and a commercial path if one appears. The decisions
below are recorded here so a later session does not re-derive them or quietly reverse them.

### Already done in this feature

- **`mcp/` exists as the explicit map** (`mcp/README.md` + one README per server): where each server's
  code lives, who owns it, what it is licensed under, and how many tools it really serves.
  Registration files stay per-machine and untracked (`git ls-files` verified).
- **The UE plugin's personal-path defect is fixed.** `IRSofkaBYOKManager.cpp` carried one user's
  absolute config path twice — a read fallback and, worse, a mirror write inside `SaveConfig()` whose
  return value was discarded, so on another machine keys silently stopped being shared. Both now
  derive from the home directory (matching `<home>/.ai-station`, the convention every `tools/*.py`
  uses) with `AI_STATION_BYOK` as the override and no mirror at all when neither is set; a failed
  mirror is now reported instead of swallowed. Verified: zero personal absolute paths remain in
  `Source/`, `Content/`, `Config/`.
  *Still owed:* rebuild `IRSofkaCore` and restart the editor. The change is inert until UBT runs, and
  a running editor must not have its module swapped from an automation loop.

### License decision: BSL 1.1 for the plugin and any irsofka-owned MCP server

Chosen over PolyForm Noncommercial, Apache-2.0 and a private EULA, because it is the only one that
satisfies all three of the operator's demands at once. Terms as read from the
[SPDX BUSL-1.1 text](https://github.com/spdx/license-list-data/blob/main/text/BUSL-1.1.txt) — the
[mariadb.com/bsl11/ page](https://mariadb.com/bsl11/) is the licence's home but renders its covenants
flattened (see the correction below), so it is background here and not the transcription source. BSL 1.1
restricts **Production Use** by default,
the Licensor may add an **Additional Use Grant**, and on the **Change Date** the work converts to an
open license whose admissibility is set by covenant 1 (GPL-v2-or-later compatible). The four years is
**not** a covenant — covenant 3 only says "To specify a Change Date." The figure comes from the second
Terms paragraph, which converts the work on the Change Date **or its fourth anniversary of public
availability, whichever comes first**: a longer date is permitted and simply loses that race ([fossa overview](https://fossa.com/blog/business-source-license-requirements-provisions-history/),
[Wikipedia](https://en.wikipedia.org/wiki/Business_Source_License)).

| requirement | how BSL answers it |
|---|---|
| attribution cannot be stripped by a re-upload | copyright never transfers; notices must be retained; re-releasing the work as someone else's is infringement on the text, not on interpretation. Each version carries its own Change Date formula, so the conversion clock is fixed by the licence rather than argued later — and it has not started: no version of this work has been made publicly available, the dates run from that event, and the commits carrying this text were still unpushed when it was written |
| the work must remain demonstrable to the public | source is readable from day one, and the first Terms paragraph already grants copy, modify, derivative works and **non-production** use — a visitor reads and runs it without asking. The Additional Use Grant is the lever for the opposite case: admitting limited **production** use at no charge, the pattern NVIDIA's `nsfc-comfy-ui` grant uses. Leaving it at `None` is the current answer, and it is a choice, not an omission |
| a commercial path must exist without renegotiating terms | Production Use *requires* a licence from the licensor, so the sale is the licence itself and the published terms do not change when one happens |
| the escape hatch | Change Date (4 years, per version) converts it to the MIT License, so the asset still becomes open even if the business never sells a licence |
| admissibility of the Change License | covenant 1 accepts GPL-v2 **or any later version, or a licence compatible with either**. MIT qualifies under both readings; **Apache-2.0 qualifies too** (compatible with GPL-3.0), so it was never disqualifying — made and retracted on the **same day**, 2026-10-15. Commit `157aa74`'s
title says "a week ago", which is wrong; it is left as written and corrected here |

#### Correction: the licence text I shipped was a scraped page, and the page is flattened

The first `LICENSE` was copied from the rendered **mariadb.com/bsl11/** page. That page presents the
*Covenants of Licensor* as two unnumbered paragraphs, and in doing so fuses three covenants into one
sentence: *"…or (b) insert the text "None" to specify a Change Date. Not to modify this License in any
other way."* Read that way, `"None"` — which belongs to the **Additional Use Grant** — becomes an
instruction about the **Change Date**, and covenant 3 ("To specify a Change Date") and covenant 4 ("Not
to modify this License in any other way.") cease to exist as separate terms. Copying it would have been
modifying the License in exactly the way covenant 4 forbids, in a file whose whole job is to be the
licence.

Found by the independent push audit on 2026-10-15 (verdict `PERLU_KOREKSI`), not by me: I had checked
the file against the page it came from, which is the weakest possible confirmation of a copy. The
canonical numbered text is in SPDX (`license-list-data/text/BUSL-1.1.txt`), which carries four numbered
covenants and the `Change Date` covenant as its own item.

Both repositories' `LICENSE` files were then rebuilt **by insertion only**: the SPDX text is taken whole,
the `Parameters` block is inserted after the header, and the `Notice` block MariaDB's page carries is
appended. Nothing is sliced and rejoined — an earlier attempt that split the text at its own headings cut
the sentence *"as long as you comply with the Covenants of Licensor below"* in half, which is how the
insertion-only rule got written instead of hand-edits. Verified afterwards by line-set comparison: **0
canonical lines missing** from each file, licence text copyright and trademark lines retained, and only
`Licensor` / `Licensed Work` / `Additional Use Grant` / `Change Date` / `Change License` filled in.

The rule this leaves behind: a licence is transcribed from the license-list entry or from a real
adopter's `LICENSE` file, never from a rendered web page, and the transcription is checked by diffing
against the source rather than by reading it back.


Cost, stated plainly: **BSL is not OSI-approved open source**, so some contributors, distributions and
enterprise legal teams will refuse it. If adoption breadth ever outranks the sale, the switch is
Apache-2.0 — that trade is the operator's, not an engine's.

Reversed the same day by the operator, on the ground that the four-year conversion makes the
downside of a strict licence small; the instruction was recorded as "BSL for all of it", so
**the whole published family is BSL 1.1**: `irsofka-ai-workstation`, the UE plugin, and all three MCP
repos. Nothing is left on PolyForm Noncommercial; `README.md`, `INSTALL.md` and
`THIRD_PARTY_NOTICES.md` were changed with it, and each release still carries its own Change Date, so
new work never "un-converts" old work.

**Change License: MIT — and the reason I first gave for it was wrong, so both steps are recorded.**
Apache-2.0 is indeed not compatible with GPL-v2 — that part is true, and it was **my own** sentence in
commit `62d443c`, not somebody else's. What round two of the audit corrected was the inference I drew
from it: that non-compatibility with v2 disqualifies it as a Change License. Covenant 1 admits the GPL "Version 2.0 **or any later
version**, or a license that is compatible with GPL Version 2.0 **or a later version**", and
Apache-2.0 is compatible with GPL-3.0. So Apache-2.0 was always admissible, and "the Apache option
would have made our own licence non-compliant" is a sentence I should not have written. MIT is still
what we ship, for the honest reason: it is compatible with GPL-v2 *and* v3, so it satisfies covenant 1
under either reading, and it gives the same end state the operator asked for — fully free code after
four years. Choosing among admissible Change Licenses is a business call for the operator, never an
engine's. A separate repository is still required
for anything AGPL-derived, because AGPL forbids exactly the additional restriction BSL imposes.

### "Make the architecture complicated so it cannot be rebuilt" — recorded as rejected

The proposal was to obfuscate deliberately: hard to recreate, hard to claim, still functional. It is
rejected on this workstation's own evidence, not on taste. **Every real defect found in this session
was found because code was readable:** a spend gate comparing against a string no row contained (i.e.
an open gate), a cost check bypassable by putting a flag before the subcommand, a `sent=True` that
would have invited a paid retry of an already-billed request, a config-loss path that failed *open*,
and a fix that was never executing because a **second `def rules()` shadowed the guarded one** while
16 selftests reported green. Unreadable code does not hide secrets from a copier; it hides defects from
the author and from the auditors — and the auditors here are the operator's own engines, which read
this repository as their working material.

What actually resists re-implementation is not obscurity but **not shipping the parts that make it
work**:

- **Publishable** (BSL): the UE plugin — in-editor hands, 6 master domains, transaction sandbox,
  socket bridge; and any irsofka-owned MCP server surface.
- **Never distributed**: the ledger and its rules (`work_order`, `drainer`, `awakener`, the
  hand ladder, night breakers), `config/slots.yaml` and `engines.json` role resolution, the verifier
  tier and its per-model quota gating, `gpu_budget.json` thresholds, and the measured knowledge in
  `brain/skills/` and `brain/memory/` — including hard-won negatives such as "decimate worsens an open
  mesh" and "weld by position before judging topology".
- The asymmetry is the moat: the plugin without the loop is a good editor bridge; the loop without
  the plugin cannot touch a live graph at all, because `GEditor`, `ResetLoaders`, `UPackage` and
  `UEdGraphNode` are only reachable in-process on the game thread. **No architecture measured on this
  machine removes that constraint, Epic's own included** — the experimental `ModelContextProtocol`
  plugin in this engine is also an in-editor process. That is an observation about what is installed
  here, not a proof about every design nobody has looked at; step 1 exists to widen it.

### Capability negotiation (the operator's item 5), as an acceptance test

One codebase, three states, and the plugin must never assume ai-station exists:

1. **plugin alone** — full in-editor capability, own BYOK file in `ProjectSavedDir()`, no bridge.
   This state must keep working after every future change; it is the fallback, not the toy.
2. **plugin + ai-station detected** — bridge active: ledger events, verifier verdicts,
   `ai-station recovery`, VRAM handshake.
3. **ai-station without the plugin** — headless only: cook, commandlets, build/UBT, config edits.
   It must refuse to *claim* live Blueprint editing; an unavailable engine reports `UNAVAILABLE`.

### Shipped first because it was the only part that could be finished in one sitting: the ComfyUI repo

Built 2026-10-15 at `~/Apps/mcp/irsofka-comfyui-mcp` (own repo, BSL 1.1). Written against ComfyUI's own
HTTP API on `127.0.0.1:8188`, standard library only — and that last promise is *checked* by an AST scan
inside the selftest rather than asserted in prose, because a third-party import would quietly put a
licence in the mouth of a server that claims to have none.

**What "clean room" does and does not mean here, stated because the first version of this section
overclaimed it.** No code was copied, translated or adapted. But `comfy-mcp`'s `server.py` was read
earlier in the same session — to count its `@mcp.tool` definitions for the tool-count correction in
`mcp/README.md` — and its `LICENSE` and `NOTICE` were read too. So "no upstream MCP code read" is
false, and the strict sense of clean room (implementers never see the original) is not what happened.
What is true is the weaker claim, and it is now **measured** rather than asserted: the implementation
was written from ComfyUI's documented HTTP behaviour against a live server, and a line-level
comparison of our `server.py` against the installed `comfy_mcp` package (11 files, 6 888 significant
lines) finds **7 identical normalised lines out of our 654** — every one generic Python boilerplate
(`from __future__ import annotations`, `except Exception as exc:`, `if __name__ == "__main__":`,
`if not isinstance(data, dict):`) — with a **longest run of consecutive shared lines of 1**, and **none**
of our private identifiers (`tool_*`, `graph_text`, `GATE_TEXT_CAP`, …) present in their source.
107 of our 202 long identifiers do overlap theirs; that is HTTP/JSON vocabulary and proves nothing
either way. The wording above it was wrong and my own number refuted it: seven shared lines means **no
substantive line of theirs is in `server.py`**, not **no line** — the shared ones are
`from __future__ import annotations`, `except Exception as exc:` and `if __name__ == "__main__":`,
which every Python file of this shape contains. Exposure happened; derivation was looked for **by this
    method and this method did not find it** — which is not the same sentence as "there is no
    derivation", and the difference is the limit of the tool rather than a hedge
found. Anyone weighing the AGPL
question should read it as exactly that and no more — including the limit of the method, which a
line-level comparison cannot see a translation that kept the structure and renamed everything.

The licence files were held to the same rule, and my first check had to be replaced to satisfy it: the
written rule says the transcription must be **diffed** against the source, but what I actually ran was a
line-*set* membership test ("0 canonical lines missing"), which cannot see a reordering or a duplicated
line. A positional `SequenceMatcher` over the SPDX span now reports, for **both** repositories: canonical
66 lines, ours 66 lines, **ratio 1.00000, 0 differing blocks**.

**What this server does is gate a submit before it is sent, instead of queueing first and apologising
later.** No comparison of other servers' submit paths was performed, so that is a description of `server.py`
and not a claim about theirs. `comfy_queue` calls
`tools/gpu_gate.py` before `POST /prompt`, so the same discipline F11 step 4 asks of the plugin is
enforced at the boundary the operator actually crosses — the MCP call — and not only at the scheduler.
**This is not step 4 complete and must not be read as such:** step 4's own gate is one TRELLIS.2 run that
finishes without OOM, and every TRELLIS submit here has been refused (9966 MiB wanted against 7498 free),
so that gate has never been reached, let alone passed.

| gate | measured this machine |
|---|---|
| refused | 3D submit: rule `3d-trellis`, need 9966 MiB, free 7498 MiB, **shortfall 2468 MiB**, holders named (`UnrealEditor 2118MiB`, ComfyUI's own `python 1624MiB`). `/queue` re-read afterwards: running 0, pending 0 — the refusal blocked the wire, it did not fail downstream |
| allowed | image submit: rule `image-diffusion`, need 5919 MiB against 7241 MiB free → `prompt_id 166cbff0…`, done in **21.0 s**, output 1 290 126 bytes `PNG 832x1216`, sha256 `2ea33e87…e4e5` |
| both directions on a fake transport | **61/61** selftest checks, including "a DENY sends nothing" and "an absent gate refuses heavy work and stamps light work `unaudited`" |

Three traps were found by measuring instead of assuming, and are encoded as tests:

- `GET /object_info/<unknown node>` answers **200 with `{}`**. Trusting the status code reports a node
  that does not exist as installed. It is the body that decides.
- The whole catalog is **1 951 939 bytes across 976 classes** — one careless call costs more context
  than an agent has, so the tool returns a *page of names* or a named node, never the catalog.
- A UI-export workflow (`nodes`/`links`) is not the API form `POST /prompt` wants. It is refused here
  with its own error name; posted, it produces **500 "Server got itself in trouble"** (measured on this
  machine 2026-10-15 with an empty UI export, which queued nothing) — an error that names neither the
  format it got nor the format it wanted, which is the whole reason the refusal happens locally.

One fact worth keeping so nobody re-derives it: a valid API graph does not have to be hand-written.
`GET /history` returns the graph that produced each finished run inside `prompt[2]`, which is how the
ALLOW-path test above got real node wiring without guessing a single input name.

**Not done:** registration in either CLI's MCP config (a per-machine act, and loading a new server
changes what the other engine sees), and `python -m build` — this system Python has no `setuptools` and
`python3 -m venv` cannot seed one offline, so the wheel is unverified and the README says so. The
`mcp/irsofka-comfyui-mcp/README.md` pointer carries the same numbers.

### The build command that lies: `Build.sh` reports Succeeded while compiling zero plugin files

Measured 2026-10-15, and it invalidated a claim I had already made in this document. Three commands
(`Build.sh IRSofkaEngineEditor … -Project=…`, `Build.sh UnrealEditor …`, and the same with
`-ForPlugin=…`) all return `Result: Succeeded` while performing **no compile action on the plugin at
all**. Not a stale-cache illusion: touching an existing plugin source and rebuilding still yields
nothing, and the target receipts settle it — 3186 build products in `IRSofkaEngineEditor.target`,
7345 in `UnrealEditor.target`, and not one is an IRSofka module. The plugin is simply not in either
graph.

The route that does build it is `RunUAT.sh BuildPlugin` run **from the repo** (and note it lives in
`BatchFiles/`, not `BatchFiles/Linux/`; piping UAT through `| tail` hides a missing-file failure
behind `tail`'s exit code 0). First run here: `BUILD SUCCESSFUL`, `UAT_EXIT=0`, 37 build actions of which **29 were compiles**,
45.29 s, seven module `.so`, and `nm -DC --defined-only` finds **8 defined `FIRSofkaCppExporter` symbols** in
`libUnrealEditor-IRSofkaBlueprint.so` — `MapPinType`, `CollectShape`, `HandleExport`, `RenderHeader`,
`RenderSource`, `MapEventOverride`, `SanitizeClassName`, `FShapeMember::PropertySpecifiers`. Present in
the binary, not merely accepted by a compiler. The method is stated because the same file answers **11**
to a plain `grep -o` count: three of those are *undefined references* from other translation units, not
exports. A number without its measuring rule is a number someone else will contradict.

What that first real compile found is the reason this section exists, and the counts are from the
three failing build logs, not from memory: **six distinct errors in the new exporter** —
`CPF_EditAnywhere`, `CPF_EditInstanceOnly`, `CPF_BlueprintReadWrite` (none exist in 5.8; the real
combination is `CPF_Edit` plus `CPF_DisableEditOnTemplate`/`CPF_DisableEditOnInstance`, and
BlueprintReadWrite is `CPF_BlueprintAssignable`), `UEdGraphPin::PinDisplayName` (it is
`PinFriendlyName`), `UBlueprint::EventGraph` (it is an `EventGraphs` array), and `FText::IsNone()`
(only `IsEmpty()` exists) — and **four in the BYOK path fix recorded above as "fixed pending
rebuild"**: `FPlatformProcess::GetEnvVariable` and `HomeDir()` (5.8: `FPlatformMisc::GetEnvironment-
Variable` and `FPlatformProcess::UserHomeDir()`), the include written as `HAL/PlatformFilemanager.h`
(wrong capitalisation, so nothing had ever compiled that file), and then a second wrong include of my
own, `Misc/PlatformMisc.h` for `HAL/PlatformMisc.h`. The fix was real but unverified; "pending rebuild"
was doing a lot of work in that sentence, and the rebuild disagreed with it.

Also caught by reading rather than building: my first `EditAnywhere`/`EditInstanceOnly` condition was
inverted, and it compiled happily. A build proves a file parses; it does not prove the flags mean what
the code says.

#### The rebrand collision, stated as a decision with two open answers

`projects/IRSofka-UE-AIStation` (`IRSofkaStation.uplugin`, 26 modules) and the installed
`IRSofkaStationCore` (7 modules) both declare a module named **`IRSofkaCore`** — measured by parsing the
two `.uplugin` files, intersection `{IRSofkaCore}`. UE requires unique module names, so the two cannot be
loaded together as they stand. That is a collision to resolve, not a dead end, and both real answers are
open: rename one module, or retire the installed plugin and deploy the rebrand in its place. Each changes
what the operator's live editor has loaded (pid 532402 holds the port-9877 binary), so neither is an
engine's call.

Measured further on 2026-10-15, and the collision turned out to be worse than "cannot load
together" — and asymmetric, which matters for choosing:

| build | result |
|---|---|
| `RunUAT BuildPlugin` on the **rebrand** (26 modules) | **fails in 3.86 s, 0 compile actions**, six errors of the form `Module 'IRSofkaBlueprint' (Marketplace) should not reference module 'IRSofkaCore' (Plugin). Hierarchy is Plugin -> Project -> Marketplace -> …` |
| `RunUAT BuildPlugin` on the **installed** plugin, re-run right after the failed attempt, same machine |
  `BUILD SUCCESSFUL`, 46.39 s — the same build earlier in this section took 45.29 s; both are real, |
  they are two separate runs, and the figure is labelled by which one it came from |

So the two trees do not merely refuse to load side by side: while both are present, **the rebrand
cannot be built at all**, because UBT resolves the installed Marketplace modules' `IRSofkaCore`
reference onto the staging Plugin-tier module and the hierarchy rule forbids Marketplace → Plugin.
The installed plugin is unaffected. Any "rename one module" answer to #51 therefore has to say
*which* side gets renamed, because the pain is not symmetrical.

The rebrand is also not the smaller project: its `IRSofkaBpToCpp` is 783 lines across 8 files with a
154-line type-mapper self-test that its history records as passing **23/23 in a deployed Linux build**
on 2026-10-10 — evidence that a build of it succeeded at some point, on some layout, which is not the
layout on this machine today. Against that, the exporter written here (626 lines) is compile-verified
only, with no runtime test. Choosing between them is a real comparison, not a default to the newest
file.

A second finding belongs to the same decision and must not ride along silently: the rebrand's
`IRSofkaCore::StartupModule` calls `AutoEnableCompanionPlugins()`, which force-enables **17** companion
plugins through `IProjectManager::SetPluginEnabled` in *whatever project loads the plugin* (the 17 names
are in its source; the call is absent from the installed copy). Item 225 records that behaviour as a
success. Whether a plugin should rewrite a project's configuration on load is the operator's answer, not
a default to inherit.

### Ordered work, each with a numeric gate

0. **Measured defects in the live bridge — found by calling it, not by reading it (2026-10-15).**
   `brain/skills/ue5-station/07-live-bridge-measurements.md` carries the repro; the two that block
   everything else are: **transform does not round-trip** (`spawn` echoes the requested `[0,0,200]`,
   `get_transform` reads `[0,0,0]` after two separate `set_transform` calls that each returned
   `SUCCESS`, scale included), and **`spawn` cannot prove the class it made** (`StaticMeshActor`
   reported as `"class":"Actor"`). Until the first is fixed, no placement this bridge performs is
   verifiable, which makes every "I moved the actor" report from any engine on this workstation a
   claim rather than a measurement. `ue_vision capture` also returns **246 504 base64 characters
   (~61 600 tokens) for one 1800x746 frame**, over the MCP client's 25 000-token cap, and `frame`
   reports no camera position — so the visual loop costs a file round-trip and cannot be used to
   cross-check a transform either. Gate to close: the repro above run again with `get_transform`
   returning the values that were set, and `capture` offering a `save_to` + sha256 instead of inline
   base64. Owner: whoever rebuilds the plugin — the editor holds the binary, so this needs the
   operator's editor closed.

1. **Measure before claiming** — **ANSWERED 2026-10-15, and the answer is the opposite of what I
   first wrote.** The claim "ours is more powerful" is **refuted by the count**, and the count is
   reproducible: `python3 tools/measure_epic_surface.py`.
   Engine-wide, `UToolsetDefinition` subclasses on this install: **37 declarations, 25 shipped, 12
   under `Tests/`, `Mocks/` or named `UFake*`/`UMock*`**. Those 25 declare **224 `UFUNCTION`s**
   (`Experimental/Toolsets` 218, `ToolsetRegistry` 4, `AIAssistant` 2), and the top of that list is
   exactly the territory this plugin does not enter: `UPCGToolset` 30, `UUMGToolSet` 23,
   `UDataflowAgentToolset` 22, `UEditorAppToolset` 21, `UPhysicsAssetToolset` 17, `UPluginToolset` 17,
   `USlateInspectorToolset` 14, `UMVVMToolset` 9, `UConfigSettingsToolset` 8, `UGameplayCueToolset` 8.
   Ours: **6 tools, 38 named actions** counted from this pack's own `SKILL.md` table (`ue_world` 16,
   `ue_blueprint` 8, `ue_material` 6, `ue_media` 6, `ue_vision` 2, `ue_pipeline` 0 — it takes an array
   of the others as input). By surface area Epic is roughly six times ours and covers PCG, UMG,
   Dataflow, physics assets, plugins, Slate, config and GAS.
   The first version of this line said Epic ships two toolsets with seven functions, none of which
   edits an asset. That was produced by `grep */Source */*/Source`, whose glob silently skipped
   `Experimental/Toolsets/*` — where 23 of the 25 live. A glob that matches nothing does not report
   "nothing found", it reports a smaller world, and the smaller world flattered the claim I wanted to
   make. The audit found it by reading the raw grep output rather than the sentence.
   What is still true, and what step 1 was actually for: the 224 figure counts `UFUNCTION`
   declarations in toolset headers, which is an **upper bound on callable tools** — the registry
   decides what is exposed, and some of those functions are helpers. The comparison that survives is
   therefore about *surface*, not about quality. Whether ours has any capability Epic's set lacks
   (the transaction sandbox and the Linux socket bridge are the candidates) is a separate measurement
   and is **not claimed here**; it needs the same treatment: enumerate Epic's, look for the
   equivalent, and report the absence as a search that found nothing rather than as a fact.

2. **Test `ResolvePinFuzzy` on PCG Graph and GAS** — it exists (`IRSofkaBlueprintSubsystem.h:29`) and
   is proven on Blueprint graphs only. Gate: fuzzy-hit rate before/after on a fixed pin-name corpus.
3. **Wire rollback to a `DISPUTED` verdict** plus asset hashes before/after an operation. Gate: one
   deliberately-failed operation leaves `.uasset` byte-identical.
4. **VRAM handshake** (`quiesce?` → `ready-to-load`) joining `tools/gpu_gate.py`. Gate: VRAM figures
   before/after and one TRELLIS.2 run that completes without OOM.
5. **State Handover last.** Stable node/package identity emitted into the existing `action_log`; no
   second database client in C++ — the ledger has one owner (§12). It needs the event vocabulary from
   steps 3–4, so doing it first guarantees a schema that gets regretted.
6. **Fork decision, open:** the current `mcp-blender` is **AGPL-3.0-or-later since v0.4.0** with its
   commercial option **withdrawn** (commit `8f64128`), while [`ahujasid/mcp-for-blender`](https://github.com/ahujasid/mcp-for-blender)
   is **MIT, 30,872 stars, pushed 2026-10-06**, and `artokun/comfyui-mcp` **MIT (825, pushed
   2026-10-14)** and `joenorton/comfyui-mcp-server` **Apache-2.0 (409)** exist as permissive ComfyUI
   paths. A permissive base is the only route to a relicensable derivative; the AGPL fork is a one-way
   door. Not started — the operator chooses.
   Note also that the [Blender Foundation ships its own MCP server](https://www.blender.org/lab/mcp-server/)
   and states on that page that it runs LLM-generated code "without any guards in place to protect your
   data" — which is the sandbox and transaction work above, expressed as their gap.


### Pedoman repositori: satu repo utama, tiga server MCP, dan mana yang harus ditulis bersih

Dipulihkan dari SQL pada 2026-10-15 21:1x, karena instruksinya hanya ada di percakapan
(`session_turns` 450 dan 457/458, 2026-10-15 15:26) dan belum pernah menjadi aturan tertulis; tabel
di bawah ini adalah bentuk tertapisinya, bukan transkrip.

| repositori | isi | dari mana kode ini boleh datang | mengapa |
|---|---|---|---|
| `irsofka-ai-workstation` (repo utama) | daemon, GUI, ledger, `tools/`, aturan | milik sendiri sepenuhnya; tidak ada bersih-bersih yang diperlukan | tidak ada basis pihak ketiga yang diturunkan |
| IRSofka Comfy MCP | pelayan MCP yang bicara ke ComfyUI lewat HTTP `127.0.0.1:8188` | **bersih secara fungsi**: ditulis dari perilaku HTTP terdokumentasi, bukan dari penerjemahan kode pihak mana pun | `comfy-mcp` ber-AGPL-3.0-or-later; menulis ulang dari perilakunya menjaga kita tetap di luar ranah turunan |
| IRSofka Blender MCP | pelayan MCP untuk Blender, dimodifikasi berat untuk workstation | **WAJIB basis permissif** (`ahujasid/mcp-for-blender` MIT, atau `RFingAdam` v0.3.0 MIT): bukan `mcp-blender` v0.4.x yang AGPL | lisensi turunan mengikuti basisnya, dan AGPL tidak bisa dicabut di belakang; ini pintu satu arah |
| IRSofka Workstation MCP (jembatan mesin grafis) | sisi mesin dari jembatan editor/UBT/komandolet; plugin UE tetap artefak terpasang, bukan bagian repo ini | milik sendiri; satu-satunya batasan adalah tidak ada konten Epic yang ikut ter-paket | "Unreal Engine" milik Epic dan plugin ini menyinggung header-nya; yang dijual/dipamerkan adalah kode jembatan kita |

Tiga keputusan yang mengikat bentuknya, masing-masing dengan angka tempat ia diukur:

1. **Tidak ada git submodule untuk kode yang akan banyak ditulis ulang.** Sebuah submodule mengunci
   satu commit, dan kunci itulah yang menjadi bug ketika repo anak bergerak; yang dicatat di tiap
   `mcp/<server>/README.md` adalah hulu, versi terpasang, dan perintah pemasangannya.
2. **Registrasi MCP adalah keadaan per-mesin dan tidak pernah dilacak di git.** Terukur malam ini:
   `engines/qoder/settings.json` 4 → 5 server, `engines/antigravity-cli/settings.json` 0 → 1; dua
   berkas itu hidup di luar repo dan tidak akan pernah masuk riwayatnya.
3. **Nama produk dan nama slug repo adalah dua hal berbeda dan keduanya ditulis.** Yang dipakai operator
   adalah IRSofka Comfy MCP / IRSofka Workstation MCP / IRSofka Blender MCP; slug yang sudah terpakai
   (`irsofka-comfy-mcp`, `irsofka-comfyui-mcp`) tetap alamat yang dibaca mesin, jadi tiap README menyebut
   nama produk lebih dulu lalu slug-nya, supaya tidak ada yang menebak bahwa keduanya benda yang sama.

Batas yang jujur atas klaim "bersih" di baris Comfy: `server.py` milik `comfy-mcp` PERNAH DIBACA pada
sesi yang sama, untuk menghitung jumlah toolnya, dan `LICENSE`/`NOTICE`-nya juga dibaca. Yang diukur
bukan kenangan: 7 baris identik dari 654 baris kita, semuanya boilerplate generik, rentan bersama
terpanjang 1, nol identifier privat kita di sumber mereka — dan sebuah perbandingan baris tidak akan
melihat sebuah terjemahan yang mempertahankan struktur sambil mengganti nama. Kalimat itu batas
alatnya, bukan pembelaan.


### Target shape: one workstation repo, three MCP repos — and each has a different licence route

Decided with the operator on 2026-10-15. Splitting the repos breaks nothing: MCP registration is
already per-machine and untracked, so `ai-station` keeps its own tree and simply points at three
independent servers. **No git submodules** for code that is about to be rewritten heavily — a submodule
pins a commit, and the pin becomes the bug when the child repo moves. Each `mcp/*/README.md` records
the upstream, the version installed, and the install command instead.

| repo | whose code | licence route | the trap |
|---|---|---|---|
| IRSofka Unreal Engine MCP | entirely ours | **BSL 1.1**, no strings attached | it links UE headers: ship no Epic content, "Unreal Engine" is Epic's mark. **Fab/marketplace terms not read on this machine** — do not assume they fit a source-available licence |
| IRSofka Blender MCP | depends on the base | MIT base → any licence incl. BSL; **v0.4.x base → AGPL forever** | the installed 218-tool server is AGPL since v0.4.0 and its commercial offer was **withdrawn**, so building on it forecloses selling. Clean bases: `ahujasid/mcp-for-blender` (MIT, 30,872★) or RFingAdam `v0.3.0` (MIT). Decide **before** writing code; this is not reversible afterwards |
| IRSofka ComfyUI MCP | ours — **written, 2026-10-15** (see "Shipped first…" above: no code copied; their `server.py` was read once, for tool counts, so the strict blind-implementation sense of clean-room does not apply) | free choice, no fee | ComfyUI core is **GPL-3.0** (measured at `~/ComfyUI-Installs/ComfyUI/ComfyUI/LICENSE`), but GPL bites on *linking*, not on speaking HTTP to a separate process — comfy-mcp's own `NOTICE` records exactly that distinction for GPL `comfy-cli`. So: implement against `127.0.0.1:8188` ourselves, or fork a permissive client (`artokun/comfyui-mcp` MIT, `joenorton/comfyui-mcp-server` Apache-2.0). Copying `comfy-mcp` instead means AGPL or paying Comfy Org |

**Ledger state of the three repos, measured 2026-10-15 20:2x** (this section existed as prose only,
which is why an unattended loop could not work on it — see `AUTO_PILOT.md` "TASK DAHULU, PENAJAMAN
TERAKHIR"):

**Ledger state of the three repos, re-measured 2026-10-15 23:5x** (the table below this one is the
20:2x snapshot; it is kept because it is the state that made the loop drift, not because it is
current):

| repo | on disk | remote | registered in CLI | gate result | ledger row |
|---|---|---|---|---|---|
| IRSofka ComfyUI MCP | yes, `~/Apps/mcp/irsofka-comfyui-mcp` | private, `irsofka-comfy-mcp`, `main` at pushed sha | **2 of 2** engines | `61/61` selftest | `F11/MCP-1` id 294 — **COMPLETED** |
| IRSofka Blender MCP | yes, `~/Apps/mcp/irsofka-blender-mcp` (server.py, bridge.py, vram.py, LICENSE, UPSTREAM, README, pyproject) | local only, first commit `bc4eae3`, no remote yet | **2 of 2** engines | `28/28` selftest; live round-trip against Blender 5.2.2 measured | `F11/MCP-2` id 295 — **COMPLETED** |
| IRSofka Workstation MCP | `~/Apps/mcp/irsofka-workstation-mcp` (server.py, in build) | none yet | not yet | `--selftest` in progress | `F11/MCP-3` id 298 — WORKING, unblocked once 297 went green |

**Scope line that must be read literally.** `F11/STEP-0` (id 297) is titled "write the round-trip
probe, then fix `set_transform`/`get_transform` and prove the spawn class". What 297 delivered is
the probe, the measurement, and the proof of the spawn class; the C++ change did **not** land in
297, because it needs either a module rebuild with the editor closed or Live Coding keystrokes in
the operator's live session, and an unattended run may not do either. The fix is carried by
`F11/STEP-0b` (id 301), whose gate `tools/ue_bridge_probe.py --allow-mutation --strict` is red by
design until the plugin changes, and whose status is **HUMAN** with decision 55 (escalated: an
irreversible choice is not delegated to any engine). A reviewer correctly called the closure of 297
an overstatement against its title; the record stands as written rather than being quietly edited,
and this paragraph is the correction.

What the probe measures, and what it found (all of it reproducible with one command):

- framing read from `IRSofkaDispatcher/Private/IRSofkaSocketServer.cpp`: newline-terminated JSON on
  `127.0.0.1:9877`, one connection per command, handlers executed synchronously on the GameThread.
- `spawn /Script/Engine.StaticMeshActor` → `class=StaticMeshActor`; written location
  `[-45.5, 67.25, -8.125]`, rotation `[10,20,30]`, scale `[2,1.5,0.5]` all read back identical,
  10–15 ms per command.
- defect 1: a bare class name makes `LoadClass` return null and the handler fall back to
  `AActor::StaticClass()`, which has no RootComponent — every later transform is a silent no-op
  while all three calls answer SUCCESS, and the spawn reply echoes the *requested* location.
- defect 2: `ActionSetTransform` returns only `{"status":"SUCCESS"}` and never calls `Modify()` or
  a transaction, so a write cannot be audited from its reply and the level may not be marked dirty.
- the station-side answer, already built in `irsofka-workstation-mcp`: resolve short class names to
  `/Script/Engine.*` before sending, refuse to send one that cannot be resolved, and read the
  transform back — reporting `changed` from the editor's own numbers and `DEGRADED` when SUCCESS
  did not actually move anything.


Four rows (294, 295, 297, 298); three offerable now, 298 gated behind 297; all gates **red today and meant to be red**; the first seed of MCP-3 was parked as id 296 rather than deleted, because its gate claimed to measure the bridge while it only proved a README exists. Each gate is a
`check_command` in `ROADMAP_ITEMS`, so `work_order.py seed --roadmap` re-derives them and the queue
offers them before anything the machine writes for itself. `next_ready()` returned id 286 (a
machine-authored skill-sharpening row) before this; with rank 0 empty it returns 294 now. A second
consequence of the same audit: `drop_stale_legacy` **hid** legacy working rows whose lease had died
(decision 45, answered `qwen3.5:9b` at 03:5x). Its premise was the id-ordered queue — with ranking,
those rows can no longer overtake an operator command or a roadmap item, so hiding them only made
unfinished real work invisible forever (ids 235, 236, 244). The flag was **deleted** rather than
re-defaulted — a flag any caller can set gives one row two orders — and stale legacy rows now sit
at `RANK_STALE_LEGACY` (25), below roadmap and above idle, still cooled down by `due_epoch`.

**Naming is a licence term, not branding.** "Blender", "ComfyUI"/"Comfy Org" and "Unreal" are other
parties' marks; descriptive use ("MCP for Blender") is fine, implying official status is not, and
`mcp-blender`'s README excludes its own project name and logo from the grant, so renaming is required
anyway. Pattern to follow: `ahujasid/mcp-for-blender` states "Not affiliated with the official Blender
Foundation" — product name first ("IRSofka MCP for Blender"), non-affiliation disclaimer in the README,
none of their logos.

### What BSL actually costs us, stated so it is not discovered later

- It does not prevent copying; it makes copying a breach. Enforcement is ours to initiate.
- External contributions mostly stop: nobody sends PRs to code they cannot use in production. If
  contributions ever arrive, a **CLA is required**, otherwise contributors hold copyright inside our
  own repository.
- Competitors may re-implement from the public source. Licence law protects the expression, not the
  function — and no amount of deliberate unreadability changes that either.
- Administration: a Change Date and Change License must be set per version (1.1 requires a
  GPL-compatible Change License, default the fourth anniversary of first publication).
- Rejected trade honestly: if broad adoption or ecosystem acceptance ever matters more than the
  sale, the answer is Apache-2.0, and that is a business decision for the operator, never an engine's.

### Facts measured here, so nobody re-derives them

| subject | value | method |
|---|---|---|
| `mcp-blender` tools | **218** (naive `def blender_*` = 61) | `TOOLS: list[Tool]` in `src/mcp_blender/server.py`, 218 unique names, zero duplicates |
| `comfy-mcp` tools | **39**, not the 40 this file used to claim | `@mcp.tool` defs in `comfy_mcp/server.py` of installed 0.10.0 |
| `mcp-blender` history | MIT ≤ v0.3.0 → AGPL v0.4.0 | `git show v0.2.0:LICENSE`, `v0.3.0:LICENSE` |
| `comfy-mcp` license | AGPL-3.0-or-later **OR** commercial (`licensing@comfy.org`) | dist-info `License-Expression` + `LICENSE`, section 13 named |
| UE plugin size | 92 files, 12 MB, `Binaries/` = 9.5 MB, already gitignored with `Intermediate/`+`Saved/` | `find`/`du`/`.gitignore` |
| plugin socket | `127.0.0.1:9877`, **no authentication** | grep of `Source` |
| plugin ↔ database | **none** (`postgres`/`sqlite`/`libpq` = 0 hits) — correct, ledger stays ai-station's | grep of `Source` |
| VRAM/GC inside plugin | **none** (`CollectGarbage`/`VRAM`/`RenderResource` = 0 hits) | grep of `Source` |
| ComfyUI core licence | **GPL-3.0** (`GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007`) | first lines of `~/ComfyUI-Installs/ComfyUI/ComfyUI/LICENSE` |
| ComfyUI reachable | yes, `127.0.0.1:8188`, python pid 166974 | `ss -tlnp` |
| permissive alternatives | blender: `ahujasid/mcp-for-blender` **MIT** 30,872★ pushed 2026-10-06 · comfy: `artokun/comfyui-mcp` **MIT** 825★ pushed 2026-10-14, `joenorton/comfyui-mcp-server` **Apache-2.0** 409★ | GitHub API (`license.spdx_id`, `stargazers_count`, `pushed_at`) |
| live editor state today | bridge `UNAVAILABLE` when the editor is closed | `ue_world.get_all_actors` |

## Ledger integrity: two defects found by using the queue (2026-10-15, pushed as `ccd32cb`, `51fe713`, `06e9eae`, `cdc1a1b`)

Both were discovered because a run tried to close its own item and the ledger disagreed with what had
just been written. Neither was visible from the code.

| subject | value | method |
|---|---|---|
| evidence write path | `finish()` stored `(evidence or "")[:900]` while the column is `TEXT` with **no** length limit in either backend | `NEW_COLUMNS` declaration + `information_schema` (`data_type=text`, `character_maximum_length=NULL`) |
| damage | `MAX(LENGTH(evidence)) = 900` and **6 rows pinned at exactly 900** — tails gone permanently | PostgreSQL, read-only, before the fix |
| after the fix | **2 rows above 900** (items 291, 292), both written by shipping code | same query, after |
| verifier identity | one verifier has two names: registry key `gemini-verify` vs answering id `gemini-3.8-flash-high`; `action_log.model` stores the second, `--list` prints the first | comparing a live dispatch row against the registry |
| the trap | a `SETUJU` verdict recorded at 19:06:14 was reported as "no COMPLETED verification" at 19:06:5x, then accepted at 19:07:00 after retyping the same verifier | this run, three commands |
| where resolution lives | in the **reader** (`verifier_log_name()` inside `push_clearance()`), not the caller — the first fix patched only the caller and the reviewer caught that | reviewer TEMUAN 1 on `51fe713` |
| what the event names | `clearance["model"]`, i.e. whoever actually answered, not the typed key nor the registry's claimed primary | `record_push_verified` |
| rejected alternative | matching on `summary` text (`"verifikasi oleh <key>: …"`), which would also cover a runtime fallback model | that string is written by the same process that writes the verdict, so it widens the gate |
| headless-reviewer limit, re-measured | a dispatch told the facts were "re-runnable on this workstation" tried to run a command, was auto-denied, **exited 0**, and performed zero review | 1 of 5 dispatches tonight; fixed by opening every evidence file with `READ-ONLY REVIEW, NO TOOLS AVAILABLE IN THIS RUN` |
| suites | work_order **283/283** (was 277/277), drainer **172/172**, `ai-station workers --audit` → 0 strays | commands run after each change |

The rule this section exists to enforce: a limit applied at write time with nothing said about it is
worse than no limit, because the caller gets exit 0, the row looks populated, and the part that always
disappears is the tail — which is where the numbers live. Any future length concern belongs to display,
and a display cut must name the original length.

## Known Boundaries & Constraints

- **Token usage counters:** Some upstream CLI tools record `0` tokens in raw logs; the workstation preserves telemetry integrity without fabricating numbers.
- **CLI session working directory binding:** `--continue` flags bind to specific working directories; cross-directory persistence is bridged via PostgreSQL `world_memory` and handoff documents.
- **Unmovable system dotfiles:** Specific system paths (`.bashrc`, `.config`, `.var`, `.nv`, `.qoder`) remain as required by host tools; runtime data resides in `~/runtime`.
- **Guardrail scope:** Hooks intercept commands executed via AI shell tooling; manual operator commands bypass hooks by design.

## Historical Context

Early revisions of `ai_workstation_master_plan.md` and `PROJECT_SUMMARY_...md` noted 100% completion prematurely before daemon and logging layers were fully productionized. On October 6, 2026, those documents were archived in `~/.ai-station/archive/`, and all verified architectural truth was consolidated into `README.md` and this roadmap.
