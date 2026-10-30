"""Account-scoped reward clocks; independent from per-run gathering policies.

When something is due is the schedule policy of the action's catalog definition (`towerpilot_workflows.action_contract`: `Cooldown` for the five-minute
badge checks, `DailyCalendar` for the daily free-gems store). This module holds the state they read, in the daily state, and nothing else: no
comparison, no offset, no deadline arithmetic of its own (item 7, A3a).

A write the daily state refuses (its lock stayed held, the disk failed) is logged and kept in this process (`_unsaved`), where it stays authoritative: the
check or the claim that was just made is not repeated on the next pass because a file could not be written. A restart forgets it, which is the old crash behaviour.
"""
import time

import settings
from scheduling import daystate
from towerpilot_workflows import action_contract

# The reward clocks (side-menu visits on a 5-minute check) ...
REWARD_KEYS = ('free_store_gems', 'daily_missions', 'event_missions', 'guild_progress')
# ... plus the in-battle global behaviours: `demon_mode_always` fires Demon
# Mode whenever it is ready, on every run, whatever the rescue policy says
# (the Demon Mode kill quests need the kills, not a held rescue - user,
# 2026-09-14). Every key here is a profile-level true/false switch.
KEYS = REWARD_KEYS + ('demon_mode_always',)

_unsaved = {}       # daily-state key -> the value this process wrote and the file refused


def enabled(body, name):
    return body.get('global_behaviors', {}).get(name, False) is True


def account_key():
    return 'global_rewards:' + str(settings.instance().get('account') or settings.CONFIG['active_instance'])


CHECK = action_contract.REWARD_CHECK      # the five-minute badge check: one policy, shared by the catalog's three check actions


def _read(key, default=None):
    return _unsaved[key] if key in _unsaved else daystate.get_raw(key, default)


def _write(key, value):
    try:
        daystate.set_raw(key, value)
    except OSError as exc:                  # DaystateLockTimeout is one: nothing was written
        _unsaved[key] = value
        from runtime import logger
        logger.event('daystate_write_failed', key=key, error=str(exc)[:150], error_type=type(exc).__name__)
    else:
        _unsaved.pop(key, None)


def _next_key(name):
    return account_key() + ':' + name + ':next'


def decide(name, now=None):
    """The decision of the five-minute check of reward behaviour `name`: the check Cooldown over the stored `next` time (wall clock)."""
    now = time.time() if now is None else now
    decision, _ = CHECK.evaluate(now, {'next_at': float(_read(_next_key(name), 0))})
    return decision


def settle(name, now=None):
    """The check was made at `now`: store when the next one is allowed."""
    now = time.time() if now is None else now
    _write(_next_key(name), CHECK.after(now))


def check_interval(name):
    """Seconds between two checks of `name`, for the event log."""
    return CHECK.floor_sec


STORE = 'free_store_gems'


def _calendar():
    return action_contract.catalog().get(STORE).schedule


def store_decide(now=None, *, retry_at=None, retry_now=None):
    """The decision of the daily free-gems store: DailyCalendar over this account's closed day. `retry_at` is the host's retry gate (its own monotonic clock,
    compared with `retry_now`); `store_attempt` makes it."""
    now = time.time() if now is None else now
    key = account_key() + ':store'
    state = {'consumed': _read(key), 'retry_at': retry_at}
    decision, _ = _calendar().evaluate(now, state, {'seed': key, 'retry_now': now if retry_now is None else retry_now})
    return decision


def store_attempt(retry_now):
    """When another attempt of the same day may start, after one began at `retry_now` (the host's monotonic clock)."""
    return _calendar().attempt(retry_now)


def store_settle(now=None):
    """The claim was confirmed: this UTC day is closed for this account."""
    now = time.time() if now is None else now
    _write(account_key() + ':store', _calendar().day(now))