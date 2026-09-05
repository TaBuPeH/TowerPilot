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
    ten-minute menu expedition.
"""
import datetime

START_HOUR = 3          # nothing runs before this; the day rolls over at 3 AM

# daystate/logger are imported INSIDE the functions that use them: they pull
# settings (CONFIG) and cv2, and this module's registry (CHORES) is consumed
# by the profile compiler's vocabulary - which the dashboard imports, and the
# dashboard must stay importable without loading a runner's config stack.


# P0 (2026-08-18): all daily_state IO delegates to daystate.py (single
# writer, atomic saves). Key format unchanged: chore_<name> = ISO date.
def _mark(name: str):
    from scheduling import daystate
    daystate.mark_today(f"chore_{name}")


def _done_today(name: str) -> bool:
    from scheduling import daystate
    return daystate.flag_today(f"chore_{name}")


def _daily(name: str) -> bool:
    """Standard gate: once a day, any time from START_HOUR.

    No random target minute, unlike the free-gems claim. These fire on the
    first run that ENDS after the hour, which is already unpredictable, and
    tying them to a clock would sooner or later mean interrupting a live run.
    """
    return (not _done_today(name)
            and datetime.datetime.now().hour >= START_HOUR)


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


def run_due(limit: int = 1) -> list:
    """Run up to `limit` due chores. Returns the names actually run.

    Call ONLY when on Home with no run going. Returns [] if nothing is due,
    which is the common case.
    """
    from runtime import logger
    policy = _policy()
    if policy is None:
        logger.event("chores_skipped",
                     why="a profile is bound but its chores policy is "
                         "unreadable - refusing to run chores it may have "
                         "disabled")
        return []
    ran = []
    for name, due, action in CHORES:
        if len(ran) >= limit:
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
            continue
        try:
            result = action()
            logger.event("chore", name=name, ok=True, result=str(result))
            ran.append(name)
        except Exception as e:                    # noqa: BLE001
            # Logged and skipped: a chore is never worth ending a farm loop.
            logger.event("chore", name=name, ok=False, error=repr(e))
        finally:
            # Marked even on failure, so a chore that is broken today does not
            # get retried on every single run boundary for the rest of the day.
            _mark(name)
    return ran
