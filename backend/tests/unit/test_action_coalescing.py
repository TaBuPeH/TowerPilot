"""Coalescing and volume (spec 3.6), through the ActionSession the drivers use and the ledger under it.

`test_action_ledger.py` pins the ledger's own rules (a changed scope field, a manual or key-linked check, 10,000 identical checks at the store). This file adds what a driver
and a reader depend on: the aggregate seen through a session, the execution id a caller keeps, retention that does not drop what is still counting, and pages that survive pruning.
Temporary ledgers, a fake clock, no device and no live port.
"""
import json
import sqlite3

import pytest

import action_history_cases as cases
from towerpilot_workflows import actions
from towerpilot_workflows.action_contract import FakeClock
from towerpilot_workflows.actions import Actions
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

DAY = 86400.0


def session(tmp_path, *, device='device', account='account', clock=None, name='ledger.db', driver='orchestrator', **kwargs):
    from runtime.action_session import ActionSession
    return ActionSession(str(tmp_path / name), context=dict(device_key=device, account=account, instance='main'), driver=driver, clock=clock or cases.SteppedClock(), **kwargs)


def read(tmp_path, name='ledger.db', **filters):
    return Actions.read(str(tmp_path / name), **filters)[0]


# ------------------------------------------------------------------ one row, one id
def test_identical_automatic_checks_are_one_row_whose_id_is_the_one_the_first_check_returned(tmp_path):
    s = session(tmp_path)
    first = s.check('shopping', result='no_op', reason='nothing_due')
    for _ in range(49):
        s.clock.advance(10.0)
        again = s.check('shopping', result='no_op', reason='nothing_due')
    (row,) = read(tmp_path)
    assert row['id'] == first['id'] == again['id'] and row['aggregate']['count'] == 50
    assert row['aggregate']['first_at'] == first['started_at'] and row['aggregate']['last_at'] == first['started_at'] + 490.0
    assert row['duration_ms'] is None and row['duration_basis'] == 'none' and row['finished_at'] == first['started_at']   # no per-check duration is invented
    assert s.last['shopping']['execution_id'] == row['id'] and s.last['shopping']['duration_ms'] is None


def test_an_id_a_log_line_or_receipt_named_stays_resolvable_after_later_identical_checks(tmp_path):
    s = session(tmp_path)
    named = s.check('shopping', result='refused', reason='device_owned')['id']
    refs = {named}
    for _ in range(20):
        s.check('shopping', result='refused', reason='device_owned')
    manual = s.check('shopping', result='refused', reason='device_owned', trigger='manual')
    refs.add(manual['id'])
    s.check('shopping', result='refused', reason='device_owned')                                    # an automatic check after a manual one starts a new aggregate
    ids = {row['id'] for row in read(tmp_path)}
    assert refs <= ids and len(ids) == 3


@pytest.mark.parametrize('change', [dict(result='no_op', reason='device_owned'), dict(result='refused', reason='busy')], ids=['result', 'reason'])
def test_a_different_result_or_reason_is_its_own_row_and_the_old_aggregate_stops_counting(tmp_path, change):
    s = session(tmp_path)
    for _ in range(3):
        s.check('shopping', result='refused', reason='device_owned')
        s.clock.advance(1.0)
    s.check('shopping', **change)
    s.clock.advance(1.0)
    s.check('shopping', result='refused', reason='device_owned')
    rows = sorted(read(tmp_path), key=lambda row: row['started_at'])
    assert [row['aggregate']['count'] for row in rows] == [3, 1, 1]


def test_each_revision_and_each_context_field_is_its_own_scope_through_the_session(tmp_path):
    base = dict(result='refused', reason='device_owned')
    session(tmp_path).check('shopping', **base)
    variants = [dict(config_revision=2), dict(policy_revision=2)]
    for index, extra in enumerate(variants, start=1):
        session(tmp_path).check('shopping', **base, **extra)
        session(tmp_path).check('shopping', **base, **extra)                                         # the repeat joins the row its twin made
    assert sorted(row['aggregate']['count'] for row in read(tmp_path, instance='shopping')) == [1, 2, 2]
    session(tmp_path, account='other').check('shopping', **base)
    session(tmp_path, device='other-device').check('shopping', **base)
    assert len(read(tmp_path)) == 5 and {(row['device'], row['context']['account']) for row in read(tmp_path)} == {('device', 'account'), ('device', 'other'), ('other-device', 'account')}


def test_a_new_battle_and_a_new_generation_start_new_rows_and_a_session_that_follows_its_driver_keeps_them_apart(tmp_path):
    first = cases.RealHost(tmp_path, key='op-1')
    first.session.check('shopping', result='refused', reason='device_owned')
    first.session.check('shopping', result='refused', reason='device_owned')
    first.battle_session.finish('done')
    first.end_operation()
    second = cases.RealHost(tmp_path, key='op-2', clock=cases.SteppedClock(wall=1_700_001_000.0))
    second.session.check('shopping', result='refused', reason='device_owned')
    rows = sorted(Actions.read(str(first.path), device='device')[0], key=lambda row: row['started_at'])
    assert [row['aggregate']['count'] for row in rows] == [2, 1]
    assert len({row['host']['battle_id'] for row in rows}) == 2 and len({row['host']['generation'] for row in rows}) == 2


def test_a_started_manual_or_key_linked_execution_is_never_merged_and_never_the_target_of_a_merge(tmp_path):
    s = session(tmp_path)
    s.check('shopping', result='refused', reason='device_owned')
    keyed = s.record('shopping', result='refused', reason='device_owned', request_key='k-1')          # a key-linked, already-over execution
    s.check('shopping', result='refused', reason='device_owned')                                    # newest row is not an aggregate of this scope: a new row
    started = s.begin('shopping')
    s.finish(started, result='refused', reason='device_owned')
    s.check('shopping', result='refused', reason='device_owned')
    by_id = {row['id']: row for row in read(tmp_path)}
    assert keyed['id'] in by_id and by_id[keyed['id']]['aggregate'] is None and by_id[started]['aggregate'] is None
    assert sorted(row['aggregate']['count'] for row in by_id.values() if row['aggregate']) == [1, 1, 1]


def test_the_store_never_makes_a_manual_or_key_linked_check_an_aggregate_even_when_asked_to(tmp_path):
    """`record_check` defaults to `aggregate=True`; only an automatic check without a request key may share a row. Asked directly (not through a session, which never
    asks for it for these), a manual or key-linked check is its own row with no aggregate, and the automatic aggregate beside it keeps its count."""
    store = Actions(str(tmp_path / 'ledger.db'))

    def body(at, **fields):
        return actions.new_record(device='device', action='shopping', instance='main', trigger=fields.pop('trigger', 'automatic'), started_at=at,
                                  host=dict(driver='orchestrator'), context=dict(account='account'), **fields)

    store.record_check(body(1.0), result='refused', reason='device_owned')
    store.record_check(body(2.0), result='refused', reason='device_owned')
    manual = store.record_check(body(3.0, trigger='manual'), result='refused', reason='device_owned')
    keyed = store.record_check(body(4.0, request_key='k-1'), result='refused', reason='device_owned')
    assert manual['aggregate'] is None and keyed['aggregate'] is None
    rows = read(tmp_path)
    assert len(rows) == 3 and sorted(row['aggregate']['count'] for row in rows if row['aggregate']) == [2]


# ------------------------------------------------------------------ volume
def test_a_hundred_identical_checks_made_through_a_session_are_one_row_and_the_count_moves_past_ten_thousand_without_a_cap(tmp_path):
    """A real transaction costs tens of milliseconds, so the ledger test moves the stored count to 9,999 and makes the 10,000th check for real
    (`test_action_ledger.py`); this makes the checks through the driver's session, then repeats that step on the session's own row."""
    s = session(tmp_path, driver='dashboard')
    for _ in range(100):
        s.clock.advance(5.0)
        last = s.check('harvest_launch', result='refused', reason='device_busy')
    path = tmp_path / 'ledger.db'
    with sqlite3.connect(path) as db:
        (raw,) = db.execute('SELECT body FROM action_executions').fetchone()
        body = json.loads(raw)
        body['aggregate']['count'] = 9_999
        db.execute('UPDATE action_executions SET body=?', (json.dumps(body),))
    s.clock.advance(5.0)
    final = s.check('harvest_launch', result='refused', reason='device_busy')
    (row,) = Actions.read(str(path))[0]
    assert row['id'] == last['id'] == final['id'] and row['aggregate']['count'] == 10_000
    assert row['aggregate']['first_at'] < row['aggregate']['last_at'] and row['duration_ms'] is None and row['duration_basis'] == 'none' and row['outcome']['attempts'] == 0


# ------------------------------------------------------------------ retention: prune keeps what still counts
def stamp(tmp_path, *, at, instance, action='shopping', **fields):
    """A terminal row at `at`, written through the store (never by hand)."""
    body = actions.new_record(device='device', action=action, instance=instance, trigger='automatic', started_at=at, host=dict(driver='orchestrator'), context=dict(account='account'), **fields)
    return Actions(str(tmp_path / 'ledger.db')).record_terminal(body, result='refused', reason='old', finished_at=at)


def test_prune_removes_old_terminal_rows_in_bounded_batches_and_keeps_open_and_recent_ones(tmp_path):
    now = 1_700_000_000.0 + 100 * DAY
    store = Actions(str(tmp_path / 'ledger.db'))
    old = [stamp(tmp_path, at=now - 60 * DAY - index, instance=f'old{index}')['id'] for index in range(7)]
    recent = stamp(tmp_path, at=now - 5 * DAY, instance='recent')['id']
    opened = store.begin(actions.new_record(device='device', action='shopping', instance='open', trigger='automatic', started_at=now - 90 * DAY, host=dict(driver='orchestrator'), context=dict(account='account')))['id']
    assert store.prune(now, batch=3) == 3 and store.prune(now, batch=3) == 3 and store.prune(now, batch=3) == 1 and store.prune(now, batch=3) == 0
    assert {row['id'] for row in read(tmp_path)} == {recent, opened} and not set(old) & {row['id'] for row in read(tmp_path)}


def test_a_prune_batch_of_none_deletes_none_and_a_negative_batch_is_no_limit(tmp_path):
    now = 1_700_000_000.0 + 100 * DAY
    store = Actions(str(tmp_path / 'ledger.db'))
    for index in range(3):
        stamp(tmp_path, at=now - 60 * DAY - index, instance=f'old{index}')
    assert store.prune(now, batch=0) == 0 and len(read(tmp_path)) == 3
    assert store.prune(now, batch=-1) == 3 and read(tmp_path) == []


def test_a_receipt_referenced_row_is_kept_and_its_neighbours_are_not(tmp_path):
    now = 1_700_000_000.0 + 100 * DAY
    kept = stamp(tmp_path, at=now - 60 * DAY, instance='kept', request_key='receipt-1')['id']
    dropped = stamp(tmp_path, at=now - 60 * DAY, instance='dropped', request_key='no-receipt')['id']
    with sqlite3.connect(tmp_path / 'ledger.db') as db:
        db.execute('CREATE TABLE battle_commands (id TEXT PRIMARY KEY, battle_id TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL, body TEXT NOT NULL)')
        db.execute("INSERT INTO battle_commands VALUES ('c1', 'b1', 'receipt-1', 'f', '{}')")
    assert Actions(str(tmp_path / 'ledger.db')).prune(now) == 1
    assert {row['id'] for row in read(tmp_path)} == {kept} and dropped not in {row['id'] for row in read(tmp_path)}


def test_an_aggregate_that_is_still_counting_is_kept_whatever_its_first_check_s_age_and_loses_no_count(tmp_path):
    """The aggregate row keeps the first check's start time (its index), but its LAST check is what keeps it alive: pruning it would drop a count that is still growing."""
    now = 1_700_000_000.0 + 100 * DAY
    s = session(tmp_path, clock=cases.SteppedClock(wall=now - 60 * DAY))
    first = s.check('shopping', result='refused', reason='device_owned')['id']
    for _ in range(99):
        s.clock.advance(DAY / 20)                                                                    # the checks go on for five days, ending 55 days before `now`
    s.clock.advance(DAY * 55)
    s.check('shopping', result='refused', reason='device_owned')
    old = stamp(tmp_path, at=now - 70 * DAY, instance='aged')['id']
    assert Actions(str(tmp_path / 'ledger.db')).prune(now, retain_sec=30 * DAY) == 1                  # the aged row only
    (row,) = [row for row in read(tmp_path) if row['id'] == first]
    assert row['aggregate']['count'] == 2 and row['aggregate']['last_at'] >= now - 30 * DAY and old not in {r['id'] for r in read(tmp_path)}


def test_an_aggregate_whose_last_check_is_also_old_is_removed_whole(tmp_path):
    now = 1_700_000_000.0 + 100 * DAY
    s = session(tmp_path, clock=cases.SteppedClock(wall=now - 80 * DAY))
    for _ in range(5):
        s.check('shopping', result='refused', reason='device_owned')
        s.clock.advance(60.0)
    assert Actions(str(tmp_path / 'ledger.db')).prune(now) == 1 and read(tmp_path) == []


def test_pages_taken_across_a_prune_visit_every_surviving_row_once_and_counts_are_preserved(tmp_path):
    now = 1_700_000_000.0 + 100 * DAY
    store = Actions(str(tmp_path / 'ledger.db'))
    survivors = [stamp(tmp_path, at=now - (index + 1) * DAY, instance=f'live{index}')['id'] for index in range(12)]      # 1 .. 12 days old
    doomed = [stamp(tmp_path, at=now - 40 * DAY - index, instance=f'dead{index}')['id'] for index in range(10)]
    seen, cursor = [], None
    for page_number in range(20):
        rows, truncated, damaged, cursor = Actions.read_page(str(tmp_path / 'ledger.db'), limit=4, before=cursor)
        seen += [row['id'] for row in rows]
        if page_number == 1:
            assert store.prune(now) == 10                                                              # rows older than the cursor vanish between two pages
        if not truncated:
            break
    assert seen[:12] == survivors and set(seen) - set(survivors) <= set(doomed) and len(seen) == len(set(seen))
    assert [row['id'] for row in read(tmp_path)] == survivors
    assert Actions.read_page(str(tmp_path / 'ledger.db'), limit=500)[2] == 0
