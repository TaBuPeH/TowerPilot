"""Contract cases of the Equipment, Collection and Abilities editors (README rows E-01, C-01, A-01).

Families: loadout_patch (POST /api/loadout-patch), collection_policies and ability_policies (POST /api/profile-patch of
the gathering, Ultimate Weapon and rescue policies, which is how those pages save). Settings modules of the same pages
(rewards, ad:*, orbit:*, global_abilities) are in the `settings` family; stale revision, starter/draft refusal and the
unreadable file are the `profile_patch` family's.

The account is dashboard_contract_pages_world.populate() with an inventory: card presets, guardians, equipped modules, the
Global Presets, category presets, owned Ultimate Weapons and verified Demon Mode/Nuke, so the real validator's ownership
checks accept some loadouts and policies and refuse others.
"""
import copy

import yaml

from dashboard_contract_pages_world import populate, raw_post
from test_dashboard_contract import raising

LOADOUT, COLLECT, ABILITY = 'loadout_patch', 'collection_policies', 'ability_policies'
LOADOUT_URL = '/api/loadout-patch'
PATCH_URL = '/api/profile-patch'

PLAYER = {
    'uws': {'chain_lightning': True, 'black_hole': True, 'death_wave': True, 'golden_tower': True, 'poison_swamp': True,
            'spotlight': True, 'chronofield': True, 'inner_land_mines': False, 'smart_missiles': False},
    'abilities': {'nuke': True, 'demon_mode': True}, 'abilities_verified': True, 'wall': True, 'max_tier': 19,
    'card_presets': ['main_farm', 'no_card'], 'global_presets': ['Farm Run', 'Tournament'],
    'category_presets': {'modules': ['Farm', 'Tourney'], 'guardians': ['Farm'], 'workshop': ['Full Upgraded'], 'bots': ['Farm']},
    'guardians': ['bounty', 'fetch', 'summon'], 'modules_equipped': ['amplifying_strike', 'black_hole_digestor'], 'modules_in_grid': []}
LOADOUTS = {'plain': {}, 'spare': {'cards': 'no_card'}, 'later': {'defined': False}}


def equipped(w, *, player=None, loadouts=None, mutate=None):
    """The populated account with an inventory in the profile and a few loadouts in config.yaml."""
    def profile_change(profile):
        profile['player'] = copy.deepcopy(PLAYER if player is None else player)
        if mutate:
            mutate(profile)
    populate(w, mutate=profile_change, config=lambda cfg: cfg['loadouts'].update(copy.deepcopy(LOADOUTS if loadouts is None else loadouts)))


def config_of(w):
    return w.client.get('/api/config').get_json()


def loadout(w, case, name, body, revision=None, *, expect=None, family=LOADOUT, **extra):
    request = dict(name=name, body=body)
    if revision is not None:
        request['expected_revision'] = revision
    request.update(extra)
    reply = w.post(family, case, LOADOUT_URL, request)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def loadout_patch_cases(gen):
    with gen.world('lo_ok') as w:
        equipped(w)
        first = w.get(LOADOUT, 'read_before_the_first_save', '/api/config').get_json()
        revision = first['config_revision']
        steps = [('global_preset_selected', {'global_preset': 'Farm Run'}),
                 ('category_presets_selected', {'module_preset': 'Farm', 'guardian_preset': 'Farm'}),
                 ('manual_cards_and_guardians', {'cards': 'main_farm', 'guardians': ['bounty', 'fetch']}),
                 ('cards_only_the_other_categories_are_left_untouched', {'cards': 'main_farm'}),
                 ('workshop_and_bot_presets', {'workshop_preset': 'Full Upgraded', 'bot_preset': 'Farm'})]
        for case, body in steps:
            reply = loadout(w, case, 'plain', body, revision, expect=200).get_json()
            assert reply['ok'] and reply['loadouts']['plain'] == body and reply['config_revision'] != revision
            revision = reply['config_revision']
        placeholder = loadout(w, 'placeholder_body_for_a_loadout_no_run_uses', 'later', {'defined': False}, revision, expect=200).get_json()
        revision = placeholder['config_revision']
        read = w.get(LOADOUT, 'read_after_the_saves_holds_every_loadout', '/api/config').get_json()
        assert read['config_revision'] == revision and read['loadouts']['plain'] == steps[-1][1]
        loadout(w, 'saved_without_an_expected_revision_is_accepted', 'spare', {'cards': 'main_farm'}, expect=200)
    with gen.world('lo_warnings') as w:
        equipped(w)
        reply = loadout(w, 'manual_equipment_returns_the_corruption_advisory', 'plain',
                        {'modules': [['amplifying_strike', 'attack']]}, None).get_json()
        assert reply['ok'] is True, reply
        assert reply['warnings'], 'the manual module list should carry the standing advisory'


def loadout_patch_refusals(gen):
    with gen.world('lo_refused') as w:
        equipped(w)
        revision = config_of(w)['config_revision']
        refusals = [('global_preset_not_on_the_account', 'plain', {'global_preset': 'Nope'}),
                    ('global_preset_mixed_with_cards', 'plain', {'global_preset': 'Farm Run', 'cards': 'main_farm'}),
                    ('module_preset_not_on_the_account', 'plain', {'module_preset': 'Nope'}),
                    ('workshop_preset_not_on_the_account', 'plain', {'workshop_preset': 'Nope'}),
                    ('cards_preset_not_on_the_account', 'plain', {'cards': 'nope'}),
                    ('guardian_not_owned', 'plain', {'guardians': ['bounty', 'ghost']}),
                    ('module_not_equipped', 'plain', {'modules': [['ghost_module', 'attack']]}),
                    ('placeholder_body_for_the_loadout_a_run_uses', 'plain', {'defined': False})]
        for case, name, body in refusals:
            reply = loadout(w, case, name, body, revision, expect=400).get_json()
            assert reply['ok'] is False and reply['problems'] and reply['error'] == reply['problems'][0], case
        for case, name, body, status in (('body_empty', 'plain', {}, 400), ('body_a_list', 'plain', ['cards'], 400),
                                         ('body_missing', 'plain', None, 400), ('name_unknown_404', 'ghost', {'cards': 'main_farm'}, 404),
                                         ('name_blank_400', ' ', {'cards': 'main_farm'}, 400), ('name_not_text_400', 5, {'cards': 'main_farm'}, 400)):
            reply = loadout(w, case, name, body, revision, expect=status).get_json()
            assert reply['ok'] is False, case
        raw_post(w, LOADOUT, 'body_not_json', LOADOUT_URL, 'name=plain', content_type='text/plain')
        assert config_of(w)['config_revision'] == revision, 'a refused loadout patch changed config.yaml'
        w.get(LOADOUT, 'read_after_the_refusals_nothing_changed', '/api/config')
    with gen.world('lo_missing_inventory') as w:
        equipped(w, player={})
        revision = config_of(w)['config_revision']
        reply = loadout(w, 'inventory_missing_the_account_cannot_verify_what_it_equips', 'plain', {'cards': 'main_farm'}, revision, expect=400).get_json()
        assert 'cannot verify' in reply['error']
        loadout(w, 'a_selection_of_a_preset_with_no_inventory_is_refused', 'plain', {'module_preset': 'Farm'}, revision, expect=400)
    with gen.world('lo_state') as w:
        equipped(w)
        revision = config_of(w)['config_revision']
        assert w.plain_post(LOADOUT_URL, dict(name='spare', body={'cards': 'main_farm'}, expected_revision=revision)).status_code == 200
        stale = loadout(w, 'stale_revision_409', 'plain', {'cards': 'main_farm'}, revision, expect=409).get_json()
        assert stale['code'] == 'revision_conflict' and stale['config_revision'] != revision
        loadout(w, 'an_empty_body_is_judged_before_the_revision', 'plain', {}, revision, expect=400)
        loadout(w, 'stale_revision_is_judged_before_the_name', 'ghost', {'cards': 'main_farm'}, revision, expect=409)
        real = w.dash._profile_check
        w.mp.setattr(w.dash, '_profile_check', raising(RuntimeError('the validator could not be loaded')))
        loadout(w, 'validator_unavailable_500', 'plain', {'cards': 'main_farm'}, expect=500)
        w.mp.setattr(w.dash, '_profile_check', real)
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            locked = loadout(w, 'action_lock_another_action_in_progress', 'plain', {'cards': 'main_farm'}, expect=409).get_json()
        finally:
            w.dash._ACTION_LOCK.release()
        assert locked['code'] == 'action_lock'
    with gen.world('lo_no_profile') as w:
        equipped(w)
        w.edit_config(lambda cfg: cfg.update(active_profile=None))
        reply = loadout(w, 'no_active_profile_the_body_is_not_validated_against_any_run', 'plain', {'cards': 'nope'}, expect=200).get_json()
        assert reply['ok'] is True
    with gen.world('lo_write_failed') as w:
        equipped(w)
        revision = config_of(w)['config_revision']
        real = w.dash.save_config
        w.mp.setattr(w.dash, 'save_config', raising(OSError('config.yaml could not be written')))
        failed = loadout(w, 'write_failed_nothing_saved_500', 'plain', {'cards': 'main_farm'}, revision, expect=500).get_json()
        assert failed['error'].startswith('write failed')
        assert config_of(w)['config_revision'] == revision
        w.mp.setattr(w.dash, 'save_config', real)

        def written_then_failed(cfg):
            real(cfg)
            raise OSError('the answer could not be built after the file was written')
        w.mp.setattr(w.dash, 'save_config', written_then_failed)
        loadout(w, 'written_then_the_answer_failed_500', 'plain', {'cards': 'main_farm'}, revision, expect=500)
        w.mp.setattr(w.dash, 'save_config', real)
        held = w.get(LOADOUT, 'read_after_an_error_that_followed_the_write_holds_the_change', '/api/config').get_json()
        assert held['loadouts']['plain'] == {'cards': 'main_farm'} and held['config_revision'] != revision
        again = loadout(w, 'identical_resend_after_a_committed_write_is_refused_as_stale', 'plain', {'cards': 'main_farm'}, revision, expect=409).get_json()
        assert again['code'] == 'revision_conflict'


def run_equipment_cases(gen):
    loadout_patch_cases(gen)
    loadout_patch_refusals(gen)

# ------------------------------------------------------------ collection (C-01)
def source(w, name='mine'):
    return w.client.get(f'/api/profile-src/{name}').get_json()


def patch(w, family, case, path, *, value=None, revision=None, delete=False, expect=None, name='mine'):
    body = dict(name=name, path=path)
    if revision is not None:
        body['expected_revision'] = revision
    if delete:
        body['op'] = 'delete'
    else:
        body['value'] = value
    reply = w.post(family, case, PATCH_URL, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


GATHER = ['policies', 'gather']
WEEKEND = {'label': 'Weekend gathering', 'flying_gem': True, 'gem_delay_sec': [4, 12], 'ad_gems': True, 'quests_8h': True,
           'quest_rewards': True, 'guild': True, 'guild_store': False, 'free_store_gems': True,
           'ad_gem_options': {'confirmed_cooldown_sec': [600, 900], 'attempt_timeout_sec': 45, 'poll_sec': [2, 4]},
           'gem_orbit': {'enabled': True, 'center': [510, 805], 'radius': 170, 'angle_deg': 135, 'interval_sec': 900,
                         'attempt_timeout_sec': 40, 'retry_interval_sec': 5, 'taps': 3, 'jitter_frac': 0.03, 'spread_deg': 6}}


def collection_policy_cases(gen):
    F = COLLECT
    with gen.world('co_ok') as w:
        populate(w)
        revision = source(w)['revision']
        w.get(F, 'read_before_the_first_save', '/api/profile-src/mine')

        def step(case, path, **kw):
            nonlocal revision
            reply = patch(w, F, case, path, revision=revision, expect=200, **kw).get_json()
            assert reply['ok'] and reply['revision'] != revision
            revision = reply['revision']
            return reply
        step('policy_added', GATHER + ['weekend'], value=WEEKEND)
        step('a_run_bound_to_the_new_policy', ['blueprints', 'farm', 'policies', 'gather'], value='weekend')
        step('guild_store_spending_switched_on', GATHER + ['weekend', 'guild_store'], value=True)
        step('guild_store_spending_switched_off_again', GATHER + ['weekend', 'guild_store'], value=False)
        step('orbit_geometry_changed', GATHER + ['weekend', 'gem_orbit'], value=dict(WEEKEND['gem_orbit'], center=[540, 900], radius=200.5, angle_deg=-90))
        step('orbit_window_longer_than_its_interval_is_accepted', GATHER + ['weekend', 'gem_orbit', 'interval_sec'], value=5)
        step('ad_gem_delay_bounds_whole_seconds', GATHER + ['weekend', 'gem_delay_sec'], value=[0, 3600])
        step('ad_gem_cooldown_as_a_range', GATHER + ['weekend', 'ad_gem_options', 'confirmed_cooldown_sec'], value=[300, 1200])
        step('retry_cooldown_separate_from_the_collection_cadence', GATHER + ['weekend', 'ad_gem_options', 'retry_cooldown_sec'], value=[10, 30])
        step('chores_reordered_and_one_switched_off', ['policies', 'chores'], value=[{'name': 'shatter', 'enabled': True}, {'name': 'quest_scan', 'enabled': False}])
        step('policy_added_that_no_run_uses', GATHER + ['spare'], value=dict(WEEKEND, label='Spare'))
        step('policy_nothing_uses_deleted', GATHER + ['spare'], delete=True)
        step('a_run_rebound_to_another_policy', ['blueprints', 'farm', 'policies', 'gather'], value='all_on')
        step('the_policy_its_run_left_deleted', GATHER + ['weekend'], delete=True)
        final = w.get(F, 'read_after_the_saves_holds_every_change', '/api/profile-src/mine').get_json()
        gather = final['profile']['policies']['gather']
        assert final['revision'] == revision and set(gather) == {'all_on', 'gems_only', 'hands_off'}
        assert final['profile']['blueprints']['farm']['policies']['gather'] == 'all_on'
        assert [c['name'] for c in final['profile']['policies']['chores']] == ['shatter', 'quest_scan']
    with gen.world('co_refused') as w:
        populate(w)
        revision = source(w)['revision']
        refusals = [
            ('policy_with_an_unknown_key', GATHER + ['all_on', 'speed'], 3),
            ('ad_gems_as_text', GATHER + ['all_on', 'ad_gems'], 'yes'),
            ('delay_fraction', GATHER + ['all_on', 'gem_delay_sec'], [3.5, 9]),
            ('delay_reversed', GATHER + ['all_on', 'gem_delay_sec'], [10, 3]),
            ('delay_negative', GATHER + ['all_on', 'gem_delay_sec'], [-1, 3]),
            ('delay_missing_makes_the_policy_unusable', GATHER + ['all_on', 'gem_delay_sec'], None),
            ('ad_cooldown_below_the_minimum', GATHER + ['all_on', 'ad_gem_options', 'confirmed_cooldown_sec'], [0, 5]),
            ('ad_cooldown_reversed', GATHER + ['all_on', 'ad_gem_options', 'confirmed_cooldown_sec'], [900, 300]),
            ('ad_window_below_the_minimum', GATHER + ['all_on', 'ad_gem_options', 'attempt_timeout_sec'], 4),
            ('ad_window_above_the_maximum', GATHER + ['all_on', 'ad_gem_options', 'attempt_timeout_sec'], 61),
            ('ad_edge_inset_fraction', GATHER + ['all_on', 'ad_gem_options', 'edge_inset_px'], 5.5),
            ('ad_option_unknown', GATHER + ['all_on', 'ad_gem_options', 'speed'], 3),
            ('orbit_interval_zero', GATHER + ['all_on', 'gem_orbit'], {'enabled': True, 'interval_sec': 0}),
            ('orbit_window_too_long', GATHER + ['all_on', 'gem_orbit'], {'enabled': True, 'attempt_timeout_sec': 61}),
            ('orbit_retry_below_the_minimum', GATHER + ['all_on', 'gem_orbit'], {'enabled': True, 'retry_interval_sec': 0.1}),
            ('orbit_center_of_three', GATHER + ['all_on', 'gem_orbit'], {'enabled': True, 'center': [1, 2, 3]}),
            ('orbit_taps_as_text', GATHER + ['all_on', 'gem_orbit'], {'enabled': True, 'taps': 'a'}),
            ('orbit_unknown_key', GATHER + ['all_on', 'gem_orbit'], {'enabled': True, 'speed': 1}),
            ('run_bound_to_a_policy_that_does_not_exist', ['blueprints', 'farm', 'policies', 'gather'], 'ghost'),
            ('chore_unknown', ['policies', 'chores'], [{'name': 'tidy', 'enabled': True}]),
            ('chore_without_a_name', ['policies', 'chores'], [{'enabled': True}]),
            ('chores_not_a_list', ['policies', 'chores'], {'quest_scan': True}),
        ]
        for case, path, value in refusals:
            reply = patch(w, F, case, path, value=value, revision=revision, expect=400).get_json()
            assert reply['ok'] is False and reply['problems'], case
        gone = patch(w, F, 'policy_deleted_while_a_run_uses_it', GATHER + ['gems_only'], revision=revision, delete=True, expect=400).get_json()
        assert 'unknown policy' in gone['error']
        assert source(w)['revision'] == revision
        w.get(F, 'read_after_the_refusals_nothing_changed', '/api/profile-src/mine')
    with gen.world('co_missing_policy') as w:
        populate(w, mutate=lambda profile: profile['blueprints']['farm'].update(policies={'gather': 'ghost'}))
        src = w.get(F, 'a_run_bound_to_a_missing_policy_is_a_problem_the_page_must_show', '/api/profile-src/mine').get_json()
        assert src['ok'] and src['problems'] and 'ghost' in src['problems'][0]


def run_collection_cases(gen):
    collection_policy_cases(gen)

# ------------------------------------------------------------- abilities (A-01)
BASELINE = {'black_hole': True, 'chronofield': True, 'death_wave': True, 'golden_tower': True, 'poison_swamp': True, 'spotlight': True}
UW_POLICIES = {
    'farm_cl': {'label': 'Farm choreography', 'baseline': BASELINE,
                'chain_lightning': {'mode': 'fleet_marks', 'pre_mark_waves': [5, 25], 'off_after_waves': [53, 72], 'always_on_above': [6080, 6120]}},
    'tourney_cl': {'label': 'Tournament', 'baseline': BASELINE, 'chain_lightning': {'mode': 'off_until_wave', 'on_above': [500, 550]}}}
BURST = {'burst': {'cancel_sprint': True, 'fire': 'demon_mode', 'retaps': 3}}
RULES = [{'when': {'bar': 'wall', 'below': 0.02, 'falling_samples': 2, 'deadband': 0.01}, 'do': BURST},
         {'when': {'wall_collapse': {'from_above': 0.3}}, 'do': BURST},
         {'when': {'fleet_mark': {'after_waves': 3, 'window_waves': 60}}, 'do': {'fire': {'button': 'nuke', 'throttle_sec': 5}}}]
RESCUE_POLICIES = {
    'wall_drain': {'label': 'Wall drain', 'arm': {'on': 'second_wind', 'immunity_sec': None, 'watch_sec': 30},
                   'end_sprint_after_sw': False, 'rules': copy.deepcopy(RULES)},
    'any_falling': {'arm': {'on': 'second_wind', 'immunity_sec': 8, 'watch_sec': None}, 'end_sprint_after_sw': False, 'rules': copy.deepcopy(RULES[:2])}}


def with_abilities(w, *, player=None, mutate=None):
    def change(profile):
        profile['player'] = copy.deepcopy(PLAYER if player is None else player)
        profile['policies']['uw_policies'] = copy.deepcopy(UW_POLICIES)
        profile['policies']['rescue_policies'] = copy.deepcopy(RESCUE_POLICIES)
        profile['blueprints']['farm_deep']['policies'].update(uw='farm_cl', rescue='wall_drain')
        profile['blueprints']['tourney']['policies'].update(uw='tourney_cl', rescue='any_falling')
        if mutate:
            mutate(profile)
    populate(w, mutate=change, config=lambda cfg: cfg['loadouts'].update(copy.deepcopy(LOADOUTS)))


UW, RESCUE = ['policies', 'uw_policies'], ['policies', 'rescue_policies']


def ability_policy_cases(gen):
    F = ABILITY
    with gen.world('ab_ok') as w:
        with_abilities(w)
        revision = source(w)['revision']
        read = w.get(F, 'read_before_the_first_save', '/api/profile-src/mine').get_json()
        assert read['problems'] == [], read['problems']

        def step(case, path, **kw):
            nonlocal revision
            reply = patch(w, F, case, path, revision=revision, expect=200, **kw).get_json()
            assert reply['ok'] and reply['revision'] != revision
            revision = reply['revision']
        step('uw_policy_added', UW + ['calm'], value={'label': 'Calm', 'baseline': dict(BASELINE, spotlight=False)})
        step('uw_baseline_weapon_switched_off', UW + ['farm_cl', 'baseline', 'spotlight'], value=False)
        step('uw_weapon_the_account_does_not_own_is_allowed_in_a_baseline', UW + ['farm_cl', 'baseline', 'smart_missiles'], value=True)
        step('chain_lightning_mode_always_on', UW + ['farm_cl', 'chain_lightning'], value={'mode': 'always_on'})
        step('chain_lightning_fleet_marks_with_ranges', UW + ['farm_cl', 'chain_lightning'],
             value={'mode': 'fleet_marks', 'pre_mark_waves': [5, 30], 'off_after_waves': [50, 70], 'always_on_above': [6000, 6100]})
        step('chain_lightning_off_until_a_wave_range', UW + ['tourney_cl', 'chain_lightning'], value={'mode': 'off_until_wave', 'on_above': [400, 450]})
        step('chain_lightning_off', UW + ['tourney_cl', 'chain_lightning'], value={'mode': 'off'})
        step('rescue_arming_watch_and_immunity_changed', RESCUE + ['wall_drain', 'arm'], value={'on': 'second_wind', 'immunity_sec': 12, 'watch_sec': 45})
        step('rescue_end_the_sprint_after_second_wind', RESCUE + ['wall_drain', 'end_sprint_after_sw'], value=True)
        step('rescue_rule_appended_at_the_end', RESCUE + ['wall_drain', 'rules', 3],
             value={'when': {'bar': 'wall', 'below': 0.2, 'falling_samples': 2, 'deadband': 0.01}, 'do': {'fire': {'button': 'demon_mode', 'throttle_sec': 10}}})
        step('rescue_rule_cooldown_and_repeat_changed', RESCUE + ['wall_drain', 'rules', 2, 'do', 'fire'], value={'button': 'nuke', 'throttle_sec': 20})
        step('rescue_burst_retaps_changed', RESCUE + ['wall_drain', 'rules', 0, 'do', 'burst', 'retaps'], value=5)
        step('rescue_rules_reordered', RESCUE + ['wall_drain', 'rules'], value=[RULES[2], RULES[0], RULES[1]])
        step('rescue_rule_deleted', RESCUE + ['wall_drain', 'rules', 2], delete=True)
        step('rescue_policy_added', RESCUE + ['extra'], value=copy.deepcopy(RESCUE_POLICIES['any_falling']))
        step('rescue_policy_bound_to_a_run', ['blueprints', 'farm_deep', 'policies', 'rescue'], value='extra')
        step('unused_uw_policy_deleted', UW + ['calm'], delete=True)
        step('global_demon_mode_timing_is_a_settings_module_not_a_policy', ['policies', 'global_behaviors', 'demon_mode_always'], value=True)
        final = w.get(F, 'read_after_the_saves_holds_every_change', '/api/profile-src/mine').get_json()
        assert final['revision'] == revision and final['problems'] == []
    with gen.world('ab_refused') as w:
        with_abilities(w)
        revision = source(w)['revision']
        refusals = [
            ('uw_baseline_weapon_unknown', UW + ['farm_cl', 'baseline', 'laser'], True),
            ('uw_baseline_value_not_a_boolean', UW + ['farm_cl', 'baseline', 'spotlight'], 'on'),
            ('chain_lightning_unknown_mode', UW + ['farm_cl', 'chain_lightning'], {'mode': 'sometimes'}),
            ('chain_lightning_mode_missing', UW + ['farm_cl', 'chain_lightning'], {}),
            ('chain_lightning_range_reversed', UW + ['farm_cl', 'chain_lightning'], {'mode': 'off_until_wave', 'on_above': [550, 500]}),
            ('chain_lightning_range_of_three', UW + ['farm_cl', 'chain_lightning'], {'mode': 'off_until_wave', 'on_above': [1, 2, 3]}),
            ('chain_lightning_range_as_text', UW + ['farm_cl', 'chain_lightning'], {'mode': 'off_until_wave', 'on_above': 'late'}),
            ('chain_lightning_unknown_key', UW + ['farm_cl', 'chain_lightning', 'speed'], 3),
            ('uw_policy_unknown_key', UW + ['farm_cl', 'speed'], 3),
            ('rescue_arm_unknown_trigger', RESCUE + ['wall_drain', 'arm', 'on'], 'sometimes'),
            ('rescue_watch_negative', RESCUE + ['wall_drain', 'arm', 'watch_sec'], -5),
            ('rescue_immunity_as_text', RESCUE + ['wall_drain', 'arm', 'immunity_sec'], 'long'),
            ('rescue_rule_unknown_bar', RESCUE + ['wall_drain', 'rules', 0, 'when', 'bar'], 'mana'),
            ('rescue_rule_threshold_above_one', RESCUE + ['wall_drain', 'rules', 0, 'when', 'below'], 1.5),
            ('rescue_rule_samples_zero', RESCUE + ['wall_drain', 'rules', 0, 'when', 'falling_samples'], 0),
            ('rescue_rule_unknown_trigger', RESCUE + ['wall_drain', 'rules', 0], {'when': {'moon': 'full'}, 'do': BURST}),
            ('rescue_rule_without_an_action', RESCUE + ['wall_drain', 'rules', 0], {'when': RULES[0]['when']}),
            ('rescue_rule_unknown_action', RESCUE + ['wall_drain', 'rules', 0, 'do'], {'dance': True}),
            ('rescue_rule_unknown_button', RESCUE + ['wall_drain', 'rules', 2, 'do', 'fire', 'button'], 'old_button'),
            ('rescue_rule_throttle_negative', RESCUE + ['wall_drain', 'rules', 2, 'do', 'fire', 'throttle_sec'], -1),
            ('rescue_burst_retaps_zero', RESCUE + ['wall_drain', 'rules', 0, 'do', 'burst', 'retaps'], 0),
            ('rescue_policy_mixes_wall_and_hp_bars', RESCUE + ['wall_drain', 'rules', 3],
             {'when': {'bar': 'hp', 'below': 0.2, 'falling_samples': 2, 'deadband': 0.01}, 'do': {'fire': {'button': 'demon_mode', 'throttle_sec': 10}}}),
            ('rescue_rule_trigger_with_a_bar_and_a_collapse_together', RESCUE + ['wall_drain', 'rules', 0, 'when'], {'bar': 'wall', 'below': 0.02, 'wall_collapse': {'from_above': 0.3}}),
            ('rescue_rules_not_a_list', RESCUE + ['wall_drain', 'rules'], {'a': 1}),
            ('run_bound_to_a_rescue_policy_that_does_not_exist', ['blueprints', 'farm_deep', 'policies', 'rescue'], 'ghost'),
            ('run_bound_to_a_uw_policy_that_does_not_exist', ['blueprints', 'farm_deep', 'policies', 'uw'], 'ghost'),
        ]
        for case, path, value in refusals:
            reply = patch(w, F, case, path, value=value, revision=revision, expect=400).get_json()
            assert reply['ok'] is False and reply['problems'], case
        for case, path in (('rescue_policy_deleted_while_a_run_uses_it', RESCUE + ['wall_drain']), ('uw_policy_deleted_while_a_run_uses_it', UW + ['farm_cl'])):
            reply = patch(w, F, case, path, revision=revision, delete=True, expect=400).get_json()
            assert 'unknown policy' in reply['error'], case
        assert source(w)['revision'] == revision
        w.get(F, 'read_after_the_refusals_nothing_changed', '/api/profile-src/mine')
    with gen.world('ab_unverified') as w:
        # the account has not verified its abilities / owns no wall: binding a run to the policy is refused, the policy itself is fine
        def unverified(player):
            player = copy.deepcopy(PLAYER)
            player['abilities_verified'] = False
            return player
        with_abilities(w, player=unverified(None))
        src = w.get(F, 'abilities_not_verified_is_an_advisory_not_a_problem', '/api/profile-src/mine').get_json()
        assert src['problems'] == [] and len(src['warnings']) == 2 and 'abilities_verified' in src['warnings'][0], src
    with gen.world('ab_no_wall') as w:
        with_abilities(w, mutate=lambda profile: profile['player'].update(wall=False))
        src = w.get(F, 'no_wall_confirmed_is_an_advisory_not_a_problem', '/api/profile-src/mine').get_json()
        assert src['problems'] == [] and len(src['warnings']) == 2 and 'wall' in src['warnings'][0], src
    with gen.world('ab_unowned') as w:
        def unowned(profile):
            profile['player']['abilities'] = {'nuke': False, 'demon_mode': False}
            profile['player']['uws']['chain_lightning'] = False
        with_abilities(w, mutate=unowned)
        src = w.get(F, 'abilities_the_account_does_not_own_are_bound_to_a_run_is_a_problem', '/api/profile-src/mine').get_json()
        assert len(src['problems']) == 7 and 'does not own' in src['problems'][0], src['problems']
        assert patch(w, F, 'binding_another_run_to_an_unowned_ability_is_refused', ['blueprints', 'farm', 'policies', 'rescue'], value='wall_drain',
                     revision=src['revision'], expect=400).get_json()['problems']
    with gen.world('ab_retired_rule') as w:
        with_abilities(w, mutate=lambda profile: profile['policies']['rescue_policies']['wall_drain']['rules'].append(
            {'when': {'bar': 'wall', 'below': 0.3, 'falling_samples': 2}, 'do': {'switch_cards': 'main_farm'}}))
        src = w.get(F, 'a_rule_with_an_action_this_version_does_not_support', '/api/profile-src/mine').get_json()
        assert src['problems']


def run_abilities_cases(gen):
    ability_policy_cases(gen)