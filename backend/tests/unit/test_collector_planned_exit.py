"""A planned collector exit (docs/FOCUS.md change 2c): new code goes live without killing the gem collector.

Offline with fakes. The runflag files are real (a temporary working folder); the screen, the gem routines, the
battle record and the process are fake boundaries, as in test_gem_collector_home.py. The supervisor half runs on
the real temporary ledgers of test_harvest_lifecycle.py."""
import importlib.util
import os
import time
import types

import paths as test_paths
import pytest

from control import harvest
from flows.shard import GemOrbitTapper, GemWatch
from harvest_support import NOW, choose, finish_last, make_env
from interactions.ad_gems import AdGemCollector
from scheduling import runflag


class Exhausted(Exception):
    pass


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)      # runflag's default folder is relative to the working directory
    return tmp_path


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


def collector(monkeypatch, *, polls=None, grabs=None, busy_for=None, session=True, frames=40):
    """Run gem_collector.main on battle frames until it returns or `frames` are used up (Exhausted).

    grabs(n) runs inside the n-th screen capture, polls(n, gems) inside the n-th gem poll; both are the test's hands.
    Returns (events, gems, session, grabbed, polled, returned): returned is False when the frames ran out."""
    import gem_collector as worker
    from runtime import action_session, battle_session, device_lease, runners
    events, grabbed, polled, finished = [], [], [], []
    monkeypatch.setattr(device_lease, 'prepare_instance', lambda name: None)
    monkeypatch.setattr(worker.settings, 'select_instance', lambda *a: None)
    monkeypatch.setattr(runners, 'other_runners', lambda: iter([]))
    monkeypatch.setattr(worker, 'automatic_fence', lambda: None)
    fake = types.SimpleNamespace(
        battle=dict(status='active', id='b1'), effective_preset=lambda: {},
        observe=lambda state, raw: True, checkpoint=lambda state, **kw: None,
        finish=lambda *a, **kw: finished.append((a, kw)), prepare_next=lambda **kw: None) if session else None
    monkeypatch.setattr(battle_session.BattleSession, 'from_environment', classmethod(lambda cls, *a, **kw: fake))
    monkeypatch.setattr(action_session.ActionSession, 'for_battle', classmethod(lambda cls, *a, **kw: None))
    gems = types.SimpleNamespace(ad_gems=AdGemCollector(), orbit=GemOrbitTapper())
    gems.busy = lambda: GemWatch.busy(gems)
    gems.poll = lambda frame, reads=None: (polled.append(frame), polls and polls(len(polled), gems))
    monkeypatch.setattr(worker, 'GemWatch', lambda gather=None, **kw: gems)

    def grab():
        grabbed.append(1)
        if len(grabbed) > frames:
            raise Exhausted
        if grabs:
            grabs(len(grabbed))
        return 'battle'
    monkeypatch.setattr(worker.capture, 'grab', grab)
    monkeypatch.setattr(worker.screen, 'identify', lambda f: types.SimpleNamespace(name=f))
    monkeypatch.setattr(worker.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    monkeypatch.setattr(worker.runflag, 'requested', lambda: None)
    monkeypatch.setattr(worker.logger, 'event', lambda name, **kw: events.append((name, kw)))
    try:
        worker.main('main', 'bp_farm')
        returned = True
    except Exhausted:
        returned = False
    return events, gems, finished, grabbed, polled, returned


def ask(reason='deploy'):
    return runflag.request_collector_exit(reason)


def names(events):
    return [name for name, _ in events]


# ------------------------------------------------------------- the collector
def test_an_idle_collector_leaves_at_the_next_poll_and_closes_its_battle(work, monkeypatch):
    events, _, finished, grabbed, polled, returned = collector(monkeypatch, grabs=lambda n: n == 2 and ask())
    assert returned
    assert len(polled) == 2 and len(grabbed) == 2          # asked during the 2nd capture; no 3rd capture, no more input
    assert finished == [(('collector_restart',), dict(status='interrupted',
                         evidence={'reason': 'deploy', 'last_observed_wave': 100}))]
    assert events[-1] == ('gem_collector_stopped', {'reason': 'restart: deploy', 'planned': True})
    assert 'gem_collector_exit_deferred' not in names(events)
    assert runflag.collector_exit_requested() is None      # the request was consumed


def test_without_a_request_the_collector_keeps_polling(work, monkeypatch):
    events, _, finished, _, polled, returned = collector(monkeypatch, frames=6)
    assert not returned and len(polled) == 6 and finished == []
    assert 'gem_collector_exit_requested' not in names(events)


def test_a_collector_without_a_battle_record_still_leaves(work, monkeypatch):
    events, _, finished, _, polled, returned = collector(monkeypatch, session=False, grabs=lambda n: n == 1 and ask('r'))
    assert returned and finished == [] and len(polled) == 1
    assert events[-1] == ('gem_collector_stopped', {'reason': 'restart: r', 'planned': True})


def test_it_waits_while_an_ad_claim_is_in_progress_and_leaves_right_after(work, monkeypatch):
    def polls(n, gems):
        if n == 2:
            gems.ad_gems.claim_attempt_id = 'attempt-1'      # an attempt that has not returned yet
        if n == 5:
            gems.ad_gems.claim_attempt_id = None
    events, _, finished, grabbed, polled, returned = collector(monkeypatch, polls=polls,
                                                               grabs=lambda n: n == 3 and ask())
    assert returned
    assert len(polled) == 5 and len(grabbed) == 5          # polls 3, 4 and 5 ran while it was busy; nothing after 5
    assert ('gem_collector_exit_deferred', {'waiting_for': ['ad_claim_attempt']}) in events
    assert names(events).count('gem_collector_exit_deferred') == 1
    assert len(finished) == 1 and events[-1][0] == 'gem_collector_stopped'


def test_it_waits_for_a_scheduled_ad_claim(work, monkeypatch):
    def polls(n, gems):
        gems.ad_gems.claim_at = 10.0 if n == 1 else None if n == 4 else gems.ad_gems.claim_at   # offer seen, claim due later
    events, _, _, _, polled, returned = collector(monkeypatch, polls=polls, grabs=lambda n: n == 2 and ask())
    assert returned and len(polled) == 4
    assert ('gem_collector_exit_deferred', {'waiting_for': ['ad_claim_pending']}) in events


def test_it_waits_while_an_orbit_attempt_is_open_and_leaves_right_after(work, monkeypatch):
    def polls(n, gems):
        if n == 1:
            gems.orbit.attempt_started, gems.orbit.window_ends_at = 1.0, time.monotonic() + 1000   # a recognized gem opened a window
        if n == 4:
            gems.orbit._reset_attempt()
    events, _, finished, _, polled, returned = collector(monkeypatch, polls=polls, grabs=lambda n: n == 2 and ask())
    assert returned and len(polled) == 4 and len(finished) == 1
    assert ('gem_collector_exit_deferred', {'waiting_for': ['orbit_attempt']}) in events


def test_both_routines_busy_are_both_named_and_both_waited_out(work, monkeypatch):
    def polls(n, gems):
        if n == 1:
            gems.ad_gems.claim_attempt_id = 'a'
            gems.orbit.attempt_started, gems.orbit.window_ends_at = 1.0, time.monotonic() + 1000
        if n == 3:
            gems.ad_gems.claim_attempt_id = None
        if n == 5:
            gems.orbit._reset_attempt()
    events, _, _, _, polled, returned = collector(monkeypatch, polls=polls, grabs=lambda n: n == 2 and ask())
    assert returned and len(polled) == 5
    assert [kw['waiting_for'] for name, kw in events if name == 'gem_collector_exit_deferred'] == [
        ['ad_claim_attempt', 'orbit_attempt'], ['orbit_attempt']]


def test_a_request_written_before_this_collector_started_is_not_for_it(work, monkeypatch):
    runflag.request_collector_exit('older', now=time.time() - 5)
    events, _, finished, _, polled, returned = collector(monkeypatch, frames=5)
    assert not returned and finished == [] and len(polled) == 5
    assert 'gem_collector_exit_requested' not in names(events)


def test_a_request_withdrawn_while_the_exit_is_deferred_is_not_acted_on(work, monkeypatch):
    def polls(n, gems):
        if n == 1:
            gems.ad_gems.claim_attempt_id = 'a'
        if n == 4:
            gems.ad_gems.claim_attempt_id = None
    def grabs(n):
        if n == 2:
            ask()
        if n == 3:
            runflag.clear_collector_exit()                 # --cancel
    events, _, finished, _, polled, returned = collector(monkeypatch, polls=polls, grabs=grabs, frames=8)
    assert not returned and finished == [] and len(polled) == 8
    assert 'gem_collector_exit_cancelled' in names(events)


def test_a_stale_request_is_ignored_and_not_deleted_at_start(work, monkeypatch):
    # The collector no longer deletes stale files at start (that raced a request written at the same moment).
    runflag.request_collector_exit('older', now=time.time() - 5)
    collector(monkeypatch, frames=2)
    assert runflag.collector_exit_requested(time.time() - 1) is None      # ignored by the collector ...
    assert os.path.exists(runflag._path(runflag.COLLECTOR_EXIT_FILE))      # ... and left alone


def test_the_stop_after_run_flag_does_not_trigger_a_planned_exit_and_the_reverse(work):
    runflag.request('phase change')
    assert runflag.collector_exit_requested() is None
    runflag.clear()
    ask()
    assert runflag.requested() is None


# --------------------------------------------------------- the safe-point accessors
def test_a_scheduled_or_running_ad_claim_is_busy_and_nothing_else_is():
    ad = AdGemCollector()
    assert ad.busy() is None
    ad.window_deadline = time.monotonic() + 1000                               # an open window with no offer seen is a safe point
    assert ad.busy() is None
    ad.claim_at = 3.0
    assert ad.busy() == 'ad_claim_pending'
    ad.claim_at, ad._held_response = None, (1.0, 2.0)
    assert ad.busy() == 'ad_claim_pending'
    ad._held_response, ad.claim_attempt_id = None, 'x'
    assert ad.busy() == 'ad_claim_attempt'


def test_an_orbit_attempt_past_its_window_expires_without_a_battle_frame():
    orbit = GemOrbitTapper()
    orbit.attempt_started, orbit.window_ends_at = time.monotonic() - 50, time.monotonic() - 1
    assert orbit.busy() is None and orbit.attempt_started is None      # results or Home on screen: nobody polls it


def test_an_ad_window_past_its_deadline_closes_without_a_battle_frame():
    ad = AdGemCollector()
    ad.window_started_at, ad.window_deadline, ad.claim_at = time.monotonic() - 50, time.monotonic() - 1, 3.0
    assert ad.busy() is None and ad.claim_at is None and ad.window_deadline is None


def test_an_open_orbit_attempt_is_busy_and_a_reset_one_is_not():
    orbit = GemOrbitTapper()
    assert orbit.busy() is None
    orbit.attempt_started, orbit.window_ends_at = 1.0, time.monotonic() + 1000
    assert orbit.busy() == 'orbit_attempt'
    orbit._reset_attempt()
    assert orbit.busy() is None


def test_the_gem_watch_reports_every_reason(monkeypatch):
    watch = types.SimpleNamespace(ad_gems=AdGemCollector(), orbit=GemOrbitTapper())
    assert GemWatch.busy(watch) == []
    watch.ad_gems.claim_attempt_id, watch.orbit.attempt_started = 'a', 1.0
    watch.orbit.window_ends_at = time.monotonic() + 1000
    assert GemWatch.busy(watch) == ['ad_claim_attempt', 'orbit_attempt']


# ------------------------------------------------------------- the request file
def test_a_request_round_trips_and_is_consumed_once(tmp_path):
    runflag.request_collector_exit('deploy', log_dir=tmp_path, now=100.0)
    assert runflag.collector_exit_requested(log_dir=tmp_path) == dict(reason='deploy', requested_at=100.0)
    assert runflag.collector_exit_requested(100.0, log_dir=tmp_path) is not None
    assert runflag.collector_exit_requested(100.1, log_dir=tmp_path) is None
    assert runflag.collector_exit_pending(log_dir=tmp_path)
    assert runflag.clear_collector_exit(log_dir=tmp_path) is True
    assert runflag.clear_collector_exit(log_dir=tmp_path) is False
    assert not runflag.collector_exit_pending(log_dir=tmp_path)


def test_a_file_another_process_holds_open_never_makes_the_exit_raise(tmp_path, monkeypatch):
    runflag.request_collector_exit('deploy', log_dir=tmp_path, now=1.0)
    real_remove, real_replace, calls = os.remove, os.replace, []

    def busy_once(real):
        def inner(*args):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError('being used by another process')
            return real(*args)
        return inner
    monkeypatch.setattr(runflag.time, 'sleep', lambda _: None)
    monkeypatch.setattr(os, 'remove', busy_once(real_remove))
    assert runflag.clear_collector_exit(log_dir=tmp_path) is True and len(calls) == 2      # retried, then deleted
    monkeypatch.setattr(os, 'remove', lambda *a: (_ for _ in ()).throw(PermissionError('held')))
    runflag.request_collector_exit('again', log_dir=tmp_path, now=2.0)
    assert runflag.clear_collector_exit(log_dir=tmp_path) is False                           # held for good: no raise
    calls.clear()
    monkeypatch.setattr(os, 'replace', busy_once(real_replace))
    runflag.request_collector_exit('third', log_dir=tmp_path, now=3.0)
    assert len(calls) == 2 and runflag.collector_exit_requested(log_dir=tmp_path)['reason'] == 'third'


def test_a_hand_written_request_counts_with_its_text_and_file_age(tmp_path):
    (tmp_path / runflag.COLLECTOR_EXIT_FILE).write_text('please restart', encoding='utf-8')
    found = runflag.collector_exit_requested(log_dir=tmp_path)
    assert found['reason'] == 'please restart' and found['requested_at'] > 0


def test_reading_a_request_creates_no_folder(tmp_path):
    missing = tmp_path / 'nope'
    assert runflag.collector_exit_requested(log_dir=missing) is None
    assert runflag.clear_collector_exit(log_dir=missing) is False
    assert not missing.exists()


# ------------------------------------------------------------------- the CLI
@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location('collector_restart_tool', test_paths.RELEASE_TOOLS / 'collector_restart.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_cli_only_writes_the_request(cli, tmp_path):
    lines = []
    assert cli.main(['--log-dir', str(tmp_path), '--reason', 'new orbit code'], out=lines.append) == 0
    assert runflag.collector_exit_requested(log_dir=tmp_path)['reason'] == 'new orbit code'
    assert sorted(os.listdir(tmp_path)) == [runflag.COLLECTOR_EXIT_FILE]     # no event log, no temp file left behind
    out = []
    assert cli.main(['--log-dir', str(tmp_path), '--status'], out=out.append) == 0
    assert 'new orbit code' in out[0]
    assert cli.main(['--log-dir', str(tmp_path), '--cancel'], out=out.append) == 0
    assert runflag.collector_exit_requested(log_dir=tmp_path) is None
    assert cli.main(['--log-dir', str(tmp_path), '--status'], out=out.append) == 0 and 'no collector' in out[-1]


def test_the_cli_waits_until_the_collector_takes_the_request(cli, tmp_path):
    ticks = iter(range(100))
    taken = []

    def sleep(_):
        taken.append(1)
        if len(taken) == 3:                                # the collector consumed it
            runflag.clear_collector_exit(log_dir=tmp_path)
    out = []
    assert cli.main(['--log-dir', str(tmp_path), '--wait', '30'], sleep=sleep, clock=lambda: next(ticks), out=out.append) == 0
    assert 'is leaving' in out[-1]


def test_the_cli_reports_a_request_nobody_took(cli, tmp_path):
    ticks = iter(range(100))
    out = []
    assert cli.main(['--log-dir', str(tmp_path), '--wait', '5'], sleep=lambda _: None, clock=lambda: next(ticks),
                    out=out.append) == 2
    assert 'still pending' in out[-1]
    assert runflag.collector_exit_requested(log_dir=tmp_path) is not None     # left for the person to cancel or retry


# ----------------------------------------------------------- the supervisor
def test_a_planned_exit_is_relaunched_on_the_next_tick_and_never_counts_as_a_failure(h):
    """The collector ends normally, so the ledger records `succeeded`. However many deploys land, the circuit stays
    closed and each exit is relaunched as soon as the minimum spacing allows."""
    choose(h)
    launched_at = NOW
    assert h.tick(launched_at)['action'] == 'launched'
    for deploy in range(1, harvest.CIRCUIT + 3):
        exited_at = launched_at + 30                         # inside FAIL_WINDOW_SEC: a fast exit
        finish_last(h, 'succeeded', ran=30, now=exited_at)
        result = h.tick(exited_at + 1)
        assert (result['failures'], result['circuit_open'], result['action']) == (0, False, 'launched'), deploy
        launched_at = exited_at + 1
    assert len(h.spawned) == harvest.CIRCUIT + 3


def test_an_unrequested_death_still_counts_and_backs_off(h):
    choose(h)
    assert h.tick(NOW)['action'] == 'launched'
    finish_last(h, 'succeeded', ran=30, now=NOW + 30)                # a planned exit first
    assert h.tick(NOW + 31)['action'] == 'launched'
    finish_last(h, 'failed', ran=5, now=NOW + 36)                    # then a death nobody asked for
    result = h.tick(NOW + 37)
    assert (result['failures'], result['state'], result['action']) == (1, 'backing_off', 'none')
    assert result['next_attempt_at'] == NOW + 36 + harvest.backoff(1)


def test_planned_exits_between_deaths_are_not_failures_but_end_the_streak(h):
    """Pins today's rule: a clean exit ends the consecutive-failure count, planned or not."""
    choose(h)
    now = NOW
    assert h.tick(now)['action'] == 'launched'
    finish_last(h, 'failed', ran=5, now=now + 5)
    now = h.tick(now + 6)['next_attempt_at']
    assert h.tick(now)['action'] == 'launched'
    finish_last(h, 'succeeded', ran=30, now=now + 30)
    assert h.tick(now + 31)['failures'] == 0
