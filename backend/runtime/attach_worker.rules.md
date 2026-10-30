# Takeover worker rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-24
> **Updated:** 2026-10-24
> **Owner:** runtime/attach_worker.py
> **Specification:** [roadmap item 3](../logs/reviews/roadmap-item3/spec.md), section 3.4 and 3.6, package T3b

## Contract

[attach_worker.py](attach_worker.py) owns the worker's decisions for an explicit takeover of the battle on screen: whether the accepted operation may be trusted, whether one
frame proves a battle that is not a tournament, which collector record is the predecessor, and how a refusal is recorded. The shapes it reads belong to
[attach_policy.py](../player/attach_policy.py). It never captures, taps or swipes: [orchestrator.py](../orchestrator.py) keeps every device call and asks this module.

---

## The accepted operation

The operation is the authority, not argv or the environment. `from_operation` returns an `Attach` for a `run` operation that carries both the request `attach` and the envelope
`attach` block, and `None` for an ordinary run. It refuses (`attach_envelope_invalid`) one that carries only one of them, a delegated child, two disagreeing copies, a run key that
is not the selected one, a preset (the worker's, the envelope's or the one in the frozen configuration) that still carries a stripped key, or a control that turns repeat or rewards on.

## The entry gate

One frame, read before the first input and before any ledger record. `attach_input_revoked`, then `attach_no_battle` (the screen is not the battle or the wave is unreadable), then
`attach_tournament` or `attach_tournament_unknown` (a missing or unreadable badge template proves nothing), then `attach_wave_mismatch` (the wave is more than `WAVE_MARGIN` below
the wave the person confirmed). A refusal is recorded on the operation (`failed`, with the code); no battle record is written, no predecessor is closed, no input is sent.

## The predecessor

The server names the collector's record (`battle_id`, `generation`, `operation_id`). `settle_predecessor` checks that it exists, belongs to the same device, instance, account and serial
and to the named operation and generation. A live record whose operation has ended is finished `interrupted` with `driver_operation_ended`; one already interrupted for a collector's
reason is kept. A conflicting finalization of an already finished record counts as finalized. A collector operation that is still running, or any other mismatch, refuses
(`attach_predecessor_mismatch`) before a record is created. Only that record is ever linked: a record the process finds by itself is closed as for any ended owner but never linked.

## What the orchestrator does with an `Attach`

| Place | Behaviour |
|---|---|
| Entry | gate, then predecessor, then the session; attached readiness is checked before the first capture |
| Start preparation | does nothing (the battle is already running) |
| `_tournament_locked` | adds the lock "battle not started by this run", which every surrender path asks |
| `marks()` | empty while the tier is unknown; the declared tier's table otherwise |
| Live controls | `repeat` is not bound, so it is not offered and a patch for it is refused; rewards follow the frozen control (off) |
| Death screen | logs `attach_boundary` and returns before stats collection, death rules, the run flag and the restart; the `stop_after_run` flag is left alone |
| Replaced battle | the wave the entry gate read is the first baseline (`tracker.last` and `wave_hwm`), so a gate wave 500 followed by wave 10 is a replacement; the confirmed backward wave finishes the record, logs `attach_boundary` and returns before any successor and any input |
| Tournament results dialog | finishes the record, logs `attach_boundary` and returns before the OK taps: a takeover never presses a dialog it did not open |
| Frames a helper captures itself | the gem collectors carry a guard that raises `AttachReplaced` (an `Abort`, so a collector records `ownership_lost` and re-raises, and no tap is sent) for a battle frame whose wave is more than `WAVE_MARGIN` below the confirmed battle's highest wave; the main loop logs `attach_input_held` and judges the next frame. The fast wall watch refuses its burst on such a frame, follows the highest wave it confirms, and whatever it returns the pass ends with `refresh`: the frame in hand is older than the watch, so no rule, shopping or gem step acts on it. A confirmed battle at or below `WAVE_MARGIN` cannot be told from a new wave-1 battle by its wave alone; the margin is the spec's accepted limit. The loop and the gate read one constant, `WAVE_MARGIN` |
| `operation_worker` | keeps `stop_after_run` for an operation whose request carries `attach` |
