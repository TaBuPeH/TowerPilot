"""Deployment compatibility of the action history (spec 4.5): the offline fixtures.

  * OLD DATA, NEW CODE: rows, schema and a battle written by the unmodified writer at HEAD 38e15ec9 (`fixtures/action_history_validation/old_writer_ledger_v1.json`, captured by
    the package's tools/capture_old_writer_r18h.py in a scratch tree) are read, validated, settled and extended by this build, and the old rows are left as they were.
  * NEW DATA, OLD READERS: what this build writes has the shape the old reader's contract names - the same keys, columns, tables and indexes, no new ones - so a reader of the
    old build still reads it. The old READER itself is run against new data by the package (evidence/compat-old-reader.json, base-tree code in a scratch tree); a unit test cannot
    import the old code, so here the old reader is its frozen contract.
  * AN OLD UNCOOPERATIVE WRITER BESIDE A LOCKED ONE: the documented unprotected case, asserted so it is never mistaken for a protected one.
  * ROLLBACK: code that does not know the action tables runs beside them; consumed-day markers, command receipts and the action rows are intact.

Temporary files only; no device, no live port, no machine state.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import paths as test_paths
import pytest

import action_history_cases as cases
from scheduling import daystate
from towerpilot_workflows import action_history, actions
from towerpilot_workflows.action_contract import SCHEMA_VERSION
from towerpilot_workflows.actions import Actions
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

FIXTURE = test_paths.FIXTURES / 'action_history_validation' / 'old_writer_ledger_v1.json'


@pytest.fixture(scope='module')
def old():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def table_text(path, table):
    with sqlite3.connect(path) as db:
        return json.dumps(db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall(), sort_keys=True)


def ledger_from(old, tmp_path, *, with_actions=True, running=None):
    """A ledger holding the old writer's data: the battle in its table, the action tables created from the OLD DDL, the old rows inserted as stored."""
    path = tmp_path / 'old.db'
    Operations(str(path))
    Battles(str(path))
    battle = old['battle']
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO battles VALUES (?,?,?,?,?,?)', (battle['id'], battle['context']['device_key'], battle['status'], 'old-creation-key', 'fingerprint', json.dumps(battle, sort_keys=True)))
        if with_actions:
            for name in ('action_meta', 'action_executions'):
                db.execute(old['ddl'][name])
            for name in sorted(old['ddl']):
                if name.startswith(('action_executions_by_', 'one_')):
                    db.execute(old['ddl'][name])
            db.execute('INSERT INTO action_meta(id, schema_version, ledger_id, created_at) VALUES (1, ?, ?, ?)', (old['meta']['schema_version'], old['rows'][0]['body']['ledger_id'], battle['created_at'] - 1.0))
            for row in old['rows']:
                body = dict(row['body'])
                index = {key: row[key] for key in actions.INDEX}
                if running and row['id'] == running:
                    body.update(state='running', finished_at=None, duration_ms=None, duration_basis='none', outcome=dict(body['outcome'], result=None, reason=None, effect='none'))
                    for key in ('settled_at', 'settled_reason'):
                        body.pop(key, None)
                    index['terminal'] = 0
                db.execute(f'INSERT INTO action_executions({actions.INDEX_SQL}, body) VALUES ({", ".join("?" for _ in actions.INDEX)}, ?)',
                           (*(index[key] for key in actions.INDEX), json.dumps(body, sort_keys=True)))
    return path


# ------------------------------------------------------------------ old data, new code
def test_the_schema_this_build_creates_is_the_schema_the_old_writer_made(old, tmp_path):
    path = tmp_path / 'new.db'
    Actions(str(path))
    with sqlite3.connect(path) as db:
        fresh = {name: sql for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE 'action_%' OR name IN ('one_open_execution_per_instance', 'one_execution_per_request_key')")}
    assert fresh == old['ddl']                                                                    # the same tables, columns, indexes and constraints: nothing added, nothing renamed
    assert SCHEMA_VERSION == old['schema_version'] == old['meta']['schema_version']


def test_this_build_reads_every_old_row_unchanged_and_the_validator_finds_the_old_history_clean(old, tmp_path):
    path = ledger_from(old, tmp_path)
    rows, truncated, damaged, _ = Actions.read_page(str(path), device='device')
    assert (truncated, damaged) == (False, 0) and rows == [row['body'] for row in sorted(old['rows'], key=lambda r: (r['started_at'], r['id']), reverse=True)]
    assert all(actions.usable(json.dumps(row['body']), {key: row[key] for key in actions.INDEX}) is not None for row in old['rows'])
    report = action_history.validate_history(old['battle'], rows, ledger=Actions.read_meta(str(path)))
    assert report['status'] == 'ok' and len(report['items']) == 5 and report['coverage'] == 'since_adoption' and report['rejected'] == report['flagged'] == []
    assert list(report['generations']) == [old['battle']['generation']]


def test_this_build_settles_an_old_running_row_the_way_the_old_one_did_and_extends_the_ledger_without_touching_old_rows(old, tmp_path):
    running_id = next(row['id'] for row in old['rows'] if row['body']['state'] == 'interrupted')
    path = ledger_from(old, tmp_path, running=running_id)
    before = {row[0]: row[1] for row in sqlite3.connect(path).execute('SELECT id, body FROM action_executions')}
    assert json.loads(before[running_id])['state'] == 'running'
    store = Actions(str(path))                                                                     # constructing this build's store over old data changes no row
    assert {row[0]: row[1] for row in sqlite3.connect(path).execute('SELECT id, body FROM action_executions')} == before
    with sqlite3.connect(path) as db:
        db.execute("UPDATE battles SET status='interrupted'")                                       # (the fixture battle already ended)
    assert store.settle_open(now=1_700_002_000.0) == [running_id]
    original = next(row['body'] for row in old['rows'] if row['id'] == running_id)
    settled = json.loads(sqlite3.connect(path).execute('SELECT body FROM action_executions WHERE id=?', (running_id,)).fetchone()[0])
    def core(body):             # the fence that closed it is the ledger this test built (the battle; the old run's operation is not in it), so the reason is the one thing allowed to differ
        return {key: (dict(value, reason=None) if key == 'outcome' else value) for key, value in body.items() if key not in ('settled_at', 'settled_reason')}
    assert core(settled) == core(original) and settled['settled_reason'] == settled['outcome']['reason'] == 'battle_ended' and original['settled_reason'] == 'operation_ended'
    untouched = {key: value for key, value in before.items() if key != running_id}
    new_row = store.begin(actions.new_record(device='device', action='floating_gems', instance='floating_gems', trigger='automatic', started_at=1_700_003_000.0,
                                             host=dict(driver='orchestrator'), context=dict(account='account')))
    store.finish(new_row['id'], result='no_op', reason='x')
    now = {row[0]: row[1] for row in sqlite3.connect(path).execute('SELECT id, body FROM action_executions')}
    assert all(now[key] == value for key, value in untouched.items()) and len(now) == len(before) + 1


def test_a_ledger_with_no_action_tables_at_all_gets_them_added_and_loses_nothing(old, tmp_path):
    path = ledger_from(old, tmp_path, with_actions=False)
    kept = {table: table_text(path, table) for table in ('operations', 'battles', 'battle_commands', 'battle_events')}
    assert Actions.read_meta(str(path)) is None and Actions.read_page(str(path))[0] == []
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'action_%'").fetchone() == (0,)      # a read created nothing
    Actions(str(path))
    assert {table: table_text(path, table) for table in kept} == kept                               # additive: every old table is as it was
    assert action_history.unrecorded_reason(old['battle'], Actions.read_meta(str(path))) == 'recorded_before_action_history'


# ------------------------------------------------------------------ new data, old readers
def test_what_this_build_writes_has_exactly_the_shape_the_old_reader_contract_names(old, tmp_path):
    contract = old['contract']
    host = cases.RealHost(tmp_path, origin='adopted')
    host.session.finish(host.session.begin('ad_gems', occurrence_key='window:1'), result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1, confirmed=1, evidence=['offer_disappeared'])
    host.session.check('shopping', result='refused', reason='device_owned')
    host.session.check('shopping', result='refused', reason='device_owned')
    host.session.record('shopping', result='failed', reason='error', trigger='manual', request_key='k', command_id='c')
    host.session.begin('floating_gems')
    host.end_battle(status='interrupted')
    host.end_operation()
    assert len(host.session.settle()) == 1
    with sqlite3.connect(host.path) as db:
        stored = db.execute(f'SELECT {actions.INDEX_SQL}, body FROM action_executions').fetchall()
    assert len(stored) == 4
    for row in stored:
        body = json.loads(row[-1])
        allowed = set(contract['record_keys']) | set(contract['settled_keys']) | {'replayed'}
        assert set(contract['record_keys']) <= set(body) <= allowed - {'replayed'}, sorted(set(body) ^ set(contract['record_keys']))
        assert set(body['outcome']) == set(contract['outcome_keys']) and list(actions.INDEX) == contract['index']
        assert body['state'] in contract['states'] and body['outcome']['effect'] in contract['effects'] and body['trigger'] in contract['triggers'] and body['coverage'] in contract['coverages']
        assert body['schema_version'] == old['schema_version']
        assert actions.indexed(body) == dict(zip(actions.INDEX, row[:-1]))                           # the columns an old reader looks a row up by
    with sqlite3.connect(host.path) as db:
        assert {name: sql for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE 'action_%' OR name IN ('one_open_execution_per_instance', 'one_execution_per_request_key')")} == old['ddl']


def test_the_daily_state_keys_an_old_reader_looks_for_keep_their_names_and_shapes(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, 'STATE', str(tmp_path / 'logs' / 'daily_state.json'))
    monkeypatch.setattr(daystate, '_today', lambda: '2026-09-10')
    daystate.mark_today('chore_quest_scan')
    daystate.set_today('shard_runs', 3)
    assert daystate.increment_today('shard_runs') == 4
    daystate.set_raw('global_rewards:alice:store', '2026-09-10')
    assert json.loads(Path(daystate.STATE).read_text()) == {'chore_quest_scan': '2026-09-10', 'shard_runs': {'date': '2026-09-10', 'value': 4}, 'global_rewards:alice:store': '2026-09-10'}


# ------------------------------------------------------------------ an old writer beside a locked one
CHILD = r'''
import json, os, sys, time
from pathlib import Path
spec = json.loads(os.environ['TP_COMPAT_CHILD'])
sync = Path(spec['sync']); sync.mkdir(parents=True, exist_ok=True)
state = spec['state']
def mark(name): (sync / name).write_text('1')
def wait(name, limit):
    end = time.monotonic() + limit
    while not (sync / name).exists() and time.monotonic() < end: time.sleep(0.01)
if spec['role'] == 'A':
    if spec['mode'] == 'old':
        # the pre-lock writer: load, think, save a stale copy with an atomic replace; it takes no lock and cannot see one
        with open(state) as fh: st = json.load(fh)
        mark('A_loaded'); wait('B_done', 6)
        st['alpha'] = 1
        tmp = f'{state}.tmp-old'
        with open(tmp, 'w') as fh: json.dump(st, fh)
        os.replace(tmp, state)
    else:
        from scheduling import daystate
        daystate.STATE = state
        def slow(current):
            mark('A_loaded'); wait('B_started', 6); time.sleep(0.8)
            return 1
        daystate.update('alpha', slow)
else:
    from scheduling import daystate
    daystate.STATE = state
    wait('A_loaded', 30); mark('B_started')
    daystate.set_raw('beta', 1)
    mark('B_done')
'''


def pair(tmp_path, mode):
    state = tmp_path / f'state-{mode}.json'
    state.write_text(json.dumps({'keep': '2026-09-10'}))
    procs = []
    for role in ('A', 'B'):
        spec = dict(state=str(state), sync=str(tmp_path / f'sync-{mode}'), role=role, mode=mode)
        env = dict(os.environ, PYTHONPATH=str(test_paths.BACKEND), TP_COMPAT_CHILD=json.dumps(spec), PYTHONDONTWRITEBYTECODE='1')
        procs.append(subprocess.Popen([sys.executable, '-c', CHILD], cwd=str(test_paths.REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    for proc in procs:
        out, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, err
    return json.loads(state.read_text())


def test_two_locked_writers_lose_neither_key(tmp_path):
    state = pair(tmp_path, 'locked')
    assert state == {'keep': '2026-09-10', 'alpha': 1, 'beta': 1}


def test_an_old_writer_beside_a_locked_one_is_NOT_protected_and_loses_the_new_writers_key(tmp_path):
    """The documented boundary (spec 4.5): the lock protects only writers that take it. Until every writer has relaunched on the new code the old one's stale save
    overwrites the new one's key. This is asserted so that no one mistakes the mixed state for a protected one; activation waits for every writer (never a kill)."""
    state = pair(tmp_path, 'old')
    assert state == {'keep': '2026-09-10', 'alpha': 1}, state                                      # beta, written under the lock, was saved away by the unlocked stale copy


# ------------------------------------------------------------------ rollback keeps what was recorded
def test_code_that_does_not_know_the_action_tables_runs_beside_them_and_markers_receipts_and_rows_stay(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, 'STATE', str(tmp_path / 'logs' / 'daily_state.json'))
    monkeypatch.setattr(daystate, '_today', lambda: '2026-09-10')
    host = cases.RealHost(tmp_path)
    host.store.activate(host.battle_id, host.battle_session.generation, {'controls': [], 'actions': ['ad_gems']})
    receipt = host.store.request_action(host.battle_id, host.operation['id'], host.battle_session.generation, 'ad_gems', 'run-now-1')
    host.session.finish(host.session.begin('ad_gems', trigger='manual', command_id=receipt['id'], request_key='run-now-1'), result='no_op', reason='x')
    daystate.mark_today('chore_quest_scan')
    action_tables = {table: table_text(host.path, table) for table in ('action_meta', 'action_executions')}
    # "the reverted code": only the operations and battles layers, which never knew the action tables, and a writer of the daily state that is not this build's
    host.store.finish(host.battle_id, host.battle_session.generation, reason='rolled_back_run_ended', status='ended')
    host.end_operation()
    state = json.loads(Path(daystate.STATE).read_text())
    state['another_key'] = '2026-09-10'
    Path(daystate.STATE).write_text(json.dumps(state))
    replay = Battles(str(host.path)).recorded_command('run-now-1', Battles.action_fingerprint(host.battle_id, host.operation['id'], host.battle_session.generation, 'ad_gems'))
    assert replay is not None and replay['id'] == receipt['id']                                      # the receipt answers the identical request after the driver is gone
    assert {table: table_text(host.path, table) for table in action_tables} == action_tables        # the action rows are exactly as recorded
    assert json.loads(Path(daystate.STATE).read_text())['chore_quest_scan'] == '2026-09-10' and daystate.flag_today('chore_quest_scan')
    (row,) = Actions.read(str(host.path), device='device')[0]
    assert row['command_id'] == receipt['id'] and row['request_key'] == 'run-now-1'                  # and the row still names the command it ran for
