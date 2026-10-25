# irsofka-unreal-mcp — our UE 5.8 bridge, and what publishing it would cost

Two pieces, two homes. The split is architectural, not a packaging preference:

| piece | lives in | role |
|---|---|---|
| the MCP server (`server.py`, `bridge.py`, six domain tools) | `~/Apps/mcp/irsofka-unreal-mcp` — own repository, first commit `1108bf2`, `10/10 server.py --selftest` | the **caller**: station side of the socket |
| the plugin `IRSofkaStationCore` | source `~/Documents/ai-workstation/projects/IRSofka-UE-AIStation-Core`; the copy the editor loads is `~/Apps/LinuxUnrealEngine5.8.3/Engine/Plugins/Marketplace/IRSofkaStationCore/` (92 files, 12 MB, `Binaries/` = 9.5 MB of `.debug`/`.sym` excluded by the plugin's own `.gitignore`, which also excludes `Intermediate/` and `Saved/`) | the **receiver**, and the thing that decides whether a station is present |

The MCP server is housed in its standalone repository (`~/Apps/mcp/irsofka-unreal-mcp`), ensuring
clean decoupling from the editor plugin's internal Python paths.

Six master-domain tools — `ue_world`, `ue_blueprint`, `ue_material`, `ue_media`, `ue_vision`,
`ue_pipeline` — plus `ue_station_status`, over a GameThread-safe socket on `127.0.0.1:9877`.
Modules: `IRSofkaCore`, `IRSofkaBlueprint`, `IRSofkaDispatcher`, `IRSofkaMaterial`, `IRSofkaMedia`,
`IRSofkaVision`, `IRSofkaWorld`.

## The two states, now held by the plugin

`FIRSofkaStationPresence` (`Source/IRSofkaCore/…`, repo commit `13fde5f`) resolves the station from
its queue tool — `tools/work_order.py` — never from a directory name, so an empty `.ai-station`
cannot make the plugin claim a connection. An `AI_STATION_HOME` that is set but invalid is
**refused** rather than falling back to the default: one typo would otherwise point the plugin at
another station while reporting the right one. Detected → the plugin journals its own events into
`<station>/logs/station_events.jsonl`, so engine-side actions appear in `ai-station recovery`;
not detected → an ordinary plugin that writes nowhere and says why. Exposed as `core.presence`,
`core.capabilities` and the `station.presence` console command.

Compiled from the repo (`RunUAT BuildPlugin` → `BUILD SUCCESSFUL`, `[9/38] Compile
IRSofkaStationPresence.cpp`). The built package resides in `~/runtime/ue-plugin-build-f11`. Module
swapping in an active editor requires manual confirmation. The checker reports the source repo
and the installed copy as two separate facts.

## What is already there, measured in source

| capability | state | evidence |
|---|---|---|
| fuzzy pin resolution | **exists** | `IRSofkaBlueprintSubsystem.h:29` → `ResolvePinFuzzy(UEdGraphNode*, TargetPinName, EEdGraphPinDirection)` |
| transaction sandbox + rollback | **exists** | `FIRSofkaTransactionManager`: `BeginTransaction`(GEditor), `TrackCreatedAsset`, `CommitTransaction`, `RollbackTransaction` purging created packages, calling `ResetLoaders()` **before** `ForceDeleteObjects()` — the order that avoids use-after-free |
| PostgreSQL client in the plugin | **absent** | zero hits for `postgres`/`sqlite`/`libpq` in `Source/`. Correct: the ledger is ai-station's, and a second writer would be a second definition of "what is happening" |
| VRAM drain / GC handshake | **absent** | zero hits for `CollectGarbage`/`VRAM`/`RenderResource`. The outside half exists already as `tools/gpu_gate.py` + `config/gpu_budget.json`, enforced by the drainer before a claim |
| socket authentication | **none**, loopback only | every bind is `127.0.0.1:9877`; the `IsPassword(true)` fields are BYOK key inputs in the dashboard, not a socket credential. Anyone who rebinds to `0.0.0.0` gains unauthenticated editor control — say this out loud before shipping, do not let a user discover it |

Epic also ships its own **experimental** MCP plugin here: `Engine/Plugins/Experimental/ModelContextProtocol`
plus `Experimental/Toolsets/MCPClientToolset`. Its tool surface has **not** been compared to these
six domains yet, so "ours is the only one" is not a claim this file makes.

## Fixed 2026-10-15, before any share

`IRSofkaBYOKManager.cpp` had one user's absolute config path written literally in two places — the
read fallback and, worse, a mirror write in `SaveConfig()`. Consequences: on any other machine the
mirror failed silently because the return value was discarded, so keys looked shared and were not;
and the author's username was in source that is planned for publication.

Both now derive from the home directory, matching the workstation's own convention
(`<home>/.ai-station`), with `AI_STATION_BYOK` as an explicit override and no mirror at all when
neither is set. The mirror write reports failure instead of swallowing it. Verified: zero personal
absolute paths remain in `Source/`, `Content/`, `Config/`.

**Still required: a rebuild and an editor restart.** The running editor holds the old binary, so
this change is inert until UBT recompiles `IRSofkaCore` — deliberately not triggered from here.

## License: decided 2026-10-15 — BSL 1.1

Chosen in `ROADMAP.md` F11 because, of the three routes compared there (BSL, PolyForm Noncommercial,
private EULA), it is the only one that satisfies the licence requirements at once: source readable
as a showcase, copyright retained by the licensor (so nobody can re-claim it), and Production Use
requiring a purchased licence. A Change Date converts each version to an open licence if adoption is
preferred over selling. It is **not** OSI-approved
open source; that is the accepted cost.

`.uplugin` points `DocsURL`/`SupportURL` at `github.com/irsjofka-alt/IRSofka-UE-AIStation-Core` and
`README.md` still badges it "Proprietary / IRSofka" — that badge should be replaced by the BSL notice
when the repo is actually opened, and this plugin must live in its **own** repository: one work cannot
carry two grants at once. Note that both sides now use BSL 1.1: sharing a licence name is not the
same as sharing a licence *text*, and each repository states its own Licensor, Licensed Work and
Change Date, so the separation is still required.

This plugin contains original code developed under BSL 1.1. It links against Unreal Engine headers;
distribution excludes proprietary engine binaries, and "Unreal Engine" is a trademark of Epic Games, Inc.
