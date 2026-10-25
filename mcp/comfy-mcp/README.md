# comfy-mcp — third-party server, dual-licensed AGPL-or-commercial

Upstream: **https://github.com/Comfy-Org/comfy-mcp** (the URL appears inside the installed package's
own source; three independent hits). Author per `METADATA`: Comfy Org. Version installed: **0.10.0**.

Local install is a **wheel, not a checkout**: `~/Apps/mcp/comfy-mcp/` contains only `.venv`, with the
code at `.venv/lib/python3.12/site-packages/comfy_mcp`. Forking it therefore starts with cloning
upstream, not with copying this folder.

| fact | value | how it was measured |
|---|---|---|
| tools served | **39** | `@mcp.tool` definitions in `comfy_mcp/server.py`, all unique. This repo's README previously said 40 — off by one, corrected 2026-10-15. |
| license | **AGPL-3.0-or-later OR commercial** | `License-Expression` in the dist-info, and the `LICENSE` file: "Comfy MCP is dual-licensed… at your option" |
| commercial route | `licensing@comfy.org` | `LICENSE` option (b), written for embedding in a proprietary product or hosting without releasing source |
| network clause | AGPL **section 13** named explicitly | `LICENSE` option (a): "including the network-use source-disclosure obligation in section 13" |
| attribution owed | `NOTICE` file | "Copyright (c) Comfy Org… This product includes software developed at Comfy Org" |
| its own dependency style | separate process | `NOTICE`: it invokes `comfy-cli` (GPLv3) as a separate process and "does not import, link, or embed comfy-cli code" |
| MCP target | local ComfyUI at `127.0.0.1:8188` | server instructions; ComfyUI core is `github.com/comfyanonymous/ComfyUI` |

## Dependency isolation

This server is maintained upstream as an independent third-party tool. In this workstation,
it is treated as an optional reference dependency and executed unmodified via stdio.

## How we use it today

Unmodified over stdio. Our ComfyUI knowledge lives in `brain/skills/comfyui/` — measured setup, the
template→slot→run chain, hard limits, and the VRAM figures — not in patches to their server. The
workstation's own VRAM floor is enforced by `tools/gpu_gate.py` against `config/gpu_budget.json`,
outside their code entirely.
