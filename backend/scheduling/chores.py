"""BETWEEN-RUN actions: the things worth doing while no run is going.

One registry, one dispatcher. These used to be four hooks in four places -
free gems inside the orchestrator's mission flow, the quest scan wedged into
restart_from_home, shatter as a standalone script, completed missions nowhere
at all - each with its own ad-hoc "is it due yet" logic.

Every chore is (name, due, run) and every one of them obeys the same rules:

  * dispatched ONLY from Home, between runs. Never mid-battle. This is the
    whole reason the concept exists - the tower must not be on screen.
  * once per day, flagged in logs/daily_state.json by chore name.
  * a chore that raises is logged and SKIPPED, never fatal. These are
    nice-to-haves squeezed into a gap; none of them is worth stopping a farm
    loop over.
  * at most ONE chore per gap, so a single restart never turns into a
    ten-minute menu expedition. A chore that was attempted and failed has
    used the gap like one that completed (item 7, C1).
"""
import datetime
import time

from towerpilot_workflows import action_contract

# When a chore is due is the schedule policy of its catalog definition (`action_contract.CHORE_SCHEDULE`, a DailyCalendar): the day is the local date
# (it rolls at local midnight), and 03:00 is only the hour from which the first Home gap may run it. This module keeps the registry and the dispatcher.
START_HOUR = action_contract.CHORE_SCHEDULE.floor_hour

# daystate/logger are imported INSIDE the functions that use them: they pull
# settings (CONFIG) and cv2, and this module's registry (CHORES) is consumed
# by the profile compiler's vocabulary - which the dashboard imports, and the
# dashboard must stay importable without loading a runner's config stack.


# P0 (2026-08-18): all daily_state IO delegates to daystate.py (single
# writer, atomic saves). Key format unchanged: chore_<name> = ISO date.
_unsaved: dict[str, str] = {}      # chore -> the day whose mark the daily state refused (its lock stayed held): closed in this process anyway


def _schedule(name: str):
    return action_contract.catalog().get(name).schedule


def _mark(name: str):
    """Close today for `name`. A mark the daily state refuses is kept in this process: the chore is not retried in every gap because a file was busy."""
    from scheduling import daystate
    try:
        daystate.mark_today(f"chore_{name}")
    except OSError as exc:                      # DaystateLockTimeout is one: nothing was written
        from runtime import logger
        _unsaved[name] = action_contract.CHORE_SCHEDULE.day(time.time())     # every chore shares the schedule; this path must not depend on the catalog having `name`
        logger.event("daystate_write_failed", key=f"chore_{name}", error=str(exc)[:150], error_type=type(exc).__name__)
    else:
        _unsaved.pop(name, None)


def decide(name: str, now: float | None = None):
    """The decision on chore `name` now: its DailyCalendar over the day flag in the daily state."""
    from scheduling import daystate
    now = time.time() if now is None else now
    schedule = _schedule(name)
    consumed = _unsaved[name] if _unsaved.get(name) == schedule.day(now) else daystate.get_raw(f"chore_{name}")
    return schedule.evaluate(now, {'consumed': consumed})[0]


def _daily(name: str) -> bool:
    """Standard gate: once a day, any time from START_HOUR.

    No random target minute, unlike the free-gems claim. These fire on the
    first run that ENDS after the hour, which is already unpredictable, and
    tying them to a clock would sooner or later mean interrupting a live run.
    """
    return decide(name).kind == 'due'


# ---------------------------------------------------------------- the chores

def _scan_quests():
    from interactions import questscan
    return questscan.scan()


def _shatter_blue():
    from interactions import shatter
    return shatter.run()


def _housekeeping():
    from scheduling import housekeeping
    return housekeeping.sweep()


CHORES = [
    # (name, due predicate, action). Order is priority: the first one that is
    # due wins the gap, so cheap-and-informative comes before long-and-optional.
    ("quest_scan", lambda: _daily("quest_scan"), _scan_quests),
    ("shatter", lambda: _daily("shatter"), _shatter_blue),
    # Last: pure disk retention (housekeeping.py), no screen involved, so it
    # can afford to wait for a later gap on days with game-facing chores due.
    ("housekeeping", lambda: _daily("housekeeping"), _housekeeping),
]


def _policy() -> dict | None:
    """chore name -> enabled, from the ACTIVE profile's policies.chores.

    NOTHING IS IMPORTED ON THE LEGACY PATH (same rule as combo._blueprint):
    no `active_profile` key = no playerprofile import and every chore is
    enabled, bit-for-bit the pre-profile behaviour.

    The list is an OPT-OUT surface, not an allowlist: a chore the policy
    does not name stays enabled, so a chore added to the registry after a
    profile was written does not silently stop running for that profile.

    Returns None when a profile is bound but its policy cannot be read -
    the FAIL-CLOSED direction here is to skip chores, because running one
    the player disabled (shattering their modules) is the harm, while a
    skipped chore is a nice-to-have deferred to the next gap.
    """
    from settings import CONFIG
    if not CONFIG.get("active_profile"):
        return {}
    try:
        from player import playerprofile
        prof = getattr(playerprofile, "PROFILE", None)
        if not isinstance(prof, dict):
            return None
        entries = (prof.get("policies") or {}).get("chores") or []
        return {e["name"]: bool(e.get("enabled", True))
                for e in entries if isinstance(e, dict) and e.get("name")}
    except Exception:                             # noqa: BLE001
        return None


_disabled_logged: set[tuple[str, str]] = set()   # (date, name) - log once/day

NO_GAME_EFFECT = frozenset({"housekeeping"})     # pure disk retention: its history rows claim no game effect (the others touch the game: effect unknown)


def _bookkeeping(name, step):
    """Run one step of a chore's history. It is bookkeeping beside a chore that touches the game's menus: whatever it raises is logged and dropped."""
    try:
        return step()
    except Exception as exc:                    # noqa: BLE001
        from runtime import logger
        logger.event("action_record_failed", reason="chore", chore=name, error=str(exc)[:150], error_type=type(exc).__name__)
        return None


def _open_row(session, name):
    """The recorder holding the open row of chore `name` (None with no session: a run outside a ledger operation records nothing)."""
    if session is None:
        return None

    def open_it():
        recorder = session.recorder(name)
        recorder.opened(occurrence_key=_schedule(name).day(time.time()))
        return recorder
    return _bookkeeping(name, open_it)


def _close_row(recorder, name, completed):
    if recorder is not None:
        effect = "none" if name in NO_GAME_EFFECT else "unknown"
        close = ((lambda: recorder.closed(result="succeeded", reason="completed", effect=effect)) if completed
                 else (lambda: recorder.closed(result="failed", reason="error", effect=effect)))
        _bookkeeping(name, close)


def _refused_row(session, name):
    if session is not None:
        _bookkeeping(name, lambda: session.recorder(name).check(result="refused", reason="disabled_by_profile"))


def run_due(limit: int = 1, session=None) -> list:
    """Run up to `limit` due chores. Returns the names actually run.

    Call ONLY when on Home with no run going. Returns [] if nothing is due,
    which is the common case. `session` (an operation-scoped ActionSession,
    else None) records each attempt in the action history. The list holds the
    chores that completed; an attempted one that raised used the gap too.
    """
    from runtime import logger
    policy = _policy()
    if policy is None:
        logger.event("chores_skipped",
                     why="a profile is bound but its chores policy is "
                         "unreadable - refusing to run chores it may have "
                         "disabled")
        return []
    ran, attempted = [], 0
    for name, due, action in CHORES:
        if attempted >= limit:
            break
        try:
            if not due():
                continue
        except Exception as e:                    # noqa: BLE001
            logger.event("chore", name=name, ok=False, error=repr(e))
            continue
        if not policy.get(name, True):
            # Disabled by the profile. NOT marked done: re-enabling later the
            # same day lets it run in the next gap. Logged once per day so a
            # due-but-disabled chore does not spam every run boundary.
            key = (datetime.date.today().isoformat(), name)
            if key not in _disabled_logged:
                _disabled_logged.add(key)
                logger.event("chore", name=name, ok=True, result="disabled "
                             "by the profile's chores policy - skipped")
            _refused_row(session, name)
            continue
        recorder = _open_row(session, name)
        completed = False
        try:
            result = action()
            logger.event("chore", name=name, ok=True, result=str(result))
            ran.append(name)
            completed = True
        except Exception as e:                    # noqa: BLE001
            # Logged and skipped: a chore is never worth ending a farm loop.
            logger.event("chore", name=name, ok=False, error=repr(e))
        finally:
            # The row is finished first, then the day is closed. Marked even
            # on failure, so a chore that is broken today does not get retried
            # on every single run boundary for the rest of the day.
            _close_row(recorder, name, completed)
            _mark(name)
            attempted += 1
    return ran
