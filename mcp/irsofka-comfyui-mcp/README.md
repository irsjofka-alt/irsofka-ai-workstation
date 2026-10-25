# irsofka-comfyui-mcp — ComfyUI MCP Server

Source code lives in `~/Apps/mcp/irsofka-comfyui-mcp` (independent repository, licensed under BSL 1.1).

This server provides an independent implementation of the ComfyUI HTTP protocol, designed for
reliable client-side automation without depending on third-party SDK runtimes.

Every workflow submission is validated against local GPU memory constraints:
**`tools/gpu_gate.py` is consulted before `POST /prompt`.** A job requiring more VRAM than available
is rejected deterministically with the matching rule, memory required, free memory, and current
process holders, preventing out-of-memory driver crashes.

## Measured on this machine, 2026-10-15

| fact | value |
|---|---|
| selftest | **61/61** on a fake transport (both sides: allowed submit and blocked submit) |
| live `comfy_status` | `comfyui_version 0.39.2`, VRAM 7149/11889 MiB by ComfyUI's own pool, 7498/12288 MiB whole-card, holders `UnrealEditor 2118MiB`, `python 1624MiB` |
| live gate decision on a 3D submit | `DENY`, rule `3d-trellis`, need 9966 MiB, free 7498 MiB, **shortfall 2468 MiB**, holders named |
| queue after that refusal | running 0, pending 0 — the refusal really blocked the wire |
| `POST /prompt` with an unknown class | `400 missing_node_type`, and `/queue` re-read after it: nothing queued |
| `GET /object_info/<unknown>` | **200 with `{}`** — the status code is not the verdict, and the server treats the empty body as missing |
| whole `GET /object_info` | 1 951 939 bytes across 976 node classes — never handed to a caller; `action=names` returns a page of names |
| live `comfy_job status` on a past prompt_id | `success`, completed, output `zimage_00001_.png` |
| **live submit the gate allowed** | `gate_state ALLOW`, rule `image-diffusion`, need 5919 MiB against 7241 MiB free → `prompt_id 166cbff0…`, `number 3`, finished in **21.0 s** → `irsfoka-mcp-proof/mcp_00001_.png` |
| that image, fetched through `comfy_fetch` | 1 290 126 bytes, `PNG 832 x 1216 8-bit RGB`, sha256 `2ea33e876fc95385f615155842b92847d6fc471a522611d01feefac51b99e4e5` |
| stdlib-only | checked by an AST scan *inside* the selftest, because README promises it |
| packaging | **not built.** no `setuptools` on the system Python, `python3 -m venv` cannot seed one offline; the wheel is unverified and named that way |

The execution proof in the table above was validated against a live local diffusion graph,
verifying that the ALLOW path executes successfully on hardware.

## What is still owed before this is a release

1. Register it in both CLIs' own MCP files — `engines/qoder/settings.json` (`mcpServers`) and
   `engines/gemini_config/mcp_config.json` (`mcpServers`) — a per-machine act, deliberately not done here, because
   loading a new MCP server changes what the other engine sees.
2. Build the wheel, or delete `pyproject.toml` and say the server runs from source.
3. Complete the Blender MCP companion integration.
