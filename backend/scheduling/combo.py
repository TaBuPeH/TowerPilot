"""COMBO mode: one day-plan that owns the tower and switches what it farms.

The user's schedule, verbatim in behaviour:

    00:00  coin farming is the default - if anything else is running, it ends
    08:00  when the CURRENT RUN ENDS, switch to ~150 shard runs, then coin farm
    19:00  on tournament days (Wed/Sat), one tournament run, then coin farm

Two rules shape the whole design.

NOTHING IS INTERRUPTED MID-RUN. Every switch is "when the next run is over".
The scheduler never kills a runner - it writes runflag and waits for the
process to leave at its own death handler, where the wave is lost anyway and
the run log has already been collected. A phase boundary can therefore arrive
up to a full run late, and that is correct, not a bug.

THE CLOCK PROPOSES, THE GAME DISPOSES. Wednesday and Saturday are when
tournaments are SCHEDULED, not proof that one is open - so the weekday only
decides whether to look. tourney.setup() does the looking and refuses on
anything it cannot read, which is what keeps a gem entry from being spent on a
misread screen. A refused tournament closes the phase for the day rather than
retrying in a loop.

Phases are marked done in the same daily_state.json the chores use, keyed by
date, so a restart mid-day resumes the plan instead of replaying it.
"""
import datetime
import copy
import json
import os
import sqlite3
import subprocess
import sys
import time

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python scheduling/combo.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from scheduling import daystate
import flows
from runtime import logger
from scheduling import runflag
import settings
from settings import CONFIG

STATE = "logs/daily_state.json"

SHARD_HOUR = 8
SHARD_RUNS = 100               # 0 disables the shard block entirely
                               # (was 150; user capped 2026-08-17)
TOURNEY_HOUR = 19
TOURNEY_DAYS = {2, 5}          # Monday=0 -> Wednesday, Saturday
POLL = 20.0


# ------------------------------------------------------------------ state
# P0 (2026-08-18): daystate.py is the single daily_state writer - absolute
# path + atomic saves. Key format unchanged (combo_<phase>: ISO date), so
# a pre-P0 runner and this code can straddle a boundary restart safely.


def _key(phase: str) -> str:
    return f"combo_{phase}"


def done_today(phase: str) -> bool:
    return daystate.flag_today(_key(phase))


def mark_done(phase: str) -> None:
    daystate.mark_today(_key(phase))
    logger.event("combo_phase_done", phase=phase)


# ------------------------------------------------------------------ plan
def due(now: datetime.datetime | None = None) -> str:
    """Which phase owns the tower right now: 'tournament'|'shards'|'coin'.

    Ordered by priority, not by clock. The tournament outranks the shard block
    because it is the one thing with a closing window - a tournament entry
    missed is gone, where shard runs are only ever deferred.
    """
    now = now or datetime.datetime.now()
    if (now.hour >= TOURNEY_HOUR and now.weekday() in TOURNEY_DAYS
            and not done_today("tournament")):
        return "tournament"
    # SHARD_RUNS = 0 switches the shard block off entirely, without touching
    # the rest of the plan - the day then runs coin farm plus tournaments.
    if SHARD_RUNS > 0 and now.hour >= SHARD_HOUR and not done_today("shards"):
        return "shards"
    return "coin"


# ------------------------------------------------------------------ runners
def _spawn(args: list, *, setup=None) -> subprocess.Popen:
    from runtime.device_lease import child_environment
    environment = child_environment()  # proves no delegated child still owns input
    environment.pop('TOWER_BATTLE_ID', None)
    environment.pop('TOWER_BATTLE_GENERATION', None)
    pending = None
    from runtime.operations import environment_operation, validate_worker_context
    operations, operation_id = environment_operation()
    if operations is not None:
        from player.run_controls import effective_preset, resolve
        from towerpilot_workflows.battles import Battles
        import uuid
        store = Battles(operations.path)
        operation = operations.get(operation_id)
        validate_worker_context(operation)
        previous = None
        for old in store.active(operation['context']['device_key']):
            if old['operation_id'] != operation_id:
                raise ValueError('Another operation still owns this device battle')
            store.finish(old['id'], old['generation'], reason='delegated_driver_exited', status='interrupted')
            previous = old['id']
        if previous is None:
            recent = store.list(limit=1, device=operation['context']['device_key'])
            if recent and recent[0]['operation_id'] == operation_id:
                previous = recent[0]['id']
        # Only the generic driver implements per-battle observation and control
        # acknowledgement. A shard/quest batch is not one observed battle.
        if _Path(args[0]).as_posix() == flows.ENGINE:
            selected = args[args.index('--preset')+1] if '--preset' in args else CONFIG['preset']
            frozen_config = copy.deepcopy(CONFIG)
            frozen_config['preset'] = selected
            body = effective_preset(frozen_config, selected)
            from player import playerprofile
            envelope = dict(config=frozen_config, preset=body, preset_key=selected,
                            controls=resolve(body, frozen_config), profile=copy.deepcopy(getattr(playerprofile, 'PROFILE', None)),
                            source=copy.deepcopy(body.get('_source')), source_revision=(body.get('_source') or {}).get('profile_revision'))
            pending = store.create(operation_id, operation['context'], envelope, generation=str(uuid.uuid4()),
                                   origin='partial_adoption', previous_battle_id=previous)
            from interactions.loadout import unknown_setup
            pending = store.update_status(pending['id'], pending['generation'], {
                'setup': copy.deepcopy(setup) if setup is not None else unknown_setup(
                    body.get('loadout'), detail='adopted',
                    reason='No setup evidence was recorded for this handoff')})
            environment['TOWER_BATTLE_ID'] = pending['id']
            environment['TOWER_BATTLE_GENERATION'] = pending['generation']
            # The child observes battles under this already-running parent;
            # it never enters worker_operation or finalizes the scheduler.
            environment['TOWER_OPERATION_ID'] = operation_id
            environment['TOWER_OPERATION_DB'] = operations.path
        else:
            logger.event('combo_driver_capability', operation_id=operation_id, driver=args[0],
                         battle_history='unsupported', live_controls='unsupported')
    logger.event("combo_spawn", cmd=" ".join(args))
    try:
        process = subprocess.Popen([sys.executable] + args, env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        if pending is not None:
            process.battle_delegation = dict(path=store.path, operation_id=operation_id,
                                            device=pending['context']['device_key'],
                                            battle_id=pending['id'],
                                            generation=pending['generation'])
        return process
    except BaseException:
        if pending is not None:
            store.finish(pending['id'], pending['generation'], reason='delegated_launch_failed', status='interrupted')
        raise


def _reconcile_child(proc):
    """Retire observation only after this exact child has demonstrably exited."""
    delegation = getattr(proc, 'battle_delegation', None)
    if delegation is None or proc.poll() is None:
        return
    from towerpilot_workflows.battles import Battles
    Battles(delegation['path']).reconcile_driver(
        delegation['operation_id'], delegation['device'], delegation['generation'],
        reason='delegated_driver_exited')


# The constant-era phases, resolved through the flow registry so there is
# exactly one place (flows/<kind>.py) that says which script drives a kind.
# The preset names are the literal legacy ones - the shard phase has none to
# give (a bare `flows/shard.py`), which its adoption logic relies on.
PHASE_KIND = {"coin": "coin", "shards": "shard",
              "tournament": "tournament"}

PHASE_CMD = {
    phase: (flows.script(kind), flows.flow(kind)["legacy_preset"]
            if phase != "shards" else None)
    for phase, kind in PHASE_KIND.items()
}


class ProfileMissing(RuntimeError):
    """The active profile has no blueprint for a phase that needs one."""


def _blueprint(phase: str) -> str | None:
    """The blueprint this phase runs under the active profile, or None.

    BY KIND ONLY. The day plan (plan.week, `after:`, per-block `count`) is
    P5's job, and half-implementing it here is worse than not implementing it
    - walking the days in YAML order silently replaces a Wednesday-specific
    coin blueprint with the first farm-day one. Kind is the one mapping that
    cannot be wrong: the coin phase runs a coin blueprint. Ties break on the
    sorted name so two passes never disagree about which one that is.

    NOTHING IS IMPORTED ON THE LEGACY PATH. No `active_profile` key and this
    returns before the import - an import-time failure in playerprofile.py
    can then never take down a legacy combo, which catching ImportError alone
    would not have prevented.
    """
    if not CONFIG.get("active_profile"):
        return None
    try:
        from player import playerprofile
        prof = getattr(playerprofile, "PROFILE", None)   # parsed profile dict
        if not isinstance(prof, dict):
            return None
        names = sorted(n for n, bp in (prof.get("blueprints") or {}).items()
                       if isinstance(bp, dict)
                       and bp.get("kind") == PHASE_KIND[phase])
        return names[0] if names else None
    except Exception as e:                              # noqa: BLE001
        logger.event("combo_blueprint_err", phase=phase, error=str(e)[:120])
        return None


def _require_blueprint(phase: str) -> str | None:
    """The phase's blueprint, or None when NO profile is active.

    NEVER A SILENT LEGACY FALLBACK. If the operator bound a profile and it has
    no blueprint of this kind - or the profile module failed - the phase must
    fail loudly rather than quietly farm the config.yaml preset the profile
    was meant to replace. run()'s existing handler does the rest: log
    combo_handoff_failed, close the day for shards/tournament, re-raise for
    coin (which has nowhere to fall back to and must not spin).
    """
    bp = _blueprint(phase)
    if bp is None and CONFIG.get("active_profile"):
        raise ProfileMissing(
            f"profile {CONFIG['active_profile']!r} has no usable blueprint of "
            f"kind {PHASE_KIND[phase]!r} for the {phase} phase - refusing to "
            f"fall back to the legacy preset")
    return bp


def _phase_tokens(phase: str) -> tuple[str, ...]:
    """Command-line tokens that identify a runner for `phase`: the legacy
    preset name plus the compiled `bp_` name, when a profile is loaded."""
    bp = _blueprint(phase)
    return tuple(t for t in (PHASE_CMD[phase][1],
                             f"bp_{bp}" if bp else None) if t)


def _matches_phase(cl: list, phase: str) -> bool:
    """Does this command line belong to `phase`?

    A token of ours is proof. Failing that, a ORCHESTRATOR phase never matches - both
    orchestrator phases share one script, so a runner that does not name its preset
    could be either and must not be adopted as one (legacy behaviour, kept
    deliberately). The shard phase has no preset name to give, so it matches
    on the absence of anyone else's token - which is what keeps a plain
    `flows/shard.py --loops 100` adoptable during the migration.
    """
    if any(t in cl for t in _phase_tokens(phase)):
        return True
    if PHASE_CMD[phase][1]:
        return False
    return not any(t in cl for p in PHASE_CMD if p != phase
                   for t in _phase_tokens(p))


class _Adopted:
    """An already-running runner, wearing enough of Popen to be supervised.

    Combo is not always the thing that started the farm - the tray starts
    runners, and a coin farm may already have been going for hours when combo
    comes up. Spawning a second one would be bad; killing the first to spawn a
    replacement would abandon a live run, which is the exact thing this mode
    promises not to do. So an existing runner for the phase we want is adopted
    instead, and the handoff is skipped - the tower is already where it needs
    to be.
    """

    def __init__(self, proc):
        # proc is the psutil.Process the process walk matched by command line. A bare pid
        # reopened afterwards could be a different process by then, and terminate()/kill()
        # below would stop THAT; the walked object re-checks pid and creation time itself.
        self._p = proc
        self.pid = proc.pid
        self._created = proc.create_time()   # cached when the walk built the object: the process the walk matched
        self.returncode = None

    def poll(self):
        if self._p.is_running():
            return None
        if self.returncode is None:
            self.returncode = -1
        return self.returncode

    def terminate(self):
        """Stop the adopted runner through a kernel handle verified against the creation time the walk saw
        (runtime.process_handle), never by a bare pid. Only this process, as before; refuses when it
        cannot be shown to be the one adopted."""
        from runtime import process_handle
        stop = process_handle.terminate_tree(self.pid, self._created, descendants=False)
        if stop.state == 'unverified':
            raise PermissionError(f"pid {self.pid} could not be verified as the adopted runner: {stop.errors}")

    kill = terminate   # TerminateProcess is already forceful; there is no gentler signal to escalate from

    def wait(self, timeout=None):
        import psutil
        try:
            code = self._p.wait(timeout)
        except psutil.TimeoutExpired as exc:
            # Supervision uses the Popen timeout contract for spawned and
            # discovered children alike. An adopted process may not be our
            # OS child, so psutil cannot always recover its exit status.
            raise subprocess.TimeoutExpired(str(self.pid), timeout) from exc
        self.returncode = code if code is not None else -1
        return self.returncode


def _find_running(phase: str, instance: str):
    """An existing process already doing `phase` for this instance, or None."""
    from runtime.runners import process_rows
    # Match on the script's BASENAME: spawn sites vary between the relative
    # registry path (`flows/shard.py`) and an absolute Windows path, and the
    # basename is the one token every spelling contains.
    script = os.path.basename(PHASE_CMD[phase][0])
    me = os.getpid()
    for p, info in process_rows(["pid", "cmdline"]):
        if info["pid"] == me:
            continue
        cl = [str(c) for c in (info.get("cmdline") or [])]
        if not any(script in c for c in cl):
            continue
        if instance not in cl:
            continue
        # a orchestrator preset must match: normal_run and tournament are both
        # orchestrator.py - and under a profile they are bp_ names instead.
        if not _matches_phase(cl, phase):
            continue
        return _Adopted(p)   # the object process_iter matched by command line, never psutil.Process(pid) reopened after it
    return None


def _runner(phase: str, instance: str, *, setup=None) -> subprocess.Popen:
    # With a profile loaded the phase runs its BLUEPRINT (bp_<name>); with
    # none, the literal legacy preset names, unchanged. A profile that cannot
    # supply one raises rather than quietly spawning the legacy runner.
    bp = _require_blueprint(phase)
    if phase == "coin":
        return _spawn([PHASE_CMD["coin"][0], "--instance", instance,
                       "--preset", f"bp_{bp}" if bp else "normal_run"], setup=setup)
    if phase == "shards":
        # Resume from the persisted per-day counter, not from zero: an
        # aborted block (a human touching the screen ends one) keeps its
        # progress and only the remainder is spawned (user, 2026-08-17).
        remaining = max(1, SHARD_RUNS - daystate.get_today("shard_runs"))
        args = [PHASE_CMD["shards"][0], "--instance", instance,
                "--loops", str(remaining)]
        if bp:
            args += ["--preset", f"bp_{bp}"]
            tier = (CONFIG["presets"].get(f"bp_{bp}") or {}).get("tier")
            if tier:
                args += ["--tier", str(tier)]
        return _spawn(args, setup=setup)
    if phase == "tournament":
        return _spawn([PHASE_CMD["tournament"][0], "--instance", instance,
                       "--preset", f"bp_{bp}" if bp else "tournament"], setup=setup)
    raise ValueError(phase)


# ------------------------------------------------------------------ P5 plan
#
# THE PLAN LAYER. Everything above this point is the constant-era scheduler and
# is left exactly as it was: with no profile, or a profile that has no `plan`,
# not one line of it behaves differently. The plan path runs alongside it and
# reuses its runner/adoption/handoff machinery through the `phase` API, so
# there is one implementation of "spawn a shard block", not two.
#
# THE RUNTIME APPLIES NO DEFAULTS. playerprofile.compile_plan() emits every key
# of every block explicitly, and a block missing one is REFUSED and logged,
# never guessed at. A guessed window is a tournament entered at the wrong hour;
# a guessed count is a shard block that never ends.
#
# THE WINDOW IS READ IN MINUTES, not from the `after`/`until` strings the
# compiler also carries for humans. It parsed them once, at compile time, and
# re-parsing "08:00" on every poll is how a scheduler grows its own opinion
# about what an unset bound means. `until_min` is EXCLUSIVE, so 08:00-19:00 and
# 19:00-24:00 do not both own 19:00.
BLOCK_KEYS = ("id", "block", "blueprint", "preset", "kind",
              "after_min", "until_min", "count")
DAY_MINUTES = 24 * 60

# kind -> script lives in the FLOW REGISTRY (flows/<kind>.py declares it,
# flows.script(kind) answers it) - the same source playerprofile._runner_for
# compiles from, so the scheduler and the compiler cannot disagree.

# How the compiled plan is reached. Resolved BY NAME for the same reason
# orchestrator's capability gate is: the compiler half of P5 lands separately, and a
# hard import of a function that does not exist yet would take the scheduler
# down instead of falling back to the constants.
PLAN_HELPERS = ("compiled_plan", "compile_plan", "plan_for_today")

WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday")

_ABSENT = object()              # "the key was never there", as distinct from
                                # "the key is there and holds nothing"
_plan_absent_logged = False     # `plan_absent` is an edge log, not a heartbeat
_plan_unavailable_reason = None  # the last today_blocks read, never inferred by UI
_block_refusals: set = set()    # block ids already refused, so the log says it
                                # once rather than every POLL for a whole day


def _plan_map() -> dict | None:
    """The compiled {weekday: [block, ...]} map, or None for the legacy path.

    THREE OUTCOMES, AND ONLY ONE OF THEM REACHES THE CONSTANTS (Codex P5, HIGH
    - the fail-open fallback):

      * NO `active_profile` key at all -> None, silently. This is the legacy
        farm, and it is the ONE case allowed to run the constants.
      * profile bound, plan section genuinely ABSENT -> None, `plan_absent`
        once, then the constants. Absence is a configuration, not a failure.
      * profile bound and the plan cannot be READ - the import blew up, a
        helper raised, the artefact is the wrong shape -> ProfileMissing.
        A failure must never be mistaken for an absence: falling back there
        would farm the config.yaml preset the profile exists to replace, at
        the wrong tier, with the wrong loadout, and say nothing.

    NOTHING IS IMPORTED WITHOUT A PROFILE - same guard as _blueprint(), for the
    same reason: an import-time failure in playerprofile.py must not be able to
    take down a legacy combo.
    """
    global _plan_absent_logged
    if not CONFIG.get("active_profile"):
        return None
    # CONFIG["plan"] IS THE ARTEFACT. materialize() installs it beside the
    # bp_ presets, so the ordinary path reads an already-compiled dict and
    # never recompiles a profile on a 20-second poll. The helpers below are the
    # fallback for a process that bound a profile without materializing.
    # ABSENT AND EMPTY ARE NOT THE SAME ARTEFACT, and the whole ruling turns on
    # telling them apart - so the sentinel is `_ABSENT`, never falsiness. The
    # key MISSING means the profile never spoke about scheduling; the key
    # PRESENT and empty means something compiled the schedule down to nothing,
    # which can only be an authoring or compile accident.
    plan = CONFIG.get("plan", _ABSENT)
    if plan is _ABSENT:
        try:
            from player import playerprofile
            for name in PLAN_HELPERS:
                fn = getattr(playerprofile, name, None)
                if not callable(fn):
                    continue
                try:
                    got = fn()
                except TypeError:
                    got = fn(getattr(playerprofile, "PROFILE", None))
                if got is not None:
                    plan = got          # even an EMPTY one: it answered
                    break
            if plan is _ABSENT:
                plan = getattr(playerprofile, "PLAN", _ABSENT)
        except Exception as e:                          # noqa: BLE001
            # A HELPER THAT RAISES IS A FAILURE, and a failure holds. Only a
            # helper that RETURNS is allowed to say "there is no plan".
            raise ProfileMissing(
                f"profile {CONFIG['active_profile']!r} is bound but its plan "
                f"could not be read ({type(e).__name__}: {str(e)[:120]}) - "
                f"refusing to fall back to the legacy constants") from e
        if plan is None:
            # ABSENCE PROPAGATES. compile_plan() returns None for a profile
            # with no plan section, so a helper answering None is that profile
            # saying "I never spoke about scheduling" - the same answer as the
            # key not being there. Normalized to the sentinel HERE, on the
            # helper path only, so that a `None` surviving past this point can
            # only have come from CONFIG - which is branch (2) below.
            plan = _ABSENT
    if plan is _ABSENT:
        # NOTHING WAS EVER SAID ABOUT SCHEDULING. Deliberate and documented: a
        # rules-only profile must not change what the day runs, so this is the
        # one bound-profile case that reaches the constants.
        if not _plan_absent_logged:
            _plan_absent_logged = True
            logger.event("plan_absent", profile=CONFIG.get("active_profile"),
                         why="no compiled plan - running the combo constants")
        return None
    if plan is None:
        # PRESENT AND NULL. materialize() is the only writer of this key and it
        # never writes None, so the key existing with no value is a defective
        # artefact - a half-finished write, a hand-edit, a stub - and NOT the
        # profile declining to schedule. Absence is expressed by the key not
        # being there; this is something else, so it holds.
        raise ProfileMissing(
            f"profile {CONFIG['active_profile']!r} has CONFIG['plan'] present "
            f"but null - materialize() never writes that, so the artefact is "
            f"defective; absence is the key being absent, not a null in it")
    # ---- from here the profile DID produce a plan artefact, so any emptiness
    # in it is a defect rather than a preference, and the answer is to hold.
    # compile_plan returns {"week": {weekday: [block, ...]}}. The bare
    # weekday-keyed mapping is accepted too, so a caller that already unwrapped
    # it (tools/plan_sim.py hands `plan["week"]` around) is not a crash.
    if isinstance(plan, dict) and "week" in plan:
        plan = plan["week"]
    if not isinstance(plan, dict) or not plan:
        raise ProfileMissing(
            f"profile {CONFIG['active_profile']!r} compiled a plan that "
            f"schedules NOTHING ({plan!r}) - an empty plan is an authoring or "
            f"compile accident, never a request to farm the legacy constants")
    if not any(isinstance(v, list) and v for v in plan.values()):
        # Every day empty. ONE empty day is a day off and stays legal (see
        # _plan_today); a whole week of them is the same accident as above,
        # wearing a shape that would otherwise pass every structural check.
        raise ProfileMissing(
            f"profile {CONFIG['active_profile']!r} compiled a week in which "
            f"EVERY day is empty ({sorted(map(str, plan))}) - refusing to read "
            f"that as a week off")
    return plan


def _plan_today(now: datetime.datetime) -> list | None:
    """Today's ordered block list, or None when there is no plan.

    The compiled map is keyed by weekday because the block ids are
    (`monday#0`), so the day is already resolved - `plan.week` was applied at
    compile time and this never walks it.

    CANONICAL NAMES ONLY (Codex P5, LOW). Accepting `mon` and `0` alongside
    `monday` meant a plan carrying two of them had a precedence rather than an
    error, and precedence in a scheduler is a silently different week. The
    compiler emits `datetime.weekday()`-ordered full names and refuses aliases
    at the source; this refuses to guess between them.
    """
    plan = _plan_map()
    if plan is None:
        return None
    day = WEEKDAY_NAMES[now.weekday()]
    if day in plan:
        blocks = plan[day]
        return list(blocks) if isinstance(blocks, list) else []
    # A plan that says nothing about today is not an error - it is a day off,
    # and the honest response is to run nothing rather than to invent a filler.
    logger.event("combo_plan_no_day", day=day, have=sorted(map(str, plan)))
    return []


def _minutes(now: datetime.datetime) -> int:
    """Minutes since local midnight - the unit the compiled window is in."""
    return now.hour * 60 + now.minute


def _whole(v) -> bool:
    """A non-negative whole number. `bool` is excluded deliberately: True is an
    int in Python, and `count: true` is a typo, not a quota of one."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _block_problems(b) -> list[str]:
    """Everything wrong with one compiled block. Empty = runnable."""
    if not isinstance(b, dict):
        return [f"block is {type(b).__name__}, not a mapping"]
    out = [f"missing key {k!r}" for k in BLOCK_KEYS if k not in b]
    if out:
        return out                      # nothing below can be trusted yet
    if b["kind"] not in flows.flows():
        out.append(f"unknown kind {b['kind']!r} "
                   f"(known: {', '.join(sorted(flows.flows()))})")
    # A LEGACY block carries `blueprint`/`preset` None on purpose - it is
    # synthesized from the constants above, names a legacy preset, and is
    # spawned by _runner(phase). Only a COMPILED block has one to check.
    if not b.get("legacy"):
        if not isinstance(b["preset"], str) or not b["preset"]:
            out.append(f"preset must be a bp_ name, got {b['preset']!r}")
        elif b["preset"] not in CONFIG["presets"]:
            out.append(f"no compiled preset {b['preset']} - the blueprint was "
                       f"never materialized")
    for key in ("after_min", "until_min"):
        if not _whole(b[key]) or b[key] > DAY_MINUTES:
            out.append(f"{key} must be minutes since midnight (0-{DAY_MINUTES}"
                       f"), got {b[key]!r}")
    if _whole(b["after_min"]) and _whole(b["until_min"]) \
            and b["after_min"] >= b["until_min"]:
        out.append(f"window is empty: after_min {b['after_min']} is not before "
                   f"until_min {b['until_min']}")
    if b["count"] is not None and not _whole(b["count"]):
        out.append(f"count must be a whole number or null, got {b['count']!r}")
    if b["kind"] == "tournament" and b["count"] != 1 and not b.get("legacy"):
        # DEFENCE IN DEPTH under the compiler's own daily cap (Codex P5,
        # CRITICAL). A ticket purchase AUTO-STARTS the run and the next entry
        # costs 10 -> 20 -> 30 gems, so "how many tournaments today" is not a
        # number a plan gets to choose: `count: 2` is a request to spend gems
        # twice and `count: null` is a request to spend them until the day
        # ends. Both are refused here even if a compiler ever emits them.
        out.append(f"a tournament block must be count 1 - got {b['count']!r}, "
                   f"and a tournament entry is paid for on purchase")
    return out


def _block_ok(b) -> bool:
    """Admission for one block, refused ONCE and loudly.

    A refused block is never eligible and never falls back to a constant - if
    that leaves the day with nothing to run, the scheduler idles and says so,
    which is the safe failure. Running the wrong blueprint at 19:00 is not.
    """
    problems = _block_problems(b)
    if not problems:
        return True
    bid = (b.get("id") if isinstance(b, dict) else None) or repr(b)[:40]
    if bid not in _block_refusals:
        _block_refusals.add(bid)
        logger.event("combo_block_refused", block=bid, problems=problems)
    return False


# ---- per-block daily state. Keys are explicit ON THE BLOCK so the legacy
# synthetic blocks keep writing the pre-P5 key names: a combo restarted across
# the P5 boundary mid-day must find its own marks, not start the day again.
def _progress_key(b) -> str:
    """Where this block's completed-unit count lives.

    A SHARD BLOCK READS flows/shard.py's OWN `shard_runs` COUNTER, compiled or
    legacy. That counter is incremented by the runner per surrender, and it is
    the only thing that knows how far an aborted block actually got - a
    combo-owned key could only count whole runners, which is the resume this
    was asked to generalize, not replace. (Consequence, stated rather than
    hidden: two shard blocks on one day share the counter, because flows/shard.py
    writes one key and combo does not get to rename it.)
    """
    if b.get("progress_key"):
        return b["progress_key"]
    if b.get("kind") == "shard" or b.get("block") == "shards":
        return "shard_runs"
    return f"combo_block_{b['id']}"


def _closed_key(b) -> str:
    return b.get("closed_key") or f"combo_closed_{b['id']}"


def _block_progress(b) -> int:
    """Units of this block completed today.

    For a SHARD block that is flows/shard.py's own `shard_runs` counter - the runner
    increments it per surrender, which is what makes an aborted block resume
    mid-count instead of starting the quota again. Every other kind is counted
    here, on runner exit.
    """
    return daystate.get_today(_progress_key(b))


def _mark_block_done(b) -> None:
    daystate.mark_today(_closed_key(b))
    if b["kind"] == "tournament":
        # THE DAY LOCK, and it is the LEGACY KEY on purpose (see
        # _tournament_taken). One entry per day, whichever block or era spent
        # it. Marked here rather than only in _close_block so a tournament that
        # is closed by a failed handoff also spends the day - the entry may
        # well have been bought before the failure.
        daystate.mark_today(_key("tournament"))
    logger.event("combo_phase_done", phase=b["block"], block=b["id"])


def _tournament_taken(now: datetime.datetime, blocks: list | None = None
                      ) -> str | None:
    """Has a tournament already been entered today? Returns the evidence.

    ONE ENTRY PER DAY, ACROSS EVERY BLOCK AND BOTH ERAS (Codex P5, CRITICAL).
    The per-block counter cannot express this: two `count: 1` tournament blocks
    each see their own counter at zero and each spend a ticket, and the second
    one costs more gems than the first. So the runtime asks three questions,
    and any `yes` closes the day:

      1. `combo_tournament` - THE LEGACY FLAG, which is also the shared one. A
         migration-day combo must honour an entry made under the constants
         that morning; a plan-era entry marks it too (see _mark_block_done), so
         the two eras cannot each have a turn.
      2. any tournament block's own counter today - covers a second block that
         ran before this one, including one whose closed-flag write was lost.
      3. any tournament block's closed flag - the same, for a block closed by a
         refused handoff rather than by a completed run.
    """
    if daystate.flag_today(_key("tournament")):
        return "combo_tournament"
    for b in (blocks if blocks is not None else today_blocks(now)):
        if not isinstance(b, dict) or b.get("kind") != "tournament":
            continue
        if _block_progress(b) > 0:
            return f"{b['id']} ran {_block_progress(b)}"
        if daystate.flag_today(_closed_key(b)):
            return f"{b['id']} closed"
    return None


def _close_block(b, base: int) -> None:
    """A runner for this block exited: close the block, or leave it partial.

    Generalized straight from _close_shards, whose two conditions are kept
    verbatim: the quota is filled, OR the runner completed NOTHING since it was
    respawned - the crash guard that stops a runner dying on sight from being
    respawned in a loop all day. Anything in between is a partial block, left
    due so the next pass spawns only the remainder.
    """
    count = b["count"]
    if count is None:
        return                          # unbounded (the coin filler): respawn
    if b["block"] == "shards" or b["kind"] == "shard":
        done = _block_progress(b)
        if done >= count or done <= base:
            _mark_block_done(b)
        else:
            logger.event("combo_shards_partial", done=done, target=count,
                         block=b["id"])
        return
    # Every other kind counts ONE unit per runner exit, which is exactly what
    # the constant era did for the tournament (`mark_done` on exit, count 1).
    done = daystate.increment_today(_progress_key(b))       # one locked step (a get followed by a set would lose a unit another process counted)
    if done >= count:
        _mark_block_done(b)
    else:
        logger.event("combo_block_partial", block=b["id"], done=done,
                     target=count)


def _block_eligible(b, now: datetime.datetime,
                    blocks: list | None = None) -> bool:
    """Is this block allowed to own the tower right now?"""
    if not _block_ok(b):
        return False
    if daystate.flag_today(_closed_key(b)):
        return False
    if b["kind"] == "tournament" and _tournament_taken(now, blocks):
        return False                    # the day lock - see _tournament_taken
    if b.get("legacy"):
        # SYNTHETIC LEGACY BLOCKS ARE FLAG-ELIGIBLE, EXACTLY LIKE due()
        # (Codex P5, MEDIUM). due() asks `not done_today("shards")` and never
        # looks at the counter, so `shard_runs == 100` with `combo_shards`
        # unset still reads "shards" there - a real state, reached whenever a
        # quota fills before _close_shards runs. Counting here instead made the
        # two disagree on exactly that transition. The count still governs the
        # remainder arithmetic and the close; it just does not gate the turn.
        # SHARD_RUNS = 0 is due()'s own separate gate, kept separate.
        if b["block"] == "shards" and SHARD_RUNS <= 0:
            return False
    elif b["count"] is not None and _block_progress(b) >= b["count"]:
        return False
    # after INCLUSIVE, until EXCLUSIVE - the compiler's rule, and the one that
    # stops 08:00-19:00 and 19:00-24:00 from both owning 19:00.
    return b["after_min"] <= _minutes(now) < b["until_min"]


def _legacy_blocks(now: datetime.datetime) -> list:
    """The constant-era plan, expressed as blocks.

    THIS IS due() RESTATED, NOT REDESIGNED - same order (tournament outranks
    shards because it is the one thing with a closing window), same hours, same
    daily-state keys, same weekday gate. Expressing it as blocks is what lets
    run() have ONE loop instead of a plan branch and a constant branch, and
    test_p5_scheduler asserts block-for-block equivalence against due() across
    a week of hours.
    """
    def block(name, kind, after_min, count):
        return {"id": f"legacy#{name}", "block": name, "blueprint": None,
                "preset": None, "kind": kind, "after_min": after_min,
                "until_min": DAY_MINUTES, "count": count,
                "phase": name, "legacy": True, "closed_key": _key(name)}

    out = []
    if now.weekday() in TOURNEY_DAYS:
        out.append(block("tournament", "tournament", TOURNEY_HOUR * 60, 1))
    # SHARD_RUNS = 0 switches the block off, and it does so through the ordinary
    # count check (0 completed >= 0 wanted) rather than a special case.
    out.append(block("shards", "shard", SHARD_HOUR * 60, SHARD_RUNS))
    out.append(block("coin", "coin", 0, None))
    return out


def today_blocks(now: datetime.datetime | None = None) -> list:
    """Every block for today, in order - eligible or not (adoption needs the
    lot, because a runner already going may belong to a finished block).

    A BOUND PROFILE WHOSE PLAN CANNOT BE READ IDLES; IT NEVER FALLS BACK.
    _plan_map raises ProfileMissing for that case and this is the one place
    that decides what to do about it: an empty list, so next_block() returns
    None and the loop holds. Holding is chosen over letting the exception out
    because a scheduler that exits is a scheduler the tray restarts in a loop,
    and the operator would see a crash rather than the reason. The reason is
    logged once, loudly, and the tower is left alone meanwhile.
    """
    now = now or datetime.datetime.now()
    global _plan_absent_logged, _plan_unavailable_reason
    _plan_unavailable_reason = None
    try:
        blocks = _plan_today(now)
    except ProfileMissing as e:
        _plan_unavailable_reason = str(e)
        if not _plan_absent_logged:
            _plan_absent_logged = True
            logger.event("combo_plan_unavailable",
                         profile=CONFIG.get("active_profile"), error=str(e),
                         why="holding - a bound profile NEVER falls back to "
                             "the legacy constants")
        return []
    return _legacy_blocks(now) if blocks is None else blocks


def next_block(now: datetime.datetime | None = None) -> dict | None:
    """The block that owns the tower right now, or None to idle.

    FIRST ELIGIBLE WINS, in compiled order - so the list is a priority list and
    the LAST entry, which the compiler leaves unbounded, is the default filler.
    None means every block is done, out of its window or refused; the scheduler
    then holds, which is the plan's way of saying "nothing is scheduled".
    """
    now = now or datetime.datetime.now()
    blocks = today_blocks(now)
    for b in blocks:
        if _block_eligible(b, now, blocks):
            _publish_schedule_decision(now, blocks, b)
            return b
    _publish_schedule_decision(now, blocks, None)
    return None


def _publish_schedule_decision(now: datetime.datetime, blocks: list, winner) -> None:
    """Persist the actual decision; a window edge only proposes another check."""
    from runtime.operations import environment_operation, Conflict
    try:
        ledger, operation_id = environment_operation()
        if ledger is None:
            return
        operation = ledger.get(operation_id)
        if operation['status'] != 'running':
            return
        local = now.astimezone() if now.tzinfo is None else now
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        edges = []
        for block in blocks:
            if not _block_ok(block):
                continue
            for key in ('after_min', 'until_min'):
                edge = midnight + datetime.timedelta(minutes=block[key])
                # Convert each naive local edge separately: a DST transition
                # can change its offset later today. A supplied aware clock
                # already carries the scheduling timezone explicitly.
                edge = edge.astimezone() if edge.tzinfo is None else edge
                if edge > local:
                    edges.append(edge)
        edge = min(edges) if edges else None
        identity = operation['context']
        selected = None
        if winner is not None:
            preset = CONFIG.get('presets', {}).get(winner.get('preset')) or {}
            selected = dict(id=winner['id'], label=preset.get('label') or winner['block'],
                            kind=winner['kind'], preset=winner.get('preset'),
                            legacy=bool(winner.get('legacy')))
        snapshot = dict(
            observed_at=local.timestamp(), local_time=local.isoformat(),
            timezone=local.tzname(), utc_offset=local.strftime('%z'),
            context={key: identity.get(key) for key in
                     ('instance', 'account', 'serial', 'profile', 'profile_revision', 'device_key')},
            winner=selected,
            reason=('plan_unavailable' if _plan_unavailable_reason else
                    'first_eligible_block' if winner is not None else
                    'no_blocks_today' if not blocks else 'no_eligible_block'),
            error=_plan_unavailable_reason,
            next_clock_boundary=(dict(at=edge.timestamp(), local_time=edge.isoformat(),
                                      kind='clock_proposal', guarantees_handoff=False)
                                 if edge is not None else None),
            handoff='natural_run_boundary',
            availability='unavailable' if _plan_unavailable_reason else 'available')
        progress = {**operation.get('progress', {}), 'schedule': snapshot}
        ledger.transition(operation_id, operation['revision'], 'running', progress=progress)
    except (Conflict, OSError, KeyError, sqlite3.Error) as exc:
        logger.event('combo_schedule_progress_failed', error=str(exc))


def _plan_mode() -> bool:
    """Is a compiled plan driving the schedule right now?

    Never raises: a bound profile whose plan cannot be read is holding, not
    running the constants, and the caller only needs to know whether legacy
    runners count as foreign.
    """
    try:
        return _plan_map() is not None
    except ProfileMissing:
        return True


def _same_work(a, b) -> bool:
    """Is the running block and the newly-wanted one THE SAME WORK?

    Identity is what the runner is executing - the preset and the kind - not
    the block id, because the id carries the weekday and the weekday changes at
    midnight underneath an overnight farm.

    BOUNDED BLOCKS ALWAYS RE-EVALUATE. A count is per-day by design, so
    yesterday's half-finished shard block and today's fresh one are different
    work even though they run the same preset: continuing would carry a spent
    quota across the rollover. Only unbounded blocks - the filler - continue.
    """
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    if a.get("foreign") or b.get("foreign"):
        return False                    # foreign work is finished, not continued
    if a["count"] is not None or b["count"] is not None:
        return False
    return (a["kind"] == b["kind"] and a["preset"] == b["preset"]
            and a.get("legacy", False) == b.get("legacy", False))


# EVERY LEGACY RUNNER SHAPE, and the argv token that names it. The quest
# runners bind their preset internally and take no `--preset`, so their script
# IS the identification - _find_running_block filters on it before any token
# test, and flows/quest_sm.py / flows/quest_ilm.py are unique scripts.
#   (block name, kind, literal argv tokens)
FOREIGN_LEGACY = (
    ("coin",       "coin",           ("normal_run",)),
    ("tournament", "tournament",     ("tournament",)),
    ("shards",     "shard",          ()),          # a bare `flows/shard.py`
    ("quest_sm",   "uw_grant_quest", ()),
    ("quest_ilm",  "cycle_quest",    ()),
)


def foreign_legacy_blocks() -> list:
    """The constant-era runners, as blocks to ADOPT but never to schedule.

    THE MIGRATION GAP (Codex P5, HIGH). With a plan bound, discovery matched
    `bp_` tokens only - so a `orchestrator.py --preset normal_run` still farming from
    before the switchover was invisible, and the scheduler walked the game Home
    to hand off underneath a live run. That is the one thing this mode promises
    not to do, and it does not stop being true because the runner is of the
    previous era.

    They are found as FOREIGN WORK: adopted (which is what stops the handoff),
    then stopped through the ordinary run-boundary contract - runflag, wait for
    the death handler, close - before the plan's own block starts.
    `foreign: True` keeps them out of next_block(): something to finish, never
    something to schedule.

    THE SET IS FIXED AND TAKES NO DATE (audit P5b). It used to be derived from
    _legacy_blocks(now), i.e. from what TODAY would start - so on a Sunday it
    offered shards and coin only, and a Saturday-night legacy TOURNAMENT runner
    that crossed midnight was invisible. The plan's handoff would then navigate
    Home under a live tournament run: hard rule 3 broken to break hard rule 2.
    Foreign detection is about what MIGHT BE RUNNING, which is every runner
    this project has ever spawned, on every day of the week.

    Tokens are the LITERAL legacy names, not _phase_tokens() - that helper adds
    the kind-mapped `bp_` name when a profile is loaded, which under a plan
    would make a foreign block match the plan's OWN runner and stop it a moment
    after starting it.
    """
    out = []
    for name, kind, tokens in FOREIGN_LEGACY:
        b = {"id": f"foreign#{name}", "block": name, "blueprint": None,
             "preset": None, "kind": kind, "after_min": 0,
             "until_min": DAY_MINUTES, "count": None, "phase": name,
             "legacy": True, "foreign": True, "tokens": tokens,
             "closed_key": f"combo_foreign_{name}"}
        # The two that own LEGACY DAILY STATE keep it, so finishing an adopted
        # constant-era runner closes the same day the constants would have.
        # A finished legacy tournament therefore spends the day lock, which is
        # exactly what must happen for the crossover case above.
        if name == "tournament":
            b.update(count=1, closed_key=_key("tournament"))
        elif name == "shards":
            b.update(count=SHARD_RUNS, closed_key=_key("shards"),
                     progress_key="shard_runs")
        out.append(b)
    return out


# ---- spawning and adoption, per block. Both delegate to the phase functions
# for a legacy block, so the constant path runs the ORIGINAL code.
def _block_tokens(b) -> tuple:
    """argv tokens that identify a runner for this block."""
    if "tokens" in b:
        return b["tokens"]
    if b.get("legacy"):
        return _phase_tokens(b["phase"])
    return (b["preset"],)


def _block_matches(cl: list, b, others: list) -> bool:
    """Does this command line belong to `b`? Same rule as _matches_phase: a
    token of ours is proof; a block that HAS a token and does not show it is
    not ours; a token-less block (legacy shards) matches on the absence of
    everyone else's."""
    tokens = _block_tokens(b)
    if any(t in cl for t in tokens):
        return True
    if tokens:
        return False
    return not any(t in cl for o in others if o["id"] != b["id"]
                   for t in _block_tokens(o))


def _find_running_block(b, instance: str, others: list | None = None):
    """An existing process already running this block, or None."""
    if b.get("legacy") and not b.get("foreign"):
        return _find_running(b["phase"], instance)
    from runtime.runners import process_rows
    # Basename for the same reason as _find_running: every spawn spelling
    # (relative registry path, absolute tray path) contains it.
    script = os.path.basename(flows.script(b["kind"]))
    others = others if others is not None else [b]
    me = os.getpid()
    for p, info in process_rows(["pid", "cmdline"]):
        if info["pid"] == me:
            continue
        cl = [str(c) for c in (info.get("cmdline") or [])]
        if not any(script in c for c in cl):
            continue
        if instance not in cl:
            continue
        if not _block_matches(cl, b, others):
            continue
        return _Adopted(p)   # the object process_iter matched by command line, never psutil.Process(pid) reopened after it
    return None


def _block_argv(b, instance: str) -> list:
    """argv for one plan block. `--preset bp_<blueprint>` is the stable token
    every runner has accepted since P3, and the one adoption matches on.

    The kind-specific tail comes from the FLOW SPEC (flows.extra_argv), the
    same builder the compiler uses for runner_args - one authority for how a
    blueprint's fields become a command line. The scheduler's only addition
    is the remaining-quota arithmetic for counted flows: RESUME, DO NOT
    RESTART - only the remainder of the quota is spawned, exactly as the
    constant-era shard block does. `count: null` means unbounded, which the
    counted flows spell `0`."""
    preset = b["preset"]
    script = flows.script(b["kind"])
    args = [script, "--instance", instance, "--preset", preset]
    body = CONFIG["presets"].get(preset) or {}
    if flows.flow(b["kind"])["count_arg"] is not None:
        remaining = (0 if b["count"] is None
                     else max(1, b["count"] - _block_progress(b)))
    else:
        remaining = None
    return args + flows.extra_argv(b["kind"], body, remaining=remaining)


def _block_runner(b, instance: str, *, setup=None) -> subprocess.Popen:
    if b.get("legacy"):
        return _runner(b["phase"], instance, setup=setup)
    argv = _block_argv(b, instance)
    spec = flows.flow(b["kind"])
    # A "loadout" handoff leaves the block's battle LIVE on screen. A runner
    # that offers an adopt mode gets it - its own setup would walk Home over
    # that battle and end it (user, 2026-09-05: a live run is never ended,
    # whoever started it). _block_argv stays the adoption-matching argv.
    if spec["handoff"] == "loadout" and spec.get("adopt_arg"):
        argv = argv + [spec["adopt_arg"]]
    return _spawn(argv, setup=setup)


def _block_handoff(b, instance: str):
    """Put a battle on screen for this block.

    A legacy block keeps _handoff verbatim - that path is not re-implemented
    here. A plan block takes its loadout and tier FROM ITS OWN BLUEPRINT rather
    than from the phase->name table the constants use, which is the whole point
    of naming a blueprint in the plan.

    A quest block needs no handoff at all: flows/quest_sm.py and flows/quest_ilm.py own
    their setup end to end (loadout, tier, BATTLE) and adopt an in-progress run
    on startup, so walking the game Home underneath them takes work away.

    The tournament is the other exception, exactly as before: tourney.setup()
    does its own equipping and entry, so this only clears the way to Home.
    """
    if b.get("legacy"):
        return _handoff(b["phase"], instance)
    # The flow's spec says what screen prep it is owed: "none" (the runner
    # owns its setup end to end - walking Home underneath it takes work
    # away), "home_only" (tourney.setup() equips and enters by itself), or
    # "loadout" (equip the blueprint's loadout and set its tier below).
    from player import readiness
    from player.run_controls import effective_preset
    body = effective_preset(CONFIG, b['preset'])
    readiness.require(settings.ROOT, CONFIG, body)
    handoff = flows.flow(b["kind"])["handoff"]
    if handoff == "none":
        logger.event("combo_handoff_skipped", block=b["id"], kind=b["kind"],
                     why="the flow's runner owns its own setup")
        return
    from runtime.device_lease import ensure_current
    ensure_current()
    from interactions import loadout
    from flows import shard
    from interactions import tourney
    tourney.ensure_home()
    if handoff == "home_only":
        return
    name, tier = body.get("loadout"), body.get("tier")
    if tier is None or (not name and b["kind"] != "coin"):
        # NO GUESSING HERE EITHER. The constants can fall back to
        # `loadouts[name].tier or 18` because those names are fixed; a
        # blueprint that states neither is a compiler gap, and farming the
        # previous block's tier because this one forgot to say is how a T18
        # shard build ends up on a T14 coin run.
        raise ProfileMissing(
            f"blueprint {b['blueprint']!r} (block {b['id']}) has no "
            f"loadout/tier to hand off to - loadout={name!r} tier={tier!r}")
    # A MODULE FAILURE MUST NOT STOP THE FARM - same ruling as _handoff: the
    # deck and the chips are already correct by the time modules can fail, and
    # farming with the wrong modules is a bad night while a dead scheduler is a
    # dead weekend. The event log records what was left wrong.
    setup = loadout.unknown_setup(name, detail='as_is', reason='No loadout was requested')
    try:
        if name:
            setup = loadout.apply(name)['setup']
    except tourney.Abort as e:
        setup = loadout.setup_failure(name, e)
        logger.event("combo_loadout_partial", phase=b["block"],
                     block=b["id"], error=str(e))
    tourney.ensure_home()
    shard.set_tier(tier)
    # enter_run, not start_battle: a dissonance blueprint enters through its
    # own dialog and a blueprint with perk bans sets them first (2026-09-14).
    shard.enter_run(body)
    if setup.get('state') == 'degraded':
        setup = dict(setup, continued_with_partial=True)
    logger.event("combo_handoff", phase=b["block"], block=b["id"], tier=tier,
                 dissonant=body.get("dissonant_tab"))
    return setup


def _handoff(phase: str, instance: str):
    """Equip the phase's loadout and put a battle on screen.

    orchestrator.py will not start a run from a menu - it sees no tower and correctly
    refuses to click. A loadout swap always ends in a menu, so the scheduler
    owns the walk back: Home -> tier -> BATTLE. Without this the runner spawns
    and idles forever logging off_battle.

    The tournament is the exception: its own setup() does the equipping and the
    entry, so this only clears the way to Home.
    """
    from runtime.device_lease import ensure_current
    ensure_current()
    from interactions import loadout
    from flows import shard
    from interactions import tourney

    # THE ABORT POINT for a profile that cannot serve this phase. Raising here
    # rather than in _runner is deliberate: this call already sits inside
    # run()'s try/except, which logs combo_handoff_failed, marks non-coin
    # phases done for the day and re-raises for coin.
    _require_blueprint(phase)

    from player import readiness
    from player.run_controls import effective_preset
    legacy_kind = "shard" if phase == "shards" else phase
    legacy_key = flows.flow(legacy_kind).get("legacy_preset")
    legacy_body = dict(effective_preset(CONFIG, legacy_key) if legacy_key in CONFIG['presets'] else {},
                       kind=legacy_kind)
    if phase in ("coin", "shards"):
        legacy_body["loadout"] = "coin_farm" if phase == "coin" else "shard_farm"
    readiness.require(settings.ROOT, CONFIG, legacy_body)

    if phase == "tournament":
        tourney.ensure_home()
        return

    name = {"coin": "coin_farm", "shards": "shard_farm"}[phase]
    # HOME BEFORE ANYTHING ELSE. The loadout screens are reached from Home, and
    # open_nav cannot get to them from inside a battle - it looked for the card
    # preset tab, found nothing, and aborted with "expected cards screen". Every
    # phase change arrives with a run still on screen, so this is the normal
    # case, not an edge case.
    tourney.ensure_home()
    # A MODULE FAILURE MUST NOT STOP THE FARM. apply() does cards, then
    # guardians, then modules - so an Abort in the module stage still leaves the
    # deck and the chips correct, and the tower can farm on. This is not
    # hypothetical: restoring the coin build after a tournament needs GRID
    # templates for Amplifying Strike, Sharp Fortitude and Black Hole Digestor,
    # which do not exist and cannot be cut while those modules are equipped.
    # Farming with the wrong modules is a bad night; a dead scheduler is a dead
    # weekend. The event log records exactly which modules were left wrong.
    try:
        setup = loadout.apply(name)['setup']
    except tourney.Abort as e:
        setup = loadout.setup_failure(name, e)
        logger.event("combo_loadout_partial", phase=phase, error=str(e))
    tourney.ensure_home()
    tier = CONFIG["loadouts"][name].get("tier") or (
        legacy_body["tier"] if phase == "coin" else 18)
    shard.set_tier(tier)
    shard.start_battle()
    if setup.get('state') == 'degraded':
        setup = dict(setup, continued_with_partial=True)
    logger.event("combo_handoff", phase=phase, tier=tier)
    return setup


def _boundary_progress(step: str, reason: str, elapsed: float) -> None:
    """Publish a hold without turning diagnostic failure into a driver exit."""
    from runtime.operations import environment_operation, Conflict
    try:
        ledger, operation_id = environment_operation()
        if ledger is None:
            return
        operation = ledger.get(operation_id)
        if operation['status'] != 'running':
            return  # explicit cancellation owns its own progress and outcome
        if step == 'scheduler_running':
            if operation['step'] not in ('boundary_wait', 'boundary_overdue'):
                return
            recovery = None
        else:
            overdue = (f'Boundary wait overdue after {elapsed:.0f} seconds. '
                       if step == 'boundary_overdue' else '')
            recovery = (f'{overdue}Pending transition: {reason}. Automation continues '
                        'until the current run ends naturally; Stop now stops automation immediately.')
        ledger.transition(operation_id, operation['revision'], 'running',
                          step=step, recovery_action=recovery)
    except (Conflict, OSError, KeyError, sqlite3.Error) as exc:
        # A cancellation can race this observation. Keep the live driver and
        # boundary flag intact even if publishing progress is unavailable.
        logger.event('combo_boundary_progress_failed', step=step, error=str(exc))


def _wait_for_child(proc: subprocess.Popen, timeout: float = POLL) -> None:
    """Wake on child exit while bounding the next schedule check."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass  # still alive; the caller re-evaluates the schedule or hold


def _stop_and_wait(proc: subprocess.Popen, reason: str,
                   timeout: float = 28800.0) -> None:
    """Ask the runner to leave at its next run boundary, then wait it out.

    `timeout` is an overdue reporting threshold, never permission to terminate
    automation. Elapsed time proves neither a run boundary nor a stuck game.
    Keep the child, its delegated lease and the boundary flag until confirmed
    exit. Explicit Stop now remains the operation cancellation path.
    """
    runflag.request(reason)
    started = time.monotonic()
    overdue = False
    fields = dict(reason=reason, overdue_after_sec=timeout,
                  child_pid=proc.pid, adopted=isinstance(proc, _Adopted))
    logger.event('combo_boundary_wait', **fields)
    _boundary_progress('boundary_wait', reason, 0)
    while proc.poll() is None:
        elapsed = time.monotonic() - started
        if not overdue and elapsed >= timeout:
            overdue = True
            logger.event('combo_boundary_overdue', elapsed_sec=elapsed, **fields)
            _boundary_progress('boundary_overdue', reason, elapsed)
        # Before the threshold, wake in time to report it. Thereafter wait
        # normally: an overdue hold must not become a zero-timeout busy loop.
        wait = POLL if overdue else min(POLL, max(0.001, timeout - elapsed))
        _wait_for_child(proc, wait)
    _reconcile_child(proc)
    runflag.clear()
    logger.event('combo_boundary_exited', elapsed_sec=time.monotonic() - started,
                 overdue=overdue, code=proc.returncode, **fields)
    _boundary_progress('scheduler_running', reason, time.monotonic() - started)


def _close_shards(base: int) -> None:
    """Mark the shard phase done only when it truly is.

    Done = quota filled, OR the runner exited with zero completed loops
    since it was (re)spawned - the crash guard that keeps a runner that
    dies on sight from being respawned in a loop all day. Anything in
    between is a partial block: leave the phase due and the remainder is
    spawned on the next pass.
    """
    done = daystate.get_today("shard_runs")
    if done >= SHARD_RUNS or done <= base:
        mark_done("shards")
    else:
        logger.event("combo_shards_partial", done=done, target=SHARD_RUNS)


def _orphan_battle() -> "int | None":
    """The wave of a live NON-tournament battle no runner process owns.

    The game resumes its previous round after an emulator restart ("Welcome
    back", always answered Resume per the user), and a human can leave a
    run going - either way the battle exists with no process for the
    adoption scan to find, and a handoff would end it (wave 5715, killed
    live 2026-08-28). Returns the wave (or 0 when unreadable but clearly a
    battle), None when there is no orphan battle. A tournament run reads as
    None too - nothing here may touch it, and the caller's hold covers it.
    An unreachable screen also reads as None: the handoff path fails loudly
    on its own.
    """
    try:
        from device import capture
        from interactions import tourney
        from vision import wave_reader
        frame = capture.grab()
        if not tourney._in_battle(frame) or tourney.in_tournament(frame):
            return None
        return wave_reader.read_wave(frame) or 0
    except Exception:                   # noqa: BLE001 - screen unreachable
        return None


# The ONLY screens a handoff may start from: Home, the run itself, and the
# run-end dialogs that tourney.ensure_home banks on its way Home (GAME STATS,
# the exit-battle and end-round confirmations). Every other screen - a menu a
# person opened (CARDS, MODULES, GUILD, TOURNAMENT...), any dialog or reward
# screen, and anything unrecognized - is somebody's, and the handoff waits.
HANDOFF_SCREENS = frozenset({"home", "battle", "game_stats", "exit_battle",
                             "end_round"})


def _hold_while_unknown() -> None:
    """Never start a handoff over a screen the handoff does not own.

    Any screen outside HANDOFF_SCREENS is where a HUMAN may be working, or a
    place automation cannot name: 2026-08-17 a shard runner aborted ("left the
    battle screen") because the user opened the module screens, and combo
    swapped loadouts right through their session. Naming the screen was not
    enough - a recognized personal menu (CARDS) used to release the hold and
    tourney.ensure_home then tapped Home over the person. Waits until the game
    shows Home, a battle or one of its end dialogs; the hold is announced on
    EVERY poll with the screen and how long it has lasted, so a stranded menu
    is visible rather than a silent stall. Each poll takes a fresh screenshot.
    (The name predates the allowlist; run() and the tests call it by it.)
    """
    from device import capture
    from vision import screen
    held_at = None
    while True:
        try:
            name = screen.identify(capture.grab()).name
        except Exception as e:              # noqa: BLE001 - adb hiccup
            logger.event("combo_hold_err", error=str(e)[:120])
            time.sleep(POLL)
            continue
        if name in HANDOFF_SCREENS:
            if held_at is not None:
                logger.event("combo_handoff_release", screen=name,
                             held_sec=int(time.monotonic() - held_at))
            return
        if held_at is None:
            held_at = time.monotonic()
        logger.event("combo_handoff_hold", screen=name,
                     reason=("unrecognized screen" if name == "unknown"
                             else "screen the handoff does not own"),
                     held_sec=int(time.monotonic() - held_at))
        time.sleep(POLL)


# ------------------------------------------------------------------ loop
def run(instance: str = "main") -> None:
    """The scheduler loop, driven by BLOCKS.

    One loop for both eras: with no plan the blocks are _legacy_blocks(), which
    is due() restated, so every decision, key and log line below is the one the
    constant era produced. With a plan they are the compiled ones. Nothing else
    about the loop moved - the runflag contract, the observable boundary hold, the
    unknown-screen hold and the adopt-before-handoff order are all as they were.
    """
    # Before adopting a runner, a handoff or any child: refuse while recorded
    # setup evidence leaves a setup battle unresolved on this emulator.
    from control.battles import require_runner_entry
    require_runner_entry(settings.ROOT, CONFIG, instance, event=logger.event)
    cur: dict | None = None                 # the block we are running
    proc: subprocess.Popen | None = None
    base = 0                                # progress at (re)spawn time
    logger.event("combo_start", instance=instance)
    try:
        while True:
            now = datetime.datetime.now()
            want = next_block(now)
            blocks = today_blocks(now)
            # In plan mode the constant-era runners are FOREIGN WORK: adoptable
            # so nothing navigates underneath them, never schedulable.
            candidates = blocks + (foreign_legacy_blocks()
                                   if _plan_mode() else [])

            # ADOPT ANY BLOCK, NOT JUST THE WANTED ONE. Adopting only a
            # matching runner meant that on startup with a coin farm already
            # going and the shard hour passed, combo saw "no runner" and drove
            # straight into a loadout swap - navigating menus underneath a live
            # battle. Claiming the foreign runner first routes it through the
            # normal boundary stop below, which is the whole promise of the mode.
            if proc is None:
                for other in candidates:
                    if not _block_ok(other):
                        continue
                    found = _find_running_block(other, instance, candidates)
                    if found is not None:
                        proc, cur = found, other
                        base = _block_progress(other)
                        logger.event("combo_adopted", phase=other["block"],
                                     block=other["id"],
                                     foreign=bool(other.get("foreign")),
                                     wanted=want["id"] if want else None)
                        break

            # the runner left on its own: a finished shard block, a finished
            # tournament, or a crash. Either way this block's turn is over.
            if proc is not None and proc.poll() is not None:
                _reconcile_child(proc)
                logger.event("combo_runner_exit", phase=cur["block"],
                             block=cur["id"], code=proc.returncode)
                _close_block(cur, base)
                proc, cur = None, None
                want = next_block()

            cur_id = cur["id"] if cur else None
            want_id = want["id"] if want else None
            if cur_id != want_id and _same_work(cur, want):
                # MIDNIGHT CONTINUITY (Codex P5, MEDIUM). Block ids carry the
                # weekday, so an overnight coin farm's id changes at 00:00 even
                # though nothing about the work did. Stopping and respawning
                # there would cost a run boundary and a full handoff for a
                # rename. Rebind to the new day's block instead and carry on;
                # the counter follows the new id, which is what keeps the
                # per-day quotas per-day.
                logger.event("combo_block_continued", was=cur_id, now=want_id,
                             preset=want["preset"])
                cur, cur_id = want, want_id
                base = _block_progress(want)
            if cur_id != want_id:
                if proc is not None:
                    _stop_and_wait(proc, f"combo -> {want_id}")
                    _close_block(cur, base)
                    proc, cur = None, None
                    # A long run can cross several schedule windows. Choose
                    # from the current clock after its boundary, rather than
                    # launching the block that was due hours earlier.
                    now = datetime.datetime.now()
                    blocks = today_blocks(now)
                    candidates = blocks + (foreign_legacy_blocks()
                                           if _plan_mode() else [])
                    want = next_block(now)
                    want_id = want['id'] if want else None
                runflag.clear()
                if want is None:
                    # NOTHING IS SCHEDULED. Every block is done, outside its
                    # window or refused - so the tower is left alone. Holding
                    # is the honest answer; inventing a filler is how a day
                    # off becomes an unplanned coin run.
                    logger.event("combo_idle", day=WEEKDAY_NAMES[
                        datetime.datetime.now().weekday()],
                        blocks=[b.get("id") for b in blocks
                                if isinstance(b, dict)])
                    time.sleep(POLL)
                    continue
                # NAME THE SCREEN FIRST (2026-09-05). The orphan check below
                # ran while a person had the Battle History open: no wave, so
                # "no battle"; then the hold before the handoff waited 77
                # minutes for them to leave, and on release the handoff ran
                # straight over the wave-3757 coin run that had been there
                # all along (ensure_home's new hold caught it; before that
                # day it would have been ended). Hold first, then look.
                _hold_while_unknown()
                # Adopt before building: if the tower is ALREADY doing this
                # block, the handoff would walk it Home and abandon a live run.
                existing = _find_running_block(want, instance, blocks)
                if existing is not None:
                    proc, cur = existing, want
                    base = _block_progress(want)
                    logger.event("combo_adopted", phase=want["block"],
                                 block=want["id"])
                    _wait_for_child(proc)
                    continue
                # AN ORPHAN LIVE RUN - a battle with NO runner process (the
                # game resumed its round after an emulator restart, or a
                # human left one going) - is invisible to the runner adoption
                # above, and the handoff below would walk it Home and end it.
                # That killed a wave-5715 run live (2026-08-28). The boundary
                # rule owns this: the orphan is farmed to its NATURAL death
                # by the runner that adopts battles (coin), and the wanted
                # block starts at that boundary via the ordinary
                # _stop_and_wait path on the next poll.
                orphan = _orphan_battle()
                if orphan is not None:
                    adopt_b = (want if want["kind"] == "coin" else
                               next((b for b in blocks
                                     if isinstance(b, dict)
                                     and b.get("kind") == "coin"
                                     and _block_ok(b)), None))
                    if adopt_b is None:
                        logger.event("combo_hold_orphan_run", wave=orphan,
                                     wanted=want["id"],
                                     why="no coin block to farm it with")
                        time.sleep(POLL)
                        continue
                    # NO handoff - the coin runner adopts the live battle;
                    # loadout/tier normalize at its death like any adoption.
                    proc = _block_runner(adopt_b, instance)
                    cur, base = adopt_b, _block_progress(adopt_b)
                    logger.event("combo_adopted_orphan_run", wave=orphan,
                                 block=adopt_b["id"], wanted=want["id"])
                    _wait_for_child(proc)
                    continue
                if want["kind"] == "tournament":
                    # LAST LOOK BEFORE THE GEMS. The eligibility check ran a
                    # poll ago and an adopted foreign runner may have closed the
                    # day in between; re-asking here is cheap and the mistake is
                    # not (the entry auto-starts and the next one costs more).
                    taken = _tournament_taken(now, candidates)
                    if taken:
                        logger.event("combo_tournament_refused",
                                     block=want["id"], evidence=taken)
                        _mark_block_done(want)
                        continue
                _hold_while_unknown()
                if _orphan_battle() is not None:
                    # The screen changed under the hold and a live battle is
                    # back: re-run the adoption above, never the handoff.
                    logger.event("combo_handoff_deferred", block=want["id"],
                                 why="live battle after hold")
                    time.sleep(POLL)
                    continue
                try:
                    setup = _block_handoff(want, instance)
                except Exception as e:              # noqa: BLE001
                    # A block that cannot be set up must not spin. Close it for
                    # the day and fall through to the next one - the filler
                    # needs no special state, which is why it is last.
                    logger.event("combo_handoff_failed", phase=want["block"],
                                 block=want["id"], error=str(e))
                    if want["kind"] != "coin":
                        _mark_block_done(want)
                        continue
                    raise
                base = _block_progress(want)
                proc = _block_runner(want, instance, setup=setup)
                cur = want
                logger.event("combo_phase", phase=want["block"],
                             block=want["id"])

            if proc is None:
                time.sleep(POLL)
            else:
                _wait_for_child(proc)
    except KeyboardInterrupt:
        logger.event("combo_stop", phase=cur["block"] if cur else None)
        if proc is not None:
            _stop_and_wait(proc, "combo interrupted")


if __name__ == "__main__":
    import argparse
    import settings
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="accepted for tray parity")
    ap.add_argument("--plan", action="store_true",
                    help="print TODAY's schedule and exit (today, not a "
                         "rolling 24h - use tools/plan_sim.py for the week)")
    a = ap.parse_args()
    settings.select_instance(a.instance)
    if a.plan and _plan_map() is not None:
        # A PLAN IS BOUND, so printing the constants would describe a schedule
        # this process is not going to run - the "accepted but ignored" failure
        # profiles exist to abolish. Print the blocks instead, and point at the
        # tool that walks them properly (15-minute grid, --diff vs constants).
        now = datetime.datetime.now()
        print(f"profile {CONFIG['active_profile']!r}, "
              f"today is {now:%A %Y-%m-%d}\n")
        for b in today_blocks(now):
            if not _block_ok(b):
                print(f"  {b.get('id', '?'):<14} REFUSED")
                continue
            win = (f"{b['after_min'] // 60:02d}:{b['after_min'] % 60:02d}-"
                   f"{b['until_min'] // 60:02d}:{b['until_min'] % 60:02d}")
            done = _block_progress(b)
            print(f"  {b['id']:<14} {b['block']:<11} {win}  "
                  f"{done}/{b['count'] if b['count'] is not None else '-'}"
                  f"  {b['preset']}"
                  f"{'  <- now' if b is next_block(now) else ''}")
        print("\nfull week: python tools/plan_sim.py --profile "
              f"{CONFIG['active_profile']} --diff")
    elif a.plan:
        # Simulated, not live: due() only reports what is still OUTSTANDING, so
        # without modelling completion every hour after 08:00 reads "shards".
        # ~150 runs at the measured ~90s is a shade under 4h.
        now = datetime.datetime.now()
        print(f"today is {now:%A %Y-%m-%d}; tournament days = Wed, Sat")
        print(f"shard block = {SHARD_RUNS} runs ~= "
              f"{SHARD_RUNS * 90 / 3600:.1f}h\n")
        finished, prev, started = set(), None, {}
        for h in range(24):
            t = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if (t.hour >= TOURNEY_HOUR and t.weekday() in TOURNEY_DAYS
                    and "tournament" not in finished):
                phase = "tournament"
            elif t.hour >= SHARD_HOUR and "shards" not in finished:
                phase = "shards"
            else:
                phase = "coin"
            if phase != prev:
                started[phase] = h
            elif phase == "shards" and h - started.get("shards", h) >= 4:
                finished.add("shards")
            elif phase == "tournament" and h - started.get("tournament", h) >= 1:
                finished.add("tournament")
            print(f"  {t:%a %H:%M}  ->  {phase}")
            prev = phase
    else:
        run(a.instance)
