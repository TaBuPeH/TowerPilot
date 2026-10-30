"""Public command rows carry their exact accepted request identities.

The client correlates a refused retry or an unknown outcome only through the
idempotency keys the ledger recorded for a command (battle_commands.request_key),
never by revision or equal payload. Real temporary SQLite, no device.
"""
import json
import uuid

import pytest

from control import battles as battle_api
from player.run_controls import resolve
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

DISABLE = {'routines': {'ad_gems': {'enabled': False}}}


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / 'operations.sqlite'
    ops, store = Operations(path), Battles(path)
    context = dict(device_key='device', account='account', instance='main', serial='fake')
    operation, _ = ops.accept('run', context, {}, 'launch')
    ops.transition(operation['id'], operation['revision'], 'running')
    envelope = dict(preset={'gather': {'ad_gems': True}}, controls=resolve({'gather': {'ad_gems': True}}, {}))

    def battle(generation='driver', key=None):
        created = store.create(operation['id'], context, envelope, generation=generation,
                               idempotency_key=key or str(uuid.uuid4()))
        store.activate(created['id'], generation, dict(controls=['routines.ad_gems.enabled'], actions=['ad_gems']))
        return created
    return store, operation, battle


def keys(store, battle_id):
    return {command['id']: command['request_keys'] for command in store.commands(battle_id)}


def test_controls_command_lists_its_own_request_key(ledger):
    store, op, make = ledger
    battle = make()
    first = store.request_controls(battle['id'], op['id'], 'driver', 0, DISABLE, 'client-a-1')
    assert keys(store, battle['id']) == {first['id']: ['client-a-1']}
    store.acknowledge(battle['id'], 'driver', first['id'])
    # another client's identical edit is a different command with its own key
    second = store.request_controls(battle['id'], op['id'], 'driver', 1, DISABLE, 'client-b-1')
    assert keys(store, battle['id']) == {first['id']: ['client-a-1'], second['id']: ['client-b-1']}


def test_action_aliases_stay_correlated_through_acknowledge_and_finish(ledger):
    store, op, make = ledger
    battle = make()
    first = store.request_action(battle['id'], op['id'], 'driver', 'ad_gems', 'client-a', expected_revision=0)
    alias = store.request_action(battle['id'], op['id'], 'driver', 'ad_gems', 'client-b', expected_revision=0)
    assert alias['id'] == first['id']
    assert keys(store, battle['id']) == {first['id']: ['client-a', 'client-b']}
    store.acknowledge(battle['id'], 'driver', first['id'], state='started')
    rows = store.commands(battle['id'])
    assert [(row['state'], row['request_keys']) for row in rows] == [('started', ['client-a', 'client-b'])]
    store.finish(battle['id'], 'driver', reason='observed_results')
    rows = store.commands(battle['id'])
    assert [(row['state'], row['request_keys']) for row in rows] == [('expired', ['client-a', 'client-b'])]


def test_identical_payload_after_completion_is_a_new_command(ledger):
    store, op, make = ledger
    battle = make()
    first = store.request_action(battle['id'], op['id'], 'driver', 'ad_gems', 'client-a', expected_revision=0)
    store.acknowledge(battle['id'], 'driver', first['id'], state='started')
    store.acknowledge(battle['id'], 'driver', first['id'], state='completed', evidence={'confirmed': True})
    second = store.request_action(battle['id'], op['id'], 'driver', 'ad_gems', 'client-b', expected_revision=0)
    assert second['id'] != first['id']
    assert keys(store, battle['id']) == {first['id']: ['client-a'], second['id']: ['client-b']}


def test_rows_from_other_battles_or_generations_never_mix_in(ledger):
    store, op, make = ledger
    battle = make()
    command = store.request_action(battle['id'], op['id'], 'driver', 'ad_gems', 'own-key', expected_revision=0)
    store.finish(battle['id'], 'driver', reason='observed_results')
    later = make(generation='next-driver')
    other = store.request_action(later['id'], op['id'], 'next-driver', 'ad_gems', 'later-key', expected_revision=0)
    with store._db() as db:
        # rows filed under this battle but describing the same command id from
        # another generation (filed FIRST, so recording order cannot decide),
        # and one describing another battle: all foreign
        db.execute('UPDATE battle_commands SET rowid=-1 WHERE request_key=?', ('own-key',))
        db.execute('INSERT INTO battle_commands(rowid,id,battle_id,request_key,fingerprint,body) VALUES (-2,?,?,?,?,?)',
                   (str(uuid.uuid4()), battle['id'], 'first-foreign', 'x', json.dumps(dict(command, operation_id='other-op'))))
        db.execute('INSERT INTO battle_commands VALUES (?,?,?,?,?)',
                   (str(uuid.uuid4()), battle['id'], 'foreign-generation', 'x',
                    json.dumps(dict(command, generation='someone-else'))))
        db.execute('INSERT INTO battle_commands VALUES (?,?,?,?,?)',
                   (str(uuid.uuid4()), battle['id'], 'foreign-battle', 'x', json.dumps(other)))
    assert keys(store, battle['id']) == {command['id']: ['own-key']}
    assert keys(store, later['id']) == {other['id']: ['later-key']}


def test_public_battle_rows_expose_request_keys(ledger):
    store, op, make = ledger
    battle = make()
    store.request_action(battle['id'], op['id'], 'driver', 'ad_gems', 'client-a', expected_revision=0)
    public = battle_api.public(store, store.get(battle['id']))
    assert [row['request_keys'] for row in public['commands']] == [['client-a']]
    assert {'battle_id', 'operation_id', 'generation'} <= set(public['commands'][0])
