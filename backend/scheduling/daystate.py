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
"""
import datetime
import json
import os
import time

from settings import ROOT

STATE = os.path.join(str(ROOT), "logs", "daily_state.json")


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
    st = _load()
    st[key] = {"date": _today(), "value": value}
    _save(st)


# -------------------------------------------------------------- UTC values
# v29 (2026-08-27): the game resets its Ad-Gem claim cap at 00:00 UTC, three
# hours off local midnight - a local-date key would over- or under-collect by
# those three hours every single day, so UTC-capped counters get their own
# helpers instead of quietly reusing the local-date ones.
def _utc_today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def get_utc_today(key: str, default: int = 0) -> int:
    rec = _load().get(key)
    if isinstance(rec, dict) and rec.get("date") == _utc_today():
        return rec.get("value", default)
    return default


def bump_utc_today(key: str, by: int = 1) -> int:
    """Increment a UTC-date-scoped counter and return the new value."""
    st = _load()
    rec = st.get(key)
    value = (rec.get("value", 0)
             if isinstance(rec, dict) and rec.get("date") == _utc_today()
             else 0) + by
    st[key] = {"date": _utc_today(), "value": value}
    _save(st)
    return value


# ---------------------------------------------------------------- flags
def mark_today(key: str) -> None:
    """Set a daily flag (combo_<phase> / chore_<name> style: bare ISO date)."""
    st = _load()
    st[key] = _today()
    _save(st)


def flag_today(key: str) -> bool:
    """Is the daily flag set for today?"""
    return _load().get(key) == _today()


# ---------------------------------------------------------------- misc
def get_raw(key: str, default=None):
    """Read any key verbatim (orchestrator's free-gems scheme keeps odd shapes)."""
    return _load().get(key, default)


def set_raw(key: str, value) -> None:
    st = _load()
    st[key] = value
    _save(st)


def clear(key: str) -> None:
    st = _load()
    if key in st:
        del st[key]
        _save(st)
