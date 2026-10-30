"""Contract cases shared by the editor pages: the configuration, the profile files and the profile patch.

Families of dashboard_contract_pages_v1.json (README rows G-01, G-02, G-03 and the Schedule row S-01):

  config_state      GET/POST /api/config: a populated machine config (accounts, loadouts), a missing and a corrupt file,
                    a save and its read-after-save;
  profile_reads     GET /api/profiles, /api/profile-files, /api/profile-src/{name}, /api/vocab, /api/flows,
                    /api/run-templates: valid, empty, draft, starter, other account, validator warnings and errors,
                    missing and unreadable sources, unsupported vocabulary;
  profile_patch     POST /api/profile-patch: whole-list and single-key writes, deletes, appends, every validator refusal
                    the Schedule/Collection/Abilities editors meet, stale revision, starter/draft refusal, write failures
                    and the read that settles them, a serialized multi-save that stops half way, and the limits of the
                    route (it names a profile, not the context the page was composed for).

The profile all of them edit is populate() of dashboard_contract_pages_world.py: several runs, three gathering policies,
two shopping lists and a plan that the real validator accepts. Every answer is the real route's.
"""
import copy

import yaml

from control import operations
from dashboard_contract_pages_world import PLAN, pin_mtimes, populate, raw_post
from test_dashboard_contract import raising

CONFIG, READS, PATCH = 'config_state', 'profile_reads', 'profile_patch'


def write_profile(w, name, text_or_data):
    text = text_or_data if isinstance(text_or_data, str) else yaml.safe_dump(text_or_data, sort_keys=False)
    (w.root / 'profiles' / f'{name}.yaml').write_text(text, encoding='utf-8')


def two_accounts(cfg, *, active='alpha', profile='mine'):
    cfg['accounts'] = {'alpha': {'label': 'Alpha', 'active_profile': profile, 'loadouts': {'plain': {}}},
                       'beta': {'label': 'Beta', 'active_profile': 'theirs', 'loadouts': {'plain': {}}}}
    cfg['instances']['main']['account'] = active


def config_state_cases(gen):
    with gen.world('cfg_populated') as w:
        populate(w, config=two_accounts)
        got = w.get(CONFIG, 'populated_accounts_and_loadouts', '/api/config').get_json()
        assert got['accounts']['alpha']['loadouts'] == {'plain': {}} and got['config_revision'] and got['loadouts'] == {'plain': {}}
        w.edit_config(lambda cfg: two_accounts(cfg, active='beta'))
        other = w.get(CONFIG, 'effective_view_of_the_other_account', '/api/config').get_json()
        assert other['active_profile'] == 'theirs' and other['config_revision'] != got['config_revision']
        w.edit_config(lambda cfg: two_accounts(cfg, active='ghost'))
        w.get(CONFIG, 'account_missing_from_the_library_refused', '/api/config')
    with gen.world('cfg_missing') as w:
        (w.root / 'config.yaml').unlink()
        w.get(CONFIG, 'file_missing_and_no_example_500', '/api/config')
    with gen.world('cfg_reseeded') as w:
        example = w.root / 'config.example.yaml'
        example.write_text((w.root / 'config.yaml').read_text(encoding='utf-8'), encoding='utf-8')
        (w.root / 'config.yaml').unlink()
        assert w.get(CONFIG, 'file_missing_is_reseeded_from_the_example', '/api/config').status_code == 200
    with gen.world('cfg_corrupt') as w:
        (w.root / 'config.yaml').write_text('active_instance: [main\n', encoding='utf-8')
        w.get(CONFIG, 'file_corrupt', '/api/config')
    with gen.world('cfg_save') as w:
        before = w.get(CONFIG, 'read_before_the_save', '/api/config').get_json()
        edited = {key: value for key, value in before.items() if key != 'config_revision'}
        edited['loadouts'] = {'plain': {}}
        saved = w.post(CONFIG, 'save_accepted', '/api/config', edited)
        assert saved.status_code == 200 and saved.get_json()['ok'] is True
        after = w.get(CONFIG, 'read_after_the_save_holds_the_change', '/api/config').get_json()
        assert after['loadouts'] == {'plain': {}} and after['config_revision'] != before['config_revision']
        w.post(CONFIG, 'save_refused_a_list_is_not_a_config', '/api/config', ['not', 'a', 'config'])
        raw_post(w, CONFIG, 'save_refused_body_not_json', '/api/config', 'active_instance=main', content_type='text/plain')
        assert w.get(CONFIG, 'read_after_the_refusals_is_unchanged', '/api/config').get_json() == after

# ------------------------------------------------------------- profile reads
def profile_reads_cases(gen):
    with gen.world('pr_populated') as w:
        populate(w)
        pin_mtimes(w)
        w.get(READS, 'profiles_list', '/api/profiles')
        w.get(READS, 'profile_files_active_and_patchable', '/api/profile-files')
        src = w.get(READS, 'profile_src_valid', '/api/profile-src/mine').get_json()
        assert src['ok'] and src['problems'] == [] and src['warnings'] == [] and src['validator_error'] is None and src['revision']
        vocab = w.get(READS, 'vocab_ok', '/api/vocab').get_json()
        assert vocab['ok'] is True and 'kinds' in vocab['vocab'] and 'block_fields' in vocab['vocab']
        w.get(READS, 'flows_ok', '/api/flows')
        w.get(READS, 'run_templates_ok', '/api/run-templates')
    with gen.world('pr_warnings') as w:
        def unreachable(profile):      # the first block has no window and no count: it always wins and the one below never runs
            profile['plan']['days']['farm_day'] = [{'block': 'coin', 'blueprint': 'farm'},
                                                   {'block': 'shards', 'blueprint': 'shards', 'after': '08:00', 'count': 100}]
            profile['plan']['days']['spare_day'] = [{'block': 'coin', 'blueprint': 'farm'}]
        populate(w, mutate=unreachable)
        src = w.get(READS, 'profile_src_with_validator_warnings', '/api/profile-src/mine').get_json()
        assert src['problems'] == [] and len(src['warnings']) == 2, src['warnings']
    with gen.world('pr_problems') as w:
        populate(w, mutate=lambda profile: profile['blueprints']['farm_deep'].update(loadout='nope'))
        src = w.get(READS, 'profile_src_with_problems', '/api/profile-src/mine').get_json()
        assert src['ok'] and len(src['problems']) == 1 and 'nope' in src['problems'][0]
    with gen.world('pr_files') as w:
        populate(w)
        write_profile(w, 'default', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        write_profile(w, 'mine.draft', {'player': {'seeded_from': 'scan'}})
        write_profile(w, 'theirs', {'player': {}, 'blueprints': {}})
        pin_mtimes(w)
        listed = w.get(READS, 'profile_files_with_a_draft_and_the_starter', '/api/profile-files').get_json()
        assert {p['name']: p['patchable'] for p in listed['profiles']} == {'default': True, 'mine': True, 'mine.draft': False, 'theirs': True}
        w.get(READS, 'profiles_list_with_a_draft', '/api/profiles')
        w.get(READS, 'profile_src_of_a_draft', '/api/profile-src/mine.draft')
        w.get(READS, 'profile_src_of_the_starter', '/api/profile-src/default')
        w.edit_config(lambda cfg: two_accounts(cfg))
        hidden = w.get(READS, 'profile_files_hide_the_other_accounts_profile', '/api/profile-files').get_json()
        assert 'theirs' not in {p['name'] for p in hidden['profiles']}
        theirs = w.get(READS, 'profile_src_of_the_other_accounts_profile_is_not_refused', '/api/profile-src/theirs')
        assert theirs.status_code == 200
    with gen.world('pr_sources') as w:
        populate(w)
        assert w.get(READS, 'profile_src_unknown_404', '/api/profile-src/ghost').status_code == 404
        assert w.get(READS, 'profile_src_bad_name_400', '/api/profile-src/..x').status_code == 400
        write_profile(w, 'list', '- not\n- a mapping\n')
        assert w.get(READS, 'profile_src_not_a_mapping_400', '/api/profile-src/list').status_code == 400
        write_profile(w, 'broken', 'blueprints: [unclosed\n')
        assert w.get(READS, 'profile_src_unreadable_yaml_400', '/api/profile-src/broken').status_code == 400
        write_profile(w, 'empty', '')
        assert w.get(READS, 'profile_src_empty_file_400', '/api/profile-src/empty').status_code == 400
        real = w.dash._profile_check
        w.mp.setattr(w.dash, '_profile_check', raising(RuntimeError('the validator could not be loaded')))
        broken = w.get(READS, 'profile_src_validator_unavailable_is_reported_not_hidden', '/api/profile-src/mine').get_json()
        assert broken['ok'] is True and broken['validator_error'] and broken['problems'] == []
        w.mp.setattr(w.dash, '_profile_check', real)
    with gen.world('pr_vocab') as w:
        from player import playerprofile
        w.mp.setattr(playerprofile, 'vocab', raising(RuntimeError('the vocabulary could not be built')))
        failed = w.get(READS, 'vocab_unavailable_is_http_200_with_ok_false', '/api/vocab')
        assert failed.status_code == 200 and failed.get_json()['ok'] is False and failed.get_json()['vocab'] == {}
        w.mp.setattr(playerprofile, 'vocab', lambda: ['not', 'a', 'dict'])
        assert w.get(READS, 'vocab_of_the_wrong_type_is_http_200_with_ok_false', '/api/vocab').get_json()['ok'] is False
        import flows
        w.mp.setattr(flows, 'flows', raising(RuntimeError('the flow registry could not be read')))
        assert w.get(READS, 'flows_unavailable_500', '/api/flows').status_code == 500
    with gen.world('pr_none') as w:
        (w.root / 'profiles' / 'mine.yaml').unlink()
        none = w.get(READS, 'no_profile_files', '/api/profile-files').get_json()
        assert none['profiles'] == [] and none['active'] == 'mine'
        assert w.get(READS, 'profiles_list_empty', '/api/profiles').get_json() == []

# ------------------------------------------------------------- profile patch
PATCH_URL = '/api/profile-patch'
DAY = ['plan', 'days', 'farm_day']
SHARD = {'block': 'shards', 'blueprint': 'shards', 'after': '08:00', 'count': 100}
COIN = {'block': 'coin', 'blueprint': 'farm'}
TOURNEY = {'block': 'tournament', 'blueprint': 'tourney', 'after': '19:00', 'count': 1}


def source(w, name='mine'):
    return w.client.get(f'/api/profile-src/{name}').get_json()


def patch(w, case, name, path, *, value=None, revision=None, delete=False, send_value=True, expect=None):
    """One profile-patch request, the way the editors send it: {name, path, value|op:'delete', expected_revision?}."""
    body = dict(name=name, path=path)
    if revision is not None:
        body['expected_revision'] = revision
    if delete:
        body['op'] = 'delete'
    elif send_value:
        body['value'] = value
    reply = w.post(PATCH, case, PATCH_URL, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def saved(w, case, path, revision, **kwargs):
    reply = patch(w, case, 'mine', path, revision=revision, expect=200, **kwargs).get_json()
    assert reply['ok'] and reply['revision'] != revision
    return reply['revision']


def profile_patch_succeeds(gen):
    with gen.world('pp_ok') as w:
        populate(w)
        first = w.get(PATCH, 'read_before_the_first_save', '/api/profile-src/mine').get_json()['revision']
        revision = saved(w, 'saved_a_whole_day_list', DAY, first,
                         value=[dict(COIN, after='06:00'), dict(SHARD, count=50)])
        revision = saved(w, 'saved_a_block_appended_at_the_end_of_a_list', DAY + [2], revision, value={'block': 'coin', 'blueprint': 'farm_deep'})
        revision = saved(w, 'saved_one_block_deleted_from_a_list', DAY + [2], revision, delete=True)
        revision = saved(w, 'saved_a_weekday_mapped_to_a_day_plan', ['plan', 'week', 'sunday'], revision, value='tourney_day')
        revision = saved(w, 'saved_a_weekday_mapping_deleted', ['plan', 'week', 'sunday'], revision, delete=True)
        revision = saved(w, 'saved_the_default_day_plan_changed', ['plan', 'week', 'default'], revision, value='tourney_day')
        coupled = {'week': {'default': 'weekend', 'monday': 'farm_day'},
                   'days': {'farm_day': [COIN], 'weekend': [dict(SHARD), COIN]}}
        revision = saved(w, 'saved_the_whole_plan_days_and_week_together', ['plan'], revision, value=coupled)
        gem = {'flying_gem': True, 'gem_delay_sec': [4, 9], 'ad_gems': True, 'quests_8h': False, 'quest_rewards': True, 'guild': False}
        revision = saved(w, 'saved_a_whole_gathering_policy', ['policies', 'gather', 'all_on'], revision, value=gem)
        revision = saved(w, 'saved_one_field_of_a_policy', ['policies', 'gather', 'all_on', 'ad_gems'], revision, value=False)
        final = w.get(PATCH, 'read_after_the_saves_holds_every_change', '/api/profile-src/mine').get_json()
        assert final['revision'] == revision and final['profile']['plan'] == coupled and final['profile']['policies']['gather']['all_on']['ad_gems'] is False
        tail = patch(w, 'saved_without_an_expected_revision_is_accepted', 'mine', ['plan', 'week', 'tuesday'], value='weekend', expect=200).get_json()
        assert tail['revision'] != revision
    with gen.world('pp_warnings') as w:
        populate(w)
        r = source(w)['revision']
        warn = patch(w, 'saved_with_validator_warnings_returned', 'mine', DAY, value=[COIN, SHARD], revision=r, expect=200).get_json()
        assert len(warn['warnings']) == 1 and 'can never run' in warn['warnings'][0]

def profile_patch_is_refused(gen):
    with gen.world('pp_refusals') as w:
        populate(w)
        r = source(w)['revision']
        two = [dict(TOURNEY), dict(TOURNEY, after='21:00')]
        refusals = [
            ('block_references_an_unknown_run', DAY + [0], dict(SHARD, blueprint='ghost')),
            ('window_closes_before_it_opens', DAY + [0], dict(SHARD, after='10:00', until='09:00')),
            ('window_closes_when_it_opens', DAY + [0], dict(SHARD, after='10:00', until='10:00')),
            ('count_zero', DAY + [0], dict(SHARD, count=0)),
            ('count_negative', DAY + [0], dict(SHARD, count=-3)),
            ('count_fraction', DAY + [0], dict(SHARD, count=2.5)),
            ('count_text', DAY + [0], dict(SHARD, count='5')),
            ('count_true', DAY + [0], dict(SHARD, count=True)),
            ('time_hour_out_of_range', DAY + [0], dict(SHARD, after='25:00')),
            ('time_text', DAY + [0], dict(SHARD, after='8am')),
            ('time_not_text', DAY + [0], dict(SHARD, after=800)),
            ('block_kind_does_not_match_the_run', DAY + [0], dict(SHARD, block='tournament')),
            ('unknown_block_name', DAY + [0], dict(SHARD, block='lunch')),
            ('unknown_key_in_a_block', DAY + [0], dict(SHARD, speed=3)),
            ('tournament_count_raised', ['plan', 'days', 'tourney_day', 0], dict(TOURNEY, count=2)),
            ('two_tournament_blocks_in_one_day', DAY, two),
            ('day_emptied', DAY, []),
            ('plan_emptied', ['plan'], {}),
            ('week_names_an_unknown_day_plan', ['plan', 'week', 'sunday'], 'no_such_day'),
            ('week_names_an_unknown_weekday', ['plan', 'week', 'funday'], 'farm_day'),
            ('policy_field_of_the_wrong_type', ['policies', 'gather', 'all_on', 'ad_gems'], 'yes'),
            ('policy_range_reversed', ['policies', 'gather', 'all_on', 'gem_delay_sec'], [10, 3]),
        ]
        for case, path, value in refusals:
            reply = patch(w, case, 'mine', path, value=value, revision=r, expect=400).get_json()
            assert reply['ok'] is False and reply['problems'] and reply['error'] == reply['problems'][0], case
        deletes = [('week_default_deleted_leaves_weekdays_unnamed', ['plan', 'week', 'default']),
                   ('policy_deleted_while_a_run_uses_it', ['policies', 'gather', 'hands_off']),
                   ('run_deleted_while_a_block_uses_it', ['blueprints', 'shards'])]
        for case, path in deletes:
            reply = patch(w, case, 'mine', path, revision=r, delete=True, expect=400).get_json()
            assert reply['problems'], case
        for case, path, expected in (('path_empty', [], 400), ('path_not_a_list', 'plan', 400), ('path_step_a_float', ['plan', 1.5], 400),
                                     ('path_step_a_bool', ['plan', True], 400), ('list_index_out_of_range', DAY + [9], 400),
                                     ('key_into_a_text_value', DAY + [0, 'block', 'x'], 400)):
            reply = patch(w, case, 'mine', path, value=1, revision=r, expect=expected).get_json()
            assert reply['ok'] is False and 'problems' not in reply, case
        assert patch(w, 'delete_index_out_of_range', 'mine', DAY + [9], revision=r, delete=True, expect=400).status_code == 400
        patch(w, 'value_missing_writes_null', 'mine', ['plan', 'week', 'default'], revision=r, send_value=False)
        assert source(w)['revision'] == r, 'a refused patch changed the saved profile'
        w.get(PATCH, 'read_after_the_refusals_nothing_changed', '/api/profile-src/mine')
    with gen.world('pp_state') as w:
        populate(w)
        r = source(w)['revision']
        write_profile(w, 'default', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        write_profile(w, 'mine.draft', {'player': {}})
        starter = patch(w, 'starter_refused_with_a_flag', 'default', DAY, value=[COIN], expect=409).get_json()
        assert starter['starter'] is True
        patch(w, 'scan_draft_refused', 'mine.draft', ['player', 'x'], value=1, expect=409)
        patch(w, 'unknown_profile_404', 'ghost', DAY, value=[COIN], expect=404)
        patch(w, 'name_with_a_path_400', '../outside', DAY, value=[COIN], expect=400)
        w.post(PATCH, 'name_missing_400', PATCH_URL, dict(path=DAY, value=[COIN]))
        w.post(PATCH, 'name_not_text_400', PATCH_URL, dict(name=5, path=DAY, value=[COIN]))
        raw_post(w, PATCH, 'body_not_json_400', PATCH_URL, 'name=mine', content_type='text/plain')
        w.post(PATCH, 'body_a_list_500', PATCH_URL, [1])
        # a client saved first: this client's revision is stale
        assert w.plain_post(PATCH_URL, dict(name='mine', path=['plan', 'week', 'sunday'], value='tourney_day', expected_revision=r)).status_code == 200
        stale = patch(w, 'stale_revision_409', 'mine', DAY, value=[COIN], revision=r, expect=409).get_json()
        assert stale['code'] == 'revision_conflict' and stale['revision'] != r
        patch(w, 'stale_revision_is_judged_before_the_values', 'mine', DAY + [0], value=dict(SHARD, count=0), revision=r, expect=409)
        w.get(PATCH, 'read_after_the_stale_refusal_holds_the_other_clients_save', '/api/profile-src/mine')
        write_profile(w, 'broken', 'blueprints: [unclosed\n')
        patch(w, 'profile_file_unreadable_400', 'broken', DAY, value=[COIN], expect=400)
        write_profile(w, 'list', '- a\n')
        patch(w, 'profile_file_not_a_mapping_400', 'list', DAY, value=[COIN], expect=400)
        real = w.dash._profile_check
        w.mp.setattr(w.dash, '_profile_check', raising(RuntimeError('the validator could not be loaded')))
        patch(w, 'validator_unavailable_500', 'mine', DAY, value=[COIN], expect=500)
        w.mp.setattr(w.dash, '_profile_check', real)
    with gen.world('pp_lock') as w:
        populate(w)
        r = source(w)['revision']
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            locked = patch(w, 'action_lock_another_action_in_progress', 'mine', DAY, value=[COIN], revision=r, expect=409).get_json()
        finally:
            w.dash._ACTION_LOCK.release()
        assert locked['code'] == 'action_lock'
        assert source(w)['revision'] == r


def profile_patch_write_failures(gen):
    with gen.world('pp_write_failed') as w:
        populate(w)
        r = source(w)['revision']
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('profiles/mine.yaml could not be written')))
        failed = patch(w, 'write_failed_nothing_saved_500', 'mine', DAY, value=[COIN], revision=r, expect=500).get_json()
        assert failed['error'].startswith('write failed')
        assert source(w)['revision'] == r
        w.get(PATCH, 'read_after_the_failed_write_the_revision_is_unchanged', '/api/profile-src/mine')
    with gen.world('pp_answer_failed') as w:
        populate(w)
        r = source(w)['revision']
        real = w.dash._save_yaml_backup

        def written_then_failed(path, data):
            real(path, data)
            raise OSError('the answer could not be built after the file was written')
        w.mp.setattr(w.dash, '_save_yaml_backup', written_then_failed)
        patch(w, 'written_then_the_answer_failed_500', 'mine', DAY, value=[COIN], revision=r, expect=500)
        w.mp.setattr(w.dash, '_save_yaml_backup', real)
        held = w.get(PATCH, 'read_after_an_error_that_followed_the_write_holds_the_change', '/api/profile-src/mine').get_json()
        assert held['revision'] != r and held['profile']['plan']['days']['farm_day'] == [COIN]
        resend = patch(w, 'identical_resend_after_a_committed_write_is_refused_as_stale', 'mine', DAY, value=[COIN], revision=r, expect=409).get_json()
        assert resend['code'] == 'revision_conflict'
    with gen.world('pp_multi') as w:
        populate(w)
        r0 = source(w)['revision']
        r1 = saved(w, 'multi_save_first_request_saved', ['plan', 'week', 'sunday'], r0, value='tourney_day')
        r2 = saved(w, 'multi_save_second_request_saved', DAY + [0, 'count'], r1, value=60)
        patch(w, 'multi_save_third_request_refused_by_the_validator', 'mine', DAY + [1], value=dict(COIN, count=0), revision=r2, expect=400)
        part = w.get(PATCH, 'read_after_a_partial_batch_holds_the_first_two', '/api/profile-src/mine').get_json()
        assert part['revision'] == r2 and part['profile']['plan']['week']['sunday'] == 'tourney_day'
        assert part['profile']['plan']['days']['farm_day'][0]['count'] == 60 and part['profile']['plan']['days']['farm_day'][1] == COIN


def profile_patch_context_limits(gen):
    with gen.world('pp_limits') as w:
        populate(w, config=two_accounts)
        write_profile(w, 'theirs', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        theirs = source(w, 'theirs')['revision']
        refused = patch(w, 'another_accounts_profile_is_not_refused_by_this_route', 'theirs', ['plan', 'week', 'sunday'],
                        value='tourney_day', revision=theirs, expect=200).get_json()
        assert refused['ok'] is True
        write_profile(w, 'second', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        mine = source(w)['revision']
        w.edit_config(lambda cfg: cfg['accounts']['alpha'].update(active_profile='second'))
        switched = patch(w, 'the_route_names_a_profile_so_an_active_profile_switch_is_not_noticed', 'mine', ['plan', 'week', 'monday'],
                         value='tourney_day', revision=mine, expect=200).get_json()
        pin_mtimes(w)
        assert switched['ok'] is True and w.get(PATCH, 'read_names_the_new_active_profile_after_the_switch', '/api/profile-files').get_json()['active'] == 'second'


def profile_patch_cases(gen):
    profile_patch_succeeds(gen)
    profile_patch_is_refused(gen)
    profile_patch_write_failures(gen)
    profile_patch_context_limits(gen)


def run_profile_cases(gen):
    config_state_cases(gen)
    profile_reads_cases(gen)
    profile_patch_cases(gen)

# ------------------------------------------------------------ schedule (S-01)
SCHEDULE = 'schedule_plan'


def block(uid, kind, start, end, count=None):
    return dict(id=uid, block=uid, blueprint=uid, preset='bp_' + uid, kind=kind, after_min=start, until_min=end, count=count)


def plan_world(gen, name, blocks, *, observe=True):
    """The real scheduler (scheduling.combo.next_block) deciding over `blocks` at Friday 08:00 UTC (minute 480), publishing its
    decision into a running combo operation's progress. The same construction as test_dashboard_contract.scheduler_world, with
    the blocks a parameter."""
    import contextlib
    import datetime
    from scheduling import combo, daystate

    @contextlib.contextmanager
    def world():
        with gen.world(name) as w:
            cfg = dict(active_profile='mine', plan={'week': {'friday': copy.deepcopy(blocks)}},
                       presets={'bp_farm': {'label': 'Farm'}, 'bp_shards': {'label': 'Shards'}, 'bp_tourney': {'label': 'Tournament'}})
            w.mp.setattr(combo, 'CONFIG', cfg)
            w.mp.setattr(combo, '_plan_unavailable_reason', None)
            w.mp.setattr(combo, '_plan_absent_logged', False)
            w.mp.setattr(combo, '_block_refusals', set())
            w.mp.setattr(daystate, 'STATE', str(w.root / 'daily.json'))
            op = w.running('run', {'preset': 'combo'}, args=[])
            w.mp.setenv('TOWER_OPERATION_ID', op['id'])
            w.mp.setenv('TOWER_OPERATION_DB', str(w.store.path))
            w.op = op
            if observe:
                combo.next_block(datetime.datetime.fromtimestamp(w.clock.now, tz=datetime.timezone.utc))
            yield w
    return world()


def schedule_plan_cases(gen):
    status = '/api/schedule/status?instance=main'
    cases = [
        ('overlapping_windows_the_first_listed_wins', [block('shards', 'shard', 420, 540, 10), block('farm', 'coin', 0, 1440)], 'shards'),
        ('window_closed_at_its_exclusive_end_falls_to_the_next', [block('shards', 'shard', 420, 480, 10), block('farm', 'coin', 0, 1440)], 'farm'),
        ('window_open_one_minute_before_its_end_still_wins', [block('shards', 'shard', 420, 481, 10), block('farm', 'coin', 0, 1440)], 'shards'),
        ('window_that_opens_later_is_not_yet_eligible', [block('shards', 'shard', 540, 600, 10), block('farm', 'coin', 0, 1440)], 'farm'),
        ('window_written_in_both_days_the_half_covering_now_wins', [block('shards', 'shard', 1320, 1440, 10), block('shards', 'shard', 0, 600, 10), block('farm', 'coin', 0, 1440)], 'shards'),
        ('tournament_window_outranks_the_filler_below_it', [block('tourney', 'tournament', 420, 600, 1), block('farm', 'coin', 0, 1440)], 'tourney'),
    ]
    for case, blocks, winner in cases:
        with plan_world(gen, 'sch_' + case[:40], blocks) as w:
            reply = w.get(SCHEDULE, case, status).get_json()
            assert reply['available'] is True and reply['winner']['id'] == winner, (case, reply['winner'])
    with plan_world(gen, 'sch_profile_changed', [block('farm', 'coin', 0, 1440)]) as w:
        observed = w.get(SCHEDULE, 'observed_under_the_active_profile', status).get_json()
        assert observed['available'] is True
        copy_src = yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8'))
        write_profile(w, 'second', copy_src)
        w.edit_config(lambda cfg: cfg.update(active_profile='second'))
        changed = w.get(SCHEDULE, 'the_active_profile_changed_after_the_observation', status).get_json()
        assert changed['available'] is False and changed['reason'] == 'scheduler_not_observed' and changed['context']['profile'] == 'second'
        assert changed['observed_winner'] is None
    with gen.world('sch_plan_sources') as w:
        populate(w, plan=False)
        src = w.get(SCHEDULE, 'profile_without_a_plan_has_no_plan_member', '/api/profile-src/mine').get_json()
        assert 'plan' not in src['profile'] and src['problems'] == []
        write_profile(w, 'empty_plan', dict(copy.deepcopy(yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8'))), plan={}))
        empty = w.get(SCHEDULE, 'profile_with_an_empty_plan_is_a_validator_problem', '/api/profile-src/empty_plan').get_json()
        assert empty['profile']['plan'] == {} and empty['problems'] and 'schedules nothing' in empty['problems'][0]
        bare = w.get(SCHEDULE, 'schedule_status_without_a_plan', status).get_json()
        assert bare['available'] is False
        first = source(w)['revision']
        created = patch(w, 'a_plan_added_to_a_profile_that_has_none', 'mine', ['plan'], value=PLAN, revision=first, expect=200).get_json()
        assert created['profile']['plan'] == PLAN
        midnight = {'week': {'default': 'night', 'monday': 'night'},
                    'days': {'night': [{'block': 'shards', 'blueprint': 'shards', 'after': '22:00', 'count': 20},
                                       {'block': 'shards', 'blueprint': 'shards', 'until': '02:00', 'count': 20},
                                       {'block': 'coin', 'blueprint': 'farm'}]}}
        accepted = patch(w, 'a_window_across_midnight_is_written_as_two_blocks_and_accepted', 'mine', ['plan'], value=midnight,
                         revision=created['revision'], expect=200).get_json()
        assert accepted['warnings'] == []


def run_schedule_cases(gen):
    schedule_plan_cases(gen)