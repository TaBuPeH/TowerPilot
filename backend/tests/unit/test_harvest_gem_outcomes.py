"""What the Dashboard is told about gem outcomes: confirmations seen, a consecutive-failure warning and a cause line.

Real temporary ledgers; the collector's battle publication and its recorded ad windows are written as the collector writes
them. Only the process table and adb are fake boundaries (harvest_support)."""
import sqlite3

import pytest

from control import harvest
from harvest_support import NOW, _battle, _collector, _run, ad_window, choose, finish_last, make_env, routine


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


EVIDENCE = dict(generation='g1', started_at=NOW - 300, coverage='sampled_driver_observations',
                ad_claim_confirmations=6, orbit_balance_confirmations=2, orbit_recorded_increase=4,
                latest_confirmations={'ad_gems': dict(id='claim-6', observed_at=NOW - 40)}, observed_through_at=NOW - 1)


def routines(missed=2):
    return {'ad_gems': routine(True, counters=dict(checks=9, seen=7, skips=0, attempts=8, confirmed=6, windows_missed=missed)),
            'floating_gems': routine(True)}


def collecting(h, **kwargs):
    choose(h)
    op = _collector(h)
    store, battle = _battle(h, op, routines=kwargs.pop('routines', routines()), evidence=kwargs.pop('evidence', EVIDENCE))
    return op, battle


def fail(h, battle, count, first=NOW - 600, reason='offer_moved'):
    for index in range(count):
        ad_window(h, battle, 'failed', reason, at=first + 30 * index)


def test_the_published_counts_are_the_collectors_own_evidence_and_missed_windows(h):
    _, battle = collecting(h)
    shown = h.status()['operation']
    assert shown['evidence']['ad_claim_confirmations'] == 6 and shown['evidence']['orbit_balance_confirmations'] == 2
    assert shown['claims']['windows_missed'] == 2
    assert (shown['claims']['failure_streak'], shown['claims']['failing'], shown['claims']['cause']) == (0, False, None)


def test_a_full_drivers_owner_view_publishes_the_same_counts(h):
    choose(h)
    run = _run(h)
    _battle(h, run, routines=routines(1), evidence=EVIDENCE)
    owner = h.status()['owner']
    assert owner['evidence']['ad_claim_confirmations'] == 6 and owner['evidence']['orbit_balance_confirmations'] == 2
    assert owner['claims']['windows_missed'] == 1


def test_an_owner_without_a_published_battle_has_no_counts(h):
    choose(h)
    _run(h)
    owner = h.status()['owner']
    assert owner['evidence'] is None and owner['claims'] is None


@pytest.mark.parametrize('streak,code', [(0, 'collecting'), (harvest.CLAIM_FAILURES_WARN - 1, 'collecting'),
                                         (harvest.CLAIM_FAILURES_WARN, 'claims_failing'), (5, 'claims_failing')])
def test_collecting_turns_to_a_stated_warning_when_claims_keep_failing(h, streak, code):
    _, battle = collecting(h)
    fail(h, battle, streak)
    shown = h.status()
    assert (shown['state'], shown['code']) == ('collecting', code)
    assert shown['operation']['claims']['failure_streak'] == streak
    if code == 'claims_failing':
        assert shown['message'] == f'Ad gem claims failing: {streak} in a row.'


def test_a_confirmed_claim_ends_the_streak_and_a_window_with_no_offer_does_not(h):
    _, battle = collecting(h)
    fail(h, battle, 4, first=NOW - 900)
    ad_window(h, battle, 'succeeded', 'offer_disappeared', at=NOW - 700)
    ad_window(h, battle, 'failed', 'offer_moved', at=NOW - 600)
    ad_window(h, battle, 'no_op', 'no_offer_seen', at=NOW - 500, attempts=0)
    ad_window(h, battle, 'failed', 'offer_moved', at=NOW - 400)
    shown = h.status()
    assert shown['operation']['claims']['failure_streak'] == 2 and shown['code'] == 'collecting'


def test_the_cause_line_is_the_latest_missed_window(h):
    _, battle = collecting(h)
    ad_window(h, battle, 'failed', 'offer_moved', at=NOW - 300)
    ad_window(h, battle, 'no_op', 'no_offer_seen', at=NOW - 200, attempts=0)
    ad_window(h, battle, 'succeeded', 'offer_disappeared', at=NOW - 100)
    cause = h.status()['operation']['claims']['cause']
    assert cause == dict(code='no_offer_seen', at=NOW - 200)


@pytest.mark.parametrize('reason,code', [('offer_moved', 'offer_moved'), ('fresh_offer_missing', 'offer_gone'),
                                         ('timeout', 'window_ran_out'), ('tap_refused', 'tap_refused'),
                                         ('something_new', 'not_confirmed'), (None, 'not_confirmed')])
def test_every_recorded_reason_maps_to_a_known_cause_code(h, reason, code):
    _, battle = collecting(h)
    ad_window(h, battle, 'failed', reason, at=NOW - 100)
    cause = h.status()['operation']['claims']['cause']
    assert cause['code'] == code and cause['code'] in harvest.CAUSE_CODES


def test_a_window_the_collector_died_in_reads_as_a_stopped_collector():
    cause = harvest.miss_cause(dict(state='interrupted', outcome=dict(reason=None), started_at=NOW - 50, finished_at=None))
    assert (cause['code'], cause['at']) == ('collector_stopped', NOW - 50)


def test_a_collector_that_exited_during_this_battle_is_a_restart_when_it_is_the_latest_event(h):
    choose(h)
    h.tick()
    finish_last(h, 'failed', ran=5.0, now=NOW)
    op = _collector(h)
    _, battle = _battle(h, op, routines=routines(), evidence=EVIDENCE)
    ad_window(h, battle, 'no_op', 'no_offer_seen', at=NOW - 100, attempts=0)
    cause = h.status()['operation']['claims']['cause']
    assert (cause['code'], cause['at']) == ('collector_restarted', NOW)
    ad_window(h, battle, 'failed', 'offer_moved', at=NOW + 3)
    assert h.status(now=NOW + 4)['operation']['claims']['cause']['code'] == 'offer_moved'


def test_an_exit_before_the_battle_began_is_not_a_cause(h):
    choose(h)
    h.tick()
    finish_last(h, 'failed', ran=5.0, now=NOW - 400)
    op = _collector(h)
    _battle(h, op, routines=routines(), evidence=EVIDENCE)
    assert h.status()['operation']['claims']['cause'] is None


def test_unreadable_windows_claim_neither_a_streak_nor_a_cause(h, monkeypatch):
    _, battle = collecting(h)
    fail(h, battle, 5)

    def unreadable(*args, **kwargs):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(harvest.action_history, 'for_battle', unreadable)
    shown = h.status()
    assert (shown['code'], shown['operation']['claims']) == ('collecting', dict(
        windows_missed=2, failure_streak=None, failing=False, cause=None, finder_suspected=None))


def test_a_missing_counter_is_not_a_zero(h):
    _, battle = collecting(h, routines={'ad_gems': routine(True), 'floating_gems': routine(True)})
    assert h.status()['operation']['claims']['windows_missed'] is None


def sensing(near_miss_frames, recognized=0):
    return dict(floating_gems=dict(frames=40, recognized=recognized, near_miss_frames=near_miss_frames,
                                   recent=dict(window=120, frames=40, recognized=recognized, near_miss_frames=near_miss_frames)))


@pytest.mark.parametrize('evidence,orbit_on,expected', [
    (dict(EVIDENCE, sensing=sensing(harvest.FINDER_NEAR_MISS_FRAMES)), True, True),
    (dict(EVIDENCE, sensing=sensing(harvest.FINDER_NEAR_MISS_FRAMES - 1)), True, False),
    (dict(EVIDENCE, sensing=sensing(harvest.FINDER_NEAR_MISS_FRAMES, recognized=1)), True, False),
    (dict(EVIDENCE, sensing=sensing(harvest.FINDER_NEAR_MISS_FRAMES)), False, False),
    (EVIDENCE, True, None)])
def test_a_suspected_finder_reaches_the_status_beside_the_evidence_it_comes_from(h, evidence, orbit_on, expected):
    choose(h)
    op = _collector(h)
    _battle(h, op, routines={'ad_gems': routine(True), 'floating_gems': routine(orbit_on)}, evidence=evidence)
    shown = h.status()['operation']
    assert shown['claims']['finder_suspected'] is expected
    assert shown['evidence'].get('sensing') == evidence.get('sensing')
    assert h.status()['code'] == 'collecting' or not orbit_on             # the health line is untouched: only the field is new


def test_a_window_the_loop_never_opened_is_the_latest_cause_but_not_a_failed_claim(h):
    _, battle = collecting(h)
    fail(h, battle, 2, first=NOW - 900)
    ad_window(h, battle, 'no_op', 'window_missed', at=NOW - 100, attempts=0)
    claims = h.status()['operation']['claims']
    assert (claims['failure_streak'], claims['cause']) == (2, dict(code='window_ran_out', at=NOW - 100))
