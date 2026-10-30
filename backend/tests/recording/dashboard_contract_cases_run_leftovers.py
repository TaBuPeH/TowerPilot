"""Contract cases the Runs leftovers need (frontend brief f6-11-runs-leftovers-brief.md section 6; r17 decision 9.5 "whole-plan removal").

Five families of dashboard_contract_pages_v1.json, each the real route over the synthetic world of the other pages:

  run_controls_live_reset_all  POST /api/operations/{id}/live-controls with `null` for several overrides in ONE request (brief 6.1, RL2);
  run_entry_patch              POST /api/profile-patch on ['blueprints', <run>, <field>] for a coin, a tournament, a shard and a dissonance
                               coin run (6.2, RL3);
  run_templates_add            POST /api/run-templates/add for every shipped recipe, its refusals and the reads around it (6.3, RL4);
  battle_details_runtime       GET /api/battles/active with a recorded Floating gems refusal and an Ad gems offer that was seen and failed (6.4, RL7);
  plan_section_delete          POST /api/profile-patch {path: ["plan"], op: "delete"} and the reads around it (6.5, RL8).

They are NEW families (not appended to run_controls_live, profile_patch or battles_active) so that every family already published keeps its
bytes and its hash: the consumers pinned to those do not have to regenerate. The brief's case names are kept, under the new family name.
"""
import copy

from control import operations
from dashboard_contract_cases_editors import LOADOUTS as EQUIPMENT, with_abilities
from dashboard_contract_cases_profile import COIN, DAY, PATCH_URL, SHARD, TOURNEY
from dashboard_contract_cases_runs import live_body, live_url
from dashboard_contract_cases_profile import write_profile
from dashboard_contract_pages_world import populate, raw_post
from test_dashboard_contract import Driver, raising

RESET, ENTRY, TEMPLATES, DETAILS, PLAN_DELETE, TEMPLATES_FENCED = ('run_controls_live_reset_all', 'run_entry_patch', 'run_templates_add',
                                                                   'battle_details_runtime', 'plan_section_delete', 'run_templates_add_fenced')


def active(w, family, case):
    return w.get(family, case, '/api/battles/active')


def source(w, family=None, case=None, name='mine'):
    path = f'/api/profile-src/{name}'
    reply = w.get(family, case, path) if case else w.client.get(path)
    return reply.get_json()


# ------------------------------------------------------------- 6.1 reset every override at once
def leaves(overrides, prefix=''):
    """The paths that still hold an override: `overrides` keeps the (empty) routine mappings after a reset, so "no override" means no leaf."""
    found = []
    for key, value in (overrides or {}).items():
        found += leaves(value, f'{prefix}{key}.') if isinstance(value, dict) else [prefix + key]
    return found


def live_reset(w, case, d, changes, key, **override):
    return w.post(RESET, case, live_url(d), live_body(d, changes, key, **override))


def run_controls_live_reset_all_cases(gen):
    with gen.world('rlr_reset_all') as w:
        d = Driver(w).go_live()
        two = {'routines.ad_gems.interval_sec': 600, 'routines.floating_gems.enabled': True}
        first = live_reset(w, 'two_overrides_set_before_the_reset', d, two, 'rlr-1')
        assert first.status_code == 200, first.get_json()
        d.checkpoint()
        before = active(w, RESET, 'read_before_the_reset_two_overrides_are_in_force').get_json()['battle']
        assert sorted(leaves(before['overrides'])) == sorted(two) and before['applied_revision'] == before['control_revision'] == 1, before
        reset = live_reset(w, 'accepted_null_for_every_override_resets_them_together', d,
                           {path: None for path in two}, 'rlr-2')
        assert reset.status_code == 200 and reset.get_json()['command']['state'] == 'requested', reset.get_json()
        d.checkpoint()
        after = active(w, RESET, 'read_after_every_override_was_reset').get_json()['battle']
        assert not leaves(after['overrides']) and after['applied_revision'] == after['control_revision'] == 2, after
        replay = live_reset(w, 'identical_resend_of_the_reset_answered_from_the_record', d, {path: None for path in two}, 'rlr-2', expected_revision=1)
        assert replay.status_code == 200 and replay.get_json()['command']['id'] == reset.get_json()['command']['id']
        live_reset(w, 'reset_of_paths_that_no_longer_hold_an_override', d, {path: None for path in two}, 'rlr-3')
        d.checkpoint()
        active(w, RESET, 'read_after_the_second_reset_nothing_to_remove')
    with gen.world('rlr_reset_partial') as w:
        d = Driver(w).go_live()
        live_reset(w, 'three_overrides_set', d, {'routines.ad_gems.interval_sec': 600, 'routines.ad_gems.window_sec': 20,
                                                'routines.floating_gems.enabled': True}, 'rlr-p1')
        d.checkpoint()
        live_reset(w, 'reset_two_of_the_three_keeps_the_third', d, {'routines.ad_gems.interval_sec': None, 'routines.floating_gems.enabled': None}, 'rlr-p2')
        d.checkpoint()
        kept = active(w, RESET, 'read_after_the_partial_reset_one_override_remains').get_json()['battle']
        assert leaves(kept['overrides']) == ['routines.ad_gems.window_sec'] and kept['applied_revision'] == kept['control_revision'], kept
    with gen.world('rlr_reset_refused') as w:
        d = Driver(w).go_live()
        live_reset(w, 'overrides_set', d, {'routines.ad_gems.interval_sec': 600, 'routines.floating_gems.enabled': True}, 'rlr-r1')
        d.checkpoint()
        stale = live_reset(w, 'reset_all_with_a_stale_control_revision_refused', d, {'routines.ad_gems.interval_sec': None, 'routines.floating_gems.enabled': None},
                           'rlr-r2', expected_revision=0)
        assert stale.status_code == 409, stale.get_json()
        unknown = live_reset(w, 'reset_all_with_one_unknown_path_refuses_the_whole_request', d,
                             {'routines.ad_gems.interval_sec': None, 'routines.ad_gems.speed': None}, 'rlr-r3')
        assert unknown.status_code == 400, unknown.get_json()
        still = active(w, RESET, 'read_after_the_refusals_both_overrides_remain').get_json()['battle']
        assert len(leaves(still['overrides'])) == 2 and still['control_revision'] == 1, still
        empty = live_reset(w, 'reset_with_no_path_refused', d, {}, 'rlr-r4')
        assert empty.status_code == 400, empty.get_json()


# ------------------------------------------------------------- 6.4 battle details (RL7)
def with_orbit_and_shopping(profile):
    profile['presets'] = profile.get('presets', {})


def run_battle_details_cases(gen):
    from test_dashboard_contract import with_orbit
    with gen.world('bd_floating_refusal') as w:
        w.edit_profile(with_orbit)
        d = Driver(w)
        d.observe()
        orbit = d.rs.gems.orbit
        w.mp.setattr('flows.shard.time.time', w.clock.time)
        orbit.window_id, orbit.attempt_id = f'{w.name}-orbit-window', f'{w.name}-orbit-attempt'
        orbit.window_ends_at = w.clock.monotonic() + 30        # a window is open, as it always is when the tapper refuses
        orbit._backoff('invalid_orbit_arc')          # the tapper's own refusal path: it records last_refusal
        d.publish()
        got = active(w, DETAILS, 'floating_gems_refusal_recorded').get_json()['battle']['runtime']['routines']['floating_gems']
        assert got['last_refusal']['reason'] == 'invalid_orbit_arc' and got['last_refusal']['observed_at'], got
    with gen.world('bd_ad_offer_failed') as w:
        w.edit_profile(with_orbit)
        d = Driver(w)
        d.observe()
        ad = d.rs.gems.ad_gems
        now = w.clock.monotonic()
        ad.visibility, ad.visibility_reason = 'visible', 'battle_frame'
        ad.first_seen_at, ad.appearance_after_at, ad.last_observed_at, ad.claim_at = now - 9, now - 14, now - 1, now - 4
        ad.counters.update(checks=7, seen=1, attempts=1)      # one attempt was made (the poll counts it before it taps)
        d.ad_outcome(False)                           # the collector's own record of an attempt that did not remove the offer
        d.publish()
        got = active(w, DETAILS, 'ad_gems_offer_seen_and_failed_attempt').get_json()['battle']['runtime']['routines']['ad_gems']
        assert got['visibility'] == 'visible' and got['last_failed_attempt'] and got['counters']['attempts'] >= 1, got


# ------------------------------------------------------------- 6.5 remove the whole plan section (RL8)
def run_plan_section_delete_cases(gen):
    body = lambda revision, **extra: dict(name='mine', path=['plan'], op='delete', expected_revision=revision, **extra)
    with gen.world('psd_delete') as w:
        populate(w)
        r = source(w, PLAN_DELETE, 'read_before_the_plan_section_was_deleted')['revision']
        reply = w.post(PLAN_DELETE, 'saved_the_whole_plan_section_deleted', PATCH_URL, body(r))
        assert reply.status_code == 200 and 'plan' not in reply.get_json()['profile'], reply.get_json()
        after = source(w, PLAN_DELETE, 'read_after_the_plan_section_was_deleted')
        assert 'plan' not in after['profile'] and after['revision'] == reply.get_json()['revision'], after
        again = w.post(PLAN_DELETE, 'plan_section_delete_when_absent', PATCH_URL, body(after['revision']))
        source(w, PLAN_DELETE, 'read_after_the_delete_of_an_absent_plan_section')
    with gen.world('psd_stale') as w:
        populate(w)
        r = source(w)['revision']
        assert w.plain_post(PATCH_URL, dict(name='mine', path=['plan', 'week', 'sunday'], value='farm_day', expected_revision=r)).status_code == 200
        stale = w.post(PLAN_DELETE, 'plan_section_delete_stale_revision_409', PATCH_URL, body(r))
        assert stale.status_code == 409 and stale.get_json()['code'] == 'revision_conflict', stale.get_json()
        assert 'plan' in source(w, PLAN_DELETE, 'read_after_the_stale_refusal_the_plan_section_remains')['profile']
    with gen.world('psd_without_revision') as w:
        populate(w)
        reply = w.post(PLAN_DELETE, 'plan_section_delete_without_an_expected_revision_is_accepted', PATCH_URL, dict(name='mine', path=['plan'], op='delete'))
        assert reply.status_code == 200, reply.get_json()


# ------------------------------------------------------------- 6.2 a run's own entry and equipment settings (RL3)
# (run, case, field path under the run, value): each is saved with the revision of the previous save and must be accepted
ENTRY_SAVES = [
    # a coin run: label, equipment, tier, perk bans, restart, intro-sprint cancel, wave cap, policy references, shopping
    ('farm', 'saved_label', ['label'], 'Daily coin farm'),
    ('farm', 'saved_loadout_an_existing_equipment', ['loadout'], 'plain'),
    ('farm', 'saved_loadout_as_is_on_a_coin_run', ['loadout'], 'as_is'),
    ('farm', 'saved_tier', ['tier'], 2),
    ('farm', 'saved_perk_bans_a_list', ['perk_bans'], ['coins, but tower max health']),
    ('farm', 'saved_perk_bans_an_empty_list_clears_them', ['perk_bans'], []),
    ('farm', 'saved_restart_via_home', ['restart_via_home'], True),
    ('farm', 'saved_cancel_sprint', ['cancel_sprint'], True),
    ('farm', 'saved_max_wave', ['max_wave'], 500),
    ('farm', 'saved_max_wave_null_means_no_cap', ['max_wave'], None),
    ('farm', 'saved_gathering_policy', ['policies', 'gather'], 'gems_only'),
    ('farm', 'saved_uw_policy', ['policies', 'uw'], 'farm_cl'),
    ('farm', 'saved_rescue_policy', ['policies', 'rescue'], 'wall_drain'),
    ('farm', 'saved_shopping_policy', ['shopping'], 'default_sweep'),
    # a dissonance coin run
    ('dissonance', 'saved_dissonant_tab', ['dissonant_tab'], 'defense'),
    ('dissonance', 'saved_dissonance_perk_bans', ['perk_bans'], ['coins, but tower max health', 'wave requirement']),
    # a tournament run
    ('tourney', 'saved_tournament_gem_cap', ['gem_entry_max'], 5),
    ('tourney', 'saved_tournament_gem_cap_zero_never_pays', ['gem_entry_max'], 0),
    ('tourney', 'saved_tournament_restart_via_home', ['restart_via_home'], True),
    # a shard run
    ('shards', 'saved_shard_count', ['count'], 3),
    ('shards', 'saved_shard_count_zero', ['count'], 0),
    ('shards', 'saved_shard_tier', ['tier'], 20),
]


ENTRY_DELETES = [('farm', 'saved_uw_policy_deleted_means_no_uw_plan', ['policies', 'uw']),
                 ('farm', 'saved_shopping_policy_deleted', ['shopping']),
                 ('farm', 'saved_perk_bans_deleted_leaves_the_games_bans_alone', ['perk_bans'])]


# (run, case, field path, value): each is refused 400 with the validator text and writes nothing
ENTRY_REFUSALS = [
    ('farm', 'loadout_unknown_equipment', ['loadout'], 'ghost'),
    ('tourney', 'loadout_as_is_on_a_tournament_run', ['loadout'], 'as_is'),
    ('shards', 'loadout_as_is_on_a_shard_run', ['loadout'], 'as_is'),
    ('farm', 'loadout_not_text', ['loadout'], 7),
    ('farm', 'tier_zero', ['tier'], 0),
    ('farm', 'tier_negative', ['tier'], -3),
    ('farm', 'tier_text', ['tier'], '2'),
    ('farm', 'tier_fraction', ['tier'], 2.5),
    ('farm', 'tier_true', ['tier'], True),
    ('dissonance', 'dissonant_tab_unknown', ['dissonant_tab'], 'sideways'),
    ('farm', 'perk_bans_thirty_one_entries', ['perk_bans'], [f'perk {n}' for n in range(31)]),
    ('farm', 'perk_bans_not_a_list', ['perk_bans'], 'coins'),
    ('farm', 'perk_bans_a_blank_entry', ['perk_bans'], ['coins', ' ']),
    ('tourney', 'gem_cap_negative', ['gem_entry_max'], -1),
    ('tourney', 'gem_cap_text', ['gem_entry_max'], '5'),
    ('tourney', 'gem_cap_fraction', ['gem_entry_max'], 2.5),
    ('farm', 'count_on_a_coin_run_names_the_alternative', ['count'], 3),
    ('tourney', 'count_on_a_tournament_run', ['count'], 3),
    ('shards', 'count_negative', ['count'], -1),
    ('shards', 'count_text', ['count'], '3'),
    ('farm', 'max_wave_zero', ['max_wave'], 0),
    ('farm', 'max_wave_text', ['max_wave'], '500'),
    ('farm', 'restart_via_home_text', ['restart_via_home'], 'yes'),
    ('farm', 'cancel_sprint_text', ['cancel_sprint'], 'yes'),
    ('farm', 'gathering_policy_unknown', ['policies', 'gather'], 'ghost'),
    ('farm', 'uw_policy_unknown', ['policies', 'uw'], 'ghost'),
    ('farm', 'rescue_policy_unknown', ['policies', 'rescue'], 'ghost'),
    ('farm', 'shopping_policy_unknown', ['shopping'], 'ghost'),
    ('farm', 'field_not_legal_for_a_coin_run', ['gem_entry_max'], 5),
    ('shards', 'field_not_legal_for_a_shard_run', ['cancel_sprint'], True),
    ('tourney', 'field_not_legal_for_a_tournament_run', ['dissonant_tab'], 'defense'),
    ('farm', 'field_that_no_kind_has', ['speed'], 3),
]


ENTRY_DELETE_REFUSALS = [('farm', 'tier_deleted_a_coin_run_needs_one', ['tier']),
                         ('farm', 'loadout_deleted_a_run_must_know_what_to_equip', ['loadout']),
                         ('farm', 'kind_deleted', ['kind'])]


# (recipe, case, run id, choices): each is added with the previous adds in place and must be accepted
ADD_STEPS = [
    ('farm', 'added_a_coin_run_with_only_a_label', 'coin_min', {'label': 'Minimal coin run'}),
    ('farm', 'added_a_coin_run_with_every_choice_and_both_optional_policies', 'coin_two',
     {'label': 'Second coin run', 'tier': 3, 'loadout': 'plain', 'enable_uw': True, 'enable_rescue': True}),
    ('tournament', 'added_a_tournament_run', 'tourney_two',
     {'label': 'Second tournament', 'tier': 14, 'loadout': 'plain', 'gem_entry_max': 5, 'enable_uw': True, 'enable_rescue': True}),
    ('shard', 'added_a_shard_run', 'shard_two', {'label': 'Second shard run', 'loadout': 'plain', 'tier': 18, 'count': 3}),
    ('dissonance', 'added_a_dissonance_run', 'dissonance_two',
     {'label': 'Second dissonance run', 'loadout': 'plain', 'tier': 2, 'dissonant_tab': 'defense', 'perk_bans': ['coins, but tower max health'], 'enable_uw': True, 'enable_rescue': True}),
    ('dissonance', 'added_a_dissonance_run_with_blank_perk_bans_leaves_the_games_bans_alone', 'dissonance_three',
     {'label': 'Third dissonance run', 'loadout': 'plain', 'perk_bans': ['', '  ']}),
]


# (case, recipe, run id, choices, status): each is refused and writes nothing
ADD_REFUSALS = [
    ('run_id_taken', 'farm', 'farm', {}, 400),
    ('run_id_taken_by_an_existing_dissonance_run', 'farm', 'dissonance', {}, 400),
    ('default_label_the_same_as_an_existing_runs_name_is_refused_by_the_validator', 'farm', 'new_run', {}, 400),
    ('default_label_of_the_dissonance_recipe_clashes_with_the_existing_dissonance_run', 'dissonance', 'new_run', {'loadout': 'plain'}, 400),
    ('run_id_with_a_capital', 'farm', 'Farm_Two', {}, 400),
    ('run_id_with_a_space', 'farm', 'farm two', {}, 400),
    ('run_id_starting_with_a_digit', 'farm', '2farm', {}, 400),
    ('run_id_empty', 'farm', '', {}, 400),
    ('run_id_longer_than_64_characters', 'farm', 'a' * 65, {}, 400),
    ('run_id_not_text', 'farm', 7, {}, 400),
    ('unknown_template', 'ghost', 'new_run', {}, 400),
    ('unsupported_choice', 'farm', 'new_run', {'speed': 3}, 400),
    ('count_is_a_shard_only_choice', 'farm', 'new_run', {'count': 3}, 400),
    ('gem_cap_is_a_tournament_only_choice', 'farm', 'new_run', {'gem_entry_max': 5}, 400),
    ('dissonant_tab_is_a_dissonance_only_choice', 'farm', 'new_run', {'dissonant_tab': 'defense'}, 400),
    ('enable_uw_on_a_recipe_without_uw_rules', 'shard', 'new_run', {'loadout': 'plain', 'enable_uw': True}, 400),
    ('enable_rescue_on_a_recipe_without_rescue_rules', 'shard', 'new_run', {'loadout': 'plain', 'enable_rescue': True}, 400),
    ('enable_uw_not_true_or_false', 'farm', 'new_run', {'enable_uw': 'yes'}, 400),
    ('dissonant_tab_unknown', 'dissonance', 'new_run', {'loadout': 'plain', 'dissonant_tab': 'sideways'}, 400),
    ('perk_bans_not_a_list', 'dissonance', 'new_run', {'loadout': 'plain', 'perk_bans': 'coins'}, 400),
    ('perk_bans_with_a_non_text_entry', 'dissonance', 'new_run', {'loadout': 'plain', 'perk_bans': ['coins', 5]}, 400),
    ('choices_not_an_object', 'farm', 'new_run', ['plain'], 400),
    ('tournament_default_loadout_the_account_does_not_define_is_refused_by_the_validator', 'tournament', 'new_run', {'label': 'Fresh tournament'}, 400),
    ('shard_default_loadout_the_account_does_not_define_is_refused_by_the_validator', 'shard', 'new_run', {'label': 'Fresh shard run'}, 400),
    ('dissonance_default_loadout_the_account_does_not_define_is_refused_by_the_validator', 'dissonance', 'new_run', {'label': 'Fresh dissonance run'}, 400),
    ('unknown_loadout_is_refused_by_the_validator', 'farm', 'new_run', {'label': 'Fresh run', 'loadout': 'ghost'}, 400),
    ('tier_zero_is_refused_by_the_validator', 'farm', 'new_run', {'label': 'Fresh run', 'tier': 0}, 400),
    ('tier_text_is_refused_by_the_validator', 'farm', 'new_run', {'label': 'Fresh run', 'tier': '2'}, 400),
    ('shard_count_negative_is_refused_by_the_validator', 'shard', 'new_run', {'label': 'Fresh shard run', 'loadout': 'plain', 'count': -1}, 400),
    ('gem_cap_negative_is_refused_by_the_validator', 'tournament', 'new_run', {'label': 'Fresh tournament', 'loadout': 'plain', 'gem_entry_max': -1}, 400),
]


def add_dissonance_run(profile):
    """The dissonance coin run `dissonance` of the run-entry worlds (a coin run with a tab), added to a populated profile."""
    run = copy.deepcopy(profile['blueprints']['farm_deep'])
    run.update(label='Dissonance', dissonant_tab='utility', loadout='plain')
    run['policies'] = {'gather': 'all_on'}
    profile['blueprints']['dissonance'] = run


def entry_world(w, *, profile_change=None):
    """The populated account with UW and rescue policies, equipment in config.yaml, and a dissonance coin run `dissonance`."""
    def add_dissonance(profile):
        add_dissonance_run(profile)
        if profile_change:
            profile_change(profile)
    with_abilities(w, mutate=add_dissonance)


def entry(w, case, run, fields, *, value=None, revision, delete=False, expect=None, name='mine'):
    """One field of one run, the way the Runs page sends it: {name, path:['blueprints', run, *fields], value|op:'delete', expected_revision}."""
    body = dict(name=name, path=['blueprints', run, *fields], expected_revision=revision)
    if delete:
        body['op'] = 'delete'
    else:
        body['value'] = value
    reply = w.post(ENTRY, case, PATCH_URL, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def run_entry_patch_cases(gen):
    with gen.world('rep_saved') as w:
        entry_world(w)
        revision = source(w, ENTRY, 'read_before_the_first_save')['revision']
        for run, case, fields, value in ENTRY_SAVES:
            reply = entry(w, case, run, fields, value=value, revision=revision, expect=200).get_json()
            assert reply['ok'] and reply['revision'] != revision, case
            revision = reply['revision']
        for run, case, fields in ENTRY_DELETES:
            reply = entry(w, case, run, fields, revision=revision, delete=True, expect=200).get_json()
            revision = reply['revision']
        held = source(w, ENTRY, 'read_after_the_saves_holds_every_change')
        assert held['revision'] == revision and held['problems'] == [], held
        runs = held['profile']['blueprints']
        assert runs['farm']['label'] == 'Daily coin farm' and runs['farm']['tier'] == 2 and 'uw' not in runs['farm']['policies']
        assert runs['dissonance']['dissonant_tab'] == 'defense' and runs['tourney']['gem_entry_max'] == 0 and runs['shards']['count'] == 0
    with gen.world('rep_refused') as w:
        entry_world(w)
        revision = source(w)['revision']
        for run, case, fields, value in ENTRY_REFUSALS:
            reply = entry(w, case, run, fields, value=value, revision=revision, expect=400).get_json()
            assert reply['ok'] is False and reply['problems'] and reply['error'] == reply['problems'][0], (case, reply)
        for run, case, fields in ENTRY_DELETE_REFUSALS:
            reply = entry(w, case, run, fields, revision=revision, delete=True, expect=400).get_json()
            assert reply['problems'], case
        assert source(w)['revision'] == revision, 'a refused patch changed the saved profile'
        w.get(ENTRY, 'read_after_the_refusals_nothing_changed', '/api/profile-src/mine')
    with gen.world('rep_state') as w:
        entry_world(w)
        r = source(w)['revision']
        # a second client saved first: this client's revision is stale, whatever it sends
        assert w.plain_post(PATCH_URL, dict(name='mine', path=['blueprints', 'farm', 'label'], value='Saved by another client', expected_revision=r)).status_code == 200
        stale = entry(w, 'stale_revision_409', 'farm', ['tier'], value=3, revision=r, expect=409).get_json()
        assert stale['code'] == 'revision_conflict' and stale['revision'] != r
        entry(w, 'stale_revision_is_judged_before_the_values', 'farm', ['tier'], value=0, revision=r, expect=409)
        w.get(ENTRY, 'read_after_the_stale_refusals_holds_the_other_clients_save', '/api/profile-src/mine')
        profile = w.plain_get('/api/profile-src/mine').get_json()['profile']
        write_profile(w, 'default', profile)
        write_profile(w, 'mine.draft', {'player': {}})
        starter = entry(w, 'starter_profile_refused_with_a_flag', 'farm', ['tier'], value=3, revision='any', expect=409, name='default').get_json()
        assert starter['starter'] is True
        entry(w, 'scan_draft_refused', 'farm', ['tier'], value=3, revision='any', expect=409, name='mine.draft')
        entry(w, 'unknown_profile_404', 'farm', ['tier'], value=3, revision='any', expect=404, name='ghost')
        entry(w, 'unknown_run_is_a_validator_problem', 'ghost', ['tier'], value=3, revision=source(w)['revision'], expect=400)
    with gen.world('rep_write_failed') as w:
        entry_world(w)
        r = source(w)['revision']
        real_check = w.dash._profile_check
        w.mp.setattr(w.dash, '_profile_check', raising(RuntimeError('the validator could not be loaded')))
        entry(w, 'validator_unavailable_nothing_written_500', 'farm', ['tier'], value=3, revision=r, expect=500)
        w.mp.setattr(w.dash, '_profile_check', real_check)
        w.get(ENTRY, 'read_after_the_validator_failure_the_revision_is_unchanged', '/api/profile-src/mine')
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('profiles/mine.yaml could not be written')))
        failed = entry(w, 'write_failed_nothing_saved_500', 'farm', ['tier'], value=3, revision=r, expect=500).get_json()
        assert failed['error'].startswith('write failed') and source(w)['revision'] == r
        w.get(ENTRY, 'read_after_the_failed_write_the_revision_is_unchanged', '/api/profile-src/mine')
    with gen.world('rep_answer_failed') as w:
        entry_world(w)
        r = source(w)['revision']
        real = w.dash._save_yaml_backup

        def written_then_failed(path, data):
            real(path, data)
            raise OSError('the answer could not be built after the file was written')
        w.mp.setattr(w.dash, '_save_yaml_backup', written_then_failed)
        entry(w, 'written_then_the_answer_failed_500', 'farm', ['tier'], value=3, revision=r, expect=500)
        w.mp.setattr(w.dash, '_save_yaml_backup', real)
        held = w.get(ENTRY, 'read_after_an_error_that_followed_the_write_holds_the_change', '/api/profile-src/mine').get_json()
        assert held['revision'] != r and held['profile']['blueprints']['farm']['tier'] == 3
        resend = entry(w, 'identical_resend_after_a_committed_write_is_refused_as_stale', 'farm', ['tier'], value=3, revision=r, expect=409).get_json()
        assert resend['code'] == 'revision_conflict'
    with gen.world('rep_body') as w:
        entry_world(w)
        r = source(w)['revision']
        w.post(ENTRY, 'path_to_a_run_that_is_not_a_mapping_step_400', PATCH_URL, dict(name='mine', path=['blueprints', 'farm', 'tier', 'x'], value=1, expected_revision=r))
        w.post(ENTRY, 'path_of_one_step_into_the_blueprints_only_400', PATCH_URL, dict(name='mine', path=['blueprints'], value=3, expected_revision=r))
        w.get(ENTRY, 'read_after_the_malformed_paths_nothing_changed', '/api/profile-src/mine')


# ------------------------------------------------------------- 6.3 add a run from a shipped recipe (RL4)
ADD_URL = '/api/run-templates/add'


def add_world(w, *, profile_change=None):
    """entry_world, whose equipment `plain` the recipes' default loadouts (tourney_1, shard_farm, dissonance) are not."""
    entry_world(w, profile_change=profile_change)


def add(w, case, template, run_id, options=None, *, name='mine', expect=None, body=None, family=TEMPLATES, **guards):
    """POST /api/run-templates/add the way the Runs page sends it: {profile, template, run_id, options[, expected_revision][, expected_context]}."""
    sent = body if body is not None else dict(profile=name, template=template, run_id=run_id)
    if body is None and options is not None:
        sent['options'] = options
    if body is None:
        sent.update(guards)
    reply = w.post(family, case, ADD_URL, sent)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def run_templates_add_cases(gen):
    with gen.world('rta_added') as w:
        add_world(w)
        source(w, TEMPLATES, 'read_before_the_first_add')
        w.get(TEMPLATES, 'read_the_recipes_the_page_offers', '/api/run-templates')
        for template, case, run_id, options in ADD_STEPS:
            reply = add(w, case, template, run_id, options, expect=200).get_json()
            assert reply['ok'] and run_id in reply['profile']['blueprints'] and reply['backup'], case
        held = source(w, TEMPLATES, 'read_after_the_adds_holds_every_new_run_and_its_copied_policies')
        assert held['problems'] == [] and 'coin_two__farm_cl_choreo' in held['profile']['policies']['uw_policies'], held['problems']
        assert held['profile']['blueprints']['coin_two']['policies']['uw'] == 'coin_two__farm_cl_choreo'
    with gen.world('rta_refused') as w:
        add_world(w)
        before = source(w)['revision']
        for case, template, run_id, options, status in ADD_REFUSALS:
            reply = add(w, case, template, run_id, options, expect=status).get_json()
            assert reply['ok'] is False and reply['error'], case
        assert source(w)['revision'] == before, 'a refused add changed the saved profile'
        w.get(TEMPLATES, 'read_after_the_refusals_nothing_changed', '/api/profile-src/mine')
    with gen.world('rta_body') as w:
        add_world(w)
        add(w, 'body_a_list_400', None, None, body=[1], expect=400)
        add(w, 'profile_missing_400', None, None, body=dict(template='farm', run_id='new_run'), expect=400)
        add(w, 'profile_name_with_a_path_400', None, None, body=dict(profile='../outside', template='farm', run_id='new_run'), expect=400)
        add(w, 'profile_not_text_400', None, None, body=dict(profile=5, template='farm', run_id='new_run'), expect=400)
        add(w, 'template_missing_400', None, None, body=dict(profile='mine', run_id='new_run'), expect=400)
        add(w, 'run_id_missing_400', None, None, body=dict(profile='mine', template='farm'), expect=400)
        raw_post(w, TEMPLATES, 'body_not_json_400', ADD_URL, 'profile=mine', content_type='text/plain')
        source(w, TEMPLATES, 'read_after_the_malformed_requests_nothing_changed')
    with gen.world('rta_state') as w:
        add_world(w)
        profile = source(w)['profile']
        write_profile(w, 'default', profile)
        write_profile(w, 'mine.draft', {'player': {}})
        starter = add(w, 'starter_profile_refused', 'farm', 'new_run', {}, name='default', expect=409).get_json()
        assert starter['ok'] is False
        add(w, 'scan_draft_refused', 'farm', 'new_run', {}, name='mine.draft', expect=409)
        add(w, 'unknown_profile_is_refused_with_the_operating_system_error', 'farm', 'new_run', {}, name='ghost', expect=400)
        write_profile(w, 'broken', 'blueprints: [unclosed\n')
        add(w, 'profile_file_unreadable_400', 'farm', 'new_run', {}, name='broken', expect=400)
        write_profile(w, 'list', '- a\n')
        add(w, 'profile_file_not_a_mapping_400', 'farm', 'new_run', {}, name='list', expect=400)
    with gen.world('rta_policy_taken') as w:
        def policy_exists(profile):
            profile['policies']['gather']['pre__all_on'] = copy.deepcopy(profile['policies']['gather']['all_on'])
        add_world(w, profile_change=policy_exists)
        before = source(w)['revision']
        reply = add(w, 'a_copied_policy_id_already_present_is_refused', 'farm', 'pre', {'label': 'Pre run'}, expect=400).get_json()
        assert 'already exists' in reply['error']
        assert source(w)['revision'] == before
        w.get(TEMPLATES, 'read_after_the_policy_clash_nothing_changed', '/api/profile-src/mine')
    with gen.world('rta_lost_answer') as w:
        add_world(w)
        first = add(w, 'added_a_run_and_the_answer_is_lost', 'farm', 'lost_run', {'label': 'Lost run', 'tier': 4}, expect=200).get_json()
        resend = add(w, 'identical_resend_after_a_committed_add_is_refused_the_run_exists', 'farm', 'lost_run', {'label': 'Lost run', 'tier': 4}, expect=400).get_json()
        assert 'already exists' in resend['error']
        held = source(w, TEMPLATES, 'read_after_the_resend_holds_the_one_run')
        assert held['profile']['blueprints']['lost_run']['tier'] == 4
    with gen.world('rta_write_failed') as w:
        add_world(w)
        before = source(w)['revision']
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('profiles/mine.yaml could not be written')))
        failed = add(w, 'write_failed_nothing_saved', 'farm', 'new_run', {'label': 'Fresh run'}, expect=400).get_json()
        assert failed['error'] == 'profiles/mine.yaml could not be written' and source(w)['revision'] == before
        w.get(TEMPLATES, 'read_after_the_failed_write_the_revision_is_unchanged', '/api/profile-src/mine')
    with gen.world('rta_answer_failed') as w:
        add_world(w)
        before = source(w)['revision']
        real = w.dash._save_yaml_backup

        def written_then_failed(path, data):
            real(path, data)
            raise OSError('the answer could not be built after the file was written')
        w.mp.setattr(w.dash, '_save_yaml_backup', written_then_failed)
        add(w, 'written_then_the_answer_failed', 'farm', 'half_run', {'label': 'Half run'}, expect=400)
        w.mp.setattr(w.dash, '_save_yaml_backup', real)
        held = w.get(TEMPLATES, 'read_after_an_error_that_followed_the_write_holds_the_run', '/api/profile-src/mine').get_json()
        assert held['revision'] != before and 'half_run' in held['profile']['blueprints']
        add(w, 'identical_resend_after_the_error_is_refused_the_run_exists', 'farm', 'half_run', {'label': 'Half run'}, expect=400)


# ------------------------------------------------------------- 6.3b the same add with the optional guards (root-approved behaviour change)
def fenced_world(w):
    """The add world with two accounts: alpha (profile `mine`, in force) and beta (profile `theirs`, a copy), so a switch can be staged."""
    from dashboard_contract_cases_profile import two_accounts
    add_world(w)
    write_profile(w, 'theirs', source(w)['profile'])
    w.edit_config(lambda cfg: two_accounts(cfg, active='alpha'))


def alpha(w):
    from dashboard_contract_cases_context import page_context
    return page_context(w)


def fenced(w, case, template, run_id, options, *, expect, **guards):
    return add(w, case, template, run_id, options, expect=expect, family=TEMPLATES_FENCED, **guards)


def run_templates_add_fenced_cases(gen):
    F = TEMPLATES_FENCED
    with gen.world('rtf_ok') as w:
        fenced_world(w)
        r0 = source(w, F, 'read_before_the_first_add')['revision']
        context = alpha(w)
        w.get(F, 'read_config_the_page_context_comes_from', '/api/config')
        first = fenced(w, 'added_with_the_matching_revision_and_context', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=200,
                       expected_revision=r0, expected_context=context).get_json()
        assert first['revision'] != r0 and first['ok'], first
        second = fenced(w, 'added_again_with_the_revision_the_answer_returned', 'farm', 'fenced_two', {'label': 'Fenced two'}, expect=200,
                        expected_revision=first['revision'], expected_context=context).get_json()
        held = source(w, F, 'read_after_the_chained_adds_the_revision_is_the_last_answers')
        assert held['revision'] == second['revision'] and {'fenced_one', 'fenced_two'} <= set(held['profile']['blueprints']), held['revision']
        only_revision = fenced(w, 'added_with_only_the_revision_no_context', 'farm', 'fenced_three', {'label': 'Fenced three'}, expect=200,
                               expected_revision=second['revision']).get_json()
        assert 'revision' in only_revision
        only_context = fenced(w, 'added_with_only_the_context_answers_without_a_revision', 'farm', 'fenced_four', {'label': 'Fenced four'}, expect=200,
                              expected_context=context).get_json()
        assert 'revision' not in only_context, only_context
        fenced(w, 'resend_of_the_first_request_after_it_committed_is_refused_stale', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409,
               expected_revision=r0, expected_context=context)
        source(w, F, 'read_after_the_resend_the_profile_holds_each_run_once')
    with gen.world('rtf_stale') as w:
        fenced_world(w)
        r0 = source(w)['revision']
        assert w.plain_post(PATCH_URL, dict(name='mine', path=['blueprints', 'farm', 'label'], value='Saved by another client', expected_revision=r0)).status_code == 200
        current = source(w)['revision']
        stale = fenced(w, 'stale_revision_409_nothing_written', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409, expected_revision=r0).get_json()
        assert stale['code'] == 'revision_conflict' and stale['revision'] == current and stale['ok'] is False, stale
        fenced(w, 'stale_revision_is_judged_before_the_recipe_and_its_choices', 'ghost', 'Not A Valid Id', {'speed': 1}, expect=409, expected_revision=r0)
        fenced(w, 'current_revision_with_the_same_bad_recipe_is_refused_by_the_recipe', 'ghost', 'Not A Valid Id', {'speed': 1}, expect=400, expected_revision=current)
        fenced(w, 'expected_revision_null_is_a_stale_revision', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409, expected_revision=None)
        fenced(w, 'expected_revision_not_text_is_a_stale_revision', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409, expected_revision=5)
        fenced(w, 'expected_revision_empty_is_a_stale_revision', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409, expected_revision='')
        assert source(w)['revision'] == current
        source(w, F, 'read_after_the_refusals_holds_the_other_clients_save')
    with gen.world('rtf_context') as w:
        from dashboard_contract_cases_context import switch_to_beta
        fenced_world(w)
        context, r0 = alpha(w), source(w)['revision']
        switch_to_beta(w)
        beta = alpha(w)
        w.get(F, 'read_config_after_another_client_switched_the_account', '/api/config')
        before = (w.root / 'profiles' / 'mine.yaml').read_bytes()
        refused = fenced(w, 'composed_for_alpha_is_refused_after_the_switch', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409,
                         expected_revision=r0, expected_context=context).get_json()
        assert refused['code'] == 'context_changed' and refused['current_context'] == beta and 'revision' not in refused, refused
        assert (w.root / 'profiles' / 'mine.yaml').read_bytes() == before
        fenced(w, 'the_context_is_judged_before_the_revision', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409,
               expected_revision='0' * 64, expected_context=context)
        fenced(w, 'the_current_context_with_a_stale_revision_is_a_revision_conflict', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409,
               expected_revision='0' * 64, expected_context=beta, name='theirs')
        source(w, F, 'read_the_profile_the_refused_add_would_have_changed')
        for case, bad in (('expected_context_null_is_refused_not_read_as_absent', None), ('expected_context_not_an_object', 'alpha'),
                          ('expected_context_missing_the_serial', {k: v for k, v in context.items() if k != 'serial'}),
                          ('expected_context_serial_not_text', dict(context, serial=5))):
            reply = fenced(w, case, 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=400, expected_revision=r0, expected_context=bad).get_json()
            assert reply['code'] == 'invalid_request', case
        assert (w.root / 'profiles' / 'mine.yaml').read_bytes() == before
        legacy = fenced(w, 'without_the_fields_the_legacy_request_adds_to_the_named_profile', 'farm', 'legacy_run', {'label': 'Legacy run'}, expect=200).get_json()
        assert 'revision' not in legacy and 'legacy_run' in legacy['profile']['blueprints']
    with gen.world('rtf_no_config') as w:
        fenced_world(w)
        context = alpha(w)
        (w.root / 'config.yaml').unlink()
        refused = fenced(w, 'context_when_config_yaml_is_missing_is_refused_and_creates_nothing', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=409,
                         expected_context=context).get_json()
        assert refused['code'] == 'context_changed' and refused['current_context'] is None and not (w.root / 'config.yaml').exists()
    with gen.world('rtf_lost') as w:
        fenced_world(w)
        r0, context = source(w)['revision'], alpha(w)
        body = dict(label='Lost run')
        fenced(w, 'added_and_the_answer_is_lost', 'farm', 'lost_run', body, expect=200, expected_revision=r0, expected_context=context)
        resend = fenced(w, 'the_identical_resend_is_refused_stale_not_applied_twice', 'farm', 'lost_run', body, expect=409, expected_revision=r0, expected_context=context).get_json()
        assert resend['code'] == 'revision_conflict'
        held = source(w, F, 'read_settles_the_lost_answer_the_run_is_there_once')
        assert held['revision'] == resend['revision'] and 'lost_run' in held['profile']['blueprints']
        without = fenced(w, 'the_resend_without_the_revision_is_refused_by_the_run_identifier', 'farm', 'lost_run', body, expect=400, expected_context=context).get_json()
        assert 'already exists' in without['error']
    with gen.world('rtf_failures') as w:
        fenced_world(w)
        r0, context = source(w)['revision'], alpha(w)
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('profiles/mine.yaml could not be written')))
        fenced(w, 'write_failed_nothing_saved', 'farm', 'fenced_one', {'label': 'Fenced one'}, expect=400, expected_revision=r0, expected_context=context)
        assert source(w)['revision'] == r0
        source(w, F, 'read_after_the_failed_write_the_revision_is_unchanged')
    with gen.world('rtf_answer_failed') as w:
        fenced_world(w)
        r0, context = source(w)['revision'], alpha(w)
        real = w.dash._save_yaml_backup

        def written_then_failed(path, data):
            real(path, data)
            raise OSError('the answer could not be built after the file was written')
        w.mp.setattr(w.dash, '_save_yaml_backup', written_then_failed)
        fenced(w, 'written_then_the_answer_failed', 'farm', 'half_run', {'label': 'Half run'}, expect=400, expected_revision=r0, expected_context=context)
        w.mp.setattr(w.dash, '_save_yaml_backup', real)
        held = source(w, F, 'read_after_an_error_that_followed_the_write_holds_the_run')
        assert held['revision'] != r0 and 'half_run' in held['profile']['blueprints']
        fenced(w, 'identical_resend_after_the_error_is_refused_stale', 'farm', 'half_run', {'label': 'Half run'}, expect=409, expected_revision=r0, expected_context=context)
    with gen.world('rtf_state') as w:
        fenced_world(w)
        context, profile = alpha(w), source(w)['profile']
        write_profile(w, 'default', profile)
        write_profile(w, 'mine.draft', {'player': {}})
        fenced(w, 'starter_profile_refused_with_the_guards', 'farm', 'new_run', {'label': 'New run'}, expect=409, name='default', expected_revision='any', expected_context=context)
        fenced(w, 'scan_draft_refused_with_the_guards', 'farm', 'new_run', {'label': 'New run'}, expect=409, name='mine.draft', expected_revision='any', expected_context=context)
        fenced(w, 'unknown_profile_with_the_guards', 'farm', 'new_run', {'label': 'New run'}, expect=400, name='ghost', expected_revision='any', expected_context=context)
