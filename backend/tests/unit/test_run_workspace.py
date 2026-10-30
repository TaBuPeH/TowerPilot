"""Saved run edits isolate shared policies and compile to real runtime settings."""
import copy

import pytest

from control import run_workspace
from player import playerprofile, shopping_plan


@pytest.mark.parametrize('kind', ['shard', 'quest'])
def test_saved_capabilities_do_not_advertise_unsupported_runtime_gates(kind):
    profile = {'blueprints': {'special': {'kind': kind}}}
    caps = run_workspace._capabilities(profile['blueprints']['special'])
    assert 'routines.ad_gems.enabled' in caps['controls']
    for routine in ('abilities', 'rescue', 'repeat', 'rewards', 'shopping'):
        path = f'routines.{routine}.enabled'
        assert path not in caps['controls']
        with pytest.raises(ValueError, match='unavailable'):
            run_workspace.update(profile, {}, 'special', {path: False})


@pytest.fixture
def profile():
    return {'player': {}, 'blueprints': {
        'farm': {'kind': 'coin', 'loadout': 'as_is', 'tier': 1,
                 'policies': {'gather': 'shared'}, 'shopping': 'shared'},
        'other': {'kind': 'coin', 'loadout': 'as_is', 'tier': 1,
                  'policies': {'gather': 'shared'}, 'shopping': 'shared'}},
        'policies': {'gather': {'shared': {'ad_gems': True,
                     'gem_delay_sec': [3, 10], 'quests_8h': True,
                     'ad_gem_options': {'poll_sec': [2, 4], 'edge_inset_px': 8},
                     'gem_orbit': {'enabled': True, 'radius': 5, 'taps': 4}}},
                     'shopping_lists': {'shared': {'enabled': True, 'directives': [
                         {'tab': 'attack', 'stats': ['damage'], 'mode': 'repeat'}]}},
                     'global_behaviors': {'free_store_gems': True}}}


def test_read_keeps_legacy_execution_and_offers_explicit_conversion(profile):
    original = copy.deepcopy(profile)
    result = run_workspace.read(profile, {'repeat': False}, 'farm')
    assert result['settings']['shopping_plan'] is None
    assert result['settings']['routines']['repeat']['enabled'] is False
    assert result['legacy_conversion']['requires_review'] is True
    assert result['legacy_conversion']['plan']['cash_reserve'] is None
    assert playerprofile.compile_preset(profile, 'farm')['shopping']
    assert profile == original


def test_gather_numeric_changes_clone_shared_policy_and_preserve_unrelated_values(profile):
    original = copy.deepcopy(profile)
    result = run_workspace.update(profile, {}, 'farm', {
        'routines.ad_gems.interval_sec': 60, 'routines.ad_gems.window_sec': 20,
        'routines.floating_gems.tap_interval_sec': 3})
    updated = result['profile']
    ref = updated['blueprints']['farm']['policies']['gather']
    assert ref != 'shared'
    gather = updated['policies']['gather'][ref]
    assert gather['ad_gem_options']['confirmed_cooldown_sec'] == [60, 60]
    assert gather['ad_gem_options']['poll_sec'] == [2, 4]
    assert gather['ad_gem_options']['edge_inset_px'] == 8
    assert gather['gem_orbit']['radius'] == 5
    assert gather['gem_orbit']['retry_interval_sec'] == 3
    assert updated['blueprints']['other'] == original['blueprints']['other']
    assert updated['policies']['gather']['shared'] == original['policies']['gather']['shared']
    assert profile == original
    assert result['settings']['routines']['ad_gems']['interval_sec'] == 60


def test_repeat_updates_only_run_gate_not_machine_or_other_runs(profile):
    cfg = {'repeat': True}
    result = run_workspace.update(profile, cfg, 'farm', {'routines': {'repeat': {'enabled': False}, 'rescue': {'enabled': False}}})
    assert cfg == {'repeat': True}
    assert result['settings']['routines']['repeat']['enabled'] is False
    assert run_workspace.read(result['profile'], cfg, 'other')['settings']['routines']['repeat']['enabled'] is True
    assert playerprofile.compile_preset(result['profile'], 'farm')['automation'] == {'repeat': False, 'rescue': False}


def test_reward_gate_retains_subtasks_for_reenable(profile):
    disabled = run_workspace.update(profile, {}, 'farm', {'routines.rewards.enabled': False})
    assert disabled['settings']['routines']['rewards']['enabled'] is False
    enabled = run_workspace.update(disabled['profile'], {}, 'farm', {'routines.rewards.enabled': True})
    assert enabled['settings']['routines']['rewards']['enabled'] is True
    assert enabled['profile']['policies'] == profile['policies']


def test_explicit_queue_conversion_is_run_owned_and_compiles_without_legacy_actions(profile):
    plan = run_workspace.read(profile, {}, 'farm')['legacy_conversion']['plan']
    plan['cash_reserve'] = 50
    result = run_workspace.update(profile, {}, 'farm', {'shopping_plan': plan})
    updated = result['profile']
    assert updated['blueprints']['farm']['shopping'] != 'shared'
    assert updated['blueprints']['other']['shopping'] == 'shared'
    assert updated['policies']['shopping_lists']['shared'] == profile['policies']['shopping_lists']['shared']
    compiled = playerprofile.compile_preset(updated, 'farm')
    assert compiled['shopping'] == []
    assert compiled['shopping_plan'] == shopping_plan.normalize_plan(plan)
    assert 'legacy_conversion' not in result
    assert playerprofile.validate(updated) == []


def test_repeated_queue_updates_reuse_owned_key_without_accumulating_policies(profile):
    plan = shopping_plan.default_plan()
    first = run_workspace.update(profile, {}, 'farm', {'shopping_plan': plan})
    second = run_workspace.update(first['profile'], {}, 'farm', {'routines.shopping.interval_sec': 45.5})
    assert len(second['profile']['policies']['shopping_lists']) == 2
    assert second['settings']['routines']['shopping']['interval_sec'] == 45.5
    assert playerprofile.validate(second['profile']) == []


def test_sustained_cadence_roundtrips_saved_profile_without_changing_interval_or_reserve(profile, tmp_path):
    import yaml
    plan = shopping_plan.normalize_plan(dict(version=2, interval_sec=90, cash_reserve=50,
                                            queue=[{'id': 'health'}]))
    original = run_workspace.update(profile, {}, 'farm', {'shopping_plan': plan})
    plan['cadence'] = 'while_affordable'
    updated = run_workspace.update(original['profile'], {}, 'farm', {'shopping_plan': plan})
    target = tmp_path / 'synthetic-profile.yaml'
    target.write_text(yaml.safe_dump(updated['profile']), encoding='utf-8')
    restored = yaml.safe_load(target.read_text(encoding='utf-8'))
    saved = run_workspace.read(restored, {}, 'farm')['settings']['shopping_plan']
    assert saved['cadence'] == 'while_affordable'
    assert saved['interval_sec'] == 90 and saved['cash_reserve'] == 50
    assert restored['policies']['shopping_lists']['shared'] == profile['policies']['shopping_lists']['shared']
    assert playerprofile.compile_preset(restored, 'farm')['shopping_plan'] == saved


def test_owned_policy_name_collision_never_overwrites_unrelated_entry(profile):
    profile['policies']['gather']['farm_controls_gather'] = {'ad_gems': False}
    result = run_workspace.update(profile, {}, 'farm', {'routines.ad_gems.window_sec': 20})
    assert result['profile']['blueprints']['farm']['policies']['gather'] == 'farm_controls_gather_2'
    assert result['profile']['policies']['gather']['farm_controls_gather'] == {'ad_gems': False}


def test_dissonance_trims_queue_by_catalog_page_and_retains_source(profile):
    queue = shopping_plan.normalize_plan({'version': 2, 'cash_reserve': 0, 'queue': [{'id': 'health'}, {'id': 'damage'}]})
    profile['policies']['shopping_lists']['shared'] = queue
    profile['blueprints']['farm']['dissonant_tab'] = 'attack'
    compiled = playerprofile.compile_preset(profile, 'farm')
    assert [item['id'] for item in compiled['shopping_plan']['queue']] == ['health']
    assert [item['id'] for item in queue['queue']] == ['health', 'damage']


@pytest.mark.parametrize('value', [True, float('inf'), -1, 0])
def test_invalid_numeric_patch_refused_before_any_input_changes(profile, value):
    original = copy.deepcopy(profile)
    with pytest.raises(ValueError):
        run_workspace.update(profile, {}, 'farm', {'routines.ad_gems.interval_sec': value})
    assert profile == original


@pytest.mark.parametrize('automation', [{'unknown': True}, {'repeat': 1}, None, []])
def test_blueprint_gate_validation_and_compile_refuse_invalid_shapes(profile, automation):
    profile['blueprints']['farm']['automation'] = automation
    assert any('automation' in error for error in playerprofile.validate(profile))
    with pytest.raises(playerprofile.ProfileError):
        playerprofile.compile_preset(profile, 'farm')


def test_run_global_rewards_override_shared_without_mutation(profile):
    profile['blueprints']['farm']['global_behaviors'] = {'free_store_gems': False}
    assert playerprofile.compile_preset(profile, 'farm')['global_behaviors']['free_store_gems'] is False
    assert playerprofile.compile_preset(profile, 'other')['global_behaviors']['free_store_gems'] is True


def test_shard_rejects_shopping_control_unsupported_by_blueprint_schema(profile):
    profile['blueprints']['farm']['kind'] = 'shard'
    with pytest.raises(ValueError, match='unavailable'):
        run_workspace.update(profile, {}, 'farm', {'shopping_plan': shopping_plan.default_plan()})
