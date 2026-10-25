# mcp-blender — third-party server, AGPL since v0.4.0

Upstream: **https://github.com/RFingAdam/mcp-blender** (read from `git remote -v` of the local
checkout, not from memory). Maintainer per `CLA.md`: Adam Engelbrecht, `RFingAdam` engineering-MCP
toolkit.

Local install: `~/Apps/mcp/mcp-blender` — a real git checkout with tags `v0.2.0`, `v0.3.0`,
`v0.4.0`, currently at **0.4.0** (`pyproject.toml:7`).

| fact | value | how it was measured |
|---|---|---|
| tools served | **218** | `TOOLS: list[Tool]` in `src/mcp_blender/server.py`; 218 unique `name="blender_*"` entries, zero duplicates. `handle_list_tools` returns that list. |
| naive count | 61 | counting `def blender_*` gives 61 — wrong by 3.6x, because most tools are declared as data and dispatched to Blender by stripping the prefix. |
| license, installed version | **AGPL-3.0-or-later** | `pyproject.toml:10`; `LICENSE` first line is the AGPL-3.0 text |
| license history | MIT → AGPL at v0.4.0 | `git show v0.2.0:LICENSE` and `v0.3.0:LICENSE` both read "MIT License"; both `pyproject.toml` say `license = "MIT"` |
| commercial option | **none** | commit `8f64128` "Withdraw the commercial licence offer" deleted `COMMERCIAL.md` |
| name/logo | **not licensed** | `README.md:451` excludes the project name and logo files from the grant |
| addon registration | `addon/blender_mcp_addon/__init__.py` imports `bpy` and defines `MCP_OT_StartServer` / `MCP_OT_StopServer` / `MCP_PT_ServerPanel` | read directly; note Blender itself is GPL-3.0+, and a `bpy` addon is a derivative of Blender |
| MCP port | 9876 | `settings.json` launches `mcp-blender --port 9876` |

## Dependency isolation

This server is maintained upstream as an independent third-party tool. In this workstation,
it is treated as an optional reference dependency and executed unmodified via stdio.

## How we use it today

Unmodified, as a separate process over stdio. No patches, no vendored copy of its code in this
repository (verified: no upstream copyright strings appear in tracked source). Anything we want
blender-side goes through our own tools, not through edits to their tree.
