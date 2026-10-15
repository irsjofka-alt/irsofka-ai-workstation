# irsofka-unreal-mcp — our UE 5.8 bridge, and what publishing it would cost

Code lives **inside the engine install**, not in this repository:
`~/Apps/LinuxUnrealEngine5.8.3/Engine/Plugins/Marketplace/IRSofkaStationCore/` — 92 files, 12 MB,
of which `Binaries/` is 9.5 MB of `.debug`/`.sym` artifacts already excluded by the plugin's own
`.gitignore` (which also excludes `Intermediate/` and `Saved/`).

Six master-domain tools — `ue_world`, `ue_blueprint`, `ue_material`, `ue_media`, `ue_vision`,
`ue_pipeline` — over a GameThread-safe socket on `127.0.0.1:9877`. Modules: `IRSofkaCore`,
`IRSofkaBlueprint`, `IRSofkaDispatcher`, `IRSofkaMaterial`, `IRSofkaMedia`, `IRSofkaVision`,
`IRSofkaWorld`.

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

Chosen in `ROADMAP.md` F11 because it is the only instrument that satisfies all three of the
operator's demands at once: source readable as a showcase, copyright never transfers (so nobody can
re-claim it), and Production Use requires a license purchased from him — with a Change Date that
converts it to an open license if he later prefers adoption over selling. It is **not** OSI-approved
open source; that is the accepted cost.

`.uplugin` points `DocsURL`/`SupportURL` at `github.com/irsjofka-alt/IRSofka-UE-AIStation-Core` and
`README.md` still badges it "Proprietary / IRSofka" — that badge should be replaced by the BSL notice
when the repo is actually opened, and this plugin must live in its **own** repository: one work cannot
carry two grants at once. Note that both sides now use BSL 1.1: sharing a licence name is not the
same as sharing a licence *text*, and each repository states its own Licensor, Licensed Work and
Change Date, so the separation is still required.

This plugin is entirely our own code: no AGPL line inside it. So the license is genuinely ours to
choose, and no third-party term forces a hand — with one boundary: it links Unreal Engine headers,
so a distribution must carry no Epic content, and "Unreal Engine" is Epic's mark. **The UE EULA text
has not been read on this machine**, so nothing here states what it permits.
