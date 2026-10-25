# F11 — MCP ownership and publication plan

F11 transitions the workstation to standalone native MCP servers with clear architectural
boundaries and deterministic licensing. The Unreal plugin is the primary receiver of the bridge,
under Business Source License (BSL 1.1).

Live status is generated into [`README.md`](README.md) by
`python3 ~/.ai-station/mcp/check_plan.py`. This file records what "finished" means and which
command measures each part.

## Resume protocol

1. `python3 ~/.ai-station/mcp/check_plan.py` — measured requirement board; the only place
   status is written.
2. `python3 ~/.ai-station/tools/work_order.py seed --roadmap` — idempotent projection of
   roadmap requirements into claimable ledger rows.
3. `ai-station recovery 20` — ledger, quests, handoffs.
4. `ROADMAP.md` — active roadmap and retired items.

## Scope

- Four published repositories: `irsofka-comfyui-mcp`, `irsofka-unreal-mcp`,
  `irsofka-workstation-mcp`, and `irsofka-blender-mcp`.
- Each repository is registered in both engine sides (Qoder and Antigravity).
- Every requirement has a `quest_tasks` row so a loop can claim it; a requirement without a
  row is a requirement a loop can skip.
- Public documents carry no account paths, transcript ids, or key-shaped strings.

## Architecture

The Unreal plugin is the receiver. The MCP server is a client of a socket owned by the editor
(TCP `127.0.0.1:9877`, one JSON line in, one JSON line out, implemented in
`FIRSofkaSocketServer::ProcessJsonCommand`).

1. **The plugin owns its state.** Station detection lives in `Source/`
   (`FIRSofkaStationPresence`); a client-side TCP probe can only prove that something answers.
   Only the detected state writes into the station spool, so `ai-station recovery` sees
   engine-side events when both are combined.
2. **Absence is a normal state.** When the station is not detected, the plugin runs in ordinary
   standalone mode and MCP tools answer `UNAVAILABLE` with the reason and error class. Never a
   fake success, never a traceback.
3. **Deployment and editor round-trip are separate facts.** `check_plan.py` distinguishes
   `station_bridge`, `standalone_listener`, and `unavailable`; the installed copy is reported
   as machine context, not as feature status.

## Licensing

- Published family: **BSL 1.1**, Change Date four years per version, Change License
  **Apache License 2.0**, licensor **PT IRSofka Data Teknologi**.
- Third-party base licences are retained in each repository's `NOTICE`: MIT for
  `ahujasid/mcp-for-blender`, Apache-2.0 for `joenorton/comfyui-mcp-server`.
- `mcp-blender` is AGPL-3.0-or-later from v0.4.0 and is no longer a path an engine reaches.
  `comfy-mcp` remains the vendor-supported Comfy Org surface and is not our successor.
- No AGPL-derived code may be merged into a BSL or PolyForm work.

## Publication boundary

Publish the editor surface. Keep private the ledger and its rules (`work_order`, `drainer`,
`awakener`), role resolution in `config/slots.yaml` and `config/engines.json`, the verifier
tier with its quota gating, measured VRAM thresholds, and the skills/memory corpus.

## Implementation milestones

Work is assigned by ledger rows and resolved at claim time. Milestones follow the modular architecture:

- Milestone 1: Blender Bridge (`irsofka-blender-mcp`).
- Milestone 2: ComfyUI Protocol Client (`irsofka-comfyui-mcp`).
- Milestone 3: Unreal Engine Bridge (`irsofka-unreal-mcp`).

No repository is pushed without a per-SHA verdict and the repository push gate.
