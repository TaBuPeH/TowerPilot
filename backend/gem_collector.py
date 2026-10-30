"""Collect diamonds on an existing battle; never start, equip or end a run.

Between battles a collector that harvesting launched also claims the Ad Gem
offer on the exact Home screen (`AdGemCollector.poll_home`): one recognized
CLAIM tap per window, nothing else on Home, and nothing at all on any other
screen."""
import argparse
import sqlite3
import time
import traceback
from types import SimpleNamespace
import settings
from device import capture
from runtime import logger, priority
from runtime.stage_clock import clock
from vision import wave_reader, screen
from vision.frame_reads import FrameReads
from scheduling import runflag
from flows.shard import GemWatch


FENCE_RECHECK_SEC = 5
PASS_PERIOD_SEC = 1.0       # target time from the start of one pass to the start of the next
LEDGER_BUSY_LIMIT_SEC = 180


class FenceUnknown(Exception):
    """The fence could not read the person's choice (the ledger was unreadable): authorization is unknown, not
    revoked. No input is sent meanwhile; a refusal that outlasts the guard's limit ends the collector as before."""


class LedgerGuard:
    """A ledger write that meets a lock another process holds is skipped, logged and retried by the next pass; the
    gem polls are not skipped with it. SQLite already waited its own busy timeout (15 s) inside the failed call, so
    that pass is slow; a lock that
    lasts LEDGER_BUSY_LIMIT_SEC of consecutive failures of one stage is not transient: that error is raised as it was before.
    Only a lock is absorbed: any other error is a real failure."""

    def __init__(self, limit=LEDGER_BUSY_LIMIT_SEC):
        self.limit, self.stages = limit, {}     # stage -> [first failure at, consecutive failures]: one budget each

    @staticmethod
    def locked(exc):
        text = str(exc).lower()
        return isinstance(exc, sqlite3.OperationalError) and ('locked' in text or 'busy' in text)

    def retrying(self, stage):
        """A failed attempt of this stage may have written part of its work; the next call finishes it."""
        return stage in self.stages

    def forget(self, stage):
        """The pending work of this stage is gone (its record closed): its failures are no longer counted."""
        self.stages.pop(stage, None)

    def run(self, stage, call, *args, **kwargs):
        """(True, result) when the call ran; (False, None) when the ledger was locked and the call did not happen."""
        try:
            result = call(*args, **kwargs)
        except (sqlite3.OperationalError, FenceUnknown) as exc:
            if not isinstance(exc, FenceUnknown) and not self.locked(exc):
                raise
            now = time.monotonic()
            since, failures = self.stages.setdefault(stage, [now, 0])
            self.stages[stage][1] = failures = failures + 1
            logger.event('gem_collector_ledger_busy', stage=stage, error=str(exc), attempts=failures,
                         busy_sec=round(now-since, 1), trace=traceback.format_exc(limit=-8))
            if now-since > self.limit:
                if isinstance(exc, FenceUnknown):
                    from towerpilot_workflows.harvest import CollectorRefused
                    raise CollectorRefused(str(exc)) from exc
                raise
            return False, None
        failed = self.stages.pop(stage, None)
        if failed:
            logger.event('gem_collector_ledger_recovered', stage=stage, attempts=failed[1])
        return True, result

def automatic_fence():
    """For a collector the lifecycle host launched (request.harvest), a check
    that the person's choice still names exactly this launch; None for a
    collector a person started. Reads the operation ledger the worker was
    accepted into, never the environment's say-so."""
    from runtime.operations import environment_operation
    ledger, operation_id = environment_operation()
    if ledger is None:
        return None
    operation = ledger.get(operation_id)
    if operation['request'].get('harvest') is not True:
        return None
    from towerpilot_workflows.harvest import CollectorRefused, automatic_launch_valid

    def check():
        ok, code = automatic_launch_valid(ledger.path, operation)
        if code == 'harvest_unreadable':     # the choice could not be read: unknown, not revoked
            raise FenceUnknown(f'Harvesting could not be read ({code}); no input was sent')
        if not ok:
            logger.event('gem_collector_refused', reason=code)
            raise CollectorRefused(f'Harvesting no longer asks for this collector ({code}); no input was sent')
    return check


def pace(started, period=PASS_PERIOD_SEC):
    """End of a pass: wait only what is left of the target period. A pass that took longer waits not at all."""
    remaining = period - (time.monotonic() - started)
    if remaining > 0:
        with clock.stage('sleep'):
            time.sleep(remaining)


def leave_for_restart(session, request, last_wave):
    """The planned exit (runflag.collector_exit_requested): close the battle record this collector owns as
    interrupted, with the reason, so the successor starts a clean adopted record, then consume the request. The
    process ends normally, so the ledger records it as succeeded: the harvest supervisor relaunches it without
    counting a failure (control/harvest.py `_failures` counts only failed or interrupted exits)."""
    if session is not None and session.battle['status'] in ('prepared', 'active'):
        session.finish('collector_restart', status='interrupted',
                       evidence={'reason': request['reason'], 'last_observed_wave': last_wave})
    runflag.clear_collector_exit()
    logger.event('gem_collector_stopped', reason=f"restart: {request['reason']}", planned=True)


def main(instance, preset):
    started_at = time.time()
    from runtime.device_lease import prepare_instance
    prepare_instance(instance)
    settings.select_instance(instance,preset)
    # One class above normal so other projects' CPU load does not stretch the 1 s pass to minutes. Logged after the instance is bound (the log file is chosen on first use); a refusal is logged only.
    logger.event('gem_collector_priority', **priority.raise_own_priority())
    # The collector always adopts the battle on screen. Before any runner
    # check, session or frame: the collection-scope setup guard for the bound
    # (accepted workers: restored) device and account, logged under this
    # instance. A person's collection-only handoff is read from the ledger,
    # never from the environment.
    from control.battles import require_runner_entry
    require_runner_entry(settings.ROOT, settings.CONFIG, instance, scope='collection', event=logger.event)
    from runtime.runners import other_runners
    if next(other_runners(), None) is not None:
        raise RuntimeError('Another runner owns the emulator; stop it before collecting gems')
    fence = automatic_fence()
    if fence is not None:
        fence()   # the person's Off, a new revision or a context change since acceptance: before any input
    fence_checked = time.monotonic()
    ledger = LedgerGuard()
    from runtime.battle_session import BattleSession
    from orchestrator import preset as resolved_preset
    session = BattleSession.from_environment(resolved_preset(), settings.CONFIG, origin='adopted')
    body = session.effective_preset() if session is not None else resolved_preset()
    from runtime.action_session import ActionSession
    actions = ActionSession.for_battle(session, 'collector', tracked_only=True) if session is not None else None
    gems=GemWatch(body.get('gather', {}), session=actions)
    state = SimpleNamespace(gems=gems, tracker=wave_reader.WaveTracker(), mission=None, shop=None)
    wave_hwm = None
    logger.event('gem_collector_started',preset=preset,ad_gems=gems.ad_gems.enabled,orbit=gems.orbit.enabled)
    last_status=None
    saw_battle=False
    exit_request = deferred = owed = prepare_owed = None     # prepare_owed: arguments of a prepare that was locked out
    while True:
        pass_started = time.monotonic()
        beat = clock.next_pass()
        if beat is not None:
            logger.event('gem_collector_stages', **beat)
        if owed is not None:
            # A terminal record that was locked out: settled before any further frame is looked at, so a new battle
            # is never observed under the old record.
            closed, _ = ledger.run('finish_results', session.finish, owed['reason'], evidence=owed['evidence'])
            if not closed:
                pace(pass_started)
                continue
            owed = None
            prepare_owed = None
            ledger.forget('prepare')
            reason = runflag.requested()
            if reason is not None:
                logger.event('gem_collector_stopped', reason=reason)
                return
        # A person's request to leave at a safe point (deploy). Between polls, so no claim attempt or orbit
        # attempt is cut; the battle on screen is never touched.
        # Read every pass (a request older than this collector's start is not for it), so a --cancel
        # while the exit is deferred withdraws it.
        asked = runflag.collector_exit_requested(started_at)
        if asked is not None and exit_request is None:
            logger.event('gem_collector_exit_requested', reason=asked['reason'])
        elif asked is None and exit_request is not None:
            logger.event('gem_collector_exit_cancelled', reason=exit_request['reason'])
            deferred = None
        exit_request = asked
        if exit_request is not None:
            busy = gems.busy()
            if not busy:
                left, _ = ledger.run('restart_finish', leave_for_restart, session, exit_request, state.tracker.last)
                if left:
                    return
                pace(pass_started)       # the battle record could not be closed yet: ask again next pass
                continue
            if busy != deferred:
                logger.event('gem_collector_exit_deferred', waiting_for=busy)
                deferred = busy
        authorized = True       # an unread fence (locked or unreadable ledger) authorizes no gem input this pass
        if fence is not None and time.monotonic() - fence_checked >= FENCE_RECHECK_SEC:
            authorized, _ = ledger.run('fence', fence)
            if authorized:
                fence_checked = time.monotonic()
        with clock.stage('capture'):
            frame=capture.grab()
        reads = FrameReads(frame)       # the readings every owner below reuses for this very frame
        with clock.stage('identify'):
            sc=reads.screen(frame)
        with clock.stage('wave'):
            raw = reads.wave(frame)
            live=sc.name == 'battle' and raw is not None
        wave = state.tracker.update(raw)
        backwards = raw is not None and wave_hwm is not None and raw < wave_hwm-100
        if backwards and (wave is None or wave >= wave_hwm-100):
            continue
        if backwards:
            if session is not None and session.battle['status'] in ('prepared', 'active'):
                previous = session.battle['id']
                closed, _ = ledger.run('finish_backwards', session.finish, 'wave_went_backwards',
                                       evidence={'previous_wave': wave_hwm, 'observed_wave': wave})
                if not closed:
                    pace(pass_started)   # the old record is still open: the next backwards frame closes it
                    continue
                prepared, _ = ledger.run('prepare', session.prepare_next, origin='partial_adoption',
                                         previous_battle_id=previous)
                prepare_owed = None if prepared else dict(origin='partial_adoption', previous_battle_id=previous)
                # when locked out, the live branch finishes it, still linked to the record this one continues
            gems = GemWatch(body.get('gather', {}), session=actions)
            state.gems = gems
            state.tracker = wave_reader.WaveTracker()
            wave_hwm = None
            continue
        if wave is not None:
            wave_hwm = wave if wave_hwm is None else max(wave_hwm, wave)
        if saw_battle and sc.name in ('game_stats', 'tournament_stats', 'home'):
            if session is not None and session.battle['status'] in ('prepared', 'active'):
                why = 'observed_results' if sc.name != 'home' else 'battle_no_longer_observed'
                proof = {'screen': sc.name, 'last_observed_wave': state.tracker.last}
                closed, _ = ledger.run('finish_results', session.finish, why, evidence=proof)
                if not closed:      # a terminal record is never dropped: it is retried first thing next pass
                    owed = dict(reason=why, evidence=proof)
                    pace(pass_started)
                    continue
            prepare_owed = None         # no battle is waiting for its record any more
            ledger.forget('prepare')
            reason=runflag.requested()
            if reason is not None:
                logger.event('gem_collector_stopped',reason=reason)
                return
        saw_battle=saw_battle or live
        # Home claims: only a collector harvesting launched (its fence proves
        # the person's choice still asks for it), on the exact Home screen,
        # with the routine enabled and no battle active.
        home = (fence is not None and not live and sc.name == 'home' and gems.ad_gems.enabled
                and (session is None or session.battle['status'] != 'active'))
        label='collecting' if live else 'claiming_home_offer' if home else 'waiting_for_battle'
        if label!=last_status:
            logger.event('gem_collector_state',state=label)
            last_status=label
        if home:
            checked, _ = ledger.run('fence', fence)     # immediately before any Home input decision, not only every FENCE_RECHECK_SEC
            if not checked:         # an unread fence authorizes no Home input
                pace(pass_started)
                continue
            fence_checked = time.monotonic()
            with clock.stage('poll_home'):
                gems.ad_gems.poll_home(frame, reads=reads)
            if session is not None and session.battle['status'] == 'prepared':
                with clock.stage('ledger'):
                    ledger.run('checkpoint_home', session.checkpoint, state, battle_visible=False, observed=('ad_gems',))
            pace(pass_started)
            continue
        if fence is not None and not live:
            gems.ad_gems.leave_home()
        if live:
            if session is not None:
                if (session.battle['status'] not in ('prepared', 'active') or prepare_owed is not None
                        or ledger.retrying('prepare')):
                    was_live = session.battle['status'] in ('prepared', 'active')
                    ledger.run('prepare', session.prepare_next, **(prepare_owed or dict(origin='adopted')))
                    if session.battle['status'] not in ('prepared', 'active'):
                        pace(pass_started)      # no record yet: nothing to collect under; the next pass asks again
                        continue
                    # The record exists, linked as asked. A setup report still owed (its write met the lock) is finished
                    # by a later pass (ledger.retrying); collecting goes on meanwhile, under this record.
                    prepare_owed = None
                    if not was_live:            # a new record: a new battle's routines
                        gems = GemWatch(body.get('gather', {}), session=actions)
                        state.gems = gems
                        state.tracker = wave_reader.WaveTracker()
                        wave_hwm = None
                with clock.stage('ledger'):
                    ledger.run('observe', lambda: (session.observe(state, raw), session.checkpoint(state, battle_visible=True)))
            if authorized:
                with clock.stage('poll'):
                    gems.poll(frame, reads=reads)
        elif session is not None and session.battle['status'] in ('prepared', 'active'):
            with clock.stage('ledger'):
                ledger.run('checkpoint', session.checkpoint, state, battle_visible=False)
        pace(pass_started)


def run_logged(instance, preset):
    try:main(instance, preset)
    except Exception as exc:
        logger.event('gem_collector_failed', error=str(exc), error_type=type(exc).__name__, trace=traceback.format_exc(limit=12))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',required=True)
    parser.add_argument('--preset',required=True)
    args=parser.parse_args()
    run_logged(args.instance, args.preset)
