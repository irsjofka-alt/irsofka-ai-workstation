# AUTO_PILOT.md — looping contract

This file is the full text of the `RUN AUTO_PILOT.md` command used by `tools/awakener.py`.
It defines how the woken engine selects work, closes it, and stops.

## Loop

1. Extend the lease if an item is held: `python3 tools/work_order.py lease <id>`.
2. Read the next item from the ledger, never from memory:
   `python3 -c "import sys;sys.path.insert(0,'tools');import db_state,work_order as wo,drainer as dr;c,b=db_state.get_db_connection();print((dr.next_ready(c,b) or {}).get('id'))"`
3. If an item exists, do it and close it with measured evidence:
   `python3 tools/work_order.py finish` through the module, or mark `COMPLETED` with an
   `evidence=` value that contains numbers (`269/269`, `exit=1`, `4 commits`).
4. Repeat from step 2 until the ON window expires or a breaker opens. The final report must
   contain measurements; a report without numbers is incomplete.

## Priority order

Tasks come first. Skill sharpening is filler and only runs when no task and no claimable
roadmap item exists. `work_rank()` in `tools/work_order.py` enforces the order below.

| rank | source | examples |
|---|---|---|
| 0 | operator command (`origin='operator'`) | `queue`, Human Decide answers, instructions written into SQL |
| 10 | roadmap item (`origin='roadmap'`) | F11/MCP-1..3, F10.x, F9.x in `ROADMAP_ITEMS` |
| 20 | open legacy row (`origin='legacy'`) | pre-scheme work; a row held by another session stays visible and `claim()` refuses it |
| 25 | legacy row with a dead lease (`RANK_STALE_LEGACY`) | unfinished work nobody holds; offered, never ahead of rank 0/10 |
| 30 | machine-generated work (`origin='idle'`) | skill sharpening, decision-note review, `FOLLOWUP_CONTRACTS` |

The ranks are spaced by tens so a new level can be inserted without fractions.

### Guards on rank 30

- `ensure_idle_item()` and `queue_followup()` call `roadmap_open()` first and refuse with
  `IDLE_HELD_BY_ROADMAP`, `IDLE_MAP_NOT_PROJECT`, or `IDLE_DEBT_UNKNOWN`. An unreadable
  ledger is asked first and never treated as zero rows.
- Only projectable open phases block sharpening. A phase that is open but has no projected
  ledger row is a finding: run `work_order.py seed --roadmap`; do not switch to sharpening.
- A sharpening report must state how many roadmap rows remain open and why none could be
  claimed. Without that, sharpening is out of scope.
- If no rank 0–2 work exists while the roadmap still has open phases, raise the missing
  projection instead of silently changing jobs.

## Boundaries

- **`level` is human-only.** `arm`/`disarm` refuse without `/dev/tty`; this worker never calls
  them. A tick that only writes `OBSERVED` leaves the hand at `observe`.
- **Mode OFF means silence.** No claim, no typing.
- **A dirty tree means no claim.** Read `night_state.blocking`; do not guess.
- **One active command per engine.** Do not pile work onto a running session.
- **A push needs a verdict.** `python3 tools/work_order.py push-gate` succeeds only when a
  `push_verified` event names the SHA. Without it, the commit stays local.
- **Do not invent work.** `ensure_idle_item` writes a row only for measured debt. Invented
  work is a failure, and it may not overtake operator or roadmap work.

## Current work

F11 — MCP ownership and publication. The licence is BSL 1.1 with a four-year conversion to
Apache-2.0 for the published family, under PT IRSofka Data Teknologi. The claimable rows are
read from `work_order.py next`; do not restate status here.

## Shutdown

```bash
systemctl --user stop irsofka-awakener.timer     # stop waking
python3 tools/work_order.py disarm               # drop the hand to observe
python3 tools/work_order.py autopilot off        # stop claiming
```
