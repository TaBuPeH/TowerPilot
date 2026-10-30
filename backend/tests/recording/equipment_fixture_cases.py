"""Generate supplemental Equipment reads by calling real routes in the existing isolated world.

The published page family has no GET of a manual/global loadout or of the discovered
inventory used by its saves. These samples add those browser states without inventing
backend answers. Run from the repository root:
python -m pytest backend/tests/recording/equipment_fixture_cases.py
"""
import copy
import json

import paths
from fixture_json import write_fixture_json
from conftest import dash  # noqa: F401 - pytest fixture, temporary backend root
from dashboard_contract_cases_editors import equipped
from dashboard_contract_pages_world import PagesGenerator
from test_dashboard_contract import scrub

PAGES_FIXTURE = paths.FIXTURES / 'dashboard_contract_pages_v1.json'
# Equipment sample name -> recorded pages-fixture case it repeats.
CONTEXT_ALIASES = {
    'context_config': 'context_fence.read_config_before_the_loadout_save',
    'context_match_saved': 'context_fence.loadout_with_the_matching_context_is_written',
    'context_match_readback': 'context_fence.read_config_after_the_matching_loadout_holds_it',
    'context_malformed_null': 'context_fence.loadout_expected_context_null_is_refused_not_read_as_absent',
    'context_malformed_missing_fields': 'context_fence.loadout_expected_context_missing_the_serial',
    'context_malformed_extra_field': 'context_fence.loadout_expected_context_with_an_unknown_key',
    'context_malformed_wrong_value_type': 'context_fence.loadout_expected_context_serial_not_text',
    'context_malformed_not_an_object': 'context_fence.loadout_expected_context_not_an_object',
    'context_refusals_readback': 'context_fence.read_config_after_the_malformed_loadout_requests_unchanged',
    'context_changed_config': 'context_fence.read_config_after_the_matching_loadout_holds_it',
    'context_changed_refused': 'context_fence.loadout_composed_for_alpha_is_refused_with_its_expected_revision',
    'context_changed_readback': 'context_fence.read_config_after_the_loadout_refusals_nothing_was_written',
    'context_stale_and_changed': 'context_fence.loadout_with_a_stale_context_and_a_stale_revision_is_told_the_context_changed',
    'context_stale_revision': 'context_fence.loadout_for_the_current_context_with_a_stale_revision_is_a_revision_conflict',
    'context_legacy': 'context_fence.loadout_without_the_field_is_not_fenced_the_legacy_page',
    'context_profile': 'ability_policies.read_before_the_first_save',
    'context_match_profile': 'ability_policies.read_before_the_first_save',
    'context_changed_profile': 'ability_policies.read_before_the_first_save',
    'context_unknown_refused': 'context_fence.tier_when_the_configuration_names_an_unknown_account_is_refused',
}


def test_generate_equipment_samples(tmp_path, monkeypatch, dash):
    gen = PagesGenerator(dash, tmp_path / 'equipment', monkeypatch)
    family = 'equipment_samples'
    for scenario in ('mixed', 'manual', 'missing', 'empty', 'long'):
        with gen.world(scenario) as w:
            loadouts = {
                'plain': {}, 'spare': {'cards': 'no_card'}, 'later': {'defined': False},
            }
            if scenario in ('mixed', 'manual', 'long'):
                loadouts['plain'] = {'cards': 'main_farm', 'modules': [['amplifying_strike', 'attack']],
                                     'guardians': ['bounty', 'fetch']}
            if scenario in ('mixed', 'long'):
                loadouts.update(global_choice={'global_preset': 'Farm Run'},
                                categories={'module_preset': 'Farm', 'guardian_preset': 'Farm'},
                                untouched={})
            if scenario == 'missing':
                loadouts['plain'] = {'cards': 'undiscovered_saved_choice'}
            equipped(w, loadouts=loadouts)
            if scenario == 'empty':
                w.edit_config(lambda cfg: cfg.update(loadouts={}))
            if scenario == 'long':
                def long_consumers(profile):
                    run = copy.deepcopy(profile['blueprints']['farm_deep'])
                    for label in ('x', 'A long saved run label ' * 5, 'unbreakable' * 40, 'RTL-\u0645\u062b\u0627\u0644-' * 12):
                        profile['blueprints'][label] = copy.deepcopy(run)
                w.edit_profile(long_consumers)
            first = w.get(family, f'{scenario}_config', '/api/config').get_json()
            w.get(family, f'{scenario}_profile', '/api/profile-src/mine')
            if scenario not in ('manual', 'mixed'):
                continue
            revision = first['config_revision']
            steps = (
                ('global', {'global_preset': 'Farm Run'}),
                ('categories', {'module_preset': 'Farm', 'guardian_preset': 'Farm'}),
                ('cards', {'cards': 'main_farm'}),
                ('workshop_bots', {'workshop_preset': 'Full Upgraded', 'bot_preset': 'Farm'}),
            )
            for name, body in steps:
                reply = w.post(family, f'{scenario}_{name}_saved', '/api/loadout-patch',
                               dict(name='plain', body=body, expected_revision=revision))
                assert reply.status_code == 200, reply.get_json()
                revision = reply.get_json()['config_revision']
                w.get(family, f'{scenario}_{name}_readback', '/api/config')
                w.get(family, f'{scenario}_{name}_profile', '/api/profile-src/mine')
    # Arbitrary user-owned names must work even when strict fixture schemas have never seen them.
    # Keep this family out of the sample descriptor to exercise the tolerant runtime boundary.
    with gen.world('edge') as w:
        equipped(w, loadouts={'plain': {}, 'constructor': {}, 'toString': {}, '__proto__': {}})
        first = w.get('equipment_edge', 'config', '/api/config').get_json()
        w.get('equipment_edge', 'profile', '/api/profile-src/mine')
        reply = w.post('equipment_edge', 'saved', '/api/loadout-patch', dict(
            name='constructor', body={'global_preset': 'Farm Run'}, expected_revision=first['config_revision']))
        assert reply.status_code == 200, reply.get_json()
        saved = w.get('equipment_edge', 'readback', '/api/config').get_json()
        same = w.post('equipment_edge', 'noop_saved', '/api/loadout-patch', dict(
            name='constructor', body={'global_preset': 'Farm Run'}, expected_revision=saved['config_revision']))
        assert same.status_code == 200, same.get_json()
        assert same.get_json()['config_revision'] == saved['config_revision']
        w.get('equipment_edge', 'noop_readback', '/api/config')
        current = same.get_json()['config_revision']
        proto = w.post('equipment_edge', 'proto_saved', '/api/loadout-patch', dict(
            name='__proto__', body={'global_preset': 'Farm Run'}, expected_revision=current))
        assert proto.status_code == 200, proto.get_json()
        w.get('equipment_edge', 'proto_readback', '/api/config')
    assert len(gen.cases[family]) == 34
    # The context-fence answers are recorded by the backend's pages fixture; copy them unchanged
    # under the names the Equipment specs use. No new answer is invented.
    pages = json.loads(PAGES_FIXTURE.read_text(encoding='utf-8'))['cases']
    for name, source in CONTEXT_ALIASES.items():
        source_family, case = source.split('.', 1)
        gen.cases[family][name] = copy.deepcopy(pages[source_family][case])
    document = scrub(dict(version=1, generated_by='backend/tests/recording/equipment_fixture_cases.py', cases=gen.cases), gen.base)
    write_fixture_json(paths.FRONTEND_FIXTURES / 'equipment-samples.json', document)
