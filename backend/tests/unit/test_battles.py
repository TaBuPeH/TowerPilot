"""Offline battle ledger tests: real temporary SQLite, no device actions."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from player.run_controls import apply_to_preset, merge_patch, resolve, validate_patch
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Conflict, Operations


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'operations.sqlite'
    ops, store = Operations(path), Battles(path)
    context = dict(device_key='device', account='account', instance='main', serial='fake')
    operation, _ = ops.accept('run', context, {}, 'launch')
    preset = dict(gather={'ad_gems': True}, shopping=[{'tab': 'attack'}], shop_interval_sec=60)
    controls = resolve(preset, {})
    envelope = dict(preset=preset, controls=controls, source_revision='frozen')
    battle = store.create(operation['id'], context, envelope, generation='driver', idempotency_key='battle')
    capabilities = dict(controls=['routines.ad_gems.enabled', 'routines.ad_gems.interval_sec',
                                  'routines.rewards.enabled', 'shopping_plan'],
                        actions=['ad_gems', 'shopping:attack'])
    store.activate(battle['id'], 'driver', capabilities)
    return store, operation, store.get(battle['id']), envelope


def request(setup, patch, key='control', revision=0):
    store, operation, battle, _ = setup
    return store.request_controls(battle['id'], operation['id'], 'driver', revision, patch, key)


def test_envelope_and_returns_are_independent_persistent_copies(setup):
    store, _, battle, original = setup
    expected = copy.deepcopy(original)
    original['preset']['gather']['ad_gems'] = False
    battle['envelope']['controls']['routines']['ad_gems']['enabled'] = False
    persisted = Battles(store.path).get(battle['id'])
    assert persisted['envelope'] == expected
    assert persisted['outcome'] == 'unknown'
    assert persisted['status'] == 'active'


def test_partial_acknowledgements_distinguish_requested_applied(setup):
    store, _, battle, _ = setup
    command = request(setup, {'routines': {'ad_gems': {'enabled': False}, 'rewards': {'enabled': False}}})
    requested = store.get(battle['id'])
    assert requested['requested_controls']['routines']['ad_gems']['enabled'] is False
    assert requested['applied_controls']['routines']['ad_gems']['enabled'] is True
    ack = store.acknowledge(battle['id'], 'driver', command['id'], applied_paths=['routines.ad_gems.enabled'], reason='finishing_owned_flow')
    assert ack['state'] == 'pending'
    partial = store.get(battle['id'])
    assert partial['applied_revision'] == 0
    assert partial['applied_controls']['routines']['ad_gems']['enabled'] is False
    assert partial['envelope']['controls']['routines']['ad_gems']['enabled'] is True
    with pytest.raises(Conflict, match='pending'):
        request(setup, {'routines': {'ad_gems': {'enabled': True}}}, key='next', revision=1)
    store.acknowledge(battle['id'], 'driver', command['id'], applied_paths=['routines.rewards.enabled'])
    assert store.get(battle['id'])['applied_revision'] == 1
    assert not store.pending_commands(battle['id'], 'driver')


def test_null_inherits_frozen_base_and_saved_preset_is_untouched(setup):
    store, _, battle, original = setup
    command = request(setup, {'routines': {'ad_gems': {'enabled': False}}})
    store.acknowledge(battle['id'], 'driver', command['id'])
    command = request(setup, {'routines': {'ad_gems': {'enabled': None}}}, 'reset', 1)
    store.acknowledge(battle['id'], 'driver', command['id'])
    assert store.get(battle['id'])['applied_controls'] == original['controls']
    assert original['preset']['gather']['ad_gems'] is True


def test_idempotency_and_replay_conflicts(setup):
    patch = {'routines': {'ad_gems': {'enabled': False}}}
    first = request(setup, patch)
    assert request(setup, patch) == first
    with pytest.raises(Conflict, match='Idempotency'):
        request(setup, {'routines': {'ad_gems': {'enabled': True}}})
    with pytest.raises(Conflict, match='revision'):
        request(setup, patch, 'stale')


@pytest.mark.parametrize('patch', [
    {'routines': {'ad_gems': {'enabled': 1}}},
    {'routines': {'ad_gems': {'interval_sec': True}}},
    {'routines': {'ad_gems': {'interval_sec': float('nan')}}},
    {'routines': {'ad_gems': {'window_sec': 61}}},
    {'routines': {'ad_gems': {'interval_sec': 0}}},
    {'routines': {'ad_gems': {'unrecognized': False}}},
    {'routines': {'nonsense': {'enabled': False}}}, {},
    {'tier': 1}, {'shopping_plan': {'version': 1}},
])
def test_invalid_typed_patches_are_rejected_atomically(setup, patch):
    with pytest.raises(ValueError):
        request(setup, patch)
    store, _, battle, _ = setup
    assert store.get(battle['id'])['control_revision'] == 0


def test_unknown_gates_remain_unavailable_until_driver_support(setup):
    with pytest.raises(ValueError, match='Unsupported'):
        request(setup, {'routines': {'abilities': {'enabled': False}}})


def test_scope_operation_and_generation_are_fenced(setup):
    store, operation, battle, _ = setup
    patch = {'routines': {'ad_gems': {'enabled': False}}}
    for operation_id, generation in [('other', 'driver'), (operation['id'], 'stale')]:
        with pytest.raises(Conflict):
            store.request_controls(battle['id'], operation_id, generation, 0, patch, 'bad')
    ops = Operations(store.path)
    ops.transition(operation['id'], operation['revision'], 'interrupted')
    with pytest.raises(Conflict, match='not active'):
        request(setup, patch)


def test_manual_commands_coalesce_and_delivery_is_not_outcome(setup):
    store, operation, battle, _ = setup
    args = battle['id'], operation['id'], 'driver', 'ad_gems'
    command = store.request_action(*args, 'once')
    assert store.request_action(*args, 'duplicate')['id'] == command['id']
    assert len(store.pending_commands(battle['id'], 'driver')) == 1
    with pytest.raises(Conflict):
        store.request_action(battle['id'], operation['id'], 'driver', 'shopping:attack', 'conflict')
    with pytest.raises(Conflict, match='start'):
        store.acknowledge(battle['id'], 'driver', command['id'], state='completed')
    store.acknowledge(battle['id'], 'driver', command['id'], state='started')
    store.acknowledge(battle['id'], 'driver', command['id'], state='completed', evidence={'confirmed_claim': False})
    replay = store.request_action(*args, 'duplicate')
    assert replay['state'] == 'completed'
    assert replay['evidence']['confirmed_claim'] is False
    assert store.get(battle['id'])['outcome'] == 'unknown'


def test_finish_is_idempotent_expires_commands_preserves_stats_failures(setup):
    store, _, battle, _ = setup
    request(setup, {'routines': {'ad_gems': {'enabled': False}}})
    first = store.finish(battle['id'], 'driver', reason='wave_regression')
    assert first == store.finish(battle['id'], 'driver', reason='wave_regression')
    assert first['outcome'] == 'unknown'
    assert store.active() == []
    store.update_status(battle['id'], 'driver', {'stats_capture': {'status': 'failed', 'reason': 'clipboard_stale'}})
    assert store.get(battle['id'])['status'] == 'ended'
    assert [e['kind'] for e in store.events(battle['id'])].count('ended') == 1
    assert any(e['kind'] == 'command_expired' for e in store.history(battle['id']))
    with pytest.raises(Conflict):
        store.finish(battle['id'], 'driver', reason='death', outcome='death')
    with pytest.raises(Conflict, match='ended'):
        request(setup, {'routines': {'ad_gems': {'enabled': True}}}, 'late', 1)


def test_interrupted_partial_adoption_links_new_record_without_guessing_continuity(setup):
    store, operation, battle, envelope = setup
    store.finish(battle['id'], 'driver', reason='process_lost', status='interrupted')
    new = store.create(operation['id'], battle['context'], envelope, generation='new-driver',
                       origin='partial_adoption', previous_battle_id=battle['id'])
    assert new['id'] != battle['id']
    assert new['previous_battle_id'] == battle['id']
    assert new['outcome'] == 'unknown'
    assert new['control_revision'] == 0
    assert new['overrides'] == {}
    with pytest.raises(Conflict):
        store.update_status(new['id'], 'driver', {})


def test_concurrent_control_acceptance_has_one_winner(setup):
    def accept(key):
        try:
            return request(setup, {'routines': {'ad_gems': {'enabled': False}}}, key)
        except Conflict:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(accept, ['one', 'two']))
    assert sum(r is not None for r in results) == 1


def test_plan_patch_is_validated_and_applied_as_one_field(setup):
    from player.shopping_plan import default_plan
    store, _, battle, _ = setup
    plan = default_plan()
    command = request(setup, {'shopping_plan': plan})
    assert command['payload'] == {'shopping_plan': plan}
    store.acknowledge(battle['id'], 'driver', command['id'], applied_paths=['shopping_plan'])
    assert store.get(battle['id'])['applied_controls']['shopping_plan'] == plan


def test_pure_alias_mapping_isolated_and_numeric_values_validated():
    preset = {'gather': {'ad_gems': True, 'guild': True}, 'global_behaviors': {'daily_missions': True}, 'shopping': [1]}
    saved = copy.deepcopy(preset)
    controls = resolve(preset, {'repeat': False})
    patch = validate_patch({'routines': {'ad_gems': {'interval_sec': 800}, 'rewards': {'enabled': False}}})
    effective = merge_patch(controls, patch)
    out = apply_to_preset(preset, effective)
    assert out['gather']['ad_gem_options']['confirmed_cooldown_sec'] == [800, 800]
    assert out['gather']['guild'] is False
    assert out['global_behaviors']['daily_missions'] is False
    assert preset == saved
    assert controls['routines']['repeat']['enabled'] is False


def test_history_context_and_status_filters_precede_pagination_and_survive_reload(setup):
    store, operation, battle, envelope = setup
    operations = Operations(store.path)
    store.finish(battle['id'], 'driver', reason='observed_end', outcome='death')
    cancelled = operations.transition(operation['id'], operation['revision'], 'cancelling')
    operations.transition(cancelled['id'], cancelled['revision'], 'cancelled')
    original = battle['context']
    for index, changes in enumerate(({'account': 'other'}, {'serial': 'other'}, {'instance': 'other'}, {'device_key': 'other'}, {})):
        context = {**original, **changes}
        operation, _ = operations.accept('run', context, {})
        row = store.create(operation['id'], context, envelope, generation=f'driver-{index}')
        store.finish(row['id'], row['generation'], reason='observed_end', outcome='death')
        cancelled = operations.transition(operation['id'], operation['revision'], 'cancelling')
        operations.transition(cancelled['id'], cancelled['revision'], 'cancelled')
    reloaded = Battles(store.path)
    first = reloaded.list(limit=1, offset=0, device='device', status='ended', context=original)
    second = reloaded.list(limit=1, offset=1, device='device', status='ended', context=original)
    assert first[0]['id'] == row['id']
    assert second[0]['id'] == battle['id']
    assert reloaded.list(limit=1, offset=2, device='device', status='ended', context=original) == []
    assert reloaded.list(device='device', status='active', context=original) == []


@pytest.mark.parametrize('filters', [dict(status='succeeded'), dict(context={'unknown': 'x'}),
                                    dict(context=[]), dict(context={'account': 0})])
def test_history_rejects_invalid_filters(setup, filters):
    store, *_ = setup
    with pytest.raises(ValueError):
        store.list(**filters)
