"""Ownership parity (roadmap item 6): the action history never makes a second input owner and never lets one
generation, device or account close another's record."""
import pytest

from control import harvest, operations
from harvest_support import NOW, choose, make_env
from runtime.action_session import ActionSession
from towerpilot_workflows.actions import Actions, new_record
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Conflict, Operations

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')


def rows(path, device='device'):
    return Actions.read(str(path), device=device)[0]


def test_a_held_device_makes_the_attempt_a_recorded_device_owned_refusal_and_starts_no_worker(tmp_path, monkeypatch):
    h = make_env(tmp_path, monkeypatch)
    choose(h)

    def owned(cfg, body, command):
        raise Conflict('Another automation is using this emulator.')
    session = harvest.action_session(h.root, h.cfg)
    assert h.tick(NOW, actions=session, launch=owned)['action'] == 'refused'
    (row,) = rows(operations.ledger_path(h.root), harvest.identity_for(h.cfg)['device_key'])
    assert (row['state'], row['outcome']['reason'], row['host']['operation_id']) == ('refused', 'device_owned', None)
    assert h.spawned == []                                              # the refusal is history; it started no second owner


def test_a_sessions_settlement_never_touches_another_devices_or_a_live_hosts_rows(tmp_path):
    path = tmp_path / 'operations.db'
    ops, battles = Operations(path), Battles(path)
    other = dict(CONTEXT, device_key='other-device', serial='fake:2')
    first, _ = ops.accept('farm', CONTEXT, {}, 'k1')
    second, _ = ops.accept('farm', other, {}, 'k2')
    mine = ActionSession(path, context=CONTEXT, driver='orchestrator', operation_id=first['id'])
    theirs = ActionSession(path, context=other, driver='orchestrator', operation_id=second['id'])
    a, b = mine.begin('ad_gems'), theirs.begin('ad_gems')
    assert a and b
    ops.transition(first['id'], ops.get(first['id'])['revision'], 'interrupted')
    ops.transition(second['id'], ops.get(second['id'])['revision'], 'interrupted')
    assert theirs.settle() == [b]                                       # its own device only; the first device's row is untouched
    assert {row['id']: row['state'] for row in rows(path, 'device')} == {a: 'running'}
    assert mine.settle() == [a]


def test_a_row_of_a_live_generation_is_not_settled_by_a_replaced_one(tmp_path):
    path = tmp_path / 'operations.db'
    ops, battles = Operations(path), Battles(path)
    operation, _ = ops.accept('farm', CONTEXT, {}, 'k1')
    envelope = {'preset': {}, 'config': {}, 'controls': {'routines': {}}}
    battle = battles.create(operation['id'], CONTEXT, envelope, generation='g-live')
    store = Actions(path)
    live = store.begin(new_record(device='device', action='ad_gems', instance='ad_gems', trigger='automatic',
                                  host=dict(operation_id=operation['id'], battle_id=battle['id'], generation='g-live')))
    stale = store.begin(new_record(device='device', action='floating_gems', instance='floating_gems', trigger='automatic',
                                   host=dict(operation_id=operation['id'], battle_id=battle['id'], generation='g-old')))
    assert store.settle_open() == [stale['id']]
    states = {row['id']: row['state'] for row in rows(path)}
    assert states[live['id']] == 'running' and states[stale['id']] == 'interrupted'
    store.finish(stale['id'], result='succeeded', effect='confirmed')   # a stale writer cannot revive the settled row
    assert {row['id']: row['state'] for row in rows(path)}[stale['id']] == 'interrupted'


def test_two_dashboard_hosts_launching_the_same_key_make_one_row(tmp_path):
    path = tmp_path / 'operations.db'
    sessions = [ActionSession(path, context=CONTEXT, driver='dashboard') for _ in range(2)]
    first = sessions[0].record('harvest_launch', result='succeeded', effect='confirmed', request_key='harvest-device-1-1')
    second = sessions[1].record('harvest_launch', result='succeeded', effect='confirmed', request_key='harvest-device-1-1')
    assert first['id'] == second['id'] and len(rows(path)) == 1


def test_recording_grants_no_authority_the_session_has_no_way_to_call_a_handler_or_send_input():
    public = {name for name in dir(ActionSession) if not name.startswith('_')}
    assert public == {'begin', 'check', 'finish', 'for_battle', 'for_operation', 'project', 'record', 'recorder', 'settle'}