# MCP servers — where each one lives, what it may be, and who owns it

This directory is the explicit map of the Model Context Protocol servers this workstation uses.
It holds no server code of its own: the five servers live in five different places, and the point
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

## The five servers

| folder | server | code lives in | owned by | license |
|---|---|---|---|---|
| [`local-workstation/`](local-workstation/README.md) | `local-workstation` | `tools/mcp_workstation_local.py` (this repo) | ours | **BSL 1.1** (Change License MIT, per-version Change Date) |
| [`irsofka-comfyui-mcp/`](irsofka-comfyui-mcp/README.md) | *not registered yet* | `~/Apps/mcp/irsofka-comfyui-mcp` (own repo) | ours | **BSL 1.1** — written over ComfyUI's HTTP API with no code copied from `comfy_mcp` (the one package compared), and it gates a submit on VRAM before sending it (what the others do on that path was not measured) |
| [`irsofka-unreal-mcp/`](irsofka-unreal-mcp/README.md) | `irsofka-unreal-mcp` | UE 5.8 plugin `IRSofkaStationCore` | ours | **BSL 1.1** (decided 2026-10-15, `ROADMAP.md` F11); own repo, never merged with this one |
| [`mcp-blender/`](mcp-blender/README.md) | `mcp-blender` | `~/Apps/mcp/mcp-blender` (git checkout) | RFingAdam | **AGPL-3.0-or-later** since v0.4.0; **MIT up to v0.3.0** |
| [`comfy-mcp/`](comfy-mcp/README.md) | `comfy-mcp` | `~/Apps/mcp/comfy-mcp/.venv` (wheel only) | Comfy Org | **AGPL-3.0-or-later OR commercial** |

One count in the history is wrong and is named rather than quietly fixed: commit `5ec86cc`'s message says
"four servers … and three of them are not ours". Its own table shows **two of four** belonging to us, which was the truth then;
this table now lists **five servers, three of them ours**. The commit message stays as written, because rewriting an unpushed history to
erase a mistake is worse than recording the correction where the reader actually looks.

Two rules follow from that table and are enforced here rather than remembered:

1. **Nothing AGPL-derived is merged into this repository.** AGPL requires downstream freedom
   including commercial use and forbids added restrictions; this repo is BSL 1.1, which adds exactly
   such a restriction (production use needs a paid licence). The two cannot govern one combined work,
   so a fork of either server is its own repository or it is not distributed at all.
2. **We do not patch their servers in place.** Our behaviour belongs in our own tools and servers,
   talking to theirs over their public surface (stdio, HTTP, sockets). That is also how
   `comfy-mcp` itself handles `comfy-cli` — its `NOTICE` records that it invokes the GPL binary as
   a separate process and never imports or links it.

## From which source each block of a BSL `LICENSE` came

Written because the rule this repository states below — transcribe from the license-list entry or a real
adopter's file, never from a rendered web page — has one exception here, and an exception nobody writes
down becomes a contradiction the next reader has to rediscover.

| block | source | covered by the positional diff? |
|---|---|---|
| title, copyright, trademark lines | SPDX `BUSL-1.1.txt` | no — the measured span starts at `Terms` |
| `Parameters` | ours to fill; the five keys are what BSL asks a licensor to complete | no — not part of SPDX's text |
| `Terms` through covenant 4 | SPDX `BUSL-1.1.txt`, taken whole | **yes**: 66 lines against 66, ratio 1.00000, 0 differing blocks |
| `Notice` | the [mariadb.com/bsl11/ page](https://mariadb.com/bsl11/) — MariaDB publishes that paragraph there and the SPDX entry does not carry it | no, and that is stated instead of left to be found |

The page is the only place that paragraph is published, so the exception cannot be avoided; what could
have been avoided is not naming it. It is not where the covenants came from, and that was the first
mistake.

Tool counts are measured, not quoted; see each README, because the naive counting method is wrong
by a factor of 3.6 on one of them.
