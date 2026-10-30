"""Contract cases of the Shopping queue editor (frontend brief f6-12-shopping-brief.md section 8).

ONE new family of dashboard_contract_pages_v1.json, `shopping_queue`, generated from the real routes over the synthetic world of the
other pages. The brief lists its cases under the existing families (run_controls_save, run_controls_live, battle_actions, battles_active,
profile_patch, profile_reads); recording them there would change the hash of families consumers are pinned to, so they are all
here, under the brief's own case names. Mapping: every `<family>.<case>` of the brief is `shopping_queue.<case>`.

Notation of the brief: P1 is the full v2 plan below, P0 = P1 with an empty queue and a reserve of 0, PW / PR the weighted-split / round-robin
plans, L a legacy directive list, LM a legacy mapping, CL the plan the legacy conversion proposes. Nothing here changes a runtime file. Two
brief items that would have edited an existing case (the saved-only comment and the repaired unsupported-plan case of run_controls_save) are NOT
edited: the repaired case is recorded here as `refused_the_shopping_plan_the_run_does_not_support`, and the old one stays as published.
"""
import copy

from control import battles as battle_api
from control import operations
from dashboard_contract_cases_runs import RUN, live_body, live_url, read_run, save_body, with_a_second_run
from test_dashboard_contract import Driver, PLAN_LEGACY, action_body, action_url, raising, recorded_commands, with_shopping

SHOP = 'shopping_queue'
PATCH_URL = '/api/profile-patch'

P1 = {'version': 2, 'enabled': True, 'interval_sec': 30, 'cash_reserve': 500, 'queue_mode': 'highest_affordable', 'cadence': 'single_attempt',
      'excluded_upgrades': ['defense_absolute'],
      'queue': [{'id': 'damage', 'enabled': True, 'tap_limit': 3, 'weight': 1, 'from_wave': 1, 'until_wave': None},
                {'id': 'health', 'enabled': True, 'tap_limit': 2, 'weight': 1, 'from_wave': 5, 'until_wave': None}]}
P0 = dict(P1, queue=[], cash_reserve=0)
PW = dict(P1, cash_reserve=750.5, queue_mode='weighted_split', cadence='while_affordable',
          queue=[{'id': 'health', 'enabled': True, 'tap_limit': 2, 'weight': 3, 'from_wave': 5, 'until_wave': 20},
                 {'id': 'damage', 'enabled': True, 'tap_limit': None, 'weight': 1, 'from_wave': 1, 'until_wave': None}])
PR = dict(PW, queue_mode='round_robin')
L = [{'enabled': True, 'tab': 'attack', 'stats': ['damage'], 'mode': 'once'},
     {'enabled': True, 'tab': 'defense', 'stats': ['health'], 'mode': 'clicks', 'clicks': 3}]
LM = {'enabled': False, 'label': 'Legacy', 'directives': copy.deepcopy(L)}
BEST_COST = [{'enabled': True, 'tab': 'attack', 'stats': ['damage'], 'mode': 'best_cost'}]
CL = {'version': 2, 'enabled': True, 'interval_sec': 90, 'cash_reserve': None, 'queue_mode': 'weighted_split', 'cadence': 'single_attempt',
      'excluded_upgrades': ['defense_absolute'],
      'queue': [{'id': 'damage', 'enabled': True, 'tap_limit': 1, 'weight': 1, 'from_wave': 1, 'until_wave': None},
                {'id': 'health', 'enabled': True, 'tap_limit': None, 'weight': 3, 'from_wave': 1, 'until_wave': None}]}


# ------------------------------------------------------------------ the saved queue (run-controls)
def shopping_world(w, source=P1, *, second_kind='coin', second_source='main_list'):
    """The default world's run `farm` (a coin run) bound to the shared policy `main_list`, and a second run `other` bound to `second_source`."""
    with_a_second_run(w, second_kind)
    w.edit_profile(with_shopping(source))
    if second_kind == 'coin':
        w.edit_profile(lambda profile: profile['blueprints']['other'].update(shopping=second_source))


def read(w, case, run='farm'):
    return read_run(w, case, run, SHOP)


def save(w, case, current, plan, **override):
    return w.post(SHOP, case, RUN, save_body(current, {'shopping_plan': plan}, **override))


def shopping_saved_cases(gen):
    with gen.world('sq_saved') as w:
        shopping_world(w)
        current = read(w, 'shopping_read_before_any_save')
        saved = save(w, 'shopping_weighted_cadence_order_saved', current, PW)
        assert saved.status_code == 200, saved.get_json()
        after = read(w, 'shopping_read_after_save')
        assert after['settings']['shopping_plan'] == PW and after['profile_revision'] == saved.get_json()['profile_revision'], after['settings']
        other = read(w, 'shopping_other_run_after_private_save', 'other')
        assert other['settings']['shopping_plan'] == P1, other['settings']['shopping_plan']
    with gen.world('sq_empty') as w:
        shopping_world(w)
        assert save(w, 'shopping_empty_queue_saved', read_run(w), P0).status_code == 200
    with gen.world('sq_round_robin') as w:
        shopping_world(w)
        assert save(w, 'shopping_round_robin_saved', read_run(w), PR).status_code == 200
    with gen.world('sq_legacy_list') as w:
        shopping_world(w, L)
        preview = read(w, 'shopping_legacy_list_preview')
        assert preview['settings']['shopping_plan'] is None and preview['legacy_conversion']['requires_review'] is True, preview.get('legacy_conversion')
        source_before = w.client.get('/api/profile-src/mine').get_json()['revision']
        reviewed = save(w, 'shopping_reviewed_conversion_saved', preview, dict(CL, cash_reserve=0))
        assert reviewed.status_code == 200 and 'legacy_conversion' not in reviewed.get_json(), reviewed.get_json()
        assert w.client.get('/api/profile-src/mine').get_json()['revision'] != source_before
        read(w, 'shopping_read_after_the_reviewed_conversion')
    with gen.world('sq_legacy_mapping') as w:
        shopping_world(w, LM)
        assert read(w, 'shopping_legacy_mapping_preview')['legacy_conversion']['plan']['enabled'] is False
    with gen.world('sq_legacy_best_cost') as w:
        shopping_world(w, BEST_COST)
        assert read(w, 'shopping_legacy_best_cost_preview')['legacy_conversion']['plan']['queue_mode'] == 'highest_affordable'
    with gen.world('sq_invalid') as w:
        shopping_world(w)
        bad = copy.deepcopy(P1)
        bad['queue'][0]['weight'] = 101
        refused = save(w, 'shopping_invalid_weight_refused', read_run(w), bad)
        assert refused.status_code == 400, refused.get_json()
        read(w, 'shopping_read_after_the_refusal_nothing_changed')
    with gen.world('sq_unsupported') as w:
        shopping_world(w, second_kind='shard')
        limited = read_run(w, None, 'other')
        refused = save(w, 'refused_the_shopping_plan_the_run_does_not_support', limited, P1, blueprint='other')
        assert refused.status_code == 400, refused.get_json()
    with gen.world('sq_answer_failed') as w:
        shopping_world(w)
        current = read(w, 'shopping_read_before_the_unanswered_save')
        real, calls = operations.file_revision, []

        def revision_then_fail(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('the saved profile could not be read back')
            return real(path)
        w.mp.setattr(operations, 'file_revision', revision_then_fail)
        failed = save(w, 'shopping_save_reply_failed', current, PW)
        assert failed.status_code == 500 and len(calls) == 2
        w.mp.setattr(operations, 'file_revision', real)
        held = read(w, 'shopping_read_after_unknown_save')
        assert held['settings']['shopping_plan'] == PW and held['profile_revision'] != current['profile_revision']
        resend = save(w, 'shopping_identical_resend_after_the_unknown_save_is_refused_stale', current, PW)
        assert resend.status_code == 409


# ------------------------------------------------------------------ the live queue (live-controls) and manual shopping (actions)
def active(w, case):
    return w.get(SHOP, case, '/api/battles/active')


def live_world(w, source=P1):
    w.edit_profile(with_shopping(source))
    return Driver(w).go_live()


def plan_command(w, case, d, plan, key, **override):
    return w.post(SHOP, case, live_url(d), live_body(d, {'shopping_plan': plan}, key, **override))


def shopping_live_cases(gen):
    with gen.world('sq_live') as w:
        d = live_world(w)
        accepted = plan_command(w, 'shopping_plan_accepted', d, PW, 'sq-1')
        assert accepted.status_code == 200 and accepted.get_json()['command']['state'] == 'requested' and accepted.get_json()['control_revision'] == 1, accepted.get_json()
        requested = active(w, 'shopping_plan_read_requested').get_json()['battle']
        assert requested['effective']['shopping_plan'] == P1 and requested['requested_controls']['shopping_plan'] == PW, requested['effective']['shopping_plan']
        d.checkpoint()
        applied = active(w, 'shopping_plan_read_applied').get_json()['battle']
        assert applied['effective']['shopping_plan'] == PW and applied['applied_revision'] == applied['control_revision'] == 1
        assert applied['commands'][0]['state'] == 'applied' and applied['commands'][0]['applied_paths'] == ['shopping_plan']
    with gen.world('sq_live_overrides') as w:
        d = live_world(w)
        setup = w.post(SHOP, 'shopping_routine_overrides_set', live_url(d),
                       live_body(d, {'routines.shopping.enabled': False, 'routines.shopping.interval_sec': 60}, 'sq-o1'))
        assert setup.status_code == 200
        d.checkpoint()
        preserved = plan_command(w, 'shopping_plan_preserves_routine_overrides', d, PW, 'sq-o2')
        assert preserved.status_code == 200 and preserved.get_json()['control_revision'] == 2
        mid = active(w, 'shopping_plan_requested_over_routine_overrides_read').get_json()['battle']
        assert mid['effective']['shopping_plan'] == P1 and mid['effective']['routines']['shopping'] == dict(enabled=False, interval_sec=60), mid['effective']
        d.checkpoint()
        done = active(w, 'shopping_plan_routine_overrides_applied').get_json()['battle']
        assert done['applied_revision'] == done['control_revision'] == 2 and done['effective']['routines']['shopping'] == dict(enabled=False, interval_sec=60)
        assert done['effective']['shopping_plan'] == PW, done['effective']['shopping_plan']       # the plan keeps its own enabled/interval; the routine overrides sit in routines
    with gen.world('sq_live_unknown') as w:
        d = live_world(w)
        body = live_body(d, {'shopping_plan': PW}, 'sq-u1')
        real = battle_api.public
        w.mp.setattr(battle_api, 'public', raising(KeyError('battle row vanished')))
        unknown = w.post(SHOP, 'shopping_plan_reply_unknown', live_url(d), body)
        assert unknown.status_code == 500 and unknown.get_json()['code'] == 'command_outcome_unknown', unknown.get_json()
        w.mp.setattr(battle_api, 'public', real)
        read = active(w, 'shopping_plan_unknown_read').get_json()['battle']
        assert read['commands'][0]['request_keys'] == ['sq-u1'] and read['requested_controls']['shopping_plan'] == PW and read['effective']['shopping_plan'] == P1
        replay = w.post(SHOP, 'shopping_plan_identical_replay', live_url(d), body)
        assert replay.status_code == 200 and replay.get_json()['command']['id'] == read['commands'][0]['id']
        conflict = w.post(SHOP, 'shopping_plan_key_conflict', live_url(d), live_body(d, {'shopping_plan': P0}, 'sq-u1', expected_revision=0))
        assert conflict.status_code == 409, conflict.get_json()


ACTIONS = (('shopping_whole_accepted', 'shopping'), ('shopping_attack_accepted', 'shopping:attack'), ('shopping_defense_accepted', 'shopping:defense'),
           ('shopping_utility_accepted', 'shopping:utility'), ('shopping_upgrade_accepted', 'shopping:attack:damage'))


def shopping_action_cases(gen):
    for case, action in ACTIONS:
        with gen.world('sq_action_' + action.replace(':', '_')) as w:
            d = live_world(w)
            accepted = w.post(SHOP, case, action_url(d.op), action_body(d, action, 'sq-act'))
            assert accepted.status_code == 200 and accepted.get_json()['command']['payload'] == {'action': action}, accepted.get_json()
            assert accepted.get_json()['control_revision'] == 0
            if action == 'shopping:attack:damage':
                requested = active(w, 'shopping_upgrade_requested').get_json()['battle']
                command = requested['commands'][0]
                assert command['payload'] == {'action': action} and command['state'] == 'requested' and command['request_keys'] == ['sq-act'] and command['evidence'] is None
                d.checkpoint()
                started = active(w, 'shopping_upgrade_started').get_json()['battle']['commands'][0]
                assert started['state'] == 'started' and started['evidence'] == {'outcome': 'unknown'}, started
                import numpy as np
                from vision import wave_reader
                w.mp.setattr(wave_reader, 'read_wave', lambda frame: None)           # the digit templates are the player's own local files: the wave cannot be read
                d.rs.shop.step(np.zeros((2560, 1080, 3), np.uint8), wave=None)          # the shopper's own step
                d.checkpoint()
                done = active(w, 'shopping_upgrade_completed_without_purchase').get_json()['battle']['commands'][0]
                assert done['state'] == 'completed' and done['evidence']['attempts'] == 0, done
    with gen.world('sq_action_legacy') as w:
        w.edit_profile(with_shopping(PLAN_LEGACY))
        d = Driver(w).go_live()
        refused = w.post(SHOP, 'shopping_legacy_refused', action_url(d.op), action_body(d, 'shopping:attack', 'sq-legacy'))
        assert refused.status_code == 400 and 'Unsupported manual action' in refused.get_json()['error'], refused.get_json()
    with gen.world('sq_action_unknown') as w:
        d = live_world(w)
        body = action_body(d, 'shopping:attack:damage', 'sq-unknown')
        real = battle_api.public
        w.mp.setattr(battle_api, 'public', raising(KeyError('battle row vanished')))
        unknown = w.post(SHOP, 'shopping_upgrade_reply_unknown', action_url(d.op), body)
        assert unknown.status_code == 500 and unknown.get_json()['code'] == 'command_outcome_unknown'
        w.mp.setattr(battle_api, 'public', real)
        replay = w.post(SHOP, 'shopping_upgrade_identical_replay', action_url(d.op), body)
        assert replay.status_code == 200 and len(recorded_commands(d)) == 1


# ------------------------------------------------------------------ the shared library (profile-patch on policies.shopping_lists.<id>)
def library_world(w):
    """Alpha (profile `mine`, in force) and beta (profile `theirs`); `mine` has runs farm and other bound to main_list (= P1), the farm_deep run bound
    to the legacy list, and the policies unused (= P1), spare (= P1) and legacy (= L)."""
    from dashboard_contract_cases_profile import two_accounts, write_profile
    from dashboard_contract_pages_world import populate

    def change(profile):
        lists = profile['policies']['shopping_lists']
        lists.clear()
        lists.update(main_list=copy.deepcopy(P1), unused=copy.deepcopy(P1), spare=copy.deepcopy(P1), legacy=copy.deepcopy(L))
        profile['blueprints']['farm']['shopping'] = 'main_list'
        profile['blueprints']['farm_deep']['shopping'] = 'legacy'
        profile['blueprints']['other'] = dict(copy.deepcopy(profile['blueprints']['farm']), label='Other run')
    populate(w, mutate=change)
    write_profile(w, 'theirs', w.client.get('/api/profile-src/mine').get_json()['profile'])
    w.edit_config(lambda cfg: two_accounts(cfg, active='alpha'))


def revision(w):
    return w.client.get('/api/profile-src/mine').get_json()['revision']


def library(w, case, ident, value, *, delete=False, **override):
    """The shared-policy patch the page sends: {name, path:[policies, shopping_lists, id], value | op:'delete', expected_revision, expected_context}."""
    from dashboard_contract_cases_context import page_context
    body = dict(name='mine', path=['policies', 'shopping_lists', ident], expected_revision=revision(w), expected_context=page_context(w))
    if delete:
        body.update(op='delete', value=None)
    else:
        body['value'] = value
    body.update(override)
    return w.post(SHOP, case, PATCH_URL, body)


LEGACY_MAPPING = {'enabled': True, 'label': 'Legacy', 'directives': [
    {'enabled': False, 'tab': 'attack', 'stats': ['damage'], 'mode': 'repeat'}, {'tab': 'defense', 'stats': ['health'], 'mode': 'best_cost'}, *copy.deepcopy(L)]}


def shopping_library_cases(gen):
    def lists_of(w):
        return w.client.get('/api/profile-src/mine').get_json()['profile']['policies']['shopping_lists']
    with gen.world('sq_library') as w:
        library_world(w)
        w.get(SHOP, 'shopping_library_read_before_any_edit', '/api/profile-src/mine')
        created = library(w, 'shopping_policy_created', 'new_queue', P0)
        assert created.status_code == 200 and created.get_json()['profile']['policies']['shopping_lists']['new_queue'] == P0, created.get_json()
        copied = library(w, 'shopping_policy_copied', 'main_list_copy', P1)
        assert copied.status_code == 200 and lists_of(w)['main_list'] == P1
        updated = library(w, 'shopping_shared_v2_updated', 'main_list', PW)
        assert updated.status_code == 200 and lists_of(w)['main_list'] == PW and lists_of(w)['main_list_copy'] == P1
        bindings = w.client.get('/api/profile-src/mine').get_json()['profile']['blueprints']
        assert bindings['farm']['shopping'] == 'main_list' and bindings['other']['shopping'] == 'main_list'
        assert library(w, 'shopping_legacy_list_updated', 'legacy', L).status_code == 200
        assert library(w, 'shopping_legacy_mapping_updated', 'legacy', LEGACY_MAPPING).status_code == 200 and lists_of(w)['legacy'] == LEGACY_MAPPING
        before = revision(w)
        invalid = library(w, 'shopping_legacy_clicks_invalid', 'legacy', {'enabled': True, 'directives': [{'tab': 'attack', 'stats': ['damage'], 'mode': 'clicks', 'clicks': 0}]})
        assert invalid.status_code == 400 and invalid.get_json()['problems'] and revision(w) == before, invalid.get_json()
        w.get(SHOP, 'shopping_library_read_after_the_edits', '/api/profile-src/mine')
    with gen.world('sq_library_delete') as w:
        library_world(w)
        deleted = library(w, 'shopping_policy_deleted_unused', 'unused', None, delete=True)
        assert deleted.status_code == 200 and 'unused' not in lists_of(w) and 'spare' in lists_of(w), deleted.get_json()
    with gen.world('sq_library_delete_used') as w:
        library_world(w)
        w.edit_profile(lambda profile: profile['blueprints']['other'].update(shopping='unused'))
        before = revision(w)
        refused = library(w, 'shopping_policy_delete_used_refused', 'main_list', None, delete=True)
        assert refused.status_code == 400 and 'main_list' in refused.get_json()['error'] and revision(w) == before, refused.get_json()
    with gen.world('sq_library_conflict') as w:
        library_world(w)
        stale = revision(w)
        assert w.plain_post(PATCH_URL, dict(name='mine', path=['policies', 'shopping_lists', 'spare'], value=P0, expected_revision=stale)).status_code == 200
        conflict = library(w, 'shopping_shared_revision_conflict', 'main_list', PW, expected_revision=stale)
        assert conflict.status_code == 409 and conflict.get_json()['code'] == 'revision_conflict' and lists_of(w)['main_list'] == P1
    with gen.world('sq_library_context') as w:
        from dashboard_contract_cases_context import page_context, switch_to_beta
        library_world(w)
        composed = page_context(w)
        switch_to_beta(w)
        changed = library(w, 'shopping_shared_context_changed', 'main_list', PW, expected_context=composed)
        assert changed.status_code == 409 and changed.get_json()['code'] == 'context_changed' and changed.get_json()['current_context'] == page_context(w)
        assert lists_of(w)['main_list'] == P1
    with gen.world('sq_library_failed') as w:
        library_world(w)
        real = w.dash._save_yaml_backup

        def written_then_failed(path, data):
            real(path, data)
            raise OSError('shopping readback unavailable')
        w.mp.setattr(w.dash, '_save_yaml_backup', written_then_failed)
        failed = library(w, 'shopping_shared_reply_failed', 'main_list', PW)
        assert failed.status_code == 500 and failed.get_json()['error'] == 'write failed: shopping readback unavailable', failed.get_json()
        w.mp.setattr(w.dash, '_save_yaml_backup', real)
        held = w.get(SHOP, 'shopping_shared_read_after_unknown', '/api/profile-src/mine').get_json()
        assert held['profile']['policies']['shopping_lists']['main_list'] == PW and held['profile']['blueprints']['other']['shopping'] == 'main_list'


def shopping_queue_cases(gen):
    shopping_saved_cases(gen)
    shopping_live_cases(gen)
    shopping_action_cases(gen)
    shopping_library_cases(gen)
