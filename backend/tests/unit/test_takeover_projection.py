"""Takeover facts on the battle read: the `attach` block and the computed lineage (control/battle_lineage.py), through the real routes and ledger, and the rule
that the takeover modules reach no device. No device, no network."""
import copy
import json
import subprocess
import sys

import pytest

import paths as test_paths
from control import battle_lineage, battles, operations, takeover
from runtime import attach_worker
from takeover_http import body, install_readiness, make_app
from test_takeover_admission import with_collector

ROOT = test_paths.REPO


@pytest.fixture
def app(dash, launches, monkeypatch):
    app = make_app(dash, launches, monkeypatch)
    install_readiness(monkeypatch)
    return app


def successor(app, takeover_op, previous_id, generation, store):
    """What the T3b worker does after its entry gate: finish the collector's record and create the linked successor from the accepted envelope."""
    ops = operations.ledger(app.dash.ROOT)
    op = ops.get(takeover_op)
    return store.create(op['id'], op['context'], copy.deepcopy(op['request']['battle_envelope']), generation=generation, origin='partial_adoption',
                        previous_battle_id=previous_id)


def test_a_taken_over_battle_shows_its_attach_block_and_the_collector_record_it_continues(app):
    collector, store, first = with_collector(app)
    reply = app.client.post('/api/control', json=body(app, tier={'value': 12, 'provenance': 'declared'})).get_json()
    store.finish(first['id'], 'g1', reason='driver_operation_ended', status='interrupted')
    second = successor(app, reply['operation_id'], first['id'], 'g2', store)
    shown = app.client.get('/api/battles/active').get_json()['battle']
    assert shown['id'] == second['id']
    assert shown['attach'] == {'mode': 'current_battle', 'policy_revision': shown['attach']['policy_revision'],
                               'tier': {'value': 12, 'provenance': 'declared'}, 'stripped': ['tier'], 'predecessor_battle_id': first['id'],
                               'observed_wave': None, 'observed_wave_source': None}
    assert shown['lineage'] == {'predecessors': [{'id': first['id'], 'origin': 'adopted', 'status': 'interrupted', 'reason': 'driver_operation_ended'}],
                                'truncated': False}
    assert shown['origin'] == 'partial_adoption' and 'config' not in shown['envelope'] and 'profile' not in shown['envelope']
    old = app.client.get(f"/api/battles/{first['id']}").get_json()['battle']
    assert 'attach' not in old and 'lineage' not in old                                         # an ordinary battle's read is unchanged


def test_the_taken_over_wave_is_the_entry_gates_observation_or_unknown(app):
    collector, store, first = with_collector(app, wave=4200)
    reply = app.client.post('/api/control', json=body(app)).get_json()
    store.finish(first['id'], 'g1', reason='driver_operation_ended', status='interrupted')
    second = successor(app, reply['operation_id'], first['id'], 'g2', store)
    store.update_status(second['id'], 'g2', {'observed_wave': 4390})                                  # the battle's own later wave is never the answer
    shown = app.client.get('/api/battles/active').get_json()['battle']['attach']
    assert shown['observed_wave'] is None and shown['observed_wave_source'] is None                  # no entry observation recorded: unknown, never inferred
    store.update_status(second['id'], 'g2', {'attach_entry': {'observed_wave': 4388}})              # what the T3b worker records after its entry gate
    shown = app.client.get('/api/battles/active').get_json()['battle']['attach']
    assert shown['observed_wave'] == 4388 and shown['observed_wave_source'] == 'entry_gate'
    for bad in ({'observed_wave': '4388'}, {'observed_wave': True}, {'observed_wave': -1}, {'observed_wave': 4388.5}, 7):
        assert battle_lineage.entry_wave({'runtime': {'attach_entry': bad}}) is None, bad


def test_a_battle_adopted_outside_a_takeover_reads_exactly_as_before(app):
    collector, store, first = with_collector(app)
    store.finish(first['id'], 'g1', reason='driver_operation_ended', status='interrupted')
    ops_store = operations.ledger(app.dash.ROOT)
    ops_store.transition(collector['id'], ops_store.get(collector['id'])['revision'], 'succeeded')       # the collector is gone: the emulator is free
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    accepted, _ = ops_store.accept('run', context, {}, 'plain-run-1')
    run = ops_store.transition(accepted['id'], accepted['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    created = store.create(run['id'], run['context'], {'preset': {'label': 'Farm'}, 'controls': {}}, generation='g9', origin='partial_adoption', previous_battle_id=first['id'])
    shown = battles.public(store, created)
    assert shown['previous_battle_id'] == first['id'] and 'attach' not in shown and 'lineage' not in shown


def test_an_unlinked_adoption_has_an_attach_block_and_no_lineage(app):
    reply = app.client.post('/api/control', json=body(app)).get_json()
    store = battles.ledger(app.dash.ROOT)
    created = successor(app, reply['operation_id'], None, 'g1', store)
    shown = battles.public(store, created)
    assert shown['attach']['predecessor_battle_id'] is None and shown['attach']['observed_wave'] is None and shown['attach']['tier'] == {'value': None, 'provenance': 'unknown'}
    assert 'lineage' not in shown


def test_the_lineage_is_bounded_and_says_so(app):
    reply = app.client.post('/api/control', json=body(app)).get_json()
    store = battles.ledger(app.dash.ROOT)
    previous = None
    for index in range(battle_lineage.LINEAGE_LIMIT + 3):
        created = successor(app, reply['operation_id'], previous, f'g{index}', store)
        if index < battle_lineage.LINEAGE_LIMIT + 2:
            store.finish(created['id'], f'g{index}', reason='wave_went_backwards', status='ended')
        previous = created['id']
    shown = battle_lineage.lineage(store, store.get(previous))
    assert len(shown['predecessors']) == battle_lineage.LINEAGE_LIMIT and shown['truncated'] is True
    assert shown['predecessors'][0]['status'] == 'ended'


class Records:
    def __init__(self, *records):
        self.records = {record['id']: record for record in records}

    def get(self, battle_id):
        return self.records[battle_id]


def record(identity, previous=None, device='d1'):
    return {'id': identity, 'previous_battle_id': previous, 'context': {'device_key': device}, 'origin': 'adopted', 'status': 'ended', 'reason': None}


def test_lineage_ends_at_a_foreign_unknown_or_cyclic_record():
    assert battle_lineage.lineage(Records(), record('b')) is None
    foreign = Records(record('a', device='other'))
    assert battle_lineage.lineage(foreign, record('b', 'a')) == {'predecessors': [], 'truncated': False}
    assert battle_lineage.lineage(Records(), record('b', 'gone')) == {'predecessors': [], 'truncated': False}
    cycle = Records(record('a', 'b'), record('b', 'a'))
    assert [row['id'] for row in battle_lineage.lineage(cycle, cycle.get('b'))['predecessors']] == ['a']


def test_an_attach_block_that_no_longer_validates_is_reported_not_raised():
    assert battle_lineage.attach_view({'envelope': {}}) is None
    assert battle_lineage.attach_view({'envelope': {'attach': {'mode': 'current_battle'}}}) == {'invalid': 'attach_envelope_invalid'}


def test_the_takeover_modules_reach_no_device():
    code = ("import sys, control.takeover, control.battle_lineage, player.attach_policy;"
            "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in ('cv2', 'numpy') or m in "
            "('device.adbclient', 'device.capture', 'device.act', 'device.scrcpy'))))")
    out = subprocess.run([sys.executable, '-c', 'import json;' + code], cwd=ROOT, capture_output=True, text=True, check=True,
                         env={**__import__('os').environ, 'PYTHONPATH': str(ROOT / 'backend'), 'PYTHONDONTWRITEBYTECODE': '1'})
    assert json.loads(out.stdout) == []


def test_the_projection_reads_what_the_worker_writes_as_the_entry_record():
    """The worker's own record (T3b) is the one the projection reads; a drift in the key or shape would turn the wave unknown."""
    assert battle_lineage.entry_wave({'runtime': {battle_lineage.ENTRY_KEY: attach_worker.entry_record(120)}}) == 120
    assert battle_lineage.entry_wave({'runtime': {battle_lineage.ENTRY_KEY: attach_worker.entry_record(None)}}) is None


def test_admission_and_the_workers_entry_gate_share_one_wave_margin():
    import inspect
    assert takeover.WAVE_MARGIN == attach_worker.WAVE_MARGIN and 'WAVE_MARGIN =' not in inspect.getsource(takeover)     # imported, never redefined
