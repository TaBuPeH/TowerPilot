"""What the `runtime.actions` projection publishes for a generic reader (roadmap item 6, the A2-7 backend requests B2, B3, B4 and B13).

  B2   `values` carries the value in force of EVERY field of the descriptor (the ledger's applied controls), not only `enabled`; a field the
       controls do not hold is None (unknown), never zero; `enabled` stays the routine's own gate;
  B3   each item publishes `key`, the routine key that is both the control-path prefix and the Run now token;
  B4   `last` carries `command_id`: the Run now command that started the execution (None for an automatic one), so one read links a
       command to the execution it started - through the real collector, which hands the command id to its recorder;
  B13  whenever a publish builds the projection, `runtime.actions.observed_at` equals `runtime.server_time` (the staleness rule the UI relies on).
"""
from types import SimpleNamespace

import pytest

from flows.shard import GemWatch
from interactions.shopper import Shopper
from runtime import battle_session as battle_session_module
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from towerpilot_workflows.action_contract import CATALOG, FakeClock
from towerpilot_workflows.actions import Actions
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')


@pytest.fixture
def clock():
    return FakeClock(mono=100.0, wall=5000.0)


@pytest.fixture
def session(tmp_path, clock):
    return ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator', clock=clock)


def routine(**changes):
    return dict(dict(enabled=True, state='scheduled', next_due_at=5100.0, window_ends_at=None, counters={}), **changes)


def item(shown, key):
    return next(entry for entry in shown['items'] if entry['key'] == key)


# ------------------------------------------------------------------------------------------------ B2 values
def test_values_carry_the_value_in_force_of_every_descriptor_field(session):
    effective = {'ad_gems': dict(enabled=True, interval_sec=720, window_sec=60),
                 'floating_gems': dict(enabled=True, interval_sec=960, window_sec=60, tap_interval_sec=5)}
    shown = session.project({'ad_gems': routine(), 'floating_gems': routine()}, effective=effective)
    assert item(shown, 'ad_gems')['values'] == dict(enabled=True, interval_sec=720, window_sec=60)
    assert item(shown, 'floating_gems')['values'] == dict(enabled=True, interval_sec=960, window_sec=60, tap_interval_sec=5)
    for entry in shown['items']:
        assert set(entry['values']) == {field['path'] for field in entry['descriptor']['fields']}      # one value per field the descriptor lists


def test_a_field_the_controls_do_not_hold_is_unknown_not_zero(session):
    shown = session.project({'shopping': routine()}, effective={'shopping': dict(enabled=True)})
    assert item(shown, 'shopping')['values'] == dict(enabled=True, interval_sec=None)
    assert item(session.project({'shopping': routine()}), 'shopping')['values'] == dict(enabled=True, interval_sec=None)      # no controls at all
    assert item(session.project({'shopping': routine()}, effective={'shopping': 'not a mapping'}), 'shopping')['values']['interval_sec'] is None


def test_enabled_in_values_is_the_routines_own_gate_and_never_the_applied_value(session):
    shown = session.project({'ad_gems': routine(enabled=False, state='off')}, effective={'ad_gems': dict(enabled=True, interval_sec=720, window_sec=60)})
    entry = item(shown, 'ad_gems')
    assert entry['enabled'] is False and entry['values']['enabled'] is False and entry['values']['interval_sec'] == 720


def test_the_effective_values_of_another_routine_are_never_read(session):
    shown = session.project({'ad_gems': routine()}, effective={'floating_gems': dict(interval_sec=1)})
    assert item(shown, 'ad_gems')['values'] == dict(enabled=True, interval_sec=None, window_sec=None)


# ------------------------------------------------------------------------------------------------ B3 key
def test_each_item_publishes_its_routine_key(session):
    shown = session.project({'ad_gems': routine(), 'shopping': routine()})
    assert [(entry['key'], entry['id'], entry['instance']) for entry in shown['items']] == [('ad_gems', 'ad_gems', 'ad_gems'), ('shopping', 'shopping', 'shopping')]


def test_the_key_of_a_configured_instance_is_the_action_and_the_instance(session, monkeypatch):
    from towerpilot_workflows import action_contract
    from towerpilot_workflows.action_contract import ActionDefinition, Capabilities, Field, StartToStart, use_catalog
    catalog = CATALOG.copy()
    catalog.register(ActionDefinition(id='keyed_probe', label='Keyed probe', family='gems', hosts=frozenset({'orchestrator'}), schedule=StartToStart(),
                                      fields=(Field('enabled', 'bool', label='Enabled'), Field('interval_sec', 'number', 10, 600, 's', 'Interval')),
                                      capabilities=Capabilities(live_controls=True, pause=True, run_now=True, variants=False), saved=None, evidence=()))
    with use_catalog(catalog):
        shown = session.project({'keyed_probe@one': routine(), 'keyed_probe@two': routine()},
                                effective={'keyed_probe@one': dict(enabled=True, interval_sec=20), 'keyed_probe@two': dict(enabled=False, interval_sec=30), 'keyed_probe': dict(interval_sec=99)})
    assert [(entry['key'], entry['instance']) for entry in shown['items']] == [('keyed_probe@one', 'one'), ('keyed_probe@two', 'two')]
    # each instance reads ITS OWN controls (never the bare action id's); `enabled` is the routine's gate
    assert [entry['values'] for entry in shown['items']] == [dict(enabled=True, interval_sec=20), dict(enabled=True, interval_sec=30)]
    assert action_contract.catalog().find('keyed_probe') is None


# ------------------------------------------------------------------------------------------------ B4 last.command_id
def test_last_names_the_command_that_started_the_execution(session, clock):
    execution = session.begin('ad_gems', trigger='manual', command_id='cmd-1', occurrence_key='window:one')
    clock.advance(4)
    session.finish(execution, result='succeeded', reason='offer_disappeared', effect='confirmed', attempts=1, confirmed=1, evidence=['offer_disappeared'])
    last = item(session.project({'ad_gems': routine()}), 'ad_gems')['last']
    assert (last['execution_id'], last['command_id'], last['trigger']) == (execution, 'cmd-1', 'manual')


def test_an_automatic_execution_has_no_command(session):
    execution = session.begin('ad_gems', occurrence_key='window:auto')
    session.finish(execution, result='succeeded', reason='offer_disappeared', effect='confirmed')
    assert session.last['ad_gems']['command_id'] is None and session.last['ad_gems']['trigger'] == 'automatic'


def test_a_terminal_row_written_in_one_step_keeps_its_command_too(session):
    row = session.record('shopping', result='succeeded', reason='sweep_done', effect='confirmed', trigger='manual', command_id='cmd-2', occurrence_key='sweep:one')
    assert row['command_id'] == 'cmd-2' and session.last['shopping']['command_id'] == 'cmd-2' and session.last['shopping']['trigger'] == 'manual'


def test_a_check_has_no_command(session):
    session.check('floating_gems', result='no_op', reason='no_orbit_seen')
    assert session.last['floating_gems']['command_id'] is None


# ------------------------------------------------------------------------------------------ through a real driver
@pytest.mark.parametrize('shopping,automation,expected', [
    pytest.param(None, {}, False, id='no-plan'),
    pytest.param([], {}, False, id='empty-legacy'),
    pytest.param([{'tab': 'attack', 'stats': ['damage'], 'mode': 'once'}], {}, True, id='legacy'),
    pytest.param({'version': 2, 'enabled': True, 'queue': [{'id': 'damage'}]}, {}, True, id='plan-enabled'),
    pytest.param({'version': 2, 'enabled': False, 'queue': [{'id': 'damage'}]}, {}, False, id='plan-disabled'),
    pytest.param({'version': 2, 'enabled': True, 'queue': [{'id': 'damage'}]}, {'shopping': False}, False, id='routine-disabled'),
    pytest.param(None, {'shopping': True}, False, id='no-plan-routine-enabled'),
])
def test_contract_driver_projects_the_resolved_shopping_gate(dash, tmp_path, monkeypatch, shopping, automation, expected):
    """The fixture driver must initialize gates like RunState, not invent automatic shopping when no plan exists."""
    from dashboard_contract_cases_action_projection import attach
    from dashboard_contract_pages_world import PagesGenerator
    from player.run_controls import resolve
    from test_dashboard_contract import Driver, with_shopping

    gen = PagesGenerator(dash, tmp_path, monkeypatch)
    with gen.world('shopping-gate') as w:
        if shopping is not None:
            w.edit_profile(with_shopping(shopping))
        w.edit_profile(lambda profile: profile['blueprints']['farm'].update(automation=automation))
        host = Driver(w, 'resolved-gates', origin='started')
        attach(host)
        host.go_live()
        resolved = resolve(host.session.base_preset, host.session.config)['routines']
        for key in ('shopping', 'rewards', 'repeat'):
            assert getattr(host.rs, f'{key}_enabled') is resolved[key]['enabled']
        response = w.client.get('/api/battles/active').get_json()['battle']
        shown = item(response['runtime']['actions'], 'shopping')
        assert shown['enabled'] is shown['values']['enabled'] is expected
        assert response['runtime']['routines']['shopping']['enabled'] is expected
        assert response['effective']['routines']['shopping']['enabled'] is expected
        assert (shown['state'] == 'disabled') is (not expected)
        if isinstance(shopping, dict):
            # A paused routine can retain an enabled plan for a manual request.
            assert host.rs.shop.plan['enabled'] is shopping['enabled']
        if shopping is None or shopping == []:
            assert host.rs.shop.plan is None and host.rs.shop.directives == []
            assert host.rs.shop.request_once() is False


@pytest.fixture
def driver(tmp_path, monkeypatch, clock):
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda *args, **kwargs: None)
    monkeypatch.setattr(battle_session_module, 'time', SimpleNamespace(monotonic=clock.mono, time=clock.wall))
    apart = FakeClock(mono=100.0, wall=9000.0)                        # the action session's own clock is NOT the driver's: only the publish's epoch may stamp the projection
    path = tmp_path / 'operations.db'
    operation, _ = Operations(path).accept('farm', dict(CONTEXT), {})
    body = dict(gather={'ad_gems': True, 'quests_8h': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
    battle = BattleSession(Battles(path), operation['id'], body, {})
    actions = ActionSession.for_battle(battle, 'orchestrator')
    actions.clock = apart
    rs = SimpleNamespace(gems=GemWatch(body['gather'], session=actions), shop=Shopper(body), mission=SimpleNamespace(active=False),
                         shopping_enabled=False, rewards_enabled=True, repeat_enabled=True, tracker=SimpleNamespace(last=123), last_shop=0)
    battle.observe(rs, 123)
    return SimpleNamespace(battle=battle, rs=rs, operation=operation, actions=actions, clock=clock, apart=apart)


def published(h):
    h.clock.advance(2)
    h.apart.advance(2)
    h.battle.publish(h.rs, battle_visible=True)
    return h.battle.store.get(h.battle.battle['id'])['runtime']


def test_values_follow_the_controls_in_force_through_a_real_publish(driver):
    first = published(driver)
    ad = item(first['actions'], 'ad_gems')
    assert ad['values']['interval_sec'] == driver.battle.battle['applied_controls']['routines']['ad_gems']['interval_sec'] is not None
    driver.battle.store.request_controls(driver.battle.battle['id'], driver.operation['id'], driver.battle.generation, 0,
                                         {'routines': {'ad_gems': {'interval_sec': 900}}}, 'slower')
    driver.clock.advance(2)
    driver.battle.checkpoint(driver.rs, battle_visible=True)
    assert driver.battle.store.get(driver.battle.battle['id'])['applied_controls']['routines']['ad_gems']['interval_sec'] == 900
    assert item(published(driver)['actions'], 'ad_gems')['values']['interval_sec'] == 900


def test_a_control_still_waiting_is_not_yet_in_force(driver):
    """The values are the controls APPLIED, not the ones requested: a request the checkpoint has not installed changes nothing."""
    driver.battle.store.request_controls(driver.battle.battle['id'], driver.operation['id'], driver.battle.generation, 0,
                                         {'routines': {'ad_gems': {'interval_sec': 900}}}, 'later')
    before = item(published(driver)['actions'], 'ad_gems')['values']['interval_sec']
    assert before != 900


def test_run_now_links_its_command_to_the_execution_the_collector_recorded(driver, monkeypatch):
    from interactions import ad_gems
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: None)
    store, battle_id = driver.battle.store, driver.battle.battle['id']
    command = store.request_action(battle_id, driver.operation['id'], driver.battle.generation, 'ad_gems', 'once')
    driver.battle.checkpoint(driver.rs, battle_visible=True)             # the binding takes the request and hands the command id to the collector
    assert driver.rs.gems.ad_gems.manual_command_id == command['id']
    driver.rs.gems.ad_gems.poll(None)                                    # the collector's own poll opens the window: its recorder gets the command
    driver.battle.checkpoint(driver.rs, battle_visible=True)
    driver.rs.gems.ad_gems._close_window('timeout')
    driver.battle.checkpoint(driver.rs, battle_visible=True)
    runtime = published(driver)
    done = next(entry for entry in store.commands(battle_id) if entry['id'] == command['id'])
    last = item(runtime['actions'], 'ad_gems')['last']
    assert done['state'] == 'completed' and last['command_id'] == command['id'] and last['trigger'] == 'manual'
    row = Actions.read(driver.actions.path, device='device', battle_id=battle_id)[0][0]
    assert row['id'] == last['execution_id'] and row['command_id'] == command['id']


# ------------------------------------------------------------------------------------------------ B13 staleness stamp
def test_a_publish_that_builds_the_projection_stamps_it_with_the_epoch_of_the_routines(driver):
    for _ in range(3):
        runtime = published(driver)
        assert runtime['actions']['observed_at'] == runtime['server_time']
        assert {state['server_time'] for state in runtime['routines'].values()} == {runtime['server_time']}


def test_a_publish_that_cannot_build_the_projection_leaves_the_last_stamp_behind(driver, monkeypatch):
    first = published(driver)
    monkeypatch.setattr(driver.actions, 'project', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('cannot be built')))
    second = published(driver)
    assert second['actions']['observed_at'] == first['actions']['observed_at'] < second['server_time']      # the staleness the UI detects by comparing the two
