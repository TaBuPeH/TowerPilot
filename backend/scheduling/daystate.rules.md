# Daily state rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-23
> **Updated:** 2026-10-23
> **Owner:** scheduling/daystate.py

## Contract

[daystate.py](daystate.py) is the only writer of `logs/daily_state.json`, the per-day state the scheduler, the shard runner and the orchestrator runner share. Callers
decide what a key means; this module decides how it is read and written. The file format and every key are unchanged by the serialization below: an old process and a
new one read the same data.

Key shapes: `<key>: "ISO-date"` is a daily flag (`mark_today`, `flag_today`); `<key>: {"date": ISO, "value": V}` is a date-scoped value (`get_today`, `set_today`,
`increment_today`); an earlier date reads as the default, so yesterday's count never bleeds into today's quota. `get_raw`, `set_raw` and `update` keep any other shape
verbatim. A reader that needs a UTC date does not use a helper of this module: the daily store of `global_rewards.py` keeps the UTC date string it closed under in a raw key,
and its day arithmetic is the `DailyCalendar` of the catalog (`towerpilot_workflows/action_contract.py`). The v29 UTC counter helpers are gone (item 7, R-6): nothing called them.

## Writes are serialized

The scheduler, the shard runner and the orchestrator runner are separate processes. Every write is one step under a lock: this process's threads one at a time, other
processes by the OS byte lock on `daily_state.json.lock` ([runtime/file_lock.py](../runtime/file_lock.py), the primitive of the device lease). The step loads the file,
changes one key and saves it atomically, so two writers of different keys cannot save each other's key away, and `increment_today` cannot lose a count.

- **Read, decide, write must be one step.** A `get_today` followed by a `set_today` is two steps and is NOT protected. Use `increment_today(key, by)` for a counter and
  `update(key, change)` for anything else; `change(current)` receives the raw stored value (None if absent) and returns the new one. It runs while the lock is held, so it
  must be quick and must not touch the screen, the device or the network.
- **Not re-entrant, on purpose.** A write started inside a write step would be saved over by the outer step's older copy of the file; it raises `RuntimeError` instead of
  being lost silently.
- **Reads take no lock.** The atomic replace means a reader sees the file before or after a write, never a torn one.
- **The wait is bounded.** `LOCK_WAIT_SEC` (10 s) is far longer than a holder needs (a load and a save). When it expires the write raises `DaystateLockTimeout`, logs
  `daystate_lock_timeout` and writes nothing. A caller's own in-process record of "already attempted" (the chore and combo callers keep one) still stops a second attempt.
- **Nothing is stolen and nothing goes stale.** The OS releases the lock when its holder exits or is killed. The lock file is never deleted.

## What this does not protect

- **A pair of old and new writers.** A process running code from before this change does not take the lock. Until every writer (each runner and the scheduler) has been
  restarted on the new code, the file is exactly as protected as it was; restarts happen at a runner's natural boundary and are a separate, authorized operation.
- **Serialization is not exactly-once.** It makes each write whole; it does not make a daily effect happen once. That is the job of the flag a caller sets after the effect
  and of its own attempted record.
- **Installation-wide keys.** `chore_<name>`, `combo_*` and `shard_runs` belong to the installation, not to an account; `global_rewards:<account>:...` belongs to one
  account. This module does not change that.
