"""validate_history (spec 3.8): the rules of the pure validator, one case each, and the lifecycle facts they stand for.

The validator reads a battle record and the bounded rows of one read and nothing else. These tests build records by hand for the rules (a rule that fires, and the
control that does not), and use a real operation, battle, ActionSession and ledger for the skew case, the round trip, the coverage rules and the corrupt and newer-schema reads.
Nothing touches a device, a live port or the machine's ledger.
"""
import copy
import json
import math
import sqlite3

import pytest

import action_history_cases as cases
from control import actions as actions_api
from control import operations
from towerpilot_workflows import action_history
from towerpilot_workflows.action_contract import SCHEMA_VERSION
from towerpilot_workflows.action_history import MAX_ROWS, validate_history
from towerpilot_workflows.actions import Actions

execution, battle = cases.execution, cases.battle


def judged(report, kind):
    return {entry['id']: entry['rules'] for entry in report[kind]}


def noted(report):
    return {entry['id']: entry['notes'] for entry in report['notes']}


# ------------------------------------------------------------------ a clean history
def test_a_clean_history_is_ok_and_listed_per_generation_in_the_order_given():
    record = battle(generations=('g1', 'g2'))
    rows = [execution(generation='g2', started_at=1300.0), execution(generation='g1', started_at=1200.0), execution(action='shopping', generation='g1', started_at=1150.0, reason='confirmed_purchase')]
    report = validate_history(record, rows)
    assert report['status'] == 'ok' and report['reason'] is None and report['coverage'] == 'full'
    assert report['items'] == rows and report['rejected'] == report['flagged'] == report['notes'] == [] and report['damaged'] == 0
    assert report['generations'] == {'g2': [rows[0]['id']], 'g1': [rows[1]['id'], rows[2]['id']]}


def test_the_validator_changes_nothing_it_is_given_and_reads_no_file_or_database(monkeypatch):
    record = battle(origin='adopted')
    rows = [execution(), execution(started_at=1105.0, coverage='full'), execution(generation='elsewhere')]
    before = copy.deepcopy((record, rows))
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('the validator opened a database'))
    monkeypatch.setattr('builtins.open', lambda *a, **k: pytest.fail('the validator opened a file'))
    first = validate_history(record, rows)
    assert (record, rows) == before and first == validate_history(record, rows)


# ------------------------------------------------------------------ rejected rules
def with_schema_newer(row):
    row['schema_version'] = SCHEMA_VERSION + 1


def with_device(row):
    row['device'] = 'another-device'


def with_account(row):
    row['context']['account'] = 'another-account'


def with_battle(row):
    row['host']['battle_id'] = 'another-battle'


def with_generation(row):
    row['host']['generation'] = 'a-driver-this-battle-never-had'


def with_no_generation(row):
    row['host']['generation'] = None


def with_negative_duration(row):
    row['duration_ms'] = -1.0


def with_unmeasured_basis(row):
    row['duration_basis'] = 'none'


def with_unlabelled_duration(row):
    row['duration_ms'], row['duration_basis'] = None, 'monotonic'


@pytest.mark.parametrize('edit,rule', [
    (with_schema_newer, 'newer_schema_row'), (with_device, 'context_mismatch'), (with_account, 'context_mismatch'), (with_battle, 'context_mismatch'),
    (with_generation, 'foreign_generation'), (with_no_generation, 'foreign_generation'),
    (with_negative_duration, 'invalid_duration'), (with_unmeasured_basis, 'invalid_duration'), (with_unlabelled_duration, 'invalid_duration')],
    ids=lambda value: getattr(value, '__name__', value))
def test_a_row_that_cannot_belong_here_or_is_malformed_is_rejected_and_its_neighbour_is_not(edit, rule):
    bad, good = execution(started_at=1100.0), execution(started_at=1200.0)
    edit(bad)
    report = validate_history(battle(), [bad, good])
    assert judged(report, 'rejected') == {bad['id']: [rule]} or rule in judged(report, 'rejected')[bad['id']]
    assert report['items'] == [good] and report['status'] == 'flagged' and report['generations'] == {'g1': [good['id']]}


def test_a_generation_is_a_member_of_the_battle_or_a_stranger_whatever_its_wall_time_says():
    inside = execution(generation='g1', started_at=1100.0)
    stranger = execution(generation='g0', started_at=1100.0)                      # same moment, a driver this battle never had
    early = execution(generation='g1', started_at=900.0)                          # a member, but its wall start precedes the attach point
    report = validate_history(battle(), [inside, stranger, early])
    assert judged(report, 'rejected') == {stranger['id']: ['foreign_generation']}
    assert [row['id'] for row in report['items']] == [inside['id'], early['id']] and noted(report) == {early['id']: ['clock_skew']}


def test_a_damaged_body_is_counted_and_never_among_the_items_whatever_its_shape():
    good = execution()
    nan = execution(started_at=1200.0)
    nan['duration_ms'] = float('nan')
    inf = execution(started_at=1300.0)
    inf['duration_ms'] = math.inf
    missing = execution(started_at=1400.0)
    del missing['outcome']
    report = validate_history(battle(), [good, nan, inf, missing, {'id': 'x'}, 'text', None, 7, []])
    assert report['items'] == [good] and report['damaged'] == 8 and report['rejected'] == [] and report['status'] == 'flagged'


# ------------------------------------------------------------------ flagged rules
def test_overlapping_executions_of_one_instance_are_flagged_and_touching_or_other_instances_are_not():
    first = execution(started_at=1100.0, duration_ms=10000.0)                                 # 1100 .. 1110
    inside = execution(started_at=1105.0, duration_ms=3000.0)                                 # starts before the first ended
    touching = execution(started_at=1110.0, duration_ms=1000.0)                               # starts the moment the first ends: no overlap
    other = execution(action='shopping', started_at=1105.0, reason='confirmed_purchase')
    report = validate_history(battle(), [first, inside, touching, other])
    assert judged(report, 'flagged') == {first['id']: ['overlap'], inside['id']: ['overlap']}
    assert len(report['items']) == 4                                                          # flagged rows stay: they are stored as they were


def test_an_open_execution_overlaps_whatever_starts_after_it_and_a_settled_one_claims_no_end():
    still_open = execution(state='running', started_at=1100.0)
    later = execution(started_at=1500.0)
    assert judged(validate_history(battle(), [still_open, later]), 'flagged')[still_open['id']] == ['overlap']
    settled = execution(state='interrupted', effect='unknown', reason='operation_ended', started_at=1100.0, duration_ms=None)
    settled['finished_at'] = None
    assert 'overlap' not in sum(judged(validate_history(battle(status='ended'), [settled, later]), 'flagged').values(), [])


def test_wall_clock_overlap_is_not_judged_for_a_row_whose_wall_clock_was_stepped():
    earlier = execution(started_at=1100.0, duration_ms=20000.0)
    skewed = execution(started_at=500.0, duration_ms=1000.0)                                  # wall before the attach point
    stepped = execution(started_at=1110.0, duration_ms=2000.0, finished_at=900.0)             # wall finish before its wall start
    report = validate_history(battle(), [earlier, skewed, stepped])
    assert report['flagged'] == [] and noted(report) == {skewed['id']: ['clock_skew'], stepped['id']: ['clock_step']} and len(report['items']) == 3


@pytest.mark.parametrize('action,first,second,flagged', [
    ('ad_gems', dict(), dict(), True),                                                         # no calendar: a window id is used once
    ('quest_scan', dict(state='failed', effect='none', reason='error'), dict(), True),         # attempted: a failed attempt used the gap
    ('free_store_gems', dict(state='failed', effect='none', reason='retry'), dict(), False),   # confirmed: a failed claim has not used the day
    ('free_store_gems', dict(effect='confirmed', reason='cooldown_shown'), dict(), True),      # a confirmed claim has
], ids=['window', 'chore-attempted', 'store-retry-not-yet-consumed', 'store-consumed'])
def test_an_occurrence_is_used_once_as_its_action_defines_use(action, first, second, flagged):
    one = execution(action=action, started_at=1100.0, occurrence_key='key-1', **first)
    two = execution(action=action, started_at=1300.0, occurrence_key='key-1', **second)
    report = validate_history(battle(), [one, two])
    assert judged(report, 'flagged').get(two['id']) == (['duplicate_occurrence'] if flagged else None) or (not flagged and 'duplicate_occurrence' not in judged(report, 'flagged').get(two['id'], []))
    assert one['id'] not in judged(report, 'flagged') or 'duplicate_occurrence' not in judged(report, 'flagged')[one['id']]


def test_different_keys_instances_and_aggregates_are_not_duplicates():
    rows = [execution(started_at=1100.0, occurrence_key='window:1'), execution(started_at=1300.0, occurrence_key='window:2'),
            execution(action='shopping', started_at=1500.0, occurrence_key='window:1', reason='confirmed_purchase'),
            execution(started_at=1700.0, occurrence_key=None), execution(started_at=1800.0, occurrence_key=None)]
    assert validate_history(battle(), rows)['flagged'] == []


@pytest.mark.parametrize('change,stale', [
    (dict(status='ended'), True), (dict(status='interrupted'), True), (dict(ended_at=1500.0), True), (dict(), False)], ids=['battle-ended', 'battle-interrupted', 'attachment-ended', 'live'])
def test_a_running_row_of_a_battle_that_has_ended_is_stale(change, stale):
    running = execution(state='running', started_at=1100.0)
    report = validate_history(battle(**change), [running])
    assert (judged(report, 'flagged') == {running['id']: ['stale_running']}) is stale


def test_a_running_row_whose_operation_is_known_terminal_is_stale_and_a_finished_one_never_is():
    running, done = execution(state='running', operation_id='op-dead'), execution(action='shopping', started_at=1500.0, operation_id='op-dead', reason='confirmed_purchase')
    report = validate_history(battle(), [running, done], ended_operations={'op-dead'})
    assert judged(report, 'flagged') == {running['id']: ['stale_running']}


@pytest.mark.parametrize('state,effect,flagged', [
    ('succeeded', 'unconfirmed', True), ('succeeded', 'unknown', True), ('succeeded', 'none', True), ('succeeded', 'confirmed', False),
    ('failed', 'unconfirmed', False), ('no_op', 'none', False), ('refused', 'none', False)])
def test_a_success_without_confirmed_effect_is_flagged_for_an_evidence_gated_action(state, effect, flagged):
    row = execution(state=state, effect=effect)
    assert (judged(validate_history(battle(), [row]), 'flagged') == {row['id']: ['unconfirmed_success']}) is flagged


def test_an_action_with_no_evidence_to_gate_may_succeed_with_an_unknown_effect_and_an_unknown_action_is_only_noted():
    plain = execution(action='daily_missions', effect='unknown', reason='completed')
    stranger = execution(action='not_in_the_catalog', started_at=1500.0, effect='unknown')
    report = validate_history(battle(), [plain, stranger])
    assert report['flagged'] == [] and noted(report) == {stranger['id']: ['unknown_action']} and len(report['items']) == 2


@pytest.mark.parametrize('origin', ['adopted', 'partial_adoption'])
def test_an_adopted_battle_never_reports_lifetime_counts(origin):
    claims_full = execution(coverage='full')
    since = execution(coverage='since_adoption', started_at=1500.0)
    unknown = execution(coverage='unknown', started_at=1700.0)
    report = validate_history(battle(origin=origin), [claims_full, since, unknown])
    assert report['coverage'] == 'since_adoption' and judged(report, 'flagged') == {claims_full['id']: ['coverage_overclaim']}


def test_a_battle_that_started_here_may_claim_full_coverage():
    assert validate_history(battle(origin='started'), [execution(coverage='full')])['flagged'] == []


# ------------------------------------------------------------------ the skew case, and attachments of a real battle
def test_a_wall_clock_stepped_back_after_attach_is_still_since_adoption_history_with_a_monotonic_duration(tmp_path):
    """Attach at wall 1000, advance monotonic time, step the wall back to 500, then start and finish an execution."""
    clock = cases.SteppedClock(mono=50.0, wall=1000.0)
    host = cases.RealHost(tmp_path, origin='adopted', clock=clock)
    record = host.battle_record()
    record['segments'][0]['started_at'] = 1000.0                                          # the attach point, on the same wall clock the session reads
    record['created_at'] = 1000.0
    clock.advance(30.0)
    clock.set_wall(500.0)                                                                 # the wall clock is corrected backwards
    execution_id = host.session.begin('ad_gems')
    clock.advance_mono(12.0)
    host.session.finish(execution_id, result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1, confirmed=1, evidence=['offer_disappeared'])
    (row,) = host.rows()
    assert row['started_at'] == 500.0 and row['finished_at'] == 500.0 and row['finished_at'] < 1000.0          # the wall clock never caught up: only the monotonic duration is a length
    assert row['duration_ms'] == 12000.0 and row['duration_basis'] == 'monotonic' and row['coverage'] == 'since_adoption'
    report = validate_history(record, [row])
    assert report['status'] == 'ok' and report['items'] == [row] and report['rejected'] == [] and report['flagged'] == []
    assert noted(report) == {row['id']: ['clock_skew']} and report['coverage'] == 'since_adoption'


def test_a_truly_foreign_generation_is_still_rejected_beside_a_skewed_member(tmp_path):
    clock = cases.SteppedClock(mono=50.0, wall=1000.0)
    host = cases.RealHost(tmp_path, origin='adopted', clock=clock)
    clock.set_wall(500.0)
    member = host.session.begin('ad_gems')
    host.session.finish(member, result='no_op', reason='x')
    (row,) = host.rows()
    foreign = dict(row, id='foreign', host=dict(row['host'], generation='another-driver'), instance='shopping', action='shopping')
    report = validate_history(host.battle_record(), [foreign, row])
    assert judged(report, 'rejected') == {'foreign': ['foreign_generation']} and report['items'] == [row]


# ------------------------------------------------------------------ real ledger round trip and the corrupt / newer reads
def test_a_real_ledger_round_trips_through_the_read_and_the_validator(tmp_path):
    host = cases.RealHost(tmp_path)
    opened = host.session.begin('ad_gems')
    host.session.finish(opened, result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1, confirmed=1, evidence=['offer_disappeared'])
    for _ in range(3):
        host.session.check('shopping', result='no_op', reason='nothing_due')                 # coalesced: one row, a count of three
    rows, truncated, damaged, _ = Actions.read_page(str(host.path), device='device', battle_id=host.battle_id)
    assert (truncated, damaged, len(rows)) == (False, 0, 2) and sorted(row['action'] for row in rows) == ['ad_gems', 'shopping']
    report = validate_history(host.battle_record(), rows, ledger=Actions.read_meta(str(host.path)))
    assert report['status'] == 'ok' and [row['id'] for row in report['items']] == [row['id'] for row in rows]
    assert report['generations'] == {host.battle_session.generation: [row['id'] for row in rows]}


def test_a_running_row_is_stale_once_its_battle_ends_and_clean_again_once_the_refresher_settles_it(tmp_path):
    host = cases.RealHost(tmp_path)
    host.session.begin('ad_gems')
    read = lambda: Actions.read(str(host.path), device='device')[0]
    assert validate_history(host.battle_record(), read())['status'] == 'ok'                  # a live battle's running row is normal
    host.end_battle()
    stale = validate_history(host.battle_record(), read())
    assert judged(stale, 'flagged') == {read()[0]['id']: ['stale_running']}
    assert actions_api.settle(tmp_path) == [read()[0]['id']]
    settled = validate_history(host.battle_record(), read())
    assert settled['status'] == 'ok' and read()[0]['state'] == 'interrupted' and read()[0]['duration_ms'] is None


def test_a_damaged_row_is_skipped_by_the_read_and_a_newer_ledger_is_not_interpreted(tmp_path):
    host = cases.RealHost(tmp_path)
    for name in ('ad_gems', 'shopping'):
        host.session.finish(host.session.begin(name), result='no_op', reason='x')
    with sqlite3.connect(host.path) as db:
        db.execute("UPDATE action_executions SET body='not json' WHERE action='shopping'")
    rows, _, damaged, _ = Actions.read_page(str(host.path), device='device')
    assert damaged == 1 and [row['action'] for row in rows] == ['ad_gems']
    assert validate_history(host.battle_record(), rows, ledger=Actions.read_meta(str(host.path)))['status'] == 'ok'
    with sqlite3.connect(host.path) as db:
        db.execute('UPDATE action_meta SET schema_version=?', (SCHEMA_VERSION + 1,))
    newer = validate_history(host.battle_record(), rows, ledger=Actions.read_meta(str(host.path)))
    assert (newer['status'], newer['reason'], newer['items']) == ('read_only', 'newer_schema', [])
    assert actions_api.for_battle(tmp_path, host.battle_record()) == dict(items=None, reason='newer_schema', truncated=False)     # the route's rule is the same


def test_rows_of_a_newer_schema_in_a_current_ledger_are_rejected_not_interpreted():
    newer = execution()
    newer['schema_version'] = SCHEMA_VERSION + 1
    assert judged(validate_history(battle(), [newer]), 'rejected') == {newer['id']: ['newer_schema_row']}


# ------------------------------------------------------------------ pagination
def test_pages_validated_with_their_neighbours_agree_with_one_whole_validation_and_stay_in_context(tmp_path):
    host = cases.RealHost(tmp_path)
    record = host.battle_record()
    generation = host.battle_session.generation
    attached = record['segments'][0]['started_at']                  # rows before the attachment are skewed and take no part in an overlap judgement: stay after it
    rows = []
    with sqlite3.connect(host.path) as db:
        for index in range(24):
            row = execution(action='ad_gems', instance='ad_gems', battle_id=host.battle_id, generation=generation, started_at=attached + 10.0 + index * 10.0,
                            duration_ms=30000.0 if index == 7 else 4000.0,                           # row 7 runs 30 s: into rows 8 and 9, which lie across the page edge
                            identity=f'p{index:02d}', occurrence_key=f'window:{index % 4}')
            rows.append(row)
        for row in rows:
            db.execute('INSERT INTO action_executions(id, device, action, instance, account, operation_id, battle_id, generation, request_key, started_at, terminal, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row['id'], row['device'], row['action'], row['instance'], 'account', row['host']['operation_id'], row['host']['battle_id'], row['host']['generation'], None, row['started_at'], 1, json.dumps(row)))
        other = execution(identity='other-account', account='someone-else', battle_id=host.battle_id, generation=generation, started_at=2005.0)
        db.execute('INSERT INTO action_executions(id, device, action, instance, account, operation_id, battle_id, generation, request_key, started_at, terminal, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (other['id'], 'device', 'ad_gems', 'ad_gems', 'someone-else', 'op-0', host.battle_id, generation, None, 2005.0, 1, json.dumps(other)))
    whole = validate_history(record, list(reversed(rows)))
    pages, cursor, seen = [], None, []
    while True:
        page, truncated, _, cursor = Actions.read_page(str(host.path), device='device', battle_id=host.battle_id, account='account', limit=5, before=cursor)
        pages.append(page)
        if not truncated:
            break
    assert [row['id'] for page in pages for row in page] == [row['id'] for row in reversed(rows)] and len(pages) == 5      # nothing of the other account, every row once
    lone, joined = {}, {}
    for index, page in enumerate(pages):
        neighbours = [row for side in (pages[index - 1] if index else [], pages[index + 1] if index + 1 < len(pages) else []) for row in side]
        lone.update(judged(validate_history(record, page), 'flagged'))
        joined.update(judged(validate_history(record, page, adjacent=neighbours), 'flagged'))
    assert joined == judged(whole, 'flagged')
    overlapping = {row_id for row_id, rules in judged(whole, 'flagged').items() if 'overlap' in rules}
    assert overlapping == {'p07', 'p08', 'p09'}                                                # explicit overlap findings, not only the duplicate keys the rows also carry
    assert 'overlap' not in lone.get('p09', [])                                                # alone, the page of row 9 cannot see row 7 across its edge (the stated limit)...
    assert 'overlap' in joined['p09']                                                          # ...and the neighbour page restores it
    assert validate_history(record, [other])['rejected'] == [dict(id='other-account', rules=['context_mismatch'])]      # fed by mistake it is refused, not shown


def test_the_input_is_one_bounded_page():
    rows = [execution(started_at=1100.0 + index) for index in range(MAX_ROWS)]
    assert len(validate_history(battle(), rows)['items']) == MAX_ROWS
    with pytest.raises(ValueError):
        validate_history(battle(), rows + [execution()])


# ------------------------------------------------------------------ coverage across the life of a battle
def test_a_battle_created_before_the_action_history_has_no_history_and_no_backfilled_timing(tmp_path):
    meta = dict(schema_version=SCHEMA_VERSION, ledger_id='l', created_at=5000.0)
    before = validate_history(battle(created_at=4000.0), [], ledger=meta)
    assert before['items'] is None and before['reason'] == 'recorded_before_action_history'
    assert validate_history(battle(created_at=4000.0), [], ledger=None)['items'] is None
    assert validate_history(battle(created_at=6000.0), [], ledger=meta)['items'] == []          # after: nothing ran, which is a different statement
    assert action_history.unrecorded_reason(battle(created_at=6000.0), meta) is None


def test_the_route_and_the_validator_agree_on_when_a_battle_has_no_history(tmp_path):
    host = cases.RealHost(tmp_path)
    record = host.battle_record()
    meta = Actions.read_meta(str(host.path))
    for created in (meta['created_at'] - 5, meta['created_at'] + 5):
        probe = dict(record, created_at=created)
        assert actions_api.for_battle(tmp_path, probe)['reason'] == validate_history(probe, [], ledger=meta).get('reason')


def test_a_transfer_creates_a_successor_whose_history_is_its_own_and_never_the_predecessors(tmp_path):
    first = cases.RealHost(tmp_path, key='op-1')
    first.session.finish(first.session.begin('ad_gems'), result='no_op', reason='x')
    old = first.battle_record()
    first.end_battle(status='interrupted')
    first.end_operation()
    second = cases.RealHost(tmp_path, key='op-2', origin='partial_adoption', previous_battle_id=first.battle_id)
    second.session.finish(second.session.begin('ad_gems'), result='no_op', reason='y')
    new = second.battle_record()
    assert new['previous_battle_id'] == first.battle_id and new['origin'] == 'partial_adoption'
    rows_old, rows_new = (Actions.read(str(first.path), device='device', battle_id=battle_id)[0] for battle_id in (first.battle_id, second.battle_id))
    assert len(rows_old) == len(rows_new) == 1 and rows_old[0]['coverage'] == 'full' and rows_new[0]['coverage'] == 'since_adoption'
    assert validate_history(old, rows_old)['status'] == 'ok' and validate_history(new, rows_new)['coverage'] == 'since_adoption'
    mixed = validate_history(new, rows_old)                                                     # the predecessor's row under the successor: never merged
    assert mixed['items'] == [] and set(judged(mixed, 'rejected')[rows_old[0]['id']]) == {'context_mismatch', 'foreign_generation'}


def test_a_battle_held_by_several_drivers_lists_each_generation_separately():
    record = battle(generations=('g1', 'g2', 'g3'), origin='adopted')
    rows = [execution(generation=g, started_at=1000.0 + 100 * i + 50, coverage='since_adoption') for i, g in enumerate(('g1', 'g2', 'g3', 'g2'))]
    report = validate_history(record, rows)
    assert report['status'] == 'ok' and list(report['generations']) == ['g1', 'g2', 'g3'] and report['generations']['g2'] == [rows[1]['id'], rows[3]['id']]


def test_a_wave_backwards_replacement_cannot_claim_a_start_before_its_attach_point(tmp_path):
    """The collector's replacement starts a partial_adoption battle: its first window cannot claim a start before the attach point, so a wall start earlier than it
    is only annotated, and a claim of full coverage is flagged."""
    clock = cases.SteppedClock(mono=10.0, wall=1000.0)
    host = cases.RealHost(tmp_path, origin='partial_adoption', clock=clock)
    record = host.battle_record()
    record['segments'][0]['started_at'] = 1000.0
    clock.set_wall(700.0)
    host.session.finish(host.session.begin('ad_gems'), result='no_op', reason='x')
    (row,) = host.rows()
    report = validate_history(record, [row])
    assert noted(report) == {row['id']: ['clock_skew']} and report['coverage'] == 'since_adoption' and report['flagged'] == []
    lie = dict(row, coverage='full')
    assert judged(validate_history(record, [lie]), 'flagged') == {row['id']: ['coverage_overclaim']}


# ------------------------------------------------------------------ item 6 audit N6: the projection tells what the rows say
def routine():
    return dict(enabled=True, state='waiting', next_due_at=None, blocker=None, counters={})


@pytest.mark.parametrize('origin,expected', [('started', 'full'), ('adopted', 'since_adoption'), ('partial_adoption', 'since_adoption')])
@pytest.mark.parametrize('tracked_only', [False, True])
def test_the_projection_publishes_the_coverage_the_rows_are_stored_with(tmp_path, origin, expected, tracked_only):
    host = cases.RealHost(tmp_path, origin=origin, tracked_only=tracked_only)
    host.session.finish(host.session.begin('ad_gems'), result='no_op', reason='x')
    (item,) = host.session.project({'ad_gems': routine()})['items']
    (row,) = host.rows()
    assert item['coverage'] == row['coverage'] == expected


def test_a_tracked_only_session_publishes_full_for_what_it_records_between_battles(tmp_path):
    host = cases.RealHost(tmp_path, origin='adopted', tracked_only=True)
    host.end_battle()                                                                           # the battle is over; the collector still acts at Home
    host.session.finish(host.session.begin('ad_gems'), result='no_op', reason='x')
    (item,) = host.session.project({'ad_gems': routine()})['items']
    (row,) = host.rows()
    assert row['host']['battle_id'] is None and item['coverage'] == row['coverage'] == 'full'


def test_the_projection_follows_the_driver_to_its_next_battle(tmp_path):
    host = cases.RealHost(tmp_path, origin='adopted')
    assert host.session.project({'ad_gems': routine()})['items'][0]['coverage'] == 'since_adoption'
    host.end_battle()
    host.battle_session.prepare_next(origin='started')
    assert host.session.project({'ad_gems': routine()})['items'][0]['coverage'] == 'full'
    host.session.finish(host.session.begin('ad_gems'), result='no_op', reason='x')
    assert [row['coverage'] for row in host.rows()] == ['full']


@pytest.mark.parametrize('value', ['full', 'since_adoption', 'unknown'])
def test_each_coverage_the_contract_names_is_published_and_stored_alike(tmp_path, value):
    from runtime.action_session import ActionSession
    session = ActionSession(str(tmp_path / 'ledger.db'), context=dict(device_key='device', account='account'), driver='orchestrator', coverage=lambda: value, clock=cases.SteppedClock())
    session.finish(session.begin('ad_gems'), result='no_op', reason='x')
    assert session.project({'ad_gems': routine()})['items'][0]['coverage'] == Actions.read(str(tmp_path / 'ledger.db'))[0][0]['coverage'] == value


@pytest.mark.parametrize('callback', [lambda: (_ for _ in ()).throw(RuntimeError('boom')), lambda: 'lifetime', lambda: None], ids=['raises', 'outside-the-contract', 'none'])
def test_a_coverage_that_cannot_be_read_is_unknown_never_full_and_never_raises_into_the_driver(tmp_path, callback):
    from runtime.action_session import ActionSession
    session = ActionSession(str(tmp_path / 'ledger.db'), context=dict(device_key='device', account='account'), driver='orchestrator', coverage=callback, clock=cases.SteppedClock())
    assert session.project({'ad_gems': routine()})['items'][0]['coverage'] == 'unknown'


def test_a_session_with_no_coverage_callback_is_full(tmp_path):
    from runtime.action_session import ActionSession
    session = ActionSession(str(tmp_path / 'ledger.db'), context=dict(device_key='device', account='account'), driver='orchestrator', clock=cases.SteppedClock())
    assert session.project({'ad_gems': routine()})['items'][0]['coverage'] == 'full'


# ------------------------------------------------------------------ the validator on the actual bounded read (control.actions.for_battle, which GET /api/battles/<id> calls)
def stranger_row(host, generation='stranger', started_at=1.0):
    """A row recorded under this battle's id by a generation the battle never had (a stale writer, a restored file): the validator must keep it out of the read."""
    from towerpilot_workflows import actions
    body = actions.new_record(device='device', action='shopping', instance='shopping', trigger='automatic', started_at=started_at, context=dict(account='account'),
                              host=dict(driver='orchestrator', operation_id=host.operation['id'], battle_id=host.battle_id, generation=generation))
    return Actions(str(host.path)).record_terminal(body, result='no_op', duration_ms=1.0)


def test_the_battle_read_shows_exactly_the_rows_the_validator_accepts_and_adds_no_key_for_a_clean_history(tmp_path):
    host = cases.RealHost(tmp_path)
    for name in ('ad_gems', 'shopping'):
        host.session.finish(host.session.begin(name), result='no_op', reason='x')
    block = actions_api.for_battle(tmp_path, host.battle_record())
    rows = Actions.read_page(str(host.path), device='device', battle_id=host.battle_id)[0]
    assert {row['id'] for row in block['items']} == {row['id'] for row in rows} and len(block['items']) == 2
    assert 'unusable' not in block and block['reason'] is None and block['truncated'] is False


def test_a_row_the_battle_never_ran_under_is_kept_out_of_the_battle_read(tmp_path):
    host = cases.RealHost(tmp_path)
    own = host.session.begin('ad_gems')
    host.session.finish(own, result='no_op', reason='x')
    stranger = stranger_row(host)
    assert stranger['id'] in {row['id'] for row in Actions.read_page(str(host.path), device='device', battle_id=host.battle_id)[0]}      # the plain read does return it: only the validator keeps it out
    block = actions_api.for_battle(tmp_path, host.battle_record())
    assert [row['id'] for row in block['items']] == [own] and 'unusable' not in block          # the wire shape of the block is unchanged


def test_a_damaged_row_is_left_out_of_the_battle_read_and_the_good_rows_are_still_shown(tmp_path):
    host = cases.RealHost(tmp_path)
    for name in ('ad_gems', 'shopping'):
        host.session.finish(host.session.begin(name), result='no_op', reason='x')
    with sqlite3.connect(host.path) as db:
        db.execute("UPDATE action_executions SET body='not json' WHERE action='shopping'")
    block = actions_api.for_battle(tmp_path, host.battle_record())
    assert [row['action'] for row in block['items']] == ['ad_gems'] and 'unusable' not in block


def test_the_battle_read_is_one_bounded_page_and_hands_out_the_cursor(tmp_path):
    host = cases.RealHost(tmp_path)
    for index in range(3):
        host.session.finish(host.session.begin('ad_gems', occurrence_key=f'w{index}'), result='no_op', reason='x')
    page = actions_api.for_battle(tmp_path, host.battle_record(), limit=2)
    assert len(page['items']) == 2 and page['truncated'] is True and page['next_before'] and 'unusable' not in page


def test_a_page_the_validator_refuses_whole_does_not_hide_the_older_history_behind_it(tmp_path, monkeypatch):
    host = cases.RealHost(tmp_path)
    own = host.session.begin('ad_gems')
    host.session.finish(own, result='no_op', reason='x')
    strangers = [stranger_row(host, generation=f'stranger-{index}', started_at=9e9 + index)['id'] for index in range(2)]      # newer than the battle's own row: they fill the first page
    page = actions_api.for_battle(tmp_path, host.battle_record(), limit=2)
    assert [row['id'] for row in page['items']] == [own] and page['truncated'] is False and 'next_before' not in page and not set(strangers) & {row['id'] for row in page['items']}
    monkeypatch.setattr(actions_api, 'MAX_REFUSED_PAGES', 1)                                  # past the bound the reader says so: an empty page that is truncated, with the cursor
    bounded = actions_api.for_battle(tmp_path, host.battle_record(), limit=2)
    assert bounded['items'] == [] and bounded['truncated'] is True and bounded['next_before']


def test_the_findings_of_a_neighbour_page_are_context_and_never_reported_on_the_page_read():
    good = execution(started_at=1200.0)
    foreign = execution(generation='g0', started_at=1100.0)
    unproven = execution(started_at=1300.0, effect='none', state='succeeded', action='shopping', reason='confirmed_purchase')
    report = validate_history(battle(), [good], adjacent=[foreign, unproven])
    assert (report['status'], report['rejected'], report['flagged']) == ('ok', [], []) and report['items'] == [good]
