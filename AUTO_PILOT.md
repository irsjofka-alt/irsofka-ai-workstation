# AUTO_PILOT.md — the looping contract (read by the engine that is woken)

Woken by `tools/awakener.py` with one fixed line: `RUN AUTO_PILOT.md`. This file *is* the whole
content of that command. No other command has ever been typed by that worker, and no model edits
this file from the wake path — if the text changes, the contract changed, and that is a human hand
at a terminal.

## The loop the operator demanded (2026-10-15 03:17, his own words)

> "ketika tidak ada perintah → Looping Roadmap — Selesaikan. Ketika Roadmap.md selesai dan tidak
> ada task, dan operator tidak ada perintah → Looping Sharpening ai-station, jadikan ai-station
> ini diri kamu sendiri, Keep Improving."

## What you do, in order, without asking

1. **Extend your lease** if you hold an item: `python3 tools/work_order.py lease <id>`.
2. **Take the next one** — the queue is read from the ledger, never from memory:
   `python3 -c "import sys;sys.path.insert(0,'tools');import db_state,work_order as wo,drainer as dr;
   c,b=db_state.get_db_connection();print((dr.next_ready(c,b) or {}).get('id'))"`
3. **If there is an item** → do it, then close it with **numbers out**, not prose:
   `python3 tools/work_order.py finish` through the module, or mark COMPLETED with an `evidence=`
   that contains measured results (e.g. `269/269`, `exit=1`, `4 commit`).
4. **Repeat** from step 2 until the ON window expires or a breaker opens. Your final report MUST
   contain numbers; finishing without numbers means you did not finish.

## The binding order: TASKS FIRST, SHARPENING LAST

Written 2026-10-15 20:2x on the operator's direct order, after his loop was proven to drift. His
own words:

> "YANG PALING UTAMA ITU TASK, SKILL ITU kalau tidak ada TASK atau ROADMAP.MD bahkan sudah tidak
> ada yang bisa di kerjakan baru SKILL yang di kerjakan ... bahkan ROADMAP.MD saja masih JAUH"

So this ladder is not advice, and it is enforced in two places: `work_rank()` in
`tools/work_order.py` orders every offer, and step 2 above tells you to take the head of that order.

| rank | what | real examples |
|---|---|---|
| 0 | a command the operator typed himself (`origin='operator'`) | `queue`, Human Decide answers, instructions written into SQL |
| 1 | ROADMAP items (`origin='roadmap'`) | F11/MCP-1..3, F10.x, F9.x — anything in `ROADMAP_ITEMS` |
| 2 | living legacy rows (`origin='legacy'`) | old working rows whose lease is genuinely still held |
| 3 | work the **machine wrote for itself** (`origin='idle'`) | skill sharpening, re-reading a decision note, `FOLLOWUP_CONTRACTS` |

The rules that bind rank 3, and these are the ones that were broken:

- **Never take a sharpening row while any rank 0–2 item can be worked.** If `next_ready()` hands you
  an `origin='idle'` row while `ROADMAP.md` still has an F at 🟡 or ⬜, that is NOT the signal "the
  roadmap is done". It means the roadmap steps were never turned into rows. Fix the rows
  (`work_order.py seed --roadmap`); do not go and do the sharpening instead.
- **Sharpening is filler, and filler only speaks when the main line is silent.** Before you touch a
  skill file, write in your report how many roadmap rows are still open and why none of them could
  be claimed. Without that sentence, sharpening is drift.
- **The drift was measured, and it was expensive.** Tonight `ready_items()` sorted by `ORDER BY id`
  and `drop_stale_legacy` held back every working row without a live lease, so the only offerable
  row in the ledger was id 286 — a skill-sharpening line the machine had written for itself — while
  the operator's MCP instruction (turns 457/458, 15:26: "1 repo ini jadi memiliki 3 repo: IRSofka
  Blender MCP, IRSofka ComfyUI MCP, IRSofka Unreal Engine MCP") had **no row at all**. The loop was
  not skipping school; it could not pick up work that was never in the ledger. That is fixed now:
  the three rows exist (`F11/MCP-1..3`, ids 294/295/296, `origin='roadmap'`) and the ranking puts
  them ahead of anything the machine invents.
- **If you genuinely find no rank 0–2 work and the roadmap is still long**, the map has not been
  projected into the queue — that is a **finding**, not a licence to do something else. Raise it
  (`decide`), do not quietly swap jobs.

## The main task in progress (do not go looking elsewhere for it)

F11 — MCP ownership and what may be published. The operator opened it on 2026-10-15 and the licence
decision has landed: **BSL 1.1 for the whole published family** (turn 461: "jadikan BSL saja semua"),
with the licensor entity `PT IRSofka Data Teknologi` (turn 462). What is chased now, in gate order:

1. `F11/MCP-1` — IRSofka ComfyUI MCP as far as `origin`, VRAM gate proven in both directions, and
   installed in **both** CLIs. Measured today: the repo exists, **0 remote, 0 registrations, 0 wheel**.
2. `F11/MCP-2` — IRSofka Blender MCP from a **permissive base**. The one installed now (`mcp-blender`)
   has been AGPL-3.0-or-later since v0.4.0 with its commercial option **withdrawn** (commit
   `8f64128`): deriving from it and labelling the result BSL is a one-way door, so the gate asks for
   the IRSofka repo's own permissive `LICENSE`, not for the directory to exist.
3. `F11/MCP-3` — IRSofka Unreal Engine MCP as its own repo, **after** F11 step 0 is correct:
   `set_transform` must return what `get_transform` reads, and `spawn` must prove the class it made.
   Until then, splitting the repo only packages something that cannot prove anything.

## What does not change

- **`level` is not yours.** `arm`/`disarm` refuse without `/dev/tty`, and this worker never calls
  them either. A tick that only writes `OBSERVED` means the hand is still `observe`, and only the
  operator can raise it, from a terminal.
- **`mode` OFF means silence.** No claim, no typing.
- **A dirty tree means no claim.** §6 has no mode exception; what holds you is
  `night_state.blocking`, and it is read, not guessed.
- **One active command per engine.** You do not pile work onto a session that is already running.
- **A push needs a verdict.** `python3 tools/work_order.py push-gate` exits zero only when a
  `push_verified` event names that sha. Without a verdict the commit stays local and enters the
  morning digest.
- **Do not invent work.** `ensure_idle_item` writes a row only when the ledger shows measured debt.
  Creating work to look busy is a failure, not productivity — and since tonight, an invented row may
  no longer overtake the work the operator asked for either.

## How to shut it down (operator, without any AI's permission)

```
systemctl --user stop irsofka-awakener.timer     # waking stops
python3 tools/work_order.py disarm               # hand drops to observe
python3 tools/work_order.py autopilot off        # machine stops claiming
```
