# Irsofka AI Workstation

An AI orchestration console for Pop!_OS 24.04 COSMIC (Wayland). A single Rust daemon hosts
multiple interactive AI CLIs inside persistent tmux terminals, presents them in a single
native desktop window, and records **every** action to a relational database — ensuring sessions
can be recovered after any interruption.

This is not an IDE. There is no built-in text editor, file tree, or debugger. The focus is:
multiple AI engines working concurrently, cross-verifying each other, and leaving an immutable
audit trail that can be recovered at any time.

## Installation

A complete, step-by-step setup guide is available in **[INSTALL.md](INSTALL.md)** — prerequisites,
installing AI CLIs without cluttering `$HOME`, shared workspace setup, database configuration,
systemd user units, and verification steps. Summary:

```bash
# 1. Hosted CLIs (official installers; inspect before executing)
curl -fsSL https://qoder.com/install | bash
curl -fsSL https://antigravity.google/cli/install.sh | bash

# 2. Clone the workstation repository
git clone https://github.com/irsjofka-alt/IRSofka-Ai-Workstation.git ~/.ai-station
```

If you are trying Qoder for the first time and wish to support this project, you may register
using this referral link — granting **1,000 credits** upon first payment within 30 days:
<https://qoder.com/activities?referral_code=bBfZBkx5dhkUBQDRBYz29LOXv59SLZiB>

> When your friend registers via your link and makes their first payment within 30 days,
> you get 1,000 Credits and your friend gets 500 Credits.

Using the referral link is entirely optional. The software and license terms are identical
regardless of how you register.

What is **not** included in the git clone: `brain/` (machine memory & skills),
`config/db_local.json` (credentials), `logs/`, and `engines/` (CLI local states). The repository
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
bin/            daemon launcher, `ai-station` CLI hub, deployment, backup & restore scripts
engine-rust/    Rust source: daemon + GUI (axum, portable-pty, tao, wry)
tools/          log→SQL ingestor, MCP server, cross-engine verifier, local LLM, DB adapter
hooks/          session safety guards + automated handoff recorder
config/         engines.json (registry), slots.yaml; runtime files generated at startup
~/runtime/      OUTSIDE $HOME: cache, data, toolchains, package installs, backups/
```

## Running the Workstation

Requirements: Rust (tested on 1.99), PostgreSQL 16 (or automatic SQLite fallback), `tmux`,
`python3` + `psycopg2`.

```bash
cd engine-rust && cargo build --release
install -m 0755 target/release/irsofka-station-core ~/.ai-station/bin/
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

Changes to `gui.html` are dynamically reloaded on each request.
Changes to `main.rs` require a rebuild, deployment, and daemon restart.

## Context Recovery

```bash
ai-station recovery 40    # action history + handoff, runnable from any directory
ai-station handoff        # active quests and handoff summaries only
ai-station snapshot       # write mechanical handoff snapshot immediately
```

The `SessionEnd` and `PreCompact` hooks write mechanical handoff records automatically —
capturing real executed commands rather than hallucinated post-compaction summaries.

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

## MCP Servers — the workstation's hands

Four MCP servers are registered in **both** CLIs (verified: `engines/qoder/settings.json` and
`engines/gemini_config/mcp_config.json` list the same four). Tool counts below were read from each
server's own source, not from documentation.

| Server | Tools | Version | What it reaches |
|---|---:|---|---|
| `local-workstation` | 13 | in-repo (`tools/mcp_workstation_local.py`) | Hardware telemetry, Wayland screenshots, desktop notifications, volume, SQL state queries, quest logging, PTY read/write (`read_terminal`, `send_to_terminal`), UI refresh, daemon restart, inter-engine peer messaging |
| `irsofka-unreal-mcp` | 6 | ships with the UE plugin | A live Unreal Engine 5.8 editor, driven through six master domains — see below |
| `mcp-blender` | 218 | 0.4.0 | A live Blender session: mesh editing, modifiers, geometry nodes, sculpting, baking, UV, rigging, export (FBX/glTF/USD/STL), PolyHaven assets |
| `comfy-mcp` | 40 | 0.10.0 | A local ComfyUI install via `comfy-cli`: templates, slots, workflows, jobs, model inventory and download, node packs, custom-node install, VRAM control |

**277 tools in total.** Two of those numbers are worth reading carefully, because both have been
misquoted inside this project at some point: the Unreal surface is **6 tools, not hundreds** (that is a
deliberate design decision, documented below), and the large numbers belong to Blender and ComfyUI,
which are third-party servers with their own upstreams.

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

## Backups & State Preservation

```bash
~/.ai-station/bin/station_backup.sh          # run manual backup
systemctl --user list-timers irsofka-memory-backup.timer
~/.ai-station/bin/station_restore.sh --check # test restore to temporary DB (production safe)
```

Automated hourly backups with `Persistent=true` catch up after power cycles, retaining 24 hourly
and 30 daily snapshots in `~/runtime/backups/`. Restore operations are verified against a temporary
database before acceptance.

## Ecosystem — Three Repositories, One Pipeline

This console is the control layer of a longer pipeline. The stages are deliberately split across
repositories so the cockpit can stay public while engine-specific source and project content does not:

```
   source assets & terrain references        (Lineage 2 extraction, heightmaps, splatmaps)
                       │
                       ▼
   Irsofka AI Workstation  ◀── this repo     cockpit, telemetry, autopilot, cross-verification
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   ComfyUI (roadmap F7)            Blender        generative + assembly:
   images, PBR textures, 3D        mesh, UV,       meshes, textures
   meshes via MCP                  FBX/glTF
                       │
                       ▼
              Unreal Engine 5.8                   driven through the MCP editor bridge
```

| Stage | Repository | Visibility |
|---|---|---|
| Cockpit, rules, telemetry, autopilot | `irsjofka-alt/IRSofka-Ai-Workstation` | **public** — this repository |
| Generative studio (ComfyUI + Blender MCP wiring) | no repository: a per-machine install, described by roadmap **F7** | local only |
| Unreal Engine 5.8 MCP editor bridge (`IRSofka-UE-AIStation-Core`) | `irsjofka-alt/IRSofka-UE-AIStation-Core` | **private** |
| Unreal Engine gameplay project | `irsjofka-alt/IRSofkaEngine` | **private** |
| Extracted terrain sources | no repository | **cannot be published** — derived from third-party game assets |

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

**Six tools is not a small surface — it is a compressed one.** Each domain commands a whole subsystem of
the editor:

| Domain | What an agent can make the editor do |
|---|---|
| `ue_world` | Spawn, bulk-spawn, duplicate, parent, delete, transform and select actors; lighting, physics, visibility, outliner folders, gameplay tags, material assignment, level streaming |
| `ue_blueprint` | Create assets, add variables and functions, place `K2Node_CallFunction` nodes, **connect pins through a fuzzy resolver that matches pin names instead of failing on case**, set default values, compile with error diagnostics |
| `ue_material` | Author master materials and `MaterialInstanceConstant`s; set scalar, vector, texture and static-switch parameters; reflect the full parameter list |
| `ue_media` | Import WAV → `USoundWave`, bind audio components, one-shot playback at a world position, spawn Niagara emitters and drive their user parameters, create Level Sequences |
| `ue_vision` | Grab the active viewport as PNG and frame the camera on any actor — the agent's eyes, used to verify its own work visually |
| `ue_pipeline` | Execute an ordered array of domain steps as one job with **partial-success reporting**: if step *k* fails, steps 0..*k−1* are preserved and the failure is isolated, not rolled back blindly |

And 38 named actions is the menu, not the ceiling. `ue_blueprint.add_call_function` accepts *any*
target class and method name, so it reaches engine functions the domain layer never enumerates;
`ue_pipeline` composes that into multi-step operations that no single tool would express. Every
mutation runs inside a two-tier transaction sandbox — memory rollback via `FScopedTransaction`, plus
disk and package purge on failure, with `ResetLoaders()` before object deletion so a rollback cannot
leave a use-after-free behind.

The consolidation is deliberate on three counts: tool definitions are paid for on every agent turn,
near-synonym micro-tools are exactly where models pick the wrong one, and the six-domain shape is the
clean-room boundary — the re-architecture that let the plugin leave behind the micro-tool surface it
replaces (the legacy notes still circulating quote that older surface at 949 tools; it describes a
different, abandoned plugin, not this one).

### Requesting access to a private stage

The two Unreal Engine repositories are private because they carry in-engine C++ and project content that
is not offered under this repository's PolyForm terms. An account without access receives **HTTP 404**,
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

## License

Code in this repository is licensed under the **PolyForm Noncommercial License 1.0.0** — free to
use, modify, and distribute for noncommercial purposes. Full terms in [`LICENSE`](LICENSE).

Third-party dependencies maintain their respective licenses: xterm.js (MIT) and Inter / JetBrains Mono
fonts (SIL OFL 1.1) — see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

> Required Notice: Copyright © 2026 irsjofka-alt
