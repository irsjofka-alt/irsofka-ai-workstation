# MCP servers

This directory maps the Model Context Protocol servers used by the workstation. Server code
lives in its own repository under `~/Apps/mcp/`; `mcp/` holds the plan checker, the push
gate, and the ownership map.

## What is where

`~/.ai-station/mcp/` contains the station tools `check_plan.py`, `push_gate.py`, and `PLAN.md`.
Regenerate this section with `python3 ~/.ai-station/mcp/check_plan.py --pointers`; R10 fails
when a HEAD in the table no longer agrees with git.

| name | real location | HEAD | origin |
|---|---|---|---|
| `irsofka-comfyui-mcp` | `~/Apps/mcp/irsofka-comfyui-mcp` | c337ec2 | <https://github.com/irsjofka-alt/irsofka-comfyui-mcp> |
| `irsofka-unreal-mcp` | `~/Apps/mcp/irsofka-unreal-mcp` | a3c32b7 | <https://github.com/irsjofka-alt/irsofka-unreal-mcp> |
| `irsofka-workstation-mcp` | `~/Apps/mcp/irsofka-workstation-mcp` | 903eca6 | <https://github.com/irsjofka-alt/irsofka-workstation-mcp> |
| `irsofka-blender-mcp` | `~/Apps/mcp/irsofka-blender-mcp` | 1c0557c | <https://github.com/irsjofka-alt/irsofka-blender-mcp> |
| `comfy-mcp` | installed package, third party | — | <https://github.com/Comfy-Org/comfy-mcp> (AGPL-3.0-or-later or commercial) |
| `mcp-blender` | `~/Apps/mcp/mcp-blender`, third party | — | <https://github.com/RFingAdam/mcp-blender> (AGPL-3.0-or-later since v0.4.0) |
| `local-workstation` | `tools/mcp_workstation_local.py` in this repository | — | station plumbing, not a published server |

## Plan check (F11)

Status is measured, not typed. Run:

```bash
python3 ~/.ai-station/mcp/check_plan.py            # table, exit 0
python3 ~/.ai-station/mcp/check_plan.py --strict   # exit 1 while a requirement fails
python3 ~/.ai-station/mcp/check_plan.py --markdown # the block below
```

Three states are used. **PASS** means the measured condition holds. **FAIL** means it was
measured and does not hold. **UNAVAILABLE** means the thing needed to measure it was absent
(no remote, no editor listener, no readable ledger); it is never reported as either of the
other two.

The full plan and phase order are in [`PLAN.md`](PLAN.md).

<!-- PLAN-CHECK:BEGIN -->
Status is measured on demand. Run `python3 ~/.ai-station/mcp/check_plan.py --markdown` for the
current table, `--json` for machine-readable evidence, and `--strict` when a gate must fail on a
red requirement. The README does not cache the table, so it cannot drift from the measurement.
<!-- PLAN-CHECK:END -->

## Registration

Registration is per machine and is not tracked. Each CLI reads its own file:

| engine | registration file |
|---|---|
| Qoder CLI | `~/.ai-station/engines/qoder/settings.json` → `mcpServers` |
| Antigravity CLI | `~/.ai-station/engines/gemini_config/mcp_config.json` → `mcpServers` |

A fresh clone ships the workstation server and no registration, because registration describes
the machine, not the code.

## Servers and licences

| server | code location | owner | licence |
|---|---|---|---|
| `local-workstation` | `tools/mcp_workstation_local.py` (this repository) | ours | BSL 1.1, Change License MIT, per-version Change Date |
| `irsofka-comfyui-mcp` | `~/Apps/mcp/irsofka-comfyui-mcp` | ours | BSL 1.1; ComfyUI HTTP API client with a VRAM gate |
| `irsofka-blender-mcp` | `~/Apps/mcp/irsofka-blender-mcp` | ours | BSL 1.1; independent implementation with `UPSTREAM` provenance |
| `irsofka-workstation-mcp` | `~/Apps/mcp/irsofka-workstation-mcp` | ours | BSL 1.1; machine side of the editor bridge |
| `irsofka-unreal-mcp` | `~/Apps/mcp/irsofka-unreal-mcp` | ours | BSL 1.1; client of the plugin receiver on port 9877 |
| `mcp-blender` | `~/Apps/mcp/mcp-blender` | third party | AGPL-3.0-or-later since v0.4.0; MIT up to v0.3.0 |
| `comfy-mcp` | `~/Apps/mcp/comfy-mcp/.venv` | Comfy Org | AGPL-3.0-or-later or commercial |

Two rules are enforced here:

1. **No AGPL-derived code is merged into this repository.** AGPL and BSL 1.1 cannot govern one
   combined work.
2. **Third-party servers are not patched in place.** Our behaviour lives in our own tools and
   talks to theirs over their public surface.

## Licence transcription

The BSL `LICENSE` blocks are transcribed from SPDX `BUSL-1.1.txt` or a real adopter's file,
never from a rendered page. The single exception is the `Notice` paragraph published by MariaDB
at <https://mariadb.com/bsl11/>; the positional diff does not cover it. The `Parameters` block
is ours to fill with the five keys BSL requires.
