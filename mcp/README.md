# MCP servers — where each one lives, what it may be, and who owns it

This directory is the explicit map of the Model Context Protocol servers this workstation uses.
It holds no server code of its own: the four servers live in four different places, and the point
of this folder is that nobody has to know that from memory.

Registration is **per machine** and is not in this repository. Both CLIs read their own file, and
neither is tracked:

| engine | registration file |
|---|---|
| Qoder CLI | `~/.ai-station/engines/qoder/settings.json` → `mcpServers` |
| Antigravity CLI | `~/.ai-station/engines/gemini_config/mcp_config.json` → `mcpServers` |

Measured 2026-10-15 with `git ls-files`: both files are untracked, while
`tools/mcp_workstation_local.py` is tracked. A fresh clone therefore ships the workstation's own
server and *no* registration — which is correct, because registering a server is a statement about
the machine it runs on, not about the code.

## The four servers

| folder | server | code lives in | owned by | license |
|---|---|---|---|---|
| [`local-workstation/`](local-workstation/README.md) | `local-workstation` | `tools/mcp_workstation_local.py` (this repo) | ours | PolyForm Noncommercial 1.0.0 |
| [`irsofka-unreal-mcp/`](irsofka-unreal-mcp/README.md) | `irsofka-unreal-mcp` | UE 5.8 plugin `IRSofkaStationCore` | ours | **BSL 1.1** (decided 2026-10-15, `ROADMAP.md` F11); own repo, never merged with this one |
| [`mcp-blender/`](mcp-blender/README.md) | `mcp-blender` | `~/Apps/mcp/mcp-blender` (git checkout) | RFingAdam | **AGPL-3.0-or-later** since v0.4.0; **MIT up to v0.3.0** |
| [`comfy-mcp/`](comfy-mcp/README.md) | `comfy-mcp` | `~/Apps/mcp/comfy-mcp/.venv` (wheel only) | Comfy Org | **AGPL-3.0-or-later OR commercial** |

Two rules follow from that table and are enforced here rather than remembered:

1. **Nothing AGPL-derived is merged into this repository.** AGPL requires downstream freedom
   including commercial use and forbids added restrictions; this repo is PolyForm Noncommercial,
   which adds exactly such a restriction. The two cannot govern one combined work, so a fork of
   either server is its own repository or it is not distributed at all.
2. **We do not patch their servers in place.** Our behaviour belongs in our own tools and servers,
   talking to theirs over their public surface (stdio, HTTP, sockets). That is also how
   `comfy-mcp` itself handles `comfy-cli` — its `NOTICE` records that it invokes the GPL binary as
   a separate process and never imports or links it.

Tool counts are measured, not quoted; see each README, because the naive counting method is wrong
by a factor of 3.6 on one of them.
