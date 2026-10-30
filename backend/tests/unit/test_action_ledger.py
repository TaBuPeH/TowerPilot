"""The durable execution history (roadmap item 6, A2-1): begin/finish, fenced settlement, narrow coalescing,
read-only reads and unactivated retention, all on a temporary ledger."""
import json
import sqlite3

import pytest

from towerpilot_workflows.actions import ActiveExecution, Actions, SchemaTooNew, UnusableRow, index_of, indexed, new_record, usable
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake')


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / 'operations.db'


def row(action='ad_gems', instance=None, *, operation_id=None, battle_id=None, generation=None, trigger='automatic',
        started_at=1000.0, device='device', account='account', **kwargs):
    return new_record(device=device, action=action, instance=instance or action, trigger=trigger,
                      host=dict(driver='orchestrator', operation_id=operation_id, battle_id=battle_id, generation=generation),
                      context=dict(account=account), started_at=started_at, **kwargs)


def tables(path):
    db = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        db.close()


# --------------------------------------------------------------- lifecycle
def test_begin_and_finish_round_trip_with_the_measured_duration(ledger):
    store = Actions(ledger)
    opened = store.begin(row(refs=dict(window_id='w1')))
    assert opened['state'] == 'running' and opened['finished_at'] is None and opened['duration_ms'] is None
    done = store.finish(opened['id'], result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1,
                        confirmed=1, evidence=['offer_disappeared'], finished_at=1010.0, duration_ms=9500.0, refs=dict(attempt_id='a1'))
    assert (done['state'], done['duration_ms'], done['duration_basis']) == ('succeeded', 9500.0, 'monotonic')
    assert done['refs'] == dict(window_id='w1', attempt_id='a1') and done['outcome']['effect'] == 'confirmed'
    rows, truncated = Actions.read(str(ledger), device='device')
    assert [r['id'] for r in rows] == [opened['id']] and truncated is False


def test_finish_is_a_compare_and_set_so_a_repeat_or_stale_writer_changes_nothing(ledger):
    store = Actions(ledger)
    opened = store.begin(row())
    first = store.finish(opened['id'], result='failed', reason='first', effect='unconfirmed', finished_at=1001.0, duration_ms=1000.0)
    again = store.finish(opened['id'], result='succeeded', reason='second', effect='confirmed', finished_at=2000.0, duration_ms=9.0)
    assert again == first and again['outcome']['reason'] == 'first'
    with pytest.raises(KeyError):
        store.finish('missing', result='failed')
    with pytest.raises(ValueError):
        store.finish(opened['id'], result='succeeded', effect='proved')


def test_unknown_stays_none_and_a_non_finite_time_never_becomes_zero(ledger):
    store = Actions(ledger)
    opened = store.begin(row(scheduled_for=float('nan')))
    assert opened['scheduled_for'] is None
    done = store.finish(opened['id'], result='no_op', duration_ms=float('inf'), finished_at=1001.0)
    assert done['duration_ms'] is None and done['duration_basis'] == 'none'
    json.loads(sqlite3.connect(ledger).execute('SELECT body FROM action_executions').fetchone()[0])   # strict JSON


def test_one_open_execution_per_configured_instance_and_two_instances_are_independent(ledger):
    store = Actions(ledger)
    store.begin(row('ad_gems'))
    with pytest.raises(ActiveExecution):
        store.begin(row('ad_gems', started_at=1001.0))
    store.begin(row('floating_gems'))                                    # another instance of the device
    store.begin(row('ad_gems', device='other-device'))                   # the same instance on another device


def test_a_repeated_request_key_returns_the_one_row_from_any_host(ledger):
    first = Actions(ledger).begin(row(request_key='launch-1'))
    second = Actions(ledger).begin(row(request_key='launch-1', started_at=2000.0))      # a second host, same durable key
    assert second['id'] == first['id'] and second['replayed'] is True
    assert sqlite3.connect(ledger).execute('SELECT COUNT(*) FROM action_executions').fetchone()[0] == 1


def test_the_ledger_names_itself_once_and_every_row_carries_it(ledger):
    first = Actions(ledger).begin(row())
    assert Actions(ledger).begin(row('floating_gems'))['ledger_id'] == first['ledger_id'] == Actions.read_meta(str(ledger))['ledger_id']


# -------------------------------------------------------------- settlement
def operation_and_battle(ledger, *, end='none'):
    operations, battles = Operations(ledger), Battles(ledger)
    operation, _ = operations.accept('farm', CONTEXT, {}, 'op-key')
    operation = operations.transition(operation['id'], operation['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    battle = battles.create(operation['id'], CONTEXT, {'preset': {}, 'config': {}, 'controls': {'routines': {}}}, generation='g1')
    return operations, battles, operation, battle


def test_an_open_row_is_settled_only_when_its_operation_or_battle_is_provably_gone(ledger):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    opened = store.begin(row(operation_id=operation['id'], battle_id=battle['id'], generation='g1'))
    assert store.settle_open(now=1100.0) == []                            # alive: nothing is guessed
    operations.transition(operation['id'], operations.get(operation['id'])['revision'], 'succeeded')
    assert store.settle_open(now=1200.0) == [opened['id']]
    settled = Actions.read(str(ledger), device='device')[0][0]
    assert settled['state'] == 'interrupted' and settled['finished_at'] is None and settled['duration_ms'] is None
    assert settled['outcome'] == dict(result='interrupted', reason='operation_ended', effect='unknown', attempts=0, confirmed=0, evidence=[])
    assert store.settle_open(now=1300.0) == []                            # idempotent


def test_a_battle_that_ended_or_moved_to_another_generation_settles_its_rows_and_only_its_own(ledger):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    mine = store.begin(row('ad_gems', operation_id=operation['id'], battle_id=battle['id'], generation='g1'))
    foreign = store.begin(row('floating_gems', operation_id=operation['id'], battle_id=battle['id'], generation='g-other'))
    assert store.settle_open(now=1100.0) == [foreign['id']]               # another generation's row: the battle replaced it
    battles.finish(battle['id'], 'g1', reason='observed_results', outcome='unknown', status='ended')
    assert store.settle_open(now=1200.0) == [mine['id']]


def test_begin_settles_a_dead_hosts_row_as_a_courtesy_but_refuses_a_live_ones(ledger):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    store.begin(row(operation_id=operation['id'], battle_id=battle['id'], generation='g1'))
    with pytest.raises(ActiveExecution):
        store.begin(row(operation_id=operation['id'], battle_id=battle['id'], generation='g1', started_at=1100.0))
    battles.finish(battle['id'], 'g1', reason='x', outcome='unknown', status='ended')
    fresh = store.begin(row(operation_id=operation['id'], battle_id='next', generation='g2', started_at=1200.0))
    assert fresh['state'] == 'running'
    rows = Actions.read(str(ledger), device='device')[0]
    assert sorted(r['state'] for r in rows) == ['interrupted', 'running']


# ------------------------------------------------------------- coalescing
def check(store, *, trigger='automatic', result='refused', reason='device_owned', **kwargs):
    return store.record_check(row('harvest_launch', trigger=trigger, started_at=kwargs.pop('at', 1000.0), **kwargs),
                              result=result, reason=reason)


def test_identical_automatic_refusals_are_one_counted_row_whose_count_carries_past_ten_thousand_with_no_invented_duration(ledger):
    """A real transaction costs tens of milliseconds, so 200 identical checks are made for real; the stored count is then moved to 9,999
    and the 10,000th check is made for real too. The logic is constant per check: what this proves is the one row, the count and the
    absence of an invented per-check duration, with no cap or overflow at the volume the specification names."""
    store = Actions(ledger)
    first = check(store, at=0.0)
    for index in range(1, 200):
        last = check(store, at=float(index))
    assert last['id'] == first['id'] and last['aggregate']['count'] == 200
    db = sqlite3.connect(ledger)
    body = json.loads(db.execute('SELECT body FROM action_executions').fetchone()[0])
    body['aggregate']['count'] = 9_999
    db.execute('UPDATE action_executions SET body=?', (json.dumps(body),))
    db.commit(); db.close()
    last = check(store, at=9999.0)
    assert last['id'] == first['id'] and last['aggregate']['count'] == 10_000
    assert (last['aggregate']['first_at'], last['aggregate']['last_at']) == (0.0, 9999.0)
    assert last['duration_ms'] is None and last['outcome']['attempts'] == 0
    assert sqlite3.connect(ledger).execute('SELECT COUNT(*) FROM action_executions').fetchone()[0] == 1


@pytest.mark.parametrize('change', [
    dict(reason='emulator_offline'), dict(result='no_op'), dict(policy_revision='p2'), dict(config_revision=2),
    dict(account='other'), dict(battle_id='b2'), dict(generation='g2'), dict(device='d2')])
def test_a_change_of_any_scope_field_starts_a_new_row(ledger, change):
    store = Actions(ledger)
    base = dict(reason='device_owned', result='refused')
    check(store, **base)
    check(store, **{**base, **change})
    assert sqlite3.connect(ledger).execute('SELECT COUNT(*) FROM action_executions').fetchone()[0] == 2


def test_a_manual_or_key_linked_check_is_never_merged_and_an_intervening_row_ends_the_aggregate(ledger):
    store = Actions(ledger)
    check(store)
    check(store, trigger='manual')
    check(store, request_key='k1')
    check(store, request_key='k2')
    assert sqlite3.connect(ledger).execute('SELECT COUNT(*) FROM action_executions').fetchone()[0] == 4
    other = Actions(ledger.with_name('b.db'))
    check(other, at=1.0)
    other.finish(other.begin(row('harvest_launch', started_at=2.0))['id'], result='succeeded', finished_at=3.0)
    check(other, at=4.0)                                                  # a different row came between: a new aggregate
    assert sqlite3.connect(ledger.with_name('b.db')).execute('SELECT COUNT(*) FROM action_executions').fetchone()[0] == 3


def test_only_a_refused_or_no_op_check_skips_the_lifecycle(ledger):
    with pytest.raises(ValueError):
        Actions(ledger).record_check(row(), result='succeeded', reason='x')


# ---------------------------------------------------------------- reads
def test_reads_never_create_the_ledger_a_table_or_a_row(tmp_path):
    missing = tmp_path / 'absent' / 'operations.db'
    assert Actions.read(str(missing)) == ([], False) and Actions.read_meta(str(missing)) is None
    assert not missing.exists() and not missing.parent.exists()
    bare = tmp_path / 'bare.db'
    Operations(bare)
    before = tables(bare)
    assert Actions.read(str(bare)) == ([], False) and Actions.read_meta(str(bare)) is None
    assert tables(bare) == before and 'action_executions' not in before


def test_reads_filter_page_and_report_truncation(ledger):
    store = Actions(ledger)
    for index in range(5):
        store.finish(store.begin(row(started_at=1000.0 + index))['id'], result='no_op', finished_at=1001.0 + index)
    rows, truncated = Actions.read(str(ledger), action='ad_gems', limit=3)
    assert [r['started_at'] for r in rows] == [1004.0, 1003.0, 1002.0] and truncated is True
    older, truncated = Actions.read(str(ledger), action='ad_gems', before=1002.0, limit=3)
    assert [r['started_at'] for r in older] == [1001.0, 1000.0] and truncated is False
    assert Actions.read(str(ledger), action='floating_gems') == ([], False)


# ---------------------------------------------------- a newer schema and a damaged row
def test_a_ledger_written_by_a_newer_build_is_readable_but_never_written(ledger):
    store = Actions(ledger)
    first = store.begin(row())
    db = sqlite3.connect(ledger)
    db.execute('UPDATE action_meta SET schema_version=99')
    db.commit(); db.close()
    assert Actions.read(str(ledger), device='device')[0][0]['id'] == first['id']          # still readable
    for write in (lambda: store.begin(row('floating_gems')), lambda: store.record_check(row('harvest_launch'), result='refused', reason='x'),
                  lambda: store.record_terminal(row('shopping'), result='no_op')):
        with pytest.raises(SchemaTooNew):
            write()
    assert len(Actions.read(str(ledger), device='device')[0]) == 1


def test_a_damaged_row_is_skipped_by_a_read_and_the_rest_are_returned(ledger):
    store = Actions(ledger)
    good = store.begin(row('ad_gems', started_at=1000.0))
    db = sqlite3.connect(ledger)
    db.execute("INSERT INTO action_executions(id, device, action, instance, started_at, terminal, body) VALUES ('bad', 'device', 'ad_gems', 'x', 2000.0, 1, '{not json')")
    db.execute("INSERT INTO action_executions(id, device, action, instance, started_at, terminal, body) VALUES ('odd', 'device', 'ad_gems', 'y', 3000.0, 1, '[1, 2]')")
    db.commit(); db.close()
    rows_, truncated = Actions.read(str(ledger), device='device')
    assert [item['id'] for item in rows_] == [good['id']] and truncated is False


# -------------------------------------------------------------- retention
def test_prune_keeps_open_recent_and_receipt_referenced_rows_and_is_bounded(ledger):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    old = [store.finish(store.begin(row(started_at=float(i)))['id'], result='no_op', finished_at=i + 1.0)['id'] for i in range(6)]
    keep = store.begin(row('floating_gems', started_at=1.0, request_key='receipt-1'))
    store.finish(keep['id'], result='no_op', finished_at=2.0)
    store.begin(row('shopping', started_at=1.0))                          # open: never pruned however old
    recent = store.finish(store.begin(row('harvest_launch', started_at=9_000_000.0))['id'], result='no_op', finished_at=9_000_001.0)
    db = sqlite3.connect(ledger)
    db.execute("INSERT INTO battle_commands(id, battle_id, request_key, fingerprint, body) VALUES ('c', ?, 'receipt-1', 'f', '{}')", (battle['id'],))
    db.commit(); db.close()
    assert store.prune(now=9_000_100.0, batch=4) == 4                    # bounded per call
    assert store.prune(now=9_000_100.0, batch=4) == 2
    assert store.prune(now=9_000_100.0, batch=4) == 0
    left = {r['id'] for r in Actions.read(str(ledger))[0]}
    assert keep['id'] in left and recent['id'] in left and not left & set(old)
    assert {r['action'] for r in Actions.read(str(ledger))[0] if r['state'] == 'running'} == {'shopping'}


# ---------------------------- a damaged row never raises into a writer, a settlement or a read (review R11-01)
def corrupt(ledger, execution_id, kind):
    """Replace the stored body of one row by something a disk or a person could have left there."""
    db = sqlite3.connect(ledger)
    stored = json.loads(db.execute('SELECT body FROM action_executions WHERE id=?', (execution_id,)).fetchone()[0])
    bodies = {
        'not_json': '{', 'empty_object': '{}', 'host_null': '{"host":null}', 'a_list': '[1, 2]', 'json_null': 'null',
        'host_a_list': json.dumps(dict(stored, host=[1])), 'outcome_missing': json.dumps({k: v for k, v in stored.items() if k != 'outcome'}),
        'started_at_text': json.dumps(dict(stored, started_at='soon')), 'state_missing': json.dumps({k: v for k, v in stored.items() if k != 'state'}),
        'aggregate_count_text': json.dumps(dict(stored, aggregate=dict(scope='s', count='many'))),
        'another_id': json.dumps(dict(stored, id='somebody-else')),
    }
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (bodies[kind], execution_id))
    db.commit()
    db.close()
    return bodies[kind]


DAMAGE = ['not_json', 'empty_object', 'host_null', 'a_list', 'json_null', 'host_a_list', 'outcome_missing', 'started_at_text',
          'state_missing', 'aggregate_count_text', 'another_id']


def stored_row(ledger, execution_id):
    db = sqlite3.connect(ledger)
    try:
        return db.execute('SELECT terminal, body FROM action_executions WHERE id=?', (execution_id,)).fetchone()
    finally:
        db.close()


def test_usable_accepts_what_the_writers_make_and_nothing_else():
    good = row(operation_id='op', battle_id='b', generation='g')
    assert usable(json.dumps(good)) == good
    assert usable(json.dumps(good), dict(indexed(good), id='wrong-id')) is None and usable(json.dumps(good), indexed(good)) == good
    assert usable(json.dumps(dict(good, aggregate=dict(scope='s', count=3, first_at=1.0, last_at=2.0)))) is not None
    assert usable(json.dumps(dict(good, aggregate=dict(scope='s', count=True, first_at=1.0, last_at=2.0)))) is None
    assert usable(json.dumps(dict(good, aggregate=dict(scope='s', count=3)))) is None            # an aggregate always has both ends
    assert usable(json.dumps(dict(good, started_at=float('nan')))) is None          # a NaN start is not a time
    assert usable(None) is None and usable(b'\xff') is None


@pytest.mark.parametrize('kind', DAMAGE)
def test_no_writer_raises_on_a_damaged_row_it_has_to_read_and_none_changes_it(ledger, kind):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    host = dict(operation_id=operation['id'], battle_id=battle['id'], generation='g1')
    made = store.begin(row(**host))
    damaged = corrupt(ledger, made['id'], kind)
    before = stored_row(ledger, made['id'])
    with pytest.raises(ActiveExecution):                                   # its host is alive: refused, never an AttributeError
        store.begin(row(started_at=1100.0, **host))
    with pytest.raises(UnusableRow):
        store.finish(made['id'], result='no_op')
    assert store.settle_open(now=1200.0) == [] and store.unusable == 1       # nothing to close, and the damaged row is counted
    assert stored_row(ledger, made['id']) == before and before[1] == damaged       # the body is exactly what was found


@pytest.mark.parametrize('kind', DAMAGE)
def test_a_damaged_row_whose_host_is_gone_stops_blocking_its_instance_and_its_body_is_left_alone(ledger, kind):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    host = dict(operation_id=operation['id'], battle_id=battle['id'], generation='g1')
    made = store.begin(row(**host))
    damaged = corrupt(ledger, made['id'], kind)
    operations.transition(operation['id'], operations.get(operation['id'])['revision'], 'succeeded')
    assert store.settle_open(now=1200.0) == [] and store.unusable == 1       # it cannot be recorded as interrupted: nobody knows what it was
    terminal, body = stored_row(ledger, made['id'])
    assert terminal == 1 and body == damaged                                 # only its index entry is closed
    fresh = store.begin(row(started_at=1300.0, **host))                      # the instance is free again
    assert fresh['state'] == 'running'
    assert store.settle_open(now=1400.0) == [fresh['id']] and store.unusable == 0     # the damaged row is not counted twice


@pytest.mark.parametrize('kind', DAMAGE)
def test_begin_closes_a_damaged_row_of_a_dead_host_as_it_does_a_good_one(ledger, kind):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    made = store.begin(row(operation_id=operation['id'], battle_id=battle['id'], generation='g1'))
    corrupt(ledger, made['id'], kind)
    battles.finish(battle['id'], 'g1', reason='x', outcome='unknown', status='ended')
    assert store.begin(row(operation_id=operation['id'], battle_id='next', generation='g2', started_at=1200.0))['state'] == 'running'
    assert stored_row(ledger, made['id'])[0] == 1


@pytest.mark.parametrize('kind', DAMAGE)
def test_a_damaged_newest_row_is_not_an_aggregate_to_join_and_a_check_still_records(ledger, kind):
    store = Actions(ledger)
    first = check(store)
    damaged = corrupt(ledger, first['id'], kind)
    again = check(store, at=1005.0)                                           # the identical check
    assert again['id'] != first['id'] and again['aggregate']['count'] == 1     # its own row, never an increment of the damaged one
    assert stored_row(ledger, first['id'])[1] == damaged


def test_a_replayed_request_key_whose_row_is_damaged_is_refused_not_guessed(ledger):
    store = Actions(ledger)
    first = store.begin(row(request_key='launch-1'))
    corrupt(ledger, first['id'], 'host_null')
    for write in (lambda: store.begin(row(request_key='launch-1', started_at=1100.0)),
                  lambda: store.record_terminal(row('shopping', request_key='launch-1'), result='no_op')):
        with pytest.raises(UnusableRow):
            write()


# ------------------------------- a newer schema is never changed by ANY mutation, bodies and index alike (review R11-04)
def dump(ledger):
    db = sqlite3.connect(ledger)
    try:
        return (db.execute('SELECT * FROM action_executions ORDER BY id').fetchall(), db.execute('SELECT * FROM action_meta').fetchall())
    finally:
        db.close()


MUTATIONS = {
    'begin': lambda store, ids: store.begin(row('shopping', started_at=5000.0)),
    'record_check': lambda store, ids: store.record_check(row('harvest_launch', started_at=5000.0), result='refused', reason='x'),
    'record_terminal': lambda store, ids: store.record_terminal(row('shopping', started_at=5000.0), result='no_op'),
    'finish': lambda store, ids: store.finish(ids['open'], result='no_op'),
    'settle_open': lambda store, ids: store.settle_open(now=9_000_000.0),
    'prune': lambda store, ids: store.prune(now=9_000_000.0),
}
READS = {'read', 'read_page', 'read_meta', 'cursor', 'parse_cursor'}


def test_every_public_method_of_the_ledger_is_a_classified_read_or_a_mutation_tested_below():
    """A new method that opens a write transaction must be added to MUTATIONS, and with it to the newer-schema test."""
    public = {name for name, value in vars(Actions).items() if not name.startswith('_') and callable(getattr(Actions, name))
              or isinstance(value, staticmethod) and not name.startswith('_')}
    assert public == set(MUTATIONS) | READS, sorted(public ^ (set(MUTATIONS) | READS))


@pytest.mark.parametrize('mutation', sorted(MUTATIONS))
def test_a_ledger_written_by_a_newer_build_is_not_changed_by_this_mutation(ledger, mutation):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    opened = store.begin(row(operation_id=operation['id'], battle_id=battle['id'], generation='g1'))
    operations.transition(operation['id'], operations.get(operation['id'])['revision'], 'succeeded')    # settlement would close it
    old = store.begin(row('floating_gems', started_at=1.0))
    store.finish(old['id'], result='no_op', finished_at=2.0)                                              # pruning would delete it
    db = sqlite3.connect(ledger)
    db.execute('UPDATE action_meta SET schema_version=99')
    db.commit()
    db.close()
    before = dump(ledger)
    with pytest.raises(SchemaTooNew):
        MUTATIONS[mutation](store, dict(open=opened['id']))
    assert dump(ledger) == before


# --------------------------------------------- a page is a page of the rows asked for, and a cursor loses none (review R11-03)
def add(store, at, *, account='account', action='ad_gems', instance=None, device='device'):
    made = store.record_terminal(row(action, instance or action, started_at=at, account=account, device=device), result='no_op', finished_at=at + 0.5)
    return made['id']


def damaged_row(ledger, at, *, account='account', identity=None, device='device'):
    db = sqlite3.connect(ledger)
    db.execute("INSERT INTO action_executions(id, device, action, instance, account, started_at, terminal, body) VALUES (?, ?, 'ad_gems', 'x', ?, ?, 1, '{not json')",
               (identity or f'damaged-{at}', device, account, at))
    db.commit()
    db.close()


def traverse(ledger, limit, **filters):
    seen, cursor = [], None
    for _ in range(1000):
        rows, truncated, _, cursor = Actions.read_page(str(ledger), device='device', limit=limit, before=cursor, **filters)
        seen += [r['id'] for r in rows]
        if not truncated:
            assert cursor is None
            return seen
        assert rows and cursor, 'a truncated page with no rows or no way on'
    raise AssertionError('the traversal does not end')


@pytest.mark.parametrize('limit', [1, 2, 3, 4, 7, 100])
def test_following_the_cursor_visits_every_usable_row_exactly_once_whatever_the_ties_accounts_and_damaged_rows(ledger, limit):
    store = Actions(ledger)
    stamps = [100.0, 100.0, 100.0, 105.0, 105.0, 110.0, 100.0, 120.0, 120.0, 120.0, 120.0, 90.0, 105.0, 105.0]
    mine, other, none = [], [], []
    for index, at in enumerate(stamps):
        account = ('account', 'other', None)[index % 3]
        (mine, other, none)[index % 3].append(add(store, at, account=account, instance=f'i{index}'))
    for number, at in enumerate((100.0, 105.0, 105.0, 120.0, 120.0)):   # damaged rows inside runs of equal times, in every account
        for account in ('account', 'other', None):
            damaged_row(ledger, at, account=account, identity=f'damaged-{number}-{account}')
    add(store, 100.0, account='account', device='another-device')
    for account, expected in (('account', mine), ('other', other), (None, none)):
        visited = traverse(ledger, limit, account=account)
        assert sorted(visited) == sorted(expected) and len(visited) == len(set(visited)), (account, visited)
        every = {r['id']: r['started_at'] for r in Actions.read(str(ledger), device='device', limit=500, account=account)[0]}
        assert [every[i] for i in visited] == sorted(every.values(), reverse=True)           # newest first, ties included
    everything = traverse(ledger, limit)
    assert sorted(everything) == sorted(mine + other + none) and len(everything) == len(set(everything))


def test_the_account_is_applied_before_the_limit_so_an_older_row_of_the_selected_account_is_reachable(ledger):
    store = Actions(ledger)
    mine = add(store, 100.0)
    add(store, 200.0, account='other')
    add(store, 300.0, account='other')
    rows, truncated = Actions.read(str(ledger), device='device', account='account', limit=2)
    assert [r['id'] for r in rows] == [mine] and truncated is False
    assert Actions.read(str(ledger), device='device', account='nobody', limit=2) == ([], False)


def test_a_cursor_inside_a_run_of_equal_start_times_continues_exactly_there(ledger):
    store = Actions(ledger)
    ids = sorted((add(store, 100.0, instance=f'i{n}') for n in range(3)), reverse=True)        # equal times order by id, descending
    first, truncated, _, cursor = Actions.read_page(str(ledger), device='device', limit=2)
    assert [r['id'] for r in first] == ids[:2] and truncated is True
    assert cursor == f'100.0:{ids[1]}' == Actions.cursor(100.0, ids[1])
    second, truncated = Actions.read(str(ledger), device='device', limit=2, before=cursor)
    assert [r['id'] for r in second] == ids[2:] and truncated is False
    older = Actions.read(str(ledger), device='device', before=100.0)[0]                         # a bare time keeps its old meaning: strictly older
    assert older == []


def test_a_page_of_nothing_but_damaged_rows_never_hides_the_usable_rows_behind_them(ledger):
    store = Actions(ledger)
    mine = add(store, 10.0)
    for n in range(5):
        damaged_row(ledger, 100.0 + n)
    rows, truncated, damaged, cursor = Actions.read_page(str(ledger), device='device', limit=2)
    assert [r['id'] for r in rows] == [mine] and truncated is False and damaged == 5 and cursor is None


@pytest.mark.parametrize('cursor', ['', ':', 'x', 'x:y', '100.0:', 'nan', 'inf:abc', '-inf', 'nan:abc'])
def test_a_cursor_that_is_not_one_is_a_value_error(cursor):
    with pytest.raises(ValueError):
        Actions.parse_cursor(cursor)


# ======================================================================================================= review round 2
# R11-01: the WHOLE schema is checked, not the fields a writer happens to read (the reviewer's body lacked `trigger`)
FINISHED = dict(state='succeeded', finished_at=1001.0, duration_ms=1000.0, duration_basis='monotonic',
                outcome=dict(result='succeeded', reason='confirmed', effect='confirmed', attempts=2, confirmed=1, evidence=['offer_disappeared']))
AGGREGATE = dict(state='refused', finished_at=1000.0, aggregate=dict(scope='s', count=3, first_at=1000.0, last_at=1002.0),
                 outcome=dict(result='refused', reason='device_owned', effect='none', attempts=0, confirmed=0, evidence=[]))
SETTLED = dict(state='interrupted', settled_at=1100.0, settled_reason='operation_ended',
               outcome=dict(result='interrupted', reason='operation_ended', effect='unknown', attempts=0, confirmed=0, evidence=[]))
VARIANTS = dict(open=row(operation_id='op', battle_id='b', generation='g'), finished=dict(row(), **FINISHED), aggregate=dict(row(), **AGGREGATE),
                settled=dict(row(), **SETTLED))
NULLABLE = {'ledger_id', 'occurrence_key', 'request_key', 'command_id', 'config_revision', 'policy_revision', 'scheduled_for', 'accepted_at',
            'finished_at', 'duration_ms', 'aggregate'}
OTHER_TYPE = {'schema_version': 'x', 'id': 5, 'device': 5, 'action': 5, 'instance': 5, 'trigger': 'sometimes', 'state': 'maybe', 'started_at': 'soon',
              'duration_basis': 'wall', 'coverage': 'partial', 'host': [], 'context': 'x', 'outcome': [], 'refs': 'x', 'aggregate': [],
              'scheduled_for': 'x', 'accepted_at': 'x', 'finished_at': 'x', 'duration_ms': 'x', 'occurrence_key': 5, 'request_key': 5,
              'command_id': 5, 'ledger_id': 5, 'config_revision': [], 'policy_revision': {}}


def test_the_validator_lists_exactly_the_fields_new_record_makes():
    """A field a writer starts to make that the validator does not know would be trusted unchecked: this fails first."""
    from towerpilot_workflows.actions import OUTCOME_KEYS, RECORD_KEYS
    made = row()
    assert tuple(made) == RECORD_KEYS and tuple(made['outcome']) == OUTCOME_KEYS


@pytest.mark.parametrize('variant', sorted(VARIANTS))
def test_every_variant_the_writers_make_is_usable(variant):
    body = VARIANTS[variant]
    assert usable(json.dumps(body), indexed(body)) == body


@pytest.mark.parametrize('variant', sorted(VARIANTS))
def test_a_body_missing_any_field_a_writer_makes_is_damaged(variant):
    from towerpilot_workflows.actions import OUTCOME_KEYS, RECORD_KEYS
    body = VARIANTS[variant]
    for key in RECORD_KEYS:
        assert usable(json.dumps({k: v for k, v in body.items() if k != key})) is None, key
    for key in OUTCOME_KEYS:
        assert usable(json.dumps(dict(body, outcome={k: v for k, v in body['outcome'].items() if k != key}))) is None, key


@pytest.mark.parametrize('variant', sorted(VARIANTS))
def test_a_field_set_to_null_is_damaged_unless_it_is_one_a_writer_leaves_empty(variant):
    from towerpilot_workflows.actions import RECORD_KEYS
    body = VARIANTS[variant]
    for key in RECORD_KEYS:
        accepted = usable(json.dumps(dict(body, **{key: None}))) is not None
        assert accepted == (key in NULLABLE), (key, accepted)


@pytest.mark.parametrize('variant', sorted(VARIANTS))
def test_a_field_of_another_type_is_damaged(variant):
    body = VARIANTS[variant]
    for key, bad in OTHER_TYPE.items():
        assert usable(json.dumps(dict(body, **{key: bad}))) is None, key
    for key, bad in (('result', 'finished'), ('effect', 'maybe'), ('attempts', -1), ('attempts', 'x'), ('attempts', True), ('confirmed', 1.5),
                     ('evidence', 'seen'), ('evidence', [1]), ('reason', 5)):
        assert usable(json.dumps(dict(body, outcome=dict(body['outcome'], **{key: bad})))) is None, key
    for key in ('operation_id', 'battle_id', 'generation', 'driver', 'parent_execution'):
        assert usable(json.dumps(dict(body, host=dict(body['host'], **{key: 5})))) is None, key
    assert usable(json.dumps(dict(body, context=dict(body['context'], account=5)))) is None


def test_a_non_finite_time_is_damaged_wherever_a_time_is_kept():
    body = VARIANTS['finished']
    for key in ('started_at', 'finished_at', 'duration_ms', 'scheduled_for', 'accepted_at'):
        for text in ('NaN', 'Infinity', '-Infinity'):
            raw = json.dumps(body).replace(f'"{key}": {json.dumps(body[key])}', f'"{key}": {text}')
            assert usable(raw) is None or raw == json.dumps(body), (key, text)       # json.loads accepts the literal; the validator must not


# R11-01: the missing-trigger reproduction on the ledger itself, every consumer of a row met it as a KeyError
def test_a_row_without_its_trigger_is_refused_by_every_writer_and_never_changed(ledger):
    store = Actions(ledger)
    made = store.begin(row())
    db = sqlite3.connect(ledger)
    stored = json.loads(db.execute('SELECT body FROM action_executions WHERE id=?', (made['id'],)).fetchone()[0])
    del stored['trigger']
    damaged = json.dumps(stored)
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (damaged, made['id']))
    db.commit()
    db.close()
    with pytest.raises(UnusableRow):
        store.finish(made['id'], result='no_op')
    assert stored_row(ledger, made['id'])[1] == damaged
    assert Actions.read(str(ledger), device='device') == ([], False)


# ======================================================================================================= R11-03
# the index and the body must agree, and a page continues from the INDEX
DISAGREE = {
    'started_at': lambda body: dict(body, started_at=0.0),
    'id': lambda body: dict(body, id='somebody-else'),
    'device': lambda body: dict(body, device='another-device'),
    'action': lambda body: dict(body, action='floating_gems'),
    'instance': lambda body: dict(body, instance='another-instance'),
    'account': lambda body: dict(body, context=dict(body['context'], account='another-account')),
    'operation_id': lambda body: dict(body, host=dict(body['host'], operation_id='another-operation')),
    'battle_id': lambda body: dict(body, host=dict(body['host'], battle_id='another-battle')),
    'generation': lambda body: dict(body, host=dict(body['host'], generation='another-generation')),
    'request_key': lambda body: dict(body, request_key='another-key'),
    'terminal': lambda body: dict(body, state='running' if body['state'] != 'running' else 'succeeded'),      # the body says the opposite of the index
}


def set_body(ledger, execution_id, body):
    db = sqlite3.connect(ledger)
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (json.dumps(body), execution_id))
    db.commit()
    db.close()


def added(store, at):
    return store.record_terminal(row(started_at=at, operation_id='op', battle_id='b', generation='g', request_key=f'key-{at}'), result='no_op')


def test_the_index_columns_are_what_indexed_says_for_every_row_a_writer_stores(ledger):
    store = Actions(ledger)
    made = [added(store, 100.0 + n) for n in range(3)] + [store.begin(row('floating_gems', started_at=500.0, operation_id='op'))]
    db = sqlite3.connect(ledger)
    db.row_factory = None
    for body in made:
        stored = db.execute('SELECT id, device, action, instance, account, operation_id, battle_id, generation, request_key, started_at, terminal '
                            'FROM action_executions WHERE id=?', (body['id'],)).fetchone()
        assert index_of(stored) == indexed(body), body['id']
    db.close()


@pytest.mark.parametrize('column', sorted(DISAGREE))
def test_a_body_that_disagrees_with_the_index_of_its_row_is_damaged_and_never_a_row_of_a_page(ledger, column):
    store = Actions(ledger)
    rows = [added(store, 300.0), added(store, 200.0), added(store, 100.0)]
    set_body(ledger, rows[0]['id'], DISAGREE[column](rows[0]))
    got, truncated, damaged, cursor = Actions.read_page(str(ledger), device='device', limit=10)
    assert [r['id'] for r in got] == [rows[1]['id'], rows[2]['id']] and damaged == 1 and truncated is False and cursor is None
    assert Actions.read(str(ledger), device='device', account='account')[0] and rows[0]['id'] not in {r['id'] for r in Actions.read(str(ledger))[0]}


@pytest.mark.parametrize('column', sorted(DISAGREE))
@pytest.mark.parametrize('limit', [1, 2])
def test_every_intact_row_is_reachable_whatever_one_body_says_and_the_cursor_is_made_of_index_values(ledger, column, limit):
    """The reviewer's reproduction (the newest body's started_at changed to 0) for EVERY indexed column and at every position."""
    for position in range(4):
        store = Actions(ledger.with_name(f'{column}-{limit}-{position}.db'))
        path = ledger.with_name(f'{column}-{limit}-{position}.db')
        rows = [added(store, 400.0 - 100 * n) for n in range(4)]
        set_body(path, rows[position]['id'], DISAGREE[column](rows[position]))
        intact = [r['id'] for n, r in enumerate(rows) if n != position]
        seen, cursor, tokens = [], None, []
        for _ in range(20):
            page, truncated, damaged, cursor = Actions.read_page(str(path), device='device', limit=limit, before=cursor)
            seen += [r['id'] for r in page]
            if not truncated:
                break
            tokens.append(cursor)
        assert seen == intact, (column, limit, position, seen)
        every = {r['id']: r['started_at'] for r in rows}
        for token in tokens:
            at, identity = Actions.parse_cursor(token)
            assert every[identity] == at, 'a cursor named a value the index does not hold'


def test_the_reviewers_page_reproduction_through_the_read(ledger):
    store = Actions(ledger)
    first, second, third = added(store, 300.0), added(store, 200.0), added(store, 100.0)
    set_body(ledger, first['id'], dict(first, started_at=0))
    page1, truncated, damaged, cursor = Actions.read_page(str(ledger), device='device', limit=1)
    assert [r['id'] for r in page1] == [second['id']] and damaged == 1 and truncated is True and cursor == f"200.0:{second['id']}"
    page2 = Actions.read_page(str(ledger), device='device', limit=1, before=cursor)
    assert [r['id'] for r in page2[0]] == [third['id']] and page2[1] is False


@pytest.mark.parametrize('column', sorted(DISAGREE))
def test_an_open_row_whose_body_disagrees_with_its_index_is_damaged_for_the_writers_too(ledger, column):
    operations, battles, operation, battle = operation_and_battle(ledger)
    store = Actions(ledger)
    made = store.begin(row(operation_id=operation['id'], battle_id=battle['id'], generation='g1'))
    before = stored_row(ledger, made['id'])
    set_body(ledger, made['id'], DISAGREE[column](made))
    damaged = stored_row(ledger, made['id'])[1]
    with pytest.raises(UnusableRow):
        store.finish(made['id'], result='no_op')
    assert stored_row(ledger, made['id'])[1] == damaged and before[0] == 0
    operations.transition(operation['id'], operations.get(operation['id'])['revision'], 'succeeded')
    assert store.settle_open(now=1200.0) == [] and store.unusable == 1               # its host is gone: only the index entry is closed
    assert stored_row(ledger, made['id'])[1] == damaged and stored_row(ledger, made['id'])[0] == 1


# the class of the round-2 defect: a stored body read without being validated
def test_a_stored_action_body_is_interpreted_only_by_the_one_validator():
    """`json.loads` in the ledger module may appear in `usable` (a row's body), `_fenced` (a BATTLE's row, whose status it reads), `_settle`
    (a body `usable` already returned, copied through its own strict encoding), and nowhere else: any other reader would meet a body nobody
    validated, which is how the missing `trigger` reached a driver."""
    import ast
    import towerpilot_workflows.actions as module
    tree = ast.parse(open(module.__file__, encoding='utf-8').read())
    owners = {}

    def visit(node, owner=None):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == 'loads':
                owners.setdefault(owner, 0)
                owners[owner] += 1
            visit(child, name)
    visit(tree)
    assert set(owners) == {'usable', '_fenced', '_settle'}, owners
