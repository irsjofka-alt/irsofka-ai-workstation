# local-workstation — our own MCP server, in this repository

Code: `tools/mcp_workstation_local.py` (tracked). Registered per machine in
`engines/qoder/settings.json` and `engines/gemini_config/mcp_config.json`, neither of which is
tracked — this repo ships the server, each machine decides where to mount it.

Tools: **13**, counted from the server's own `list_tools` declarations.

| tool | what it reaches |
|---|---|
| `get_hardware_telemetry` | RTX 3060 VRAM/load/temp, RAM, CPU, NVMe free space |
| `control_system_volume` | PipeWire/PulseAudio sink volume |
| `take_screenshot_wayland` | Wayland desktop capture |
| `send_desktop_notification` | Desktop banner (see the single-owner rule below) |
| `query_workstation_db` | PostgreSQL save-state: profile, skills, quests, memory, incidents, turns, action log |
| `update_quest_task` | quest row |
| `ask_peer` / `check_messages` / `resolve_message` | cross-engine verification over `ai_message` |
| `read_terminal` / `send_to_terminal` | tmux panes on socket `-L irsofka` |
| `refresh_workstation_ui` | WebView reload |
| `restart_workstation_daemon` | daemon restart with a logged reason |

## Two rules this server exists to hold

- **One owner per word.** Banners go through `tools/notify.py`, decisions through
  `tools/work_order.py`, dispatch through `tools/drainer.py`. This server calls those modules; it
  does not reimplement their policy. A second copy of a rule is two copies that will disagree.
- **It never selects a model from memory.** Roles resolve from `config/slots.yaml` and
  `config/engines.json` at the moment of use.

## Why it stays where it is

Moving the file into `mcp/local-workstation/` would touch two untracked registration files,
`INSTALL.md`, and the generated `ARCHITECTURE.md`, and would buy nothing: this folder is the map,
and the map's job is to say where things are, not to relocate them. `tools/` already holds every
other module that owns a rule, and putting one of them in a different tree for naming reasons is
how a codebase gets two homes.
