"""Concurrency of the action history (spec 3.6): barrier-controlled child processes against a temporary ledger.

Every child waits at a file barrier until all are ready and then does one thing at the same moment, so the contested step is really contested. Children are
`python -c` processes started by the test and waited for by it (never stopped from outside); the ledger is a temporary file; there is no device and no live port.

The daystate halves of the spec's list (distinct keys lose none, N same-key increments end at exactly N, rollover, preserved markers, lock timeout) are
`test_daystate_serialization.py`; `test_that_the_daystate_concurrency_cases_are_still_there` fails if one of them is deleted. Off or a profile change during
prepare (the harvest fence) is `test_harvest_lifecycle.py`, unchanged.
"""
import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import paths as test_paths
import pytest

import action_history_cases as cases
from control import actions as actions_api
from towerpilot_workflows import actions
from towerpilot_workflows.actions import Actions

BACKEND = test_paths.BACKEND

CHILD = r'''
import json, os, sys, time
from pathlib import Path
spec = json.loads(os.environ['TP_CONCURRENCY_CHILD'])
sync = Path(spec['sync']); sync.mkdir(parents=True, exist_ok=True)
(sync / f"ready_{spec['name']}").write_text('1')
end = time.monotonic() + 60
while not (sync / 'go').exists() and time.monotonic() < end:
    time.sleep(0.005)
from towerpilot_workflows import actions
from towerpilot_workflows.actions import Actions
store = Actions(spec['path'])
def body(**over):
    fields = dict(device='device', action='ad_gems', instance='ad_gems', trigger='automatic', started_at=1000.0 + spec['n'],
                  host=dict(driver='orchestrator', operation_id=None, battle_id=None, generation=None), context=dict(account='account'))
    fields.update(over)
    return actions.new_record(**fields)
out = {'name': spec['name']}
try:
    op = spec['op']
    if op == 'begin_same_key':
        row = store.begin(body(instance=f"i{spec['n']}", request_key='shared-key')); out.update(id=row['id'], replayed=bool(row.get('replayed')))
    elif op == 'begin_same_instance':
        row = store.begin(body(request_key=f"k{spec['n']}")); out.update(id=row['id'])
    elif op == 'check_identical':
        for _ in range(spec['repeat']):
            row = store.record_check(body(action='shopping', instance='shopping'), result='refused', reason='device_owned')
        out.update(id=row['id'])
    elif op == 'finish_same':
        row = store.finish(spec['id'], result=spec['result'], reason=spec['result'], duration_ms=float(spec['n'])); out.update(state=row['state'], duration=row['duration_ms'], reason=row['outcome']['reason'])
    elif op == 'settle':
        out.update(settled=store.settle_open())
except actions.ActiveExecution as exc:
    out.update(refused='ActiveExecution')
except Exception as exc:
    out.update(error=f'{type(exc).__name__}: {exc}')
print(json.dumps(out))
'''


def spawn(tmp_path, count, op, *, extra=None, ledger_path=None):
    """Start `count` children at the barrier; returns their parsed answers once all have finished."""
    sync = tmp_path / 'sync'
    processes = []
    for n in range(count):
        spec = dict(path=str(ledger_path or tmp_path / 'ledger.db'), sync=str(sync), name=f'c{n}', n=n, op=op, **(extra or {}))
        env = dict(os.environ, PYTHONPATH=str(BACKEND), TP_CONCURRENCY_CHILD=json.dumps(spec), PYTHONDONTWRITEBYTECODE='1')
        processes.append(subprocess.Popen([sys.executable, '-c', CHILD], cwd=str(BACKEND.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    end = time.monotonic() + 60
    while len(list(sync.glob('ready_*'))) < count and time.monotonic() < end:
        time.sleep(0.01)
    assert len(list(sync.glob('ready_*'))) == count, 'a child never reached the barrier'
    (sync / 'go').write_text('1')
    answers = []
    for process in processes:
        out, err = process.communicate(timeout=180)
        assert process.returncode == 0, err
        answers.append(json.loads(out.strip().splitlines()[-1]))
    assert not [a for a in answers if 'error' in a], answers
    return answers


def rows(tmp_path, **filters):
    return Actions.read(str(tmp_path / 'ledger.db'), **filters)[0]


@pytest.fixture
def ledger(tmp_path):
    Actions(str(tmp_path / 'ledger.db'))
    return tmp_path


# ------------------------------------------------------------------ one row per key, one open execution per instance
def test_hosts_recording_the_same_request_key_at_once_insert_one_row_and_all_get_it(ledger):
    answers = spawn(ledger, 6, 'begin_same_key')
    assert len({a['id'] for a in answers}) == 1 and sum(not a['replayed'] for a in answers) == 1 and len(rows(ledger)) == 1


def test_a_second_begin_for_an_active_instance_is_refused_whoever_races_for_it(ledger):
    answers = spawn(ledger, 6, 'begin_same_instance')
    won = [a for a in answers if 'id' in a]
    assert len(won) == 1 and [a.get('refused') for a in answers if 'id' not in a] == ['ActiveExecution'] * 5
    (row,) = rows(ledger)
    assert row['id'] == won[0]['id'] and row['state'] == 'running'


def test_identical_checks_from_several_hosts_are_one_row_whose_count_is_the_sum(ledger):
    answers = spawn(ledger, 4, 'check_identical', extra=dict(repeat=5))
    (row,) = rows(ledger)
    assert row['aggregate']['count'] == 20 and len({a['id'] for a in answers}) == 1 and row['id'] == answers[0]['id']


def test_finish_by_several_writers_is_a_compare_and_set_the_first_wins_and_the_others_change_nothing(ledger):
    store = Actions(str(ledger / 'ledger.db'))
    opened = store.begin(actions.new_record(device='device', action='ad_gems', instance='ad_gems', trigger='automatic', started_at=1000.0, host=dict(driver='orchestrator'), context=dict(account='account')))
    answers = spawn(ledger, 4, 'finish_same', extra=dict(id=opened['id'], result='failed'))
    (row,) = rows(ledger)
    assert row['state'] == 'failed' and len({(a['state'], a['duration'], a['reason']) for a in answers}) == 1       # every writer was told the one stored outcome
    assert row['duration_ms'] == answers[0]['duration'] and row['outcome']['reason'] == 'failed'
    later = store.finish(opened['id'], result='succeeded', reason='late', effect='confirmed')
    assert later['state'] == 'failed' and rows(ledger)[0] == row                                                        # and a late, stale one changes nothing


def test_settlement_by_several_refreshers_closes_each_row_exactly_once(ledger):
    host = cases.RealHost(ledger)
    host.session.begin('ad_gems')
    host.session.begin('shopping')
    host.end_battle(status='interrupted')
    host.end_operation()
    answers = spawn(ledger, 4, 'settle', ledger_path=host.path)
    closed = sorted(entry for a in answers for entry in a['settled'])
    stored = Actions.read(str(host.path), device='device')[0]
    assert closed == sorted(row['id'] for row in stored) and len(closed) == 2 and all(row['state'] == 'interrupted' for row in stored)


# ------------------------------------------------------------------ in-process races
def test_a_stale_generation_cannot_settle_or_finish_a_newer_one(ledger):
    """Generations are fencing tokens, not an ordered value: a row is settled only by the exact fence of its OWN generation, and a host can finish only the
    executions it opened (the id is the handle; a late finish of its own, already settled row is a compare-and-set that changes nothing)."""
    older = cases.RealHost(ledger, key='op-1')
    stale_id = older.session.begin('ad_gems')
    older.end_battle(status='interrupted')
    older.end_operation()
    newer = cases.RealHost(ledger, key='op-2', origin='partial_adoption', previous_battle_id=older.battle_id)
    current_id = newer.session.begin('shopping')
    stored = lambda: {row['id']: row for row in Actions.read(str(older.path), device='device')[0]}
    assert actions_api.settle(ledger) == [stale_id]                                                                  # only the dead generation's row
    assert stored()[stale_id]['state'] == 'interrupted' and stored()[current_id]['state'] == 'running'
    late = older.session.finish(stale_id, result='succeeded', reason='late', effect='confirmed')
    assert late['state'] == 'interrupted' and stored()[stale_id]['outcome']['effect'] == 'unknown'                  # a stale writer cannot turn a settled row into a success
    assert stored()[current_id]['state'] == 'running' and actions_api.settle(ledger) == []
    newer.session.finish(current_id, result='no_op', reason='x')
    assert stored()[current_id]['state'] == 'no_op'


def test_prune_and_begin_race_without_losing_a_new_row_or_raising(ledger):
    store = Actions(str(ledger / 'ledger.db'))
    now = 1_700_000_000.0 + 200 * 86400
    for index in range(40):
        store.record_terminal(actions.new_record(device='device', action='shopping', instance=f'old{index}', trigger='automatic', started_at=now - 90 * 86400 - index,
                                                 host=dict(driver='orchestrator'), context=dict(account='account')), result='refused', reason='old', finished_at=now - 90 * 86400)
    errors, begun = [], []

    def pruner():
        try:
            mine = Actions(str(ledger / 'ledger.db'))
            while mine.prune(now, batch=5):
                pass
        except Exception as exc:        # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    def beginner():
        try:
            mine = Actions(str(ledger / 'ledger.db'))
            for index in range(25):
                row = mine.begin(actions.new_record(device='device', action='ad_gems', instance=f'new{index}', trigger='automatic', started_at=now - index,
                                                    host=dict(driver='orchestrator'), context=dict(account='account')))
                begun.append(row['id'])
        except Exception as exc:        # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=pruner), threading.Thread(target=beginner)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert errors == [] and len(begun) == 25
    remaining = {row['id'] for row in rows(ledger)}
    assert set(begun) == remaining                                                                                  # every old row pruned, every new one still there


def test_that_the_daystate_concurrency_cases_are_still_there():
    """The spec's daystate concurrency list is proven by test_daystate_serialization.py; this fails if a case it names is deleted or renamed."""
    tree = ast.parse((test_paths.TESTS / 'unit' / 'test_daystate_serialization.py').read_text(encoding='utf-8'))
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    needed = {'test_two_processes_writing_different_keys_lose_neither', 'test_two_processes_incrementing_one_key_end_at_exactly_two',
              'test_many_increments_from_two_processes_and_many_threads_lose_none', 'test_day_rollover_markers_and_odd_shapes_survive_an_increment',
              'test_a_lock_that_stays_held_times_out_logs_and_writes_nothing', 'test_a_holder_that_is_killed_releases_the_lock'}
    assert needed <= names, sorted(needed - names)
