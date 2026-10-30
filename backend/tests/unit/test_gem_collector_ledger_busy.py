"""The gem collector survives a transient 'database is locked' on its ledger (FOCUS #11 step 0).

On 2026-10-26 the collector died twice with that error (SQLite waited its 15 s busy timeout, then raised). Offline:
the session, the gem routines, the screen and the clock are fakes."""
import sqlite3
import types

import pytest

from gem_collector_run import run as run_world


def run(monkeypatch, frames, *, session, requested=None, fence=None):
    events, polled, returned, _ = run_world(monkeypatch, frames, session=session, requested=requested, fence=fence)
    return events, polled, returned


def locked():
    return sqlite3.OperationalError('database is locked')


def flaky(failures, then=None):
    """A fake that raises the lock `failures` times, then records its calls."""
    calls = []

    def call(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) <= failures:
            raise locked()
        return then
    return call, calls


def names(events):
    return [name for name, _ in events]


def test_a_locked_checkpoint_never_stops_the_collector_or_its_gem_polls(monkeypatch):
    checkpoint, calls = flaky(3)
    session = types.SimpleNamespace(battle=dict(status='active', id='b1'), effective_preset=lambda: {},
                                    observe=lambda state, raw: True, checkpoint=checkpoint)
    events, polled, returned = run(monkeypatch, ['battle'] * 6, session=session)
    assert not returned                         # only the test's frames ended the loop, not the lock
    assert polled == ['battle'] * 6             # collecting went on while the ledger was locked
    assert len(calls) == 6                      # the write is attempted again every pass, and lands
    busy = [kw for name, kw in events if name == 'gem_collector_ledger_busy']
    assert [kw['attempts'] for kw in busy] == [1, 2, 3]
    assert all(kw['stage'] == 'observe' and 'database is locked' in kw['error'] and 'Traceback' in kw['trace'] for kw in busy)
    assert names(events).count('gem_collector_ledger_recovered') == 1


def test_a_locked_terminal_record_is_retried_until_it_lands_and_only_then_the_collector_stops(monkeypatch):
    finish, calls = flaky(2)
    session = types.SimpleNamespace(battle=dict(status='active', id='b1'), effective_preset=lambda: {},
                                    observe=lambda state, raw: True, checkpoint=lambda state, **kw: None, finish=finish)
    events, polled, returned = run(monkeypatch, ['battle', 'game_stats', 'game_stats', 'game_stats', 'game_stats'],
                                   session=session, requested='finish this run')
    assert returned and len(calls) == 3         # two locks, then the record was written
    assert calls[-1][0][0] == 'observed_results'
    assert events[-1] == ('gem_collector_stopped', {'reason': 'finish this run'})
    assert names(events).count('gem_collector_ledger_busy') == 2


def test_a_lock_that_never_clears_still_ends_the_collector_with_the_error(monkeypatch):
    clock = iter(range(0, 10_000, 100))
    import gem_collector as worker
    monkeypatch.setattr(worker.time, 'monotonic', lambda: next(clock))
    checkpoint, calls = flaky(10_000)
    session = types.SimpleNamespace(battle=dict(status='active', id='b1'), effective_preset=lambda: {},
                                    observe=lambda state, raw: True, checkpoint=checkpoint)
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        run(monkeypatch, ['battle'] * 50, session=session)


def test_another_database_error_is_not_absorbed(monkeypatch):
    def broken(state, **kw):
        raise sqlite3.OperationalError('no such table: battles')
    session = types.SimpleNamespace(battle=dict(status='active', id='b1'), effective_preset=lambda: {},
                                    observe=lambda state, raw: True, checkpoint=broken)
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        run(monkeypatch, ['battle'] * 3, session=session)


def test_an_unread_fence_sends_no_home_input(monkeypatch):
    attempts, ad_calls = [], []

    def fence():
        attempts.append(1)
        if len(attempts) == 2:                  # the startup check passes; the first Home check meets the lock
            raise locked()
    ad = types.SimpleNamespace(enabled=True, poll_home=lambda f, reads=None: ad_calls.append(f), leave_home=lambda: None)
    gems = types.SimpleNamespace(ad_gems=ad, orbit=types.SimpleNamespace(enabled=True), busy=lambda: [],
                                 poll=lambda f, reads=None: None)
    events, _, returned, _ = run_world(monkeypatch, ['home', 'home'], fence=fence, gems=gems)
    assert not returned
    assert ad_calls == ['home']                 # the pass whose fence was locked sent nothing; the next one did
    assert [kw['stage'] for name, kw in events if name == 'gem_collector_ledger_busy'] == ['fence']

def test_an_unread_fence_sends_no_battle_input_that_pass(monkeypatch):
    import gem_collector as worker
    monkeypatch.setattr(worker, 'FENCE_RECHECK_SEC', 0)     # every pass rechecks the person's choice
    attempts = []

    def fence():
        attempts.append(1)
        if len(attempts) == 3:                  # startup and pass 1 read it; pass 2 meets the lock; pass 3 reads it
            raise locked()
    session = types.SimpleNamespace(battle=dict(status='active', id='b1'), effective_preset=lambda: {},
                                    observe=lambda state, raw: True, checkpoint=lambda state, **kw: None)
    events, polled, returned, _ = run_world(monkeypatch, ['battle', 'battle', 'battle'], session=session, fence=fence)
    assert not returned
    assert polled == ['battle', 'battle']       # the pass whose fence was unread polled (and so tapped) nothing
    assert [kw['stage'] for name, kw in events if name == 'gem_collector_ledger_busy'] == ['fence']


def test_a_collector_that_fails_logs_the_stack_that_failed(monkeypatch):
    import gem_collector as worker
    events = []

    def boom(instance, preset):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(worker, 'main', boom)
    monkeypatch.setattr(worker.logger, 'event', lambda name, **kw: events.append((name, kw)))
    with pytest.raises(sqlite3.OperationalError):
        worker.run_logged('main', 'bp_farm')
    [(name, kw)] = events
    assert name == 'gem_collector_failed' and kw['error_type'] == 'OperationalError'
    assert 'boom' in kw['trace'] and 'Traceback' in kw['trace']

def test_a_locked_terminal_record_is_closed_before_a_new_battle_is_observed(monkeypatch):
    """The person leaves the results and starts another battle while the finish is locked out."""
    log, state = [], dict(status='active')
    attempts = []

    def finish(reason, evidence=None, **kw):
        attempts.append(reason)
        if len(attempts) == 1:
            raise locked()
        state['status'] = 'ended'
        log.append(('finish', reason))

    def prepare_next(**kw):
        state['status'] = 'active'
        log.append(('prepare_next',))
    class Session:
        battle = property(lambda self: dict(status=state['status'], id='old'))
        effective_preset = staticmethod(lambda: {})
        checkpoint = staticmethod(lambda s, **kw: None)

        def observe(self, s, raw):
            log.append(('observe', state['status']))
    session = Session()
    session.finish, session.prepare_next = finish, prepare_next
    run(monkeypatch, ['battle', 'game_stats', 'battle', 'battle'], session=session)
    assert log == [('observe', 'active'), ('finish', 'observed_results'), ('prepare_next',), ('observe', 'active'),
                   ('observe', 'active')]
    assert attempts == ['observed_results'] * 2


def test_a_readable_fence_does_not_reset_the_budget_of_a_write_that_keeps_failing(monkeypatch):
    import gem_collector as worker
    now = [0.0]
    monkeypatch.setattr(worker.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(worker.logger, 'event', lambda *a, **kw: None)
    guard = worker.LedgerGuard(limit=100)

    def bad():
        raise locked()
    with pytest.raises(sqlite3.OperationalError):
        for _ in range(40):
            now[0] += 10
            guard.run('fence', lambda: None)
            guard.run('observe', bad)


def test_a_fence_that_cannot_read_the_choice_withholds_input_but_a_revocation_still_stops(monkeypatch):
    import gem_collector as worker
    from runtime import operations
    from towerpilot_workflows import harvest
    ledger = types.SimpleNamespace(path='p', get=lambda op: dict(request=dict(harvest=True)))
    monkeypatch.setattr(operations, 'environment_operation', lambda: (ledger, 'op'))
    answers = iter([(False, 'harvest_unreadable'), (True, None), (False, 'harvest_off')])
    monkeypatch.setattr(harvest, 'automatic_launch_valid', lambda path, op: next(answers))
    monkeypatch.setattr(worker.logger, 'event', lambda *a, **kw: None)
    fence, guard = worker.automatic_fence(), worker.LedgerGuard()
    assert guard.run('fence', fence) == (False, None)          # unknown: no input, no crash
    assert guard.run('fence', fence) == (True, None)
    with pytest.raises(harvest.CollectorRefused):             # revoked: fatal, as before
        guard.run('fence', fence)


def test_an_unreadable_choice_that_never_clears_ends_the_collector_as_a_refusal(monkeypatch):
    import gem_collector as worker
    from towerpilot_workflows import harvest
    now = [0.0]
    monkeypatch.setattr(worker.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(worker.logger, 'event', lambda *a, **kw: None)
    guard = worker.LedgerGuard(limit=100)

    def unreadable():
        raise worker.FenceUnknown('unreadable')
    with pytest.raises(harvest.CollectorRefused):
        for _ in range(40):
            now[0] += 10
            guard.run('fence', unreadable)
