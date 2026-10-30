"""A ledger lock in the middle of a two-write session call is retried safely (FOCUS #11 follow-ups).

prepare_next creates the record, then writes its setup report; a retry after a lock at the second write must continue it.
(finish has one write that can raise: ActionSession.settle is contained and never raises.) A prepare locked out after a
wave went backwards must keep its link to the record it continues, until that record is closed.
The collector skips a locked call and retries it."""
import sqlite3
import types

import pytest

from gem_collector_run import run as run_world
from test_battle_session import driver      # noqa: F401  (a real session over a temporary ledger)


def locked():
    return sqlite3.OperationalError('database is locked')


def test_prepare_locked_at_its_setup_write_is_continued_not_duplicated(driver, monkeypatch):
    session, rs, operation = driver
    session.finish('observed_results')
    real, calls = session.store.update_status, []

    def update_status(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise locked()
        return real(*args, **kwargs)
    monkeypatch.setattr(session.store, 'update_status', update_status)
    with pytest.raises(sqlite3.OperationalError):
        session.prepare_next(origin='partial_adoption', previous_battle_id=session.battle['id'])
    created = session.battle['id']
    session.prepare_next(origin='started')                  # the retry: the same record gets the setup report owed to it
    assert session.battle['id'] == created
    assert session.store.get(created)['runtime']['setup']['detail'] == 'adopted'     # the first call's, not the retry's
    with pytest.raises(ValueError, match='live battle'):    # nothing is owed now: a live record is not replaced
        session.prepare_next()
    assert len(session.store.list(device='device')) == 2    # the first record and this one


class Session:
    """A fake session. `locks` says for each prepare_next call whether it meets the lock: at `create` nothing was written,
    at `setup` the record exists and only its setup report is owed. Like the real one, it refuses to replace a live record."""

    def __init__(self, locks=(), *, at='setup', state='active'):
        self.state, self.locks, self.at, self.owed = state, list(locks), at, False
        self.finishes, self.prepares = [], []
        self.effective_preset = lambda: {}
        self.observe = lambda s, raw: True
        self.checkpoint = lambda s, **kw: None

    battle = property(lambda self: dict(status=self.state, id='old' if not self.prepares else 'new'))

    def finish(self, reason, **kw):
        self.finishes.append(reason)
        self.state, self.owed = 'ended', False

    def prepare_next(self, **kw):
        self.prepares.append(kw)
        lock = self.locks[len(self.prepares)-1] if len(self.prepares) <= len(self.locks) else False
        if self.state == 'active' and not self.owed:
            raise ValueError('Cannot replace a live battle envelope')
        if lock and self.at == 'create':
            raise locked()
        self.state, self.owed = 'active', lock
        if lock:
            raise locked()


BACKWARDS = ['up', 'down', 'down', 'down', 'down', 'down']
WAVES = {'up': 500, 'down': 300, 'low': 100, 'home': None}


def world(monkeypatch, session, frames=BACKWARDS):
    return run_world(monkeypatch, frames, session=session, read_wave=lambda f: WAVES[f],
                     screen=lambda f: types.SimpleNamespace(name='home' if f == 'home' else 'battle'))


PARTIAL = dict(origin='partial_adoption', previous_battle_id='old')


def test_a_prepare_locked_after_a_backwards_wave_keeps_the_link_to_the_previous_record(monkeypatch):
    session = Session([True])
    world(monkeypatch, session)
    assert session.prepares == [PARTIAL] * 2


def test_a_prepare_locked_before_its_record_exists_keeps_the_link_too(monkeypatch):
    session = Session([True, True], at='create')
    world(monkeypatch, session)
    assert session.prepares == [PARTIAL] * 3


def test_an_adopted_prepare_locked_at_its_setup_write_is_finished_on_the_next_pass(monkeypatch):
    session = Session([True], state='ended')
    world(monkeypatch, session, ['up', 'up', 'up'])
    assert session.prepares == [dict(origin='adopted')] * 2     # the second call is the continuation, not a new record


def test_a_link_waiting_for_its_record_is_dropped_when_that_record_closes(monkeypatch):
    """The prepare after a backwards wave created its record but could not write its setup; the person then left to Home."""
    session = Session([True] * 99)
    world(monkeypatch, session, ['up', 'down', 'down', 'down', 'home', 'down', 'down'])
    assert session.prepares[0] == PARTIAL
    assert session.finishes[-1] == 'battle_no_longer_observed'
    assert session.prepares[-1] == dict(origin='adopted')       # an unrelated battle is not linked to the old record


def test_a_link_for_a_record_never_created_is_dropped_at_home_too(monkeypatch):
    session = Session([True] * 99, at='create')
    world(monkeypatch, session, ['up', 'down', 'down', 'down', 'home', 'home', 'low', 'low'])
    assert session.prepares[0] == PARTIAL
    assert session.prepares[-1] == dict(origin='adopted')


def test_a_second_backwards_wave_that_prepares_cleanly_leaves_no_stale_link(monkeypatch):
    """Locked twice at the setup write, then a second backwards wave prepares fine: the old link must not be replayed."""
    session = Session([True, True])
    world(monkeypatch, session, ['up'] + ['down'] * 4 + ['low'] * 5)        # must not die on 'Cannot replace a live battle'
    assert session.prepares[-1] == dict(origin='partial_adoption', previous_battle_id='new')   # the second wave's own link


def test_a_setup_write_that_stays_locked_does_not_stop_collecting(monkeypatch):
    """The record exists; only its setup report is owed. Gem polls go on under it while the setup write is retried."""
    session = Session([True] * 5, state='ended')
    _, polled, _, _ = world(monkeypatch, session, ['up'] * 8)
    assert polled == ['up'] * 8                                 # no pass skipped while the setup write was locked
    assert session.prepares == [dict(origin='adopted')] * 6     # retried each pass until it landed, then left alone
