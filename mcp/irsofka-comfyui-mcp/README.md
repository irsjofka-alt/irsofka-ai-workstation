# irsofka-comfyui-mcp — our own ComfyUI MCP server

Code lives in `~/Apps/mcp/irsofka-comfyui-mcp` (own repository, BSL 1.1, not merged into this one).
Registered nowhere yet: `python3 server.py` runs it over stdio today.

This is the server `ROADMAP.md` F11's three-repo plan calls **IRSofka ComfyUI MCP**. It is *not* F11
step 1 (that step is the comparison against Epic's own MCP tool surface); it is F11 step 4's VRAM work
brought forward to the boundary where it could actually be finished. It was written against ComfyUI's
documented HTTP API after `comfy-mcp` was ruled out as a base, because it is AGPL-3.0-or-later (or a paid
commercial licence) and AGPL forbids exactly the restriction BSL adds. **No substantive line of theirs is in our tree** — measured, and the measurement contradicts the
stronger wording I put above it a moment ago, so the weaker one is what stands: against the installed
`comfy_mcp` package (6 888 significant lines), our 654 significant lines share **7 normalised lines**.
Seven is not zero, and they are `from __future__ import annotations`, `except Exception as exc:`,
`if __name__ == "__main__":`, `if not isinstance(data, dict):` and `if action == "list":` — Python
anyone writes the same way. Longest consecutive run **1**, no shared private identifier. The claim is
about derivation, and a derived file shares structure and runs of lines; this one shares no run longer
than a single idiomatic line. Scope note: only `server.py` was compared, not `pyproject.toml` or the
selftest blocks. Their `server.py` was still read once, to count tool
definitions for `mcp/README.md`, so "nobody here ever looked at it" remains not a claim this file makes. What is shared with a ComfyUI is the wire.

What it does on every submit: **`tools/gpu_gate.py` is consulted before `POST /prompt`.** Whether the
installed `comfy-mcp` refuses on that path was **not measured** — its submit route was never exercised —
so this line describes this server and stops.
A job the card cannot pay for is refused with the rule that matched, the megabytes needed, the
megabytes free and the names of the processes holding it — and nothing is sent. `UNKNOWN` refuses too.

## Measured on this machine, 2026-10-15

| fact | value |
|---|---|
| selftest | **61/61** on a fake transport (both sides: allowed submit and blocked submit) |
| live `comfy_status` | `comfyui_version 0.39.2`, VRAM 7149/11889 MiB by ComfyUI's own pool, 7498/12288 MiB whole-card, holders `UnrealEditor 2118MiB`, `python 1624MiB` |
| live gate decision on a 3D submit | `DENY`, rule `3d-trellis`, need 9966 MiB, free 7498 MiB, **shortfall 2468 MiB**, holders named |
| queue after that refusal | running 0, pending 0 — the refusal really blocked the wire |
| `POST /prompt` with an unknown class | `400 missing_node_type`, nothing queued (measured; the same call with a valid graph would have run) |
| `GET /object_info/<unknown>` | **200 with `{}`** — the status code is not the verdict, and the server treats the empty body as missing |
| whole `GET /object_info` | 1 951 939 bytes across 976 node classes — never handed to a caller; `action=names` returns a page of names |
| live `comfy_job status` on a past prompt_id | `success`, completed, output `zimage_00001_.png` |
| **live submit the gate allowed** | `gate_state ALLOW`, rule `image-diffusion`, need 5919 MiB against 7241 MiB free → `prompt_id 166cbff0…`, `number 3`, finished in **21.0 s** → `irsfoka-mcp-proof/mcp_00001_.png` |
| that image, fetched through `comfy_fetch` | 1 290 126 bytes, `PNG 832 x 1216 8-bit RGB`, sha256 `2ea33e876fc95385f615155842b92847d6fc471a522611d01feefac51b99e4e5` |
| stdlib-only | checked by an AST scan *inside* the selftest, because README promises it |
| packaging | **not built.** no `setuptools` on the system Python, `python3 -m venv` cannot seed one offline; the wheel is unverified and named that way |

The one image the operator authorised ("1 gambar saja") is that last row: taken from this machine's own
last-good graph in `/history` — the graph is read out of `prompt[2]`, which is why the submit needed no
hand-written node wiring — and it is the proof that the ALLOW path is real and not only a fake
transport passing a mock.

## What is still owed before this is a release

1. Register it in both CLIs' own MCP files — `engines/qoder/settings.json` (`mcpServers`) and
   `engines/gemini_config/mcp_config.json` (`mcpServers`) — a per-machine act, deliberately not done here, because
   loading a new MCP server changes what the other engine sees.
2. Build the wheel, or delete `pyproject.toml` and say the server runs from source.
3. Decide the Blender twin. The operator's question — a powerful OSS base whose rights he can claim —
   is still open there, and it is irreversible once code is written on top of a licence.
