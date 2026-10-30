"""Supplement whole-policy editor answers with REAL Flask route calls in isolated worlds.

Never calls the running dashboard, device, ledger or lease. Run from the repo root:
python -m pytest backend/tests/recording/abilities_fixture_cases.py
The sample is written by support/fixture_json.py and is listed in frontend/app/.prettierignore; do not run prettier on it.
"""
import copy
import json

import paths
from fixture_json import write_fixture_json
from conftest import dash  # noqa: F401
from dashboard_contract_cases_editors import with_abilities
from dashboard_contract_pages_world import PagesGenerator
from test_dashboard_contract import scrub


def test_generate_abilities_samples(tmp_path, monkeypatch, dash):
    gen = PagesGenerator(dash, tmp_path / 'abilities', monkeypatch)
    family = 'abilities_samples'
    changes = [
        ('uw_off', 'uw_policies', 'farm_cl', lambda p: p['baseline'].update(spotlight=False)),
        ('uw_unowned', 'uw_policies', 'farm_cl', lambda p: p['baseline'].update(smart_missiles=True)),
        ('label', 'uw_policies', 'farm_cl', lambda p: p.update(label='New label')),
        ('cl_always', 'uw_policies', 'farm_cl', lambda p: p.update(chain_lightning={'mode': 'always_on'})),
        ('cl_fleet', 'uw_policies', 'farm_cl', lambda p: p.update(chain_lightning={'mode': 'fleet_marks', 'pre_mark_waves': [5, 30], 'off_after_waves': [50, 70], 'always_on_above': [6000, 6100]})),
        ('cl_wave', 'uw_policies', 'farm_cl', lambda p: p.update(chain_lightning={'mode': 'off_until_wave', 'on_above': [400, 450]})),
        ('cl_off', 'uw_policies', 'farm_cl', lambda p: p.update(chain_lightning={'mode': 'off'})),
        ('cl_reversed', 'uw_policies', 'farm_cl', lambda p: p.update(chain_lightning={'mode': 'off_until_wave', 'on_above': [550, 500]})),
        ('cl_fractional', 'uw_policies', 'farm_cl', lambda p: p.update(chain_lightning={'mode': 'off_until_wave', 'on_above': [4.5, 5]})),
        ('cl_unset', 'uw_policies', 'farm_cl', lambda p: p['chain_lightning'].update(always_on_above=None)),
        ('cl_optional_reversed', 'uw_policies', 'farm_cl', lambda p: p['chain_lightning'].update(always_on_above=[550, 500])),
        ('cl_optional_fractional', 'uw_policies', 'farm_cl', lambda p: p['chain_lightning'].update(always_on_above=[4.5, 5])),
        ('cl_restore', 'uw_policies', 'farm_cl', lambda p: p['chain_lightning'].update(always_on_above=[6080, 6120])),
        ('rules_absent', 'rescue_policies', 'wall_drain', lambda p: p.update(rules=[{'when': {'wave_at_least': 1000}, 'do': {'cancel_sprint': True}}])),
        ('rules_null', 'rescue_policies', 'wall_drain', lambda p: p.update(rules=[{'when': {'wave_at_least': 1000}, 'do': {'cancel_sprint': True}}])),
        ('arm_always', 'rescue_policies', 'wall_drain', lambda p: p.update(arm='always')),
        ('arm_durations', 'rescue_policies', 'wall_drain', lambda p: p.update(arm={'on': 'second_wind', 'watch_sec': 45, 'immunity_sec': 12})),
        ('arm_zero', 'rescue_policies', 'wall_drain', lambda p: p.update(arm={'on': 'second_wind', 'watch_sec': 0, 'immunity_sec': None})),
        ('arm_blank', 'rescue_policies', 'wall_drain', lambda p: p.update(arm={'on': 'second_wind', 'immunity_sec': None})),
        ('end_sprint', 'rescue_policies', 'wall_drain', lambda p: p.update(end_sprint_after_sw=True)),
        ('reorder', 'rescue_policies', 'wall_drain', lambda p: p.update(rules=[p['rules'][2], p['rules'][0], p['rules'][1]])),
        ('rule_delete', 'rescue_policies', 'wall_drain', lambda p: p['rules'].pop(2)),
        ('burst_wall_options', 'rescue_policies', 'wall_drain', lambda p: p['rules'][0]['do']['burst'].update(retaps=5, require_match=True)),
        ('burst_wrong_gate', 'rescue_policies', 'wall_drain', lambda p: p['rules'][0]['do']['burst'].update(require_ready=True)),
        ('repeat_fast_watch', 'rescue_policies', 'wall_drain', lambda p: p['rules'][0].update(repeat=True, refire_sec=12)),
        ('mixed_bars', 'rescue_policies', 'wall_drain', lambda p: p['rules'].append({'when': {'bar': 'hp', 'below': 0.2}, 'do': {'fire': {'button': 'demon_mode'}}})),
        ('create_uw', 'uw_policies', 'new_plan', lambda p: p.update(baseline={})),
        ('copy_uw', 'uw_policies', 'new_plan', lambda p: None),
        ('create_rescue', 'rescue_policies', 'new_plan', lambda p: p.update(arm='always', rules=[])),
    ]
    triggers = {
        'bar': {'bar': 'hp', 'below': 0.2, 'deadband': 0.01, 'falling_samples': 2},
        'wall_collapse': {'wall_collapse': {'from_above': 0.3}},
        'fleet_mark': {'fleet_mark': {'after_waves': 3, 'window_waves': 60}},
        'wave_at_least': {'wave_at_least': 1000},
        'wave_between': {'wave_between': [1000, 2000]},
        'second_wind': {'second_wind': {'state': 'after_immunity', 'min_procs': 2}},
    }
    actions = {
        'burst': {'burst': {'fire': 'demon_mode', 'cancel_sprint': True, 'require_ready': True}},
        'fire': {'fire': {'button': 'demon_mode', 'require_ready': True, 'throttle_sec': 10}},
        'fire_refire_guard': {'fire': {'button': 'demon_mode', 'require_ready': False, 'refire_guard_sec': 10}},
        'toggle_uw': {'toggle_uw': {'weapon': 'smart_missiles', 'want_on': False}},
        'cancel_sprint': {'cancel_sprint': True},
    }
    for name, when in triggers.items():
        changes.append((f'trigger_{name}', 'rescue_policies', 'new_plan', lambda p, w=when: p.update(arm='always', rules=[{'when': w, 'do': {'cancel_sprint': True}, 'repeat': True, 'refire_sec': 12}])))
    for name, action in actions.items():
        changes.append((f'action_{name}', 'rescue_policies', 'new_plan', lambda p, a=action: p.update(arm='always', rules=[{'when': {'wave_at_least': 1000}, 'do': a, 'repeat': True}])))
    for scenario, policy_family, policy_id, mutate in changes:
        with gen.world(scenario) as w:
            with_abilities(w)
            if scenario == 'arm_always':
                w.edit_profile(lambda p: p['policies']['rescue_policies']['wall_drain'].update(rules=[]))
            if scenario == 'cl_restore':
                w.edit_profile(lambda p: p['policies']['uw_policies']['farm_cl']['chain_lightning'].update(always_on_above=None))
            if scenario == 'rules_absent':
                w.edit_profile(lambda p: p['policies']['rescue_policies']['wall_drain'].update(arm='always'))
                w.edit_profile(lambda p: p['policies']['rescue_policies']['wall_drain'].pop('rules'))
            if scenario == 'rules_null':
                w.edit_profile(lambda p: p['policies']['rescue_policies']['wall_drain'].update(arm='always', rules=None))
            w.get(family, f'{scenario}_config', '/api/config')
            before = w.get(family, f'{scenario}_before', '/api/profile-src/mine').get_json()
            w.get(family, f'{scenario}_settings', '/api/settings')
            w.get(family, f'{scenario}_vocab', '/api/vocab')
            template_id = 'farm_cl' if policy_family == 'uw_policies' else 'wall_drain'
            value = copy.deepcopy(before['profile']['policies'][policy_family][template_id])
            if scenario in ('create_uw', 'create_rescue') or scenario.startswith(('trigger_', 'action_')):
                value = {}
            mutate(value)
            response = w.post(family, f'{scenario}_saved', '/api/profile-patch', dict(name='mine', expected_revision=before['revision'], path=['policies', policy_family, policy_id], value=value))
            expected = 400 if scenario in ('cl_reversed', 'cl_fractional', 'cl_optional_reversed', 'cl_optional_fractional', 'arm_zero', 'mixed_bars', 'burst_wrong_gate', 'repeat_fast_watch') else 200
            assert response.status_code == expected, (scenario, response.get_json())
            w.get(family, f'{scenario}_after', '/api/profile-src/mine')
            w.get(family, f'{scenario}_settings_after', '/api/settings')
    # Explicit deletion of an unused policy, plus a whole-policy create/readback path.
    with gen.world('global') as w:
        with_abilities(w)
        w.get(family, 'global_config', '/api/config')
        w.get(family, 'global_before', '/api/profile-src/mine')
        settings = w.get(family, 'global_settings', '/api/settings').get_json()
        w.get(family, 'global_vocab', '/api/vocab')
        reply = w.post(family, 'global_saved', '/api/settings', dict(context=settings['context'], revision=settings['revision'], module='global_abilities', values={'demon_mode_always': True}))
        assert reply.status_code == 200, reply.get_json()
        w.get(family, 'global_after', '/api/profile-src/mine')
        w.get(family, 'global_settings_after', '/api/settings')
    with gen.world('delete') as w:
        with_abilities(w)
        w.edit_profile(lambda p: p['policies']['uw_policies'].update(unused={'baseline': {}}))
        w.get(family, 'delete_config', '/api/config')
        before = w.get(family, 'delete_before', '/api/profile-src/mine').get_json()
        w.get(family, 'delete_settings', '/api/settings')
        w.get(family, 'delete_vocab', '/api/vocab')
        reply = w.post(family, 'delete_saved', '/api/profile-patch', dict(name='mine', expected_revision=before['revision'], path=['policies', 'uw_policies', 'unused'], op='delete'))
        assert reply.status_code == 200, reply.get_json()
        w.get(family, 'delete_after', '/api/profile-src/mine')
        w.get(family, 'delete_settings_after', '/api/settings')
    for scenario, policy_family, policy_id in [('raw_rule_refresh', 'rescue_policies', 'wall_drain'), ('raw_number_refresh', 'uw_policies', 'farm_cl')]:
        with gen.world(scenario) as w:
            with_abilities(w)
            if scenario == 'raw_rule_refresh':
                w.edit_profile(lambda p: p['policies']['rescue_policies']['wall_drain']['rules'][2].update(when={'wave_between': [10, 20]}))
            w.get(family, f'{scenario}_config', '/api/config')
            before = w.get(family, f'{scenario}_before', '/api/profile-src/mine').get_json()
            w.get(family, f'{scenario}_settings', '/api/settings')
            w.get(family, f'{scenario}_vocab', '/api/vocab')
            value = copy.deepcopy(before['profile']['policies'][policy_family][policy_id])
            body = dict(name='mine', expected_revision=before['revision'], path=['policies', policy_family, policy_id], value=value)
            committed_request = copy.deepcopy(body)
            committed = w.post(family, f'{scenario}_committed', '/api/profile-patch', committed_request)
            assert committed.status_code == 200, committed.get_json()
            value['label'] = 'External label'
            body['expected_revision'] = committed.get_json()['revision']
            changed_request = copy.deepcopy(body)
            changed = w.post(family, f'{scenario}_saved', '/api/profile-patch', changed_request)
            assert changed.status_code == 200, changed.get_json()
            after = w.get(family, f'{scenario}_after', '/api/profile-src/mine').get_json()
            w.get(family, f'{scenario}_settings_after', '/api/settings')
            if scenario == 'raw_rule_refresh':
                value['rules'] = [value['rules'][2], value['rules'][0], value['rules'][1]]
                body['expected_revision'] = after['revision']
                moved = w.post(family, f'{scenario}_reordered_save', '/api/profile-patch', copy.deepcopy(body))
                assert moved.status_code == 200, moved.get_json()
                w.get(family, f'{scenario}_reordered', '/api/profile-src/mine')
            # Recording later edits must never rewrite the request that produced an earlier answer.
            assert gen.cases[family][f'{scenario}_committed']['request']['body'] == committed_request
            assert gen.cases[family][f'{scenario}_saved']['request']['body'] == changed_request
    document = scrub(dict(version=1, generated_by='backend/tests/recording/abilities_fixture_cases.py', cases=gen.cases), gen.base)
    write_fixture_json(paths.FRONTEND_FIXTURES / 'abilities-samples.json', document)
