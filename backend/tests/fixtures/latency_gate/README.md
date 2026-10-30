# Latency gate fixtures (roadmap item 7, spec 3.7)

> **Status:** Active (package r18c: the gate exists and passes on the unmigrated code; no behaviour changed)
> **Type:** Rules
> **Created:** 2026-10-23
> **Updated:** 2026-10-25
> **Owner:** Backend
> **Spec:** [roadmap-items-6-7/spec.md](../../../logs/reviews/roadmap-items-6-7/spec.md) section 3.7
> **Tags:** latency, fast rules, rescue, fleet nuke, orchestrator

## What this protects

The fast wall loop of the orchestrator (`_fast_wall_watch`) races a wall drain that is faster than a main-loop pass. A rescue tap that is late loses the run. Rows C-3 (fast rescue)
and C-4 (fleet nuke) of the caller checklist move the timing of those paths onto the shared schedule policies. Before either moves, this gate must exist and pass on the unmigrated code,
so that what a migration adds can be measured against the code as it was.

## The files

| File | What it is |
|---|---|
| `orchestrator_unmigrated.py.txt` | A byte copy of `backend/orchestrator.py` as it was when the gate was built. It is the reference of the timing replay and it must NEVER change (a test pins its hash). Its eleven fast-path functions are byte-identical to the checkout's `orchestrator.py` of the day (the report records the comparison). It is a `.txt` so nothing imports, compiles or lints it. |
| `baseline_structural_v1.json` | The I/O of one pass of each of fourteen paths of that snapshot (sqlite connections, file opens, lock acquisitions, image writes, log events, shots), up to the first tap and in total. |
| `../../support/latency_gate.py` | The harness: scripted worlds, instruments, the paired replay, the verdicts, the artifact. |
| `../../unit/test_action_latency_gate.py` | The gate and its negative controls. |
| `../../unit/test_action_latency_import_order.py` | The same controls in three fresh interpreters: gate alone, `global_rewards` imported for real first, `test_p4_interpreter._import_live` first. Variants include name-imported aliases (`from scheduling.daystate import get_raw`): they bind the metered function because the load wraps the fake daily state before any consumer imports it. |
## The two gates

1. **Structural (hard).** No path may do more I/O than the baseline, before its first tap or in total. A policy check is a comparison on host state; an execution record is written after the
   fire returns, never before the tap. A log event is counted as a file open and append (it is one in production). So is a call to the daily state (`scheduling/daystate.py`) - by
   CHARGE: the orchestrator runs under the fakes of `_fakes.py`, where the daily state opens nothing, so the meter charges each call what the real module registers (a read is one file
   open, any write a locked load-and-save: 3 opens and 3 lock acquisitions as the meter counts them; `DAYSTATE_COST` in `latency_gate.py`). The charge is a fixed model of the real
   module, pinned by `test_a_fake_daily_state_is_charged_what_the_real_one_registers`, which fails if the real call counts change; it is not a measurement of the code under test.
   Everything else is measured. The charge applies however the orchestrator reaches the daily state (its own import, `scheduling.global_rewards`, a late import) and in whatever order
   the test files ran: `load_orchestrator` executes `scheduling.global_rewards` afresh against its own fake, puts back the module it found, and leaves no module it imported bound to a
   fake. A late import of `scheduling.daystate`, or of a `scheduling.global_rewards` nothing had imported, reaches the real daily state, which the structural pass points at a scratch
   file, never at `logs/daily_state.json`.
2. **Timing (measured; the targets are provisional).** A seeded workload of 10,000 iterations is replayed through the snapshot and the live orchestrator, back to back, in alternating
   order, on identical scripted worlds. p99 of the added whole-call cost must be at most 100 microseconds, and p99 of the added time from the deciding frame to the first tap at most
   1 millisecond, overall and on every path. A miss is repeated once on another seed; the gate fails only if the repeat misses too.

## What it does not measure

Detection, capture and the adb round trip are fakes: it bounds what a migration ADDS, not the absolute latency of a rescue. The timing replay stubs `BattleSession.checkpoint` (its body is
identical on both sides); the structural pass runs the real checkpoint over a temporary ledger.

Limits, stated so the gate is not read as more than it is:

- Only the daily state is charged. The other fakes of `_fakes.py` (`runflag`, `store`, `runlog`, `missions`, `shopper`, `tourney`, `loadout`, `chores`) still open nothing, so I/O routed
  through them is invisible to the structural meter. This package does not close that class.
- The timing replay runs the fake daily state unmetered: a charge is a counting rule, not a timing proof, so a state read or write adds nothing to the microseconds. The structural counts
  refuse it; the timing numbers do not see it. `World.begin()` does not reset the fake's `_d` between scripts.
- `test_p4_interpreter._import_live` still leaves `scheduling.global_rewards` bound to a fake for the rest of its own process, so a parity test run after it in one process can still fail.
  That leak is in the test module, not here; the gate neither depends on it nor adds to it.
- A module an earlier test left bound to ITS fakes is left as found; only a module first imported inside the load window is dropped.
- The migrated code is not measured here: r18f fast migrations rerun this gate on r18e's final `orchestrator.py`.

## Regenerating

Only the structural baseline can be regenerated, only on the snapshot, and only if the harness itself changed:

    TP_REGENERATE_LATENCY_BASELINE=1 python -m pytest backend/tests/test_action_latency_gate.py -k capture_the_structural_baseline

Never after a migration: the baseline is the proof of what the code did before. To keep the artifact of a run (source and build hashes, hardware, runtime, warm-up, distributions):

    TP_LATENCY_GATE_ARTIFACT=<path> python -m pytest backend/tests/test_action_latency_gate.py -k added_cost_of_the_live
