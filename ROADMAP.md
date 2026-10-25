# 🗺️ Roadmap — Irsofka AI Workstation

Status audit: **2026-10-25**
Active plan only. Historical narrative, measurements, and incident evidence are
kept outside the public repository.

This file answers one question: **what is still worth building, and what has been
retired**. NERF items are no longer part of the active roadmap; their original text
stays in the archive so a revert or audit never depends on memory.

---

## Current state

| Area | Status | Evidence |
|---|---|---|
| Rust daemon + native window + tmux tabs | ✅ ready | `irsofka-station-core`, `/api/workspace` |
| Web console `/` | ✅ ready | new console, Project/Auto Pilot/Human Decide/Waker Console |
| Realtime run events | ✅ ready | `GET /api/stream` (SSE), `run_event` + `run_state` |
| Context display | ✅ ready | Qoder `context_usage_ratio`; sticky last value; Antigravity manual |
| Human Decide + @Decider | ✅ ready | decision policy AUTO/SEMI/OFF, Decider Gemini Flash 3.8 High |
| Auto Pilot Task | ✅ active | automation `kind=autopilot`, 1-minute cadence, Play/Pause/Stop |
| Playground site | ✅ Phase 0–7 ready | `build.py --check` 16/16, 36 posts, 25 media; clone test passed |
| ComfyUI auto-post | ✅ active | watcher ≈21 s; file→post 4,65 s; changelog from git log |
| Blender ReadyAsset showcase | ✅ ready | 19 renders EEVEE 1280²; `site/project-readyasset.html` |
| MeshCleanRoom context | ✅ ready | 11 docs / 225.803 chars; MCP + console `@Project` |
| MCP ownership / UE bridge | 🟡 active | local-workstation, Blender, ComfyUI, Unreal MCP |
| Cross-verification | ✅ active | `ai-station verify`, @Verifier, peer MCP |
| Memory / recovery | ✅ active | `ai-station recovery 40`, handoff hooks |
| ComfyUI runtime | 🟡 degraded | server not running; watcher still pending |

---

## Phases

Machine-readable phase index read by `tools/work_order.py` (`parse_phases` /
`roadmap_open`). A line without a status marker counts as **open**; the detailed
contracts are the BUFF sections below and the ledger is the authority on each item.

```text
F8  Work-order schema, ledger, leases, recovery    ✅ completed — every f84 ledger row is COMPLETED
F9  Reflexes and observability                     🟡 active — F9.3 awaits human visual proof
F10 Console, Auto Pilot Task, decision broker      🟡 active — F10.5 now runs as a weekly automation
F11 MCP ownership, UE bridge, public voice         🟡 active — three ledger rows are PENDING
```

---

## BUFF — keep and continue

### A. Core, memory and recovery
- Rust daemon, PTY/tmux tabs, native window and web console.
- PostgreSQL ledger + SQLite fallback.
- `ai-station recovery 40`, handoff hooks, action/session memory.
- **Contract:** no engine may claim work not recorded in the ledger.

### B. Verification, decisions and MCP
- `ai-station verify`, `cross_verify`, peer MCP (`ask_peer`, `check_messages`).
- Human Decide queue with recommended options, weight, evidence and model id.
- @Decider = **Gemini 3.8 Flash High** (`gemini-verify`); Argon removed from decider.
- MCP ownership: local-workstation, Blender, ComfyUI, Unreal; push gates and licensing.
- **Contract:** irreversible decisions remain human-only.

### C. Console, realtime and worker visibility
- New console views: Dashboard, @Worker, Project, Auto Pilot Task, Automations,
  Workflows, Runs, Traces, Approvals, Human Decide, Workers, Groups, Channels,
  Models, Usage, Engines, Skills & MCP, Knowledge, Memory, Settings.
- **SSE `/api/stream`** drives run events, thinking, steps, context and state changes.
- **Deep Thinking**, **View Steps**, live tool status, 1-second stable elapsed,
  idle warning, sticky context badge.
- Waker Console shares the same conversation/history as @Worker.
- **Contract:** no polling-only chat; work must be observable while it happens.

### D. Auto Pilot Task
- Task is an automation `kind=autopilot`; plan source is `Planning.md` or another
  approved markdown plan.
- Default cadence **1 minute**; overlap `skip` prevents two workers on one plan.
- **Play** = activate + one immediate run; **Pause** = stop schedule; **Stop** =
  cancel active run + deactivate.
- Mode Auto Pilot is a global gate; Play can send one run without enabling the loop.
- **Contract:** one worker per plan; loop continues until all checkboxes are done.

### E. Playground site and publication
- `site/` is gitignored; `site/README.md` is tracked through `.gitignore` negation.
- Project registry: @ReadyAsset, @MeshCleanRoom, @AIStation, @ComfyUI.
- Static pages: home, projects, blog, gallery, changelog, feed/search.
- **Contract:** every generated image/video/model/render must be published through
  the site API/MCP or a watched output directory. Watcher enforces the latter.
- Blog content is editorial and may include critique; secrets and private third-party data are redacted.
- Phase 0–7 implemented and verified; evidence is in the test table below.

### F. Creative studio
- ComfyUI generation, Blender MCP and headless batch render.
- ReadyAsset: 18 mesh + 1 complete assembly; render each mesh and the assembly.
- MeshCleanRoom: `IRSofka-Exporter` + `ProjectMeshGenesis` docs are read together;
  the actual path is `ProjectMeshGenesis` (no `IRSofka-` prefix).
- **Contract:** check VRAM first; never run ComfyUI, Blender and UE heavy work concurrently.

### G. Reflexes and observability
- F9.1 deterministic environment state (exit code, cwd, process tree, pane delta).
- F9.2 action ↔ outcome history keyed by command shape.
- F9.3 visual proof after input (screen change vs no change).
- **Contract:** unknown is never "idle" and never "clean".

### H. Security, licensing and push gates
- BSL 1.1 for workstation/plugin; third-party notices retained.
- Push gate per SHA; no force-push, no history rewrite, no silent credential changes.
- **Contract:** secret values never enter prompts, logs, site content or git.

### I. Language and surfaces
**Decision (2026-10-25):** one product, two display languages. Code, commits, API
fields, logs, and public/technical documentation stay **English**. Console and site
expose a persisted **ID / EN** selector; Indonesian remains available for
operator-facing copy. A single view must not mix both languages.
- Console header selector, stored per operator and applied without a reload.
- Site header selector; generated pages render from a locale table rather than
  hard-coded mixed strings.
- Move operator-facing strings into locale tables; keep English as the default
  for new public docs and API/ledger values.
- **Contract:** language is a display preference, not a product fork. Ledger,
  API, commit messages, and public technical docs do not change language.

---

## NERF — archived, no longer active

These items are retired from the active plan.

| Retired item | Why | Replaced by |
|---|---|---|
| F10.3 drainer + `irsofka-autopilot.timer` + ON-window/level | Autonomous loop now uses Auto Pilot Task/automations | Auto Pilot Task, Play/Pause/Stop |
| F10.4 proposal queue | Duplicated the task queue/automation model | Auto Pilot Task + automation |
| F10.5 dream phase / idle sharpening | Self-improvement moved to explicit weekly automations | `Penajaman skill`, `Perbaikan AI Station` |
| F10.7 pane heartbeat / CONTINUE injection | Run events and live steps replaced pane guessing | SSE `/api/stream`, run events |
| F10.8 old ON switch / level / window | Replaced by global Auto Pilot mode + task controls | Auto Pilot Task mode, Play/Pause/Stop |
| F10.11 OFF-as-mode / legacy autopilot narrative | Decision mode and Auto Pilot mode are separate now | `/api/decision-mode`, `/api/auto-pilot-mode` |
| F8.1 standalone warehouse table | Artifact tracking belongs with the site content/media index | `site/content`, `site/media`, publish API |
| F9.4 LISTEN for quest rows | Run updates are pushed by SSE; PTY epoll remains useful | SSE `/api/stream` (PTY event work kept) |
| Historical F11 MCP architecture & licensing research | Evidence must stay, but not as active work | Retired; active MCP ownership remains |

`work_order.py`, the decision ledger, and `quest_tasks` are **not** retired: they still
back Human Decide, leases and recovery. Only the legacy **drainer scheduling/UI** is retired.

---

## Executable work queue

`ROADMAP.md` is a contract and status map, **not** an Auto Pilot checklist. Auto Pilot
Task requires a `Planning.md` with measurable checkboxes. The claimable work itself lives
in the ledger, and the queue is read from it:

```bash
python3 tools/work_order.py next     # the next claimable row
python3 tools/work_order.py list     # the full ledger
```

Open F11 rows at the 2026-10-25 audit: `320` shipped guards, `321` handover trigger, and
`322` public voice. The prepared Nvidia plan is
`data/plans/nvidia-f11/Planning.md` (local state, derived from those rows). Do not point
an Auto Pilot task at this file.

---

## Active order

1. ✅ Finish Playground Site **Phase 3–4**: publish API/MCP, ComfyUI watcher, changelog.
2. ✅ **Phase 5**: render 19 ReadyAsset meshes + complete assembly (one mesh first).
3. ✅ **Phase 6**: MeshCleanRoom multi-document context, blog, `@Project` resolution.
4. ✅ **Phase 7**: security, backup, clone/install test, release gate.
5. Keep Auto Pilot Task looping until the active local plan is complete.

## Known gaps

- ComfyUI server is currently down; new real generations will not appear until it starts.
  The watcher path itself was verified end-to-end in Phase 4 (file→post 4,65 s).
- Antigravity does not report a context ratio automatically; it is marked **manual**.
- NVIDIA reports usage tokens when available; no context-window ratio.
- Ollama does not report context/usage.
- Site Phase 0–7 is complete and the release gate passed (evidence in the test table below).

## Test evidence (2026-10-25 release gate)

Measured on the workstation (`/home/irsofka/.ai-station`, HEAD `d3c3223` plus the working tree):

| Test | Command | Result |
|---|---|---|
| Work-order selftest | `python3 tools/work_order.py selftest` | **391/391 sesuai**, exit 0 |
| Rust daemon tests | `cargo test -q` (in `engine-rust/`) | **62 passed; 0 failed** |
| Site structure + secret scan | `python3 tools/site/build.py --check` | **16/16 berkas wajib, 16 halaman, 36 post, 0 peringatan, 0 error** |
| Clone → install → build → serve | rsync working tree (no `.git`, no site content, README kept) → `build.py --init` → `build.py` → `build.py --check` → `python3 -m http.server` | **16/16, 0 error**; `index.html`, `project.html`, `blog.html`, `gallery.html`, `changelog.html`, `feed.xml` all **200** |

The two rows that used to be red are fixed, and the same tests above cover them:

- **Push-gate normalization** judged the real repo, so it went red exactly when that repo was
  healthy and fully pushed (`pushable` is empty when nothing is unpushed). The row now builds a
  fixture repo with one commit deliberately ahead of its upstream (`fixture_git_repo`), records a
  clearance from a 7-character sha, and asserts the gate reads the normalized 40-character sha as
  pushable — with `malformed_events == 0`.
- **Phase map** had no `## Phases` block after the BUFF/NERF restructure, so `roadmap_open` reported
  `unreadable`. The block above is now the machine-readable index; against the real file and ledger
  it returns `open_phases [9, 10, 11]`, `projectable_open [9, 10, 11]`, `unprojected []`,
  `unreadable []`.

A clone rebuild also exposed a redaction/validation mismatch: `redact()` neutralised
`/home/<name>` but let a bare `/home/` mention in prose through, while `check()` rejects the
substring outright. The `home-path` pattern now covers the bare token (redaction and validation
agree), and `site/README.md` states what `--init` actually does — folders, registry, welcome post —
while the README itself comes from the repo.

## Documents

- [`README.md`](README.md) — operating guide.
- [`site/README.md`](site/README.md) — site installation and publication contract.
- Internal planning notes and historical evidence are kept locally under `data/`.
