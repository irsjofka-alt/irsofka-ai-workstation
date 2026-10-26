# Irsofka AI Workstation

An AI orchestration console for Pop!_OS 24.04 COSMIC (Wayland). A single Rust daemon hosts
multiple interactive AI CLIs inside persistent tmux terminals, presents them in a single
native desktop window, and records **every** action to a relational database — ensuring sessions
can be recovered after any interruption.

This is not an IDE. There is no built-in text editor, file tree, or debugger. The focus is:
multiple AI engines working concurrently, cross-verifying each other, and leaving an immutable
audit trail that can be recovered at any time.

## Current surface (2026-10-25)

- **Web console** at `http://127.0.0.1:8999/` plus the optional native window.
- **Realtime SSE** at `/api/stream`: thinking, tool steps, context, run state, automation and decision changes.
- **Auto Pilot Task** in Ruang Kerja: a `Planning.md` loop with Play/Pause/Stop, 1-minute cadence and `skip` overlap.
- **Human Decide** with Recommended options, reversible/heavy/irreversible weights and @Decider.
- **Waker Console** floating launcher: same conversation/history as `@Worker`.
- **Project** menu and a local playground site (`/site`) for projects, blog, gallery and changelog.
- **Worker commands**: `worker load` and `worker recovery` (context pack + ledger/handoff recovery).
- **Creative pipeline**: ComfyUI, Blender MCP, Unreal MCP and ReadyAsset/MeshCleanRoom projects.

The active plan is [`ROADMAP.md`](ROADMAP.md). Internal planning notes and historical
evidence are kept outside the public repository.

## Installation

A complete, step-by-step setup guide is available in **[INSTALL.md](INSTALL.md)** — prerequisites,
installing AI CLIs without cluttering `$HOME`, shared workspace setup, database configuration,
systemd user units, and verification steps. Summary:

```bash
# 1. Hosted CLIs (official installers; inspect before executing)
curl -fsSL https://qoder.com/install | bash
curl -fsSL https://antigravity.google/cli/install.sh | bash

# 2. Clone the workstation repository
git clone https://github.com/irsjofka-alt/irsofka-ai-workstation.git ~/.ai-station
```

If you are trying Qoder for the first time and wish to support this project, you may register
using this referral link — granting **1,000 credits** upon first payment within 30 days:
<https://qoder.com/activities?referral_code=bBfZBkx5dhkUBQDRBYz29LOXv59SLZiB>

> When your friend registers via your link and makes their first payment within 30 days,
> you get 1,000 Credits and your friend gets 500 Credits.

Using the referral link is entirely optional. The software and license terms are identical
regardless of how you register.

What is **not** included in the git clone: `brain/` (machine memory & skills),
`config/db_local.json` (credentials), `logs/`, `engines/` (CLI local states),
`site/` content/media, `config/site_projects.json`, and `config/auto_pilot.json`. Site
installation and the publication contract are in [`site/README.md`](site/README.md). The repository
deliberately tracks only code, rules, and templates — a clean clone will fail cleanly when
connecting to the database rather than shipping personal credentials.

## Architecture

```
                    ┌──────────────────────────────────────┐
   User ──────────► │  native window (tao + wry + webkit)  │
                    └───────────────┬──────────────────────┘
                                    │ HTTP :8999
                    ┌───────────────▼──────────────────────┐
                    │  irsofka-station-core (--headless)   │
                    │  axum · portable-pty · JSONL spool   │
                    └───┬───────────────┬──────────────┬───┘
              tmux -L irsofka           │              │
        ┌───────────────┬───────────────┐   MCP    ┌───▼────────────┐
        │ qoder pane    │ antigravity   │   server │ shell pane     │
        │ (builder)     │ pane (reviewer)│         │ (COSMIC bash)  │
        └───────┬───────┴───────┬───────┘  └───┬───┴────────────────┘
                └───────────────┴──────────────┘
                                │
                    ┌───────────▼───────────────────┐
                    │ PostgreSQL 16 (SQLite fallback)│
                    │ + brain/ markdown + handoff    │
                    └────────────────────────────────┘
```

Three layers of memory form the core of this system:

| Layer | Medium | Lifespan |
|---|---|---|
| Conversation context | In-memory inside CLI process | Ephemeral, lost when process exits |
| **Long-term memory** | PostgreSQL: `action_log`, `session_turns`, `world_memory`, `quest_tasks`, `incident_log`, `ai_message` | Permanent, cross-directory & cross-engine |
| Grimoire | `brain/**/*.md` (rules, skills, identity) | Permanent, readable by human & AI |

## Directory Map

```
bin/              daemon launcher, `ai-station` CLI hub, deployment, backup & restore scripts
engine-rust/      Rust daemon + console API + optional native window
engine-rust/src/console/  HTML/CSS/JS console (no build step)
engine-rust/src/api/      HTTP/SSE endpoints, including `events.rs` and `auto_pilot.rs`
tools/            log→SQL ingestor, MCP server, verifier, local LLM, DB adapter
tools/site/       playground site generator, publisher, project context, Blender render helpers
tools/worker_context.py   `worker load` / `worker recovery` context pack
hooks/            session safety guards + automated handoff recorder
config/           engines.json, slots.yaml, project/site examples; runtime files generated at startup
site/             generated playground + local content/media (gitignored; README tracked)
ROADMAP.md        active plan and contracts
data/             runtime data and local indexes (gitignored)
~/runtime/        OUTSIDE $HOME: cache, data, toolchains, package installs, backups/
```

## Running the Workstation

Requirements: Rust (tested on 1.99), PostgreSQL 16 (or automatic SQLite fallback), `tmux`,
`python3` + `psycopg2`.

```bash
# standard deploy: build, test, install, restart daemon, verify checks
~/.ai-station/bin/deploy_engine.sh --build --yes

# console (also available from the native window)
xdg-open http://127.0.0.1:8999/
# legacy/native surface
~/.ai-station/bin/launch_gui.sh
```

Systemd user units: `irsofka-ai-workstation` (daemon), `irsofka-tabs` (tmux server, isolated
outside daemon cgroup), `irsofka-action-log` (log→SQL ingestor), `irsofka-memory-backup` (timer).

### Database Credentials (Not Tracked in Git)

PostgreSQL credentials resolve in order: environment variables → `config/db_local.json` →
default local trust socket.

```bash
cat > ~/.ai-station/config/db_local.json <<'JSON'
{ "host": "localhost", "port": 5432, "user": "your_username",
  "password": "your_password", "dbname": "irsofka_ai_workstation" }
JSON
chmod 600 ~/.ai-station/config/db_local.json
```

A clean clone without this file will fail connection openly rather than leaking credentials.
Station owner identities also resolve via environment: `STATION_OWNER_NAME`,
`STATION_EMAIL_QODER`, `STATION_EMAIL_ANTIGRAVITY`.

## Tabs Survive Daemon Restarts

Tabs run under `tmux -L irsofka`, completely decoupled from the daemon cgroup. `run_tab.sh`
automatically appends `--continue` when `cli_profiles.json` enables `continue_session`.

```bash
curl -s localhost:8999/api/workspace | grep -o '"backend":"[a-z]*"'   # must return "tmux"
tmux -L irsofka ls
```

Changes to `engine-rust/src/console/*` are served from disk on each request.
Changes to Rust source (`engine-rust/src/**`) require rebuild, deployment and daemon restart.

## Context Recovery

```bash
ai-station recovery 40    # action history + handoff, runnable from any directory
ai-station handoff        # active quests and handoff summaries only
ai-station snapshot       # write mechanical handoff snapshot immediately
```

The `SessionEnd` and `PreCompact` hooks write mechanical handoff records automatically —
capturing real executed commands rather than hallucinated post-compaction summaries.

## Console, realtime and worker visibility

The console is served from `engine-rust/src/console/` and talks to the daemon at
`http://127.0.0.1:8999/`.

- **SSE** `GET /api/stream` publishes `run_event` (thinking, tool_started,
  tool_finished, context, result, status), `run_state` (queued/running/failed/
  succeeded/cancelled), automation changes, decision changes and Auto Pilot mode.
- `@Worker` renders **Deep Thinking**, **View Steps**, a live last step, a
  1-second elapsed timer and an idle warning (≥45 s without a new event).
- **Waker Console** (floating W) shares the same conversation/history with
  `@Worker`; selecting a worker loads its transcript and process steps.
- **Context badge** is provider-aware: Qoder uses the stream-json ratio,
  Antigravity is marked manual, NVIDIA uses usage tokens when reported, and
  Ollama does not report context. The last reported value stays on standby
  until the engine reports a newer one.
- Chat polling exists only as a 5-second fallback; the primary path is the event
  stream.

### Worker commands

Above the chat composer:

```text
COMMAND : (Worker Load) (Worker Recovery)   klik untuk masukkan ke kolom chat, lalu Enter
```

- **`worker load`** builds a context pack: config, engines, model readiness
  (NVIDIA/Qoder/Antigravity), MCP, workers and current station state.
- **`worker recovery`** equals `ai-station recovery 40`: ledger/SQL, handoff,
  recent runs, Human Decide, automations and git state, followed by a resume.
- Implementation: `tools/worker_context.py`, `POST /api/worker/context`, MCP
  tool `worker_context`. Secrets are redacted; API keys are only reported as
  present/missing.

## Auto Pilot Task

Auto Pilot Task lives under **Ruang Kerja** and is an automation with
`kind=autopilot`:

- Source of truth: an approved local `Planning.md` (not tracked in this repository).
- Default cadence: **every 1 minute**.
- Overlap: `skip` — while the worker is busy, the tick is skipped so two agents
  never edit the same plan concurrently.
- Every run executes a worker context load/recovery, reads the plan, and does the
  next unfinished checked group. The task is not marked Done until the plan is
  complete.
- **▶ Play** activates the task and sends one immediate run; **⏸ Pause** stops
  the schedule without cancelling the current run; **■ Stop** cancels the active
  run and deactivates the task.
- The global **Mode Auto Pilot** switch is a separate gate. Play can send one
  run while the mode is OFF; enable the mode for the loop.

## Playground site and projects

- Static site generated by `tools/site/build.py`; served at `/site/`.
- Tracked installation guide: `site/README.md`; generated content/media are
  ignored by git.
- Project registry: `@ReadyAsset`, `@MeshCleanRoom`, `@AIStation`, `@ComfyUI`.
- Pages: `index.html`, `project.html`, `project-*.html`, `blog.html`,
  `gallery.html`, `changelog.html`, `feed.xml`, `search.json`.
- Contract: every worker that generates an image, video, model or render must
  publish it through the site API/MCP or a watched ComfyUI output directory.
  The blog may contain emotion and critique; secrets and private third-party
  data are redacted.
- Phase 0–7 implemented and verified 2026-10-25: static generator + Project menu,
  publish API/MCP, ComfyUI watcher (≈21 s cadence), changelog, 19 ReadyAsset EEVEE
  renders, MeshCleanRoom context (11 docs / 225.803 chars), `@Project` in the console,
  content/media in the daily private-repo backup, and a clean-clone install test.
  Evidence lives in `ROADMAP.md` and the local planning notes.

## Context reporting matrix

| Worker | Context source | Display |
|---|---|---|
| Qoder | `context_usage_ratio` from stream-json | automatic, sticky last value |
| Antigravity / Gemini | `input_tokens` from stream-json | automatic ratio & tokens, sticky last value |
| NVIDIA | OpenAI `usage` prompt tokens | token count; no ratio unless reported |
| Ollama / Local | none | `Context —` |

The `~2.4k` figure is only a local prompt-length estimate for engines that do
not report usage. It is never used for Qoder.

## Cross-Engine Verification

When one AI engine is uncertain about code, the peer engine verifies it. Registered engines
live in `config/engines.json`.

```bash
ai-station verify gemini --file engine-rust/src/main.rs
ai-station verify gemini "Claim: Function X is safe because Y"
python3 tools/local_llm.py --status          # inspect free VRAM, RAM, and GPU holders
python3 tools/local_llm.py --tier verify "..."
```

| Engine | Role | Notes |
|---|---|---|
| Qoder | Builder | Massive context window (1M); primary code implementer |
| Antigravity / Gemini | Reviewer | Equipped with tools to inspect code before rendering verdicts |
| Local LLM (Ollama) | Validator | Offline; tiers: `light` / `verify` / `heavy`. Returns `UNAVAILABLE` when models are not loaded |

Both CLIs share the same local MCP server, allowing **bidirectional** peer verification via the
`ai_message` database table: `ask_peer`, `check_messages`, `resolve_message`. Disagreements are
flagged as `DISPUTED`, never silently resolved.

## GPU Resource Discipline

GPU VRAM is shared between desktop compositor, graphical engines, and local AI models.
Local LLMs operate under strict constraints:

1. **VRAM Gate**: Verifies available VRAM before loading. If insufficient, falls back to CPU/RAM (`num_gpu=0`).
2. **`keep_alive=0`**: Unloads models immediately after response generation rather than lingering.
3. **`ollama stop`**: Enforces memory deallocation and asserts VRAM recovery.

```bash
ollama ps        # must be empty following verification
```

## Gemini BYOK — the operator's own model pool

Three of the workstation's roles can be served by the operator's Google AI Studio keys instead of
the CLI subscriptions: an independent verifier that does not draw on the same quota as the engine
writing the code, a resolver rung for decisions that would otherwise wake a human, and a design
surface for **text** work. It is configured from **Station → Gemini BYOK**, not by an agent editing
files, and the screen shows fingerprints rather than secrets.

What the pool can actually call is measured, not assumed — see `ROADMAP.md` under
*Gemini BYOK as a collaboration surface* and
`brain/memory/reference-byok-design-capabilities.md`. Proven: `gemini-3.8-flash` answers 200.
Refused on every key: `gemini-4-argon` (429), image generation (`limit: 0` free-tier quota on all
four measured image models), and music (no model name in the pool's own list of 63). Video exists
in the list but on an endpoint this module does not call. Do not plan asset generation onto this
pool until a measurement says otherwise.

## Tripo3D — prepared route, no spend

> **Verification Status: Prepared Route / Dry-Run Only (Zero Spend Enforced)** — Live HTTP generation and paid point deduction have not been executed on a live production server because no paid production API key is configured on this workstation.

`tools/tripo.py` wraps the official `tripo-cli` (0.6.1, API **V3**) as the workstation's paid 3D
half. It is deliberately a *gate*, not a faucet. What exists today is narrow and named exactly:

- **One measured HTTP route.** `tripo make "<prompt>" --dry-run` (free, no credential) confirms
  `text_to_model` at `/v3/generation/text-to-model`. `texture` and `convert` are listed in
  `ENDPOINTS` but marked **TEBAKAN** (guess): what `tripo make --help` actually shows is that they
  are a processing chain — `--then "texture,convert:fbx"` on `make` — not routes of their own, and
  their `/v3/...` paths have never been confirmed against any server. Their cost is not a number, so
  the gate below refuses them regardless.
- `balance` is not a REST path either; it is `tripo whoami`, and it uses the vendor's own login
  session, which is why `probe` does not ask for an API key on that route.
- Image→model is reachable, but only through the raw `cli` passthrough (`make` accepts image files),
  not through the cost table.
- `ai-station` has no `tripo` verb; call `python3 tools/tripo.py status|probe|capabilities|submit|cli`.

The cost rule:

- Every command that can spend points (`make`, `ai`, `redo`) runs with `--dry-run` unless `--spend`
  is passed explicitly, and `--spend` is refused unless a **number** is available for that
  operation. `submit()` reads `ENDPOINTS[...]["biaya_poin"]`; the `cli` passthrough has no row in
  that table, so it requires `--cost <points>` written by whoever measured it. A prose value like
  "belum terukur" is not a price. One predicate, `cost_is_known()`, guards both doors — a gate that
  only guards one entry point is an open gate with extra steps.
- The module never adds `--yes`: the vendor CLI enables it automatically when it detects a
  non-TTY, which is exactly the state an unattended tick runs in. `--no-open` is always appended so
  no browser window appears on the desktop while the workstation is unattended.
- Keys never travel through argv, stdout or git. `config/tripo.json` is gitignored; credentials for
  the CLI route come from `tripo login`, whose owner is the operator, not an agent — that is why
  `probe` does not demand an API key for `whoami`.
- The V2 paths in the very first draft were guesses. They were replaced by what the installed
  CLI's own dry-run prints. Neither version has been confirmed against the live server, because no
  credential exists on this machine yet — `probe` refuses rather than assuming, and `404` handling
  in the code is tested with a fake transport, not observed.

`python3 tools/tripo.py status` reports whether a key is present (length and fingerprint only) and
`tools/tripo.py probe` runs the free `tripo whoami --json`. Live calls are still pending: no
credential exists on this machine yet, and the Studio wallet (500 points, browser session) has not
been shown to be the same balance as the API wallet — that has to be measured before any run is
allowed to spend.

## MCP Servers — the workstation's hands

Four MCP servers are registered in **both** CLIs (verified: `engines/qoder/settings.json` and
`engines/gemini_config/mcp_config.json` list the same four). Tool counts below were read from each
server's own source, not from documentation.

| Server | Tools | Version | What it reaches | Verification Status |
|---|---:|---|---|---|
| `local-workstation` | 17 | in-repo (`tools/mcp_workstation_local.py`) | Hardware telemetry, Wayland screenshots, notifications, volume, SQL state, quest logging, PTY read/write, UI refresh, daemon restart, peer messaging, Human Decide (`ask_user`/`check_user_answer`), `auto_pilot_task`, `worker_context` | **Verified** (in-repo & live tests) |
| `irsofka-unreal-mcp` | 12 | ships with the UE plugin | A live Unreal Engine 5.8 editor: 1 diagnostic (`ue_station_status`), 6 core master domains (`ue_world`, `ue_blueprint`, `ue_material`, `ue_media`, `ue_vision`, `ue_pipeline`), plus 5 extended native automation tools (`ue_importer`, `ue_landscape`, `ue_cmd`, `ue_python`, `ue_level`) | **Verified** (socket transport 10/10; live tests per domain below) |
| `mcp-blender` (upstream) | 218 | 0.4.0 | A live Blender session: mesh editing, modifiers, geometry nodes, sculpting, baking, UV, rigging, export (FBX/glTF/USD/STL), PolyHaven assets | **Verified** (upstream data-declared surface) |
| `comfy-mcp` (upstream) | 39 | 0.10.0 | A local ComfyUI install via `comfy-cli`: templates, slots, workflows, jobs, model inventory and download, node packs, custom-node install, VRAM control | **Verified** (upstream `@mcp.tool` definitions) |

### Reconciling Tool Metrics: Upstream Reference vs. Workstation Native Implementations

External reviews (such as Glama) or documentation comparisons may observe different tool counts depending on whether they inspect upstream third-party implementations or IRSofka's native workstation implementations:

1. **Blender MCP**:
   - Upstream `RFingAdam/mcp-blender` (0.4.0) declares **218 tools** as data structures (`TOOLS: list[Tool]`), mapping commands directly to Blender API endpoints.
   - Native `irsofka-blender-mcp` provides **11 tools** (6 local scene & execution, 3 network-gated assets, 2 station guardrails). It was written independently from scratch with 0 third-party code lines, implementing station VRAM gating and safe mode execution (verified by 19/19 automated self-checks).
2. **ComfyUI MCP**:
   - Upstream `comfy-mcp` registers **39 tools** via `comfy-cli` endpoints.
   - Native `irsofka-comfyui-mcp` provides **14 tools** (local workflow execution, background lifecycle management `start_comfyui`/`stop_comfyui`, 1 network-gated `view_image`, 2 station guardrails) with intercepted `guard_submits()` preventing unmetered VRAM allocation (verified by automated self-checks).
3. **Unreal Engine MCP**:
   - `irsofka-unreal-mcp` consolidates editor control into **12 tools total**: `ue_station_status` (socket presence probe), the original **6 master domains** (`ue_world`, `ue_blueprint`, `ue_material`, `ue_media`, `ue_vision`, `ue_pipeline`), and 5 extended native automation tools (`ue_importer`, `ue_landscape`, `ue_cmd`, `ue_python`, `ue_level`).
   - The surface is intentionally compressed rather than exposing hundreds of micro-tools: domain calls execute on Unreal's GameThread inside transaction sandboxes with automatic rollback.

Registration is per-machine and lives in the engine state directories, which are **not** tracked here —
a fresh clone ships the MCP server for the workstation itself, and the other three are installed by
their own projects.

## Safety Guardrails

Two critical rules are enforced by hooks rather than mere guidelines:

- **Never** run `pkill -f "irsofka-station-core"` — this terminates the production daemon,
  triggering systemd respawn and killing active PTY sessions.
- **Never** run `tmux -L irsofka kill-server` — this destroys all running AI tabs and shells.

`hooks/self_preservation.py` also blocks destructive file system operations (`rm -rf brain/`,
`DROP/TRUNCATE`, `DELETE` without `WHERE`, empty file redirects to memory files) and prevents
unauthorized root `$HOME` creation by directing runtime artifacts to `~/runtime`.
Verification suite: `hooks/test_self_preservation.py`.

### Local Attack Surface & Defense-in-Depth

The workstation enforces multi-layer isolation to secure local endpoints against DNS rebinding and cross-origin attacks:
1. **HTTP Console API (:8999)**:
   - **CORS Whitelist**: `tower_http::cors::CorsLayer` strictly restricts `allowed_origin` to `http://127.0.0.1:<port>` and `http://localhost:<port>`, blocking unauthorized cross-origin requests from external browser contexts.
   - **Token Gate**: Non-loopback/LAN requests require `STATION_ACCESS_TOKEN` via `x-station-token` header, query parameter, or session cookie.
   - **Host & Origin Validation**: Endpoints reject unverified host headers to protect against DNS rebinding attacks.
2. **IPC Bridges (:9876 Blender, :9877 Unreal)**:
   - Bound strictly to loopback interface (`127.0.0.1`).
   - Shared-secret authentication tokens between station caller and bridge receivers are architected for upcoming hardening rounds.
3. **Defense-in-Depth vs. Pattern Lists**:
   - Pattern hooks (`hooks/self_preservation.py`) serve as an immediate first line of defense against accidental CLI mistakes (`rm -rf brain/`, unindexed `DELETE`, `DROP`).
   - Because string/pattern matching alone can be circumvented (e.g., encoded Python executions, subshells), the **automated hourly snapshot backup** (`station_backup.sh` retained in `~/runtime/backups/`) provides the immutable, unalterable safety floor for complete workstation state restoration.

## Backups & State Preservation

```bash
~/.ai-station/bin/station_backup.sh          # run manual backup
systemctl --user list-timers irsofka-memory-backup.timer
~/.ai-station/bin/station_restore.sh --check # test restore to temporary DB (production safe)
```

Automated hourly backups with `Persistent=true` catch up after power cycles, retaining 24 hourly
and 30 daily snapshots in `~/runtime/backups/`. Restore operations are verified against a temporary
database before acceptance.

The workstation also has a daily **private-repo backup** automation (`backup_push.sh`, 02:00 local)
and the Auto Pilot site content/media are local-only. Back them up separately:

```bash
tar -C "$HOME/.ai-station" -czf "$HOME/runtime/backups/site-$(date +%F).tar.gz" \
  site/content site/media
```

`site/` is generated and gitignored; only `site/README.md` is tracked.

## Ecosystem — Workstation, Playground, Studio Pipeline

This console is the control layer of a longer pipeline. It is **content-agnostic by design**: the
stages below drive generation, assembly and editor automation for any project — terrain, characters,
props, VFX, cinematics — and nothing in the tooling assumes a particular genre, world or source. The
workstation is the instrument, not the composition.

The local **Playground Site** (`/site`) is the human-facing layer over this pipeline: project pages,
showcase/gallery, blog, changelog and model/vendor attribution. Its output is generated from
`site/content` + `site/media`; the contract and installation steps are in
[`site/README.md`](site/README.md).

```
   reference & source data          heightmaps · masks · concept art · meshes · audio
                       │
                       ▼
   Irsofka AI Workstation  ◀── this repo     cockpit · telemetry · autopilot · cross-verification
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   ComfyUI (roadmap F7)            Blender          generative + assembly
   images · PBR textures · 3D      mesh · UV ·       via MCP
   meshes                          FBX / glTF
                       │
                       ▼
              Unreal Engine 5.8                     driven through the MCP editor bridge
```

| Stage | Repository | Visibility |
|---|---|---|
| Cockpit, rules, telemetry, autopilot | `irsjofka-alt/irsofka-ai-workstation` | **public** — this repository |
| Generative studio (ComfyUI + Blender MCP wiring) | no repository: a per-machine install, described by roadmap **F7** | local only |
| Unreal Engine 5.8 MCP editor bridge (`IRSofka-UE-AIStation-Core`) | `irsjofka-alt/IRSofka-UE-AIStation-Core` | **private** |
| Unreal Engine gameplay project | `irsjofka-alt/IRSofkaEngine` | **private** |
| Working reference material | no repository | **not published** — third-party licences may not permit redistribution, and project content is not for sale |

### How the three stages actually connect

The connection is concrete, not aspirational — three local ports, one database, and a rule about who
owns the GPU at any moment.

```
 ComfyUI  :8188          Blender  :9876          Unreal editor  :9877
     │                       │                        │
     └───────┬───────────────┴──────────────┬─────────┘
             │        MCP (stdio)           │
             ▼                              ▼
        AI CLIs  ◀──── tmux PTY ────  irsofka-station-core :8999
             │                              │
             └──────────► PostgreSQL ◄──────
                     irsofka_ai_workstation
              (action_log · quest_tasks · world_memory
                incident_log · ai_message · decisions)
```

Every stage writes into the same ledger, which is what makes the chain auditable: a generated image,
a Blender export and an editor action are all `action_log` rows with the engine, the command and the
exit code attached. Nothing about the pipeline is trusted because it is neat.

**One phase, one GPU owner.** The card is 12 GB and the workloads do not fit together — a full
diffusion set is 9,7–11,3 GB resident while the Unreal editor alone holds ~4 GB. So the stages are
*sequenced*, and that sequencing is the design, not a limitation discovered later:

| Phase | Holds the GPU | Why |
|---|---|---|
| Find a base 2D, iterate on look | ComfyUI only | the operator is choosing images; the editor is closed |
| Image → 3D, UV, retopology | Blender + editor | opened only after the 2D is settled |
| Local LLM verification tier | Ollama, or CPU | never concurrently with generation |

The one step that is easy to forget: unload before switching (`free_memory`, then confirm with
`system_stats`), because ComfyUI keeps weights resident and the next phase needs that memory.

**Six core master domains provide a compressed, high-leverage editor surface.** Each domain commands an entire subsystem of
the editor:

| Domain | What an agent can make the editor do | Verification Status |
|---|---|---|
| `ue_world` | Spawn, bulk-spawn, duplicate, parent, delete, transform and select actors; lighting, physics, visibility, outliner folders, gameplay tags, material assignment, level streaming | **Verified** (Live GameThread execution) |
| `ue_vision` | Grab the active viewport to PNG on disk / Antigravity artifacts and frame the camera on any actor — visual verification loop | **Verified** (Live disk capture & artifact sync) |
| `ue_pipeline` | Execute an ordered array of domain steps as one job with **partial-success reporting**: if step *k* fails, steps 0..*k−1* are preserved and the failure is isolated | **Verified** (10/10 transaction test suite) |
| `ue_blueprint` | Create assets, add variables and functions, place `K2Node_CallFunction` nodes, **connect pins through fuzzy resolver**, set default values, compile with diagnostics | **Compiled & Bridge-Tested** (Deep editor test pending) |
| `ue_material` | Author master materials and `MaterialInstanceConstant`s; set scalar, vector, texture and static-switch parameters; reflect parameter list | **Compiled & Bridge-Tested** (Deep editor test pending) |
| `ue_media` | Import WAV → `USoundWave`, bind audio components, one-shot playback at a world position, spawn Niagara emitters and drive parameters | **Compiled & Bridge-Tested** (Deep editor test pending) |

*(Native automation extensions `ue_cmd`, `ue_python`, `ue_level` are verified live; `ue_importer` and `ue_landscape` are compiled and bridge-tested).*

### Unreal Surface Roadmap (Glama Analysis)

In response to the external Glama server evaluation (3.2/5 rating), the following authoring enhancements are explicitly tracked in the active roadmap:
1. **Blueprint Graph Authoring**: Dynamic component creation and custom event node bindings.
2. **Material Node Graph**: Direct node authoring, expression graph editing, and node deletion beyond parameter assignment.
3. **Sequencer Automation**: Direct Level Sequencer track binding and keyframing.

And 38 named actions is the menu, not the ceiling. `ue_blueprint.add_call_function` accepts *any*
target class and method name, so it reaches engine functions the domain layer never enumerates;
`ue_pipeline` composes that into multi-step operations that no single tool would express. Every
mutation runs inside a two-tier transaction sandbox — memory rollback via `FScopedTransaction`, plus
disk and package purge on failure, with `ResetLoaders()` before object deletion so a rollback cannot
leave a use-after-free behind.

The consolidation is deliberate on three counts: tool definitions are paid for on every agent turn,
near-synonym micro-tools are prevented from causing dispatch ambiguity, and the six-domain shape
provides a unified high-level abstraction over the engine plugin architecture.

### Requesting access to a private stage

The two Unreal Engine repositories are private because they carry in-engine C++ and project content that
is not offered under this repository's licence terms. An account without access receives **HTTP 404**,
which is how GitHub hides a private repository — it is not evidence that the name is wrong or that the
project does not exist.

There is no invite link and no automated grant. To ask for access:

1. Open an issue on this repository stating the intended use — which AI engine will drive it, which
   Unreal Engine version, and which of the six master domain tools are needed.
2. Or contact the repository administrator through the GitHub profile:
   <https://github.com/irsjofka-alt>

Access is granted per repository. A workstation clone without the private stages still starts: the
bridge reports `UNAVAILABLE` when it cannot reach a running editor, and this project's reporting rule
forbids an engine from narrating an action it did not perform — an unavailable tool is
`UNAVAILABLE`, never `COMPLETED`.

## Status and known gaps

- SSE realtime, Human Decide, Auto Pilot Task, Waker Console and the Project menu are live.
- Playground Site Phase 0–7 is implemented and verified: generator + Project menu,
  publish API/MCP, ComfyUI watcher, changelog, 19 ReadyAsset renders, MeshCleanRoom
  `@Project` context, backup coverage and clone test. Evidence is recorded in
  `ROADMAP.md` and the local planning notes.
- ComfyUI currently reports `DEGRADED` when its server is not running.
- Antigravity does not expose a context ratio automatically; it is marked manual.
- NVIDIA reports usage tokens when available; Ollama does not report context.
- Retired roadmap items are summarized in `ROADMAP.md`; full historical evidence
  stays outside the public repository.

## Documents

- [`ROADMAP.md`](ROADMAP.md) — active roadmap and contracts.
- [`rules/PUBLIC_WRITING.md`](rules/PUBLIC_WRITING.md) — public writing and tone contract.
- Internal planning notes and historical evidence are kept locally under `data/`.
- [`site/README.md`](site/README.md) — playground installation and publication contract.
- [`INSTALL.md`](INSTALL.md) — installation guide.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — third-party notices.

## License

Code in this repository is licensed under the **Business Source License 1.1** — you may copy,
modify, create derivative works, redistribute and make **non-production** use of it freely;
**production use requires a commercial licence** from the Licensor, PT IRSofka Data Teknologi (NIB 1258 0003 4132 6, DUNS 5645 3618 4) — write to <admin@irsofka.co.id>. Each version converts to the
**Apache License, Version 2.0** on its own Change Date, four years after that version is first made publicly
available, so the clock never resets no matter how many releases follow. Full terms in
[`LICENSE`](LICENSE).

### License & Provenance Matrix

| Repository / Module | Licensor / Origin | Initial License | Change License (Conversion) | Provenance & Implementation Status |
|---|---|---|---|---|
| `irsjofka-alt/irsofka-ai-workstation` (Core) | PT IRSofka Data Teknologi | **BSL 1.1** | **Apache-2.0** (4 years from release) | Primary orchestration console, daemon & tools |
| `irsjofka-alt/irsofka-blender-mcp` | PT IRSofka Data Teknologi | **BSL 1.1** | **Apache-2.0** (4 years from release) | Independent implementation; protocol compatibility reference `ahujasid/mcp-for-blender` (MIT). Zero third-party code copied |
| `irsjofka-alt/irsofka-comfyui-mcp` | PT IRSofka Data Teknologi | **BSL 1.1** | **Apache-2.0** (4 years from release) | Independent implementation; protocol compatibility reference `joenorton/comfyui-mcp-server` (Apache-2.0). Zero third-party code copied |
| `irsjofka-alt/irsofka-unreal-mcp` | PT IRSofka Data Teknologi | **BSL 1.1** | **Apache-2.0** (4 years from release) | In-house transport bridge written against `IRSofkaStationCore` UE plugin |
| `irsjofka-alt/irsofka-workstation-mcp` | PT IRSofka Data Teknologi | **BSL 1.1** | **Apache-2.0** (4 years from release) | Station local MCP bridge (`tools/mcp_workstation_local.py`) |
| `RFingAdam/mcp-blender` (upstream) | RFingAdam | **AGPL-3.0** | N/A | Optional third-party add-on integration |
| `comfy-cli` / `comfy-mcp` (upstream) | Comfy-Org | **GPL-3.0** | N/A | Optional third-party local generation tool |
| `xterm.js` | Source Authors | **MIT** | N/A | Bundled console component |
| `Inter` & `JetBrains Mono` fonts | RSms / JetBrains | **SIL OFL 1.1** | N/A | Bundled typography assets |

Third-party dependencies maintain their respective licenses: xterm.js (MIT) and Inter / JetBrains Mono
fonts (SIL OFL 1.1) — see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

> Required Notice: Copyright © 2026 irsjofka-alt
