"""Per-day shared state (logs/daily_state.json) - THE single writer.

Four schemes used to write this file independently (combo phase marks,
chores marks, orchestrator free-gems keys, this module's counters), three of them
through a CWD-relative path that broke whenever a process started outside
the autopilot dir. P0 of the configurability plan (2026-08-18) makes this
module the one writer, with an ABSOLUTE path and atomic saves; combo.py /
chores.py / orchestrator.py delegate here with their key formats unchanged, so
mixed old/new processes stay compatible across a boundary restart.

Key shapes (all coexisting in the one JSON file):
  <key>: "ISO-date"                 - a daily FLAG (mark_today/flag_today)
  <key>: {"date": ISO, "value": V}  - a date-scoped VALUE (get/set_today);
                                      an earlier date reads as the default:
                                      yesterday's shard count must never
                                      bleed into today's quota.

SERIALIZED WRITES (roadmap item 7, R-0). The atomic replace below stops a torn file, not a lost update: the scheduler, the shard runner and the
orchestrator runner are separate processes, and every setter used to load, change and save with nothing between the three steps - two writers of
different keys could each save the other's key away, and two increments of one key could both read n and both write n+1. Every WRITE is now one
step under a lock: this process's threads one at a time, other processes by the OS byte lock on <STATE>.lock (runtime/file_lock.py, the primitive
of the device lease). Waiting is bounded (LOCK_WAIT_SEC); a lock that stays held raises DaystateLockTimeout, logged as `daystate_lock_timeout`,
and nothing is written. READS take no lock (the replace is atomic, so a reader sees the file before or after a write, never a torn one). A caller
that needs "read, decide, write" must do it inside `update` or `increment_today`: a get followed by a set is two steps and is NOT protected.

The file format and every key are unchanged, so an old process (which does not take the lock) and a new one still agree on the data; only a pair
of old and new WRITERS running together is unprotected, until every writer runs the new code.
"""
import contextlib
import datetime
import json
import os
import threading
import time

from runtime import file_lock
from settings import ROOT

STATE = os.path.join(str(ROOT), "logs", "daily_state.json")
LOCK_WAIT_SEC = 10.0      # a holder keeps the lock for one load + save (milliseconds); ten seconds means something is wrong, not busy


class DaystateLockTimeout(OSError):
    """The write lock stayed held for LOCK_WAIT_SEC; nothing was written."""


_guard = threading.Lock()                        # this process's threads, one writer at a time (a second handle on the same file would not lock)
_held = {"handle": None, "owner": None}          # the OS lock this process holds, and the thread whose write step holds it


def _timed_out(reason: str) -> DaystateLockTimeout:
    try:
        from runtime import logger
        logger.event("daystate_lock_timeout", state=os.path.basename(STATE), waited_sec=LOCK_WAIT_SEC, reason=reason)
    except Exception:           # noqa: BLE001 - a log that cannot be written must not hide the timeout
        pass
    return DaystateLockTimeout(f"{os.path.basename(STATE)} stayed locked for {LOCK_WAIT_SEC:g} s ({reason}); nothing was written")


@contextlib.contextmanager
def _locked():
    """One write step: exclusive across threads and processes, bounded. NOT re-entrant, on purpose: a write started inside a write step would be saved
    over by the outer step's own (older) copy of the file, i.e. silently lost - so it raises instead."""
    if _held["owner"] == threading.get_ident():
        raise RuntimeError("a daystate write was started inside a daystate write step: the outer step would save over it")
    if not _guard.acquire(timeout=LOCK_WAIT_SEC):
        raise _timed_out("another thread of this process")
    try:
        try:
            handle = file_lock.lock(STATE + ".lock", LOCK_WAIT_SEC)
        except file_lock.LockTimeout:
            raise _timed_out("another process") from None
        _held["handle"], _held["owner"] = handle, threading.get_ident()
        try:
            yield
        finally:
            _held["handle"], _held["owner"] = None, None
            file_lock.unlock(handle)
    finally:
        _guard.release()


def _load() -> dict:
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(st: dict) -> None:
    """Atomic: tmp + os.replace, so a concurrent reader can never see a
    torn file. Windows can refuse the replace while a reader has the file
    open - retry briefly rather than corrupting or crashing a runner."""
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = f"{STATE}.tmp-{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1)
    for attempt in range(5):
        try:
            os.replace(tmp, STATE)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, STATE)      # last try surfaces the real error


def _today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- values
def get_today(key: str, default: int = 0) -> int:
    rec = _load().get(key)
    if isinstance(rec, dict) and rec.get("date") == _today():
        return rec.get("value", default)
    return default


def set_today(key: str, value) -> None:
    update(key, lambda _: {"date": _today(), "value": value})


def increment_today(key: str, by: int = 1) -> int:
    """Add `by` to a date-scoped counter (yesterday's value reads as 0) in one locked step; returns the new value.

    This is how a counter is incremented. `set_today(key, get_today(key) + 1)` is two steps: two writers can both read n and both write n + 1."""
    def bump(rec):
        today = _today()          # read once: the day cannot change between the comparison and the write
        value = rec.get("value", 0) if isinstance(rec, dict) and rec.get("date") == today else 0
        return {"date": today, "value": value + by}
    return update(key, bump)["value"]


# ---------------------------------------------------------------- flags
def mark_today(key: str) -> None:
    """Set a daily flag (combo_<phase> / chore_<name> style: bare ISO date)."""
    update(key, lambda _: _today())


def flag_today(key: str) -> bool:
    """Is the daily flag set for today?"""
    return _load().get(key) == _today()


# ---------------------------------------------------------------- misc
def get_raw(key: str, default=None):
    """Read any key verbatim (orchestrator's free-gems scheme keeps odd shapes)."""
    return _load().get(key, default)


def set_raw(key: str, value) -> None:
    update(key, lambda _: value)


def update(key: str, change):
    """Replace the stored value of `key` with `change(current)` in one locked step and return the new value.

    `current` is the raw stored value (None if the key is absent). `change` runs while the lock is held: it must be quick, it must not write through this
    module (that raises RuntimeError rather than being lost), and a caller never holds this lock across game input or a screen action."""
    with _locked():
        st = _load()
        new = change(st.get(key))
        st[key] = new
        _save(st)
        return new


def clear(key: str) -> None:
    with _locked():
        st = _load()
        if key in st:
            del st[key]
            _save(st)
