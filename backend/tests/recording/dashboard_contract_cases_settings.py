"""Contract cases of the settings editors: GET/POST /api/settings (README row G-04).

One family, `settings`. The catalogue (backend/control/settings_catalog.py) lists a module per concern - machine tap timing,
the emulator's video preview, the observation loop, mission visits, fleet windows, logging, global rewards, global abilities
and, per gathering policy of the active profile, gathering rewards, Ad Gems and orbiting gems - each with its scope
(machine, emulator, profile), its fields (type, range, unit, whole-number flag) and whether it can be edited now.

A save is {context, revision, module, values}: compare-and-set on the revision of config.yaml + the active profile together,
and on the account/emulator/profile context. Every answer here is the real route's over the populated account of
dashboard_contract_pages_world.populate().
"""
import copy

import yaml

from dashboard_contract_cases_profile import write_profile
from dashboard_contract_pages_world import populate, raw_post
from test_dashboard_contract import raising

SET = 'settings'
URL = '/api/settings'


def snapshot(w, case=None, family=SET):
    response = w.get(family, case, URL) if case else w.client.get(URL)
    return response.get_json()


def module_of(snap, ident):
    return next(m for m in snap['modules'] if m['id'] == ident)


def submit(w, case, snap, module, values, *, expect=None, family=SET, **override):
    body = dict(context=snap['context'], revision=snap['revision'], module=module, values=values)
    body.update(override)
    reply = w.post(family, case, URL, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def save(w, case, snap, module, values, family=SET):
    reply = submit(w, case, snap, module, values, expect=200, family=family).get_json()
    assert reply['ok'] and reply['revision'] != snap['revision'] and reply['backup']
    return reply


def settings_read_cases(gen):
    with gen.world('set_read') as w:
        populate(w)
        snap = snapshot(w, 'populated_account_every_module')
        ids = [m['id'] for m in snap['modules']]
        assert ids == ['tap', 'device-preview', 'loop', 'missions', 'fleet', 'loop-defaults', 'missions-defaults', 'fleet-defaults', 'instance-defaults',
                       'active-instance', 'run-defaults', 'tournament-cards', 'logging', 'rewards', 'global_abilities',
                       'gather:all_on', 'ad:all_on', 'orbit:all_on', 'gather:gems_only', 'ad:gems_only', 'orbit:gems_only',
                       'gather:hands_off', 'ad:hands_off', 'orbit:hands_off'], ids
        assert {m['scope'] for m in snap['modules']} == {'machine', 'instance', 'profile'} and all(m['editable'] for m in snap['modules'])
        assert [p['id'] for p in snap['policies']] == ['default_sweep', 'main_list']
        assert module_of(snap, 'device-preview')['apply_notice']
    with gen.world('set_read_only_starter') as w:
        populate(w)
        write_profile(w, 'default', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        w.edit_config(lambda cfg: cfg.update(active_profile='default'))
        snap = snapshot(w, 'starter_profile_active_profile_modules_read_only')
        assert {m['id']: m['editable'] for m in snap['modules']}['tap'] is True and module_of(snap, 'rewards')['editable'] is False
        refused = submit(w, 'starter_profile_active_a_profile_module_is_refused', snap, 'rewards', {'free_store_gems': True}, expect=409)
        assert 'personal profile' in refused.get_json()['error']
        save(w, 'starter_profile_active_a_machine_module_still_saves', snap, 'tap', {'jitter_px': 7})
    with gen.world('set_read_only_draft') as w:
        populate(w)
        write_profile(w, 'mine.draft', {'player': {}})
        w.edit_config(lambda cfg: cfg.update(active_profile='mine.draft'))
        snap = snapshot(w, 'scan_draft_active_profile_modules_read_only')
        assert module_of(snap, 'global_abilities')['editable'] is False
    with gen.world('set_read_only_foreign') as w:
        from dashboard_contract_cases_profile import two_accounts
        populate(w, config=lambda cfg: two_accounts(cfg, active='alpha', profile='theirs'))
        write_profile(w, 'theirs', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        w.edit_config(lambda cfg: cfg['accounts']['beta'].update(active_profile='theirs'))
        snap = snapshot(w, 'other_accounts_profile_active_profile_modules_read_only')
        assert module_of(snap, 'rewards')['editable'] is False
    with gen.world('set_no_profile') as w:
        w.edit_config(lambda cfg: cfg.update(active_profile=None))
        snap = snapshot(w, 'no_active_profile_only_machine_and_emulator_modules')
        assert [m['id'] for m in snap['modules'] if m['scope'] == 'profile'] == ['rewards', 'global_abilities']
        assert module_of(snap, 'rewards')['editable'] is False
    with gen.world('set_no_policies') as w:
        populate(w, mutate=lambda profile: profile['policies'].update(gather={}))
        blueprints = lambda profile: [bp.pop('policies') for bp in profile['blueprints'].values()]
        snap = snapshot(w, 'profile_without_gathering_policies_has_no_gather_modules')
        assert not [m for m in snap['modules'] if m['id'].startswith(('gather:', 'ad:', 'orbit:'))]
    with gen.world('set_unreadable') as w:
        populate(w)
        write_profile(w, 'mine', '- not\n- a mapping\n')
        assert w.get(SET, 'profile_not_a_mapping_400', URL).status_code == 400
        write_profile(w, 'mine', 'blueprints: [unclosed\n')
        assert w.get(SET, 'profile_unreadable_yaml_400', URL).status_code == 400


MODULE_VALUES = [
    ('tap', {'jitter_ms': [20, 60], 'jitter_px': 6, 'max_rate_per_sec': 10.5}),
    ('device-preview', {'backend': 'scrcpy', 'fps': 12.5, 'mumu.index': 2, 'mumu.install_dir': '', 'scrcpy.bitrate': 8000000}),
    ('loop', {'fps': 3, 'dry_run': True}),
    ('missions', {'visit_delay_sec': [90, 400]}),
    ('fleet', {'hold_window': 120, 'post_window': 160}),
    ('logging', {'screenshot_on_trigger': False, 'bench_interval_sec': 60}),
    ('rewards', {'free_store_gems': False, 'daily_missions': True}),
    ('global_abilities', {'demon_mode_always': True}),
    ('gather:all_on', {'quests_8h': False, 'guild_store': True}),
    ('ad:all_on', {'ad_gems': True, 'gem_delay_sec': [4, 9], 'ad_gem_options.confirmed_cooldown_sec': [600, 600],
                   'ad_gem_options.attempt_timeout_sec': 45, 'ad_gem_options.edge_inset_px': 12}),
    ('orbit:all_on', {'gem_orbit.enabled': True, 'gem_orbit.center': [510, 805], 'gem_orbit.radius': 180.5,
                      'gem_orbit.interval_sec': 900, 'gem_orbit.attempt_timeout_sec': 40, 'gem_orbit.taps': 4}),
]


def settings_save_cases(gen):
    with gen.world('set_save') as w:
        populate(w)
        snap = snapshot(w)
        for module, values in MODULE_VALUES:
            reply = save(w, 'saved_' + module.replace(':', '_').replace('-', '_'), snap, module, values)
            held = module_of(reply, module)
            for key, value in values.items():
                assert next(f for f in held['fields'] if f['key'] == key)['value'] == value, (module, key)
            snap = reply
        final = snapshot(w, 'read_after_every_module_was_saved')
        assert final['revision'] == snap['revision']
    with gen.world('set_save_unrelated_problem') as w:
        populate(w, mutate=lambda profile: profile['blueprints']['farm_deep'].update(loadout='nope'))
        snap = snapshot(w)
        save(w, 'saved_although_the_profile_has_an_older_problem_only_new_problems_refuse', snap, 'rewards', {'free_store_gems': False})


def settings_refusals(gen):
    with gen.world('set_refused') as w:
        populate(w)
        snap = snapshot(w)
        cases = [
            ('integer_with_a_fraction', 'tap', {'jitter_px': 4.5}), ('integer_as_text', 'tap', {'jitter_px': '4'}),
            ('integer_as_true', 'tap', {'jitter_px': True}), ('integer_above_the_maximum', 'tap', {'jitter_px': 41}),
            ('number_below_the_minimum', 'loop', {'fps': 0.05}), ('number_above_the_maximum', 'loop', {'fps': 31}),
            ('number_as_text', 'loop', {'fps': 'fast'}),
            ('range_reversed', 'tap', {'jitter_ms': [60, 15]}), ('range_of_three', 'tap', {'jitter_ms': [15, 30, 60]}),
            ('range_as_a_number', 'tap', {'jitter_ms': 30}), ('range_below_the_minimum', 'tap', {'jitter_ms': [-1, 20]}),
            ('range_above_the_maximum', 'tap', {'jitter_ms': [15, 5001]}),
            ('range_needing_whole_numbers_gets_a_fraction', 'ad:all_on', {'gem_delay_sec': [3.5, 9]}),
            ('boolean_as_text', 'loop', {'dry_run': 'true'}), ('boolean_as_a_number', 'loop', {'dry_run': 1}),
            ('enum_with_an_unknown_option', 'device-preview', {'backend': 'hologram'}), ('enum_as_a_number', 'device-preview', {'backend': 1}),
            ('string_as_a_number', 'device-preview', {'mumu.install_dir': 5}),
            ('point_outside_the_screen', 'orbit:all_on', {'gem_orbit.center': [1080, 100]}),
            ('point_of_three', 'orbit:all_on', {'gem_orbit.center': [1, 2, 3]}),
            ('point_below_zero', 'orbit:all_on', {'gem_orbit.center': [-1, 100]}),
            ('orbit_interval_below_the_minimum', 'orbit:all_on', {'gem_orbit.interval_sec': 0}),
            ('orbit_window_above_the_maximum', 'orbit:all_on', {'gem_orbit.attempt_timeout_sec': 61}),
            ('ad_window_below_the_minimum', 'ad:all_on', {'ad_gem_options.attempt_timeout_sec': 4}),
            ('integer_with_a_value_valid_for_another_field_in_the_same_request', 'tap', {'jitter_px': 6, 'max_rate_per_sec': 31}),
            ('unknown_field_for_the_module', 'tap', {'speed': 3}), ('field_of_another_module', 'tap', {'fps': 3}),
            ('unknown_module', 'nothing', {'a': 1}), ('module_of_a_policy_that_does_not_exist', 'gather:ghost', {'guild': True}),
            ('values_empty', 'tap', {}), ('values_a_list', 'tap', [1]), ('values_missing', 'tap', None),
        ]
        for case, module, values in cases:
            body = dict(context=snap['context'], revision=snap['revision'], module=module)
            if values is not None:
                body['values'] = values
            reply = w.post(SET, case, URL, body)
            assert reply.status_code == 400 and reply.get_json()['ok'] is False, (case, reply.status_code, reply.get_json())
        assert w.post(SET, 'body_a_list', URL, []).status_code == 400
        assert raw_post(w, SET, 'body_not_json', URL, 'module=tap', content_type='text/plain').status_code == 400
        for text in ('NaN', 'Infinity'):      # not representable in the strict-JSON fixture: asserted, not recorded
            body = ('{"context": %s, "revision": "%s", "module": "loop", "values": {"fps": %s}}'
                    % (__import__('json').dumps(snap['context']), snap['revision'], text))
            reply = w.client.post(URL, data=body, content_type='application/json')
            assert reply.status_code == 400, (text, reply.get_json())
        assert snapshot(w, 'read_after_the_refusals_nothing_changed') == snap


def settings_conflicts(gen):
    with gen.world('set_conflicts') as w:
        populate(w)
        snap = snapshot(w)
        values = {'jitter_px': 6}
        submit(w, 'context_missing', {'context': None, 'revision': snap['revision']}, 'tap', values, expect=409)
        other = dict(snap['context'], profile='ghost')
        submit(w, 'context_other_profile', dict(snap, context=other), 'tap', values, expect=409)
        submit(w, 'context_other_emulator', dict(snap, context=dict(snap['context'], instance='other')), 'tap', values, expect=409)
        submit(w, 'context_other_account', dict(snap, context=dict(snap['context'], account='beta')), 'tap', values, expect=409)
        submit(w, 'revision_missing', dict(snap, revision=None), 'tap', values, expect=409)
        assert w.plain_post(URL, dict(context=snap['context'], revision=snap['revision'], module='logging', values={'bench_interval_sec': 45})).status_code == 200
        stale = submit(w, 'stale_revision_another_client_saved_first', snap, 'tap', values, expect=409).get_json()
        assert stale['error'] == 'Settings or active account changed. Reload before saving.'
        submit(w, 'stale_revision_is_judged_before_the_values', snap, 'tap', {'jitter_px': 4.5}, expect=409)
        submit(w, 'stale_revision_is_judged_before_the_module', snap, 'nothing', values, expect=409)
        held = snapshot(w, 'read_after_the_stale_refusal_holds_the_other_clients_save')
        assert module_of(held, 'logging')['fields'][1]['value'] == 45
        save(w, 'saved_again_with_the_revision_just_read', held, 'tap', values)
    with gen.world('set_context_switch') as w:
        populate(w)
        snap = snapshot(w)
        w.edit_config(lambda cfg: cfg.update(active_instance='other'))
        reply = submit(w, 'the_emulator_was_switched_after_the_read', snap, 'tap', {'jitter_px': 6}, expect=409).get_json()
        assert 'Reload' in reply['error']
        assert snapshot(w, 'read_after_the_switch_names_the_new_emulator')['context']['instance'] == 'other'
    with gen.world('set_lock') as w:
        populate(w)
        snap = snapshot(w)
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            locked = submit(w, 'action_lock_another_action_in_progress', snap, 'tap', {'jitter_px': 6}, expect=409).get_json()
        finally:
            w.dash._ACTION_LOCK.release()
        assert locked['code'] == 'action_lock'
        assert snapshot(w) == snap


def settings_write_failures(gen):
    with gen.world('set_write_failed') as w:
        populate(w)
        snap = snapshot(w)
        w.mp.setattr(w.dash, 'save_config', raising(OSError('config.yaml could not be written')))
        failed = submit(w, 'machine_write_failed_is_a_400_and_nothing_saved', snap, 'tap', {'jitter_px': 6}, expect=400).get_json()
        assert 'could not be written' in failed['error']
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('profiles/mine.yaml could not be written')))
        submit(w, 'profile_write_failed_is_a_400_and_nothing_saved', snap, 'rewards', {'free_store_gems': False}, expect=400)
        assert snapshot(w, 'read_after_the_failed_writes_the_revision_is_unchanged') == snap
    with gen.world('set_answer_failed') as w:
        populate(w)
        snap = snapshot(w)
        real, calls = w.dash._settings_snapshot, []

        def snapshot_then_fail(cfg):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('the fresh settings could not be built')
            return real(cfg)
        w.mp.setattr(w.dash, '_settings_snapshot', snapshot_then_fail)
        body = dict(context=snap['context'], revision=snap['revision'], module='tap', values={'jitter_px': 6})
        assert w.post(SET, 'saved_then_the_answer_failed_500', URL, body).status_code == 500
        w.mp.setattr(w.dash, '_settings_snapshot', real)
        held = snapshot(w, 'read_after_the_unanswered_save_holds_the_value')
        assert held['revision'] != snap['revision'] and next(f for f in module_of(held, 'tap')['fields'] if f['key'] == 'jitter_px')['value'] == 6
        again = w.post(SET, 'identical_resend_after_a_committed_save_is_refused_as_stale', URL, body)
        assert again.status_code == 409


def run_settings_cases(gen):
    settings_read_cases(gen)
    settings_save_cases(gen)
    settings_refusals(gen)
    settings_conflicts(gen)
    settings_write_failures(gen)