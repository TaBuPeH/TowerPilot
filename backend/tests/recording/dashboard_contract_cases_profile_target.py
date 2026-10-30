"""Contract cases of the Profiles editing target (frontend brief f6-14-profiles-brief.md section 6): unbinding the profile and editing a profile that is not the
active one.

ONE new family of dashboard_contract_pages_v1.json, `profile_target`, generated from the real routes over the synthetic world of the other pages. The brief
lists its cases under the existing families (profile_files_write, config_state, context_fence, profile_reads, profile_patch); recording them there would
change the hash of families consumers are pinned to, so they are all here under the brief's own case names. Mapping: every `<family>.<case>` of the
brief is `profile_target.<case>`.

Common setup of the brief: account alpha on main / fake:1 with profile `mine` active, alpha's library holds `mine` and `second`, beta owns `theirs`;
`second` has a distinguishable source. E is alpha's context as GET /api/config publishes it, E0 the same for an installation with no accounts.
"""
import copy

import yaml

from dashboard_contract_cases_context import page_context, switch_to_beta
from dashboard_contract_cases_editors import RESCUE_POLICIES, with_abilities
from dashboard_contract_cases_profile import two_accounts, write_profile
from dashboard_contract_pages_world import pin_mtimes
from test_dashboard_contract import raising

PT = 'profile_target'
ACTIVATE = '/api/profile-activate'
PATCH_URL = '/api/profile-patch'
PLAN = {'days': {'spare_day': [{'block': 'coin', 'blueprint': 'farm'}]}, 'week': {'default': 'spare_day'}}
GATHER = {'flying_gem': True, 'gem_delay_sec': [4, 9], 'ad_gems': True, 'quests_8h': False, 'quest_rewards': True, 'guild': False}
BASELINE = {'black_hole': True, 'chronofield': True, 'death_wave': True, 'golden_tower': True, 'poison_swamp': True, 'spotlight': True}
UW_CALM = {'label': 'Calm', 'baseline': dict(BASELINE, spotlight=False)}


def target_world(w, *, accounts=True, mutate=None):
    """alpha (profile `mine` in force; library mine + second) and beta (profile `theirs`), on one emulator. `second` differs from `mine` in its farm label.
    `mutate` changes the populated profile before `second` and `theirs` are copied from it (a world with more runs, for example)."""
    with_abilities(w, mutate=mutate)
    mine = yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8'))
    second = copy.deepcopy(mine)
    second['blueprints']['farm']['label'] = 'Second farm'
    write_profile(w, 'second', second)
    write_profile(w, 'theirs', mine)
    if accounts:
        def configure(cfg):
            two_accounts(cfg, active='alpha')
            cfg['accounts']['alpha']['profiles'] = ['mine', 'second']
            for account in cfg['accounts'].values():
                account['loadouts'] = copy.deepcopy(cfg['loadouts'])
        w.edit_config(configure)


def config(w, case):
    return w.get(PT, case, '/api/config')


def source(w, name='second', case=None):
    path = f'/api/profile-src/{name}'
    return w.get(PT, case, path) if case else w.client.get(path)


def unbind(w, case, context, *, expect):
    reply = w.post(PT, case, ACTIVATE, dict(name=None, expected_context=context))
    assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply.get_json()


def active_profile_of(w):
    return w.client.get('/api/config').get_json().get('active_profile')


def runner(w):
    w.worker(4701, 'orchestrator.py', args=['--preset', 'bp_farm'])


def unbinding_cases(gen):
    with gen.world('pt_unbind_legacy') as w:
        target_world(w, accounts=False)
        e0 = page_context(w)
        assert e0['account'] is None and e0['profile'] == 'mine'
        got = unbind(w, 'unbind_with_matching_legacy_context', e0, expect=200)
        assert got['ok'] and got['active'] is None and 'unbound' in got['message'], got
        read = config(w, 'read_after_unbind_legacy_context').get_json()
        assert 'active_profile' not in read or read['active_profile'] is None, read.get('active_profile')
    with gen.world('pt_unbind_account') as w:
        target_world(w)
        e = page_context(w)
        got = unbind(w, 'unbind_with_matching_account_context', e, expect=200)
        assert got['ok'] and got['active'] is None, got
        read = config(w, 'read_after_unbind_account_context').get_json()
        assert not read.get('active_profile') and read['accounts']['alpha'].get('active_profile') is None and read['accounts']['beta']['active_profile'] == 'theirs', read['accounts']
    with gen.world('pt_unbind_runner') as w:
        target_world(w)
        e = page_context(w)
        runner(w)
        refused = unbind(w, 'unbind_refused_while_runner_listed', e, expect=409)
        assert refused['code'] == 'device_owned' and refused['retry'] == 'after_confirmed_stop', refused
        assert config(w, 'read_after_unbind_runner_refusal').get_json()['active_profile'] == 'mine'
    with gen.world('pt_unbind_switched_account') as w:
        target_world(w)
        e = page_context(w)
        switch_to_beta(w)
        refused = unbind(w, 'unbind_refused_after_account_switch', e, expect=409)
        assert refused['code'] == 'context_changed' and refused['current_context']['account'] == 'beta', refused
        assert active_profile_of(w) == 'theirs'
    with gen.world('pt_unbind_switched_profile') as w:
        target_world(w)
        e = page_context(w)
        assert w.plain_post(ACTIVATE, dict(name='second')).status_code == 200
        runner(w)
        refused = unbind(w, 'unbind_refused_after_active_profile_switch', e, expect=409)
        assert refused['code'] == 'context_changed' and refused['current_context'] == dict(e, profile='second'), refused
        assert active_profile_of(w) == 'second'
    with gen.world('pt_unbind_failed') as w:
        target_world(w)
        e = page_context(w)
        w.mp.setattr(w.dash, 'save_config', raising(OSError('fixture unbind failed')))
        failed = unbind(w, 'unbind_failed_before_write', e, expect=500)
        assert failed == {'ok': False, 'error': 'fixture unbind failed'}, failed
        assert config(w, 'read_after_unbind_failed_before_write').get_json()['active_profile'] == 'mine'
    with gen.world('pt_unbind_answer_failed') as w:
        target_world(w)
        e = page_context(w)
        real = w.dash.save_config

        def written_then_failed(cfg):
            real(cfg)
            raise OSError('fixture unbind failed')
        w.mp.setattr(w.dash, 'save_config', written_then_failed)
        unbind(w, 'unbind_written_then_answer_failed', e, expect=500)
        w.mp.setattr(w.dash, 'save_config', real)
        read = config(w, 'read_after_unbind_written_then_answer_failed').get_json()
        assert not read.get('active_profile') and read['accounts']['beta']['active_profile'] == 'theirs'


# ------------------------------------------------------------------ an inactive target of the same account (PE2 to PE6)
def patch(w, case, path, *, value=None, delete=False, name='second', context=None, revision=None, expect=None):
    """The patch the page sends for the profile it chose to edit: {name, path, value | op:'delete', expected_context, expected_revision}."""
    body = dict(name=name, path=path, expected_context=page_context(w) if context is None else context,
                expected_revision=source(w, name).get_json().get('revision', 'any') if revision is None else revision)
    if delete:
        body['op'] = 'delete'
    else:
        body['value'] = value
    reply = w.post(PT, case, PATCH_URL, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def second_profile_bytes(w, name='second'):
    return (w.root / 'profiles' / f'{name}.yaml').read_bytes()


def inactive_target_cases(gen):
    from player.run_templates import catalogue
    template = next(t for t in catalogue()['templates'] if t['id'] == 'farm')['policies']['gather']['all_on']
    with gen.world('pt_inactive_saves') as w:
        target_world(w)
        mine_before = second_profile_bytes(w, 'mine')
        config_before = (w.root / 'config.yaml').read_bytes()
        got = config(w, 'inactive_editor_context_before_save').get_json()
        assert got['active_profile'] == 'mine' and set(got['accounts']['alpha']['profiles']) == {'mine', 'second'}
        pin_mtimes(w)                                  # the listing publishes file modification times: the contract pins them
        files = w.get(PT, 'same_account_inactive_target_listed', '/api/profile-files').get_json()
        by_name = {item['name']: item for item in files['profiles']}
        assert files['active'] == 'mine' and by_name['second']['active'] is False and by_name['second']['patchable'] is True and 'theirs' not in by_name, files
        src = source(w, 'second', 'same_account_inactive_target_source').get_json()
        assert src['name'] == 'second' and src['profile']['blueprints']['farm']['label'] == 'Second farm' and src['problems'] == [], src['problems']
        runner(w)
        saved = patch(w, 'inactive_plan_saved_while_runner_listed', ['plan'], value=PLAN, expect=200).get_json()
        assert saved['ok'] and saved['profile']['plan'] == PLAN and saved['revision'] != src['revision'], saved
        label = patch(w, 'inactive_gather_label_saved', ['policies', 'gather', 'all_on', 'label'], value='Offline', expect=200).get_json()
        assert label['profile']['policies']['gather']['all_on']['label'] == 'Offline'
        patch(w, 'inactive_gather_copy_saved', ['policies', 'gather', 'spare'], value=GATHER, expect=200)
        patch(w, 'inactive_gather_template_created', ['policies', 'gather', 'fresh'], value=template, expect=200)
        patch(w, 'inactive_unused_gather_deleted', ['policies', 'gather', 'spare'], delete=True, expect=200)
        patch(w, 'inactive_chores_saved', ['policies', 'chores'], value=[{'name': 'quest_scan', 'enabled': False}], expect=200)
        patch(w, 'inactive_uw_policy_saved', ['policies', 'uw_policies', 'calm'], value=UW_CALM, expect=200)
        patch(w, 'inactive_rescue_policy_saved', ['policies', 'rescue_policies', 'extra'], value=copy.deepcopy(RESCUE_POLICIES['any_falling']), expect=200)
        patch(w, 'inactive_run_entry_label_saved', ['blueprints', 'farm', 'label'], value='Offline farm', expect=200)
        patch(w, 'inactive_run_entry_loadout_saved', ['blueprints', 'farm', 'loadout'], value='plain', expect=200)
        final = source(w, 'second', 'inactive_target_read_after_save').get_json()
        assert final['problems'] == [] and final['profile']['blueprints']['farm']['label'] == 'Offline farm' and 'fresh' in final['profile']['policies']['gather']
        assert 'spare' not in final['profile']['policies']['gather'] and final['profile']['plan'] == PLAN
        after = config(w, 'inactive_editor_context_after_save').get_json()
        assert after['active_profile'] == 'mine' and after['config_revision'] == got['config_revision']
        assert second_profile_bytes(w, 'mine') == mine_before and (w.root / 'config.yaml').read_bytes() == config_before
    with gen.world('pt_inactive_stale') as w:
        target_world(w)
        r = source(w).get_json()['revision']
        assert w.plain_post(PATCH_URL, dict(name='second', path=['plan'], value=PLAN, expected_revision=r)).status_code == 200
        stale = patch(w, 'inactive_target_stale_revision', ['plan'], value=PLAN, revision=r, expect=409).get_json()
        assert stale['code'] == 'revision_conflict' and stale['revision'] != r, stale
    with gen.world('pt_inactive_deleted') as w:
        target_world(w)
        r = source(w).get_json()['revision']
        (w.root / 'profiles' / 'second.yaml').unlink()
        missing = source(w, 'second', 'inactive_target_after_deletion')
        assert missing.status_code == 404 and missing.get_json()['error'] == 'no such profile: second', missing.get_json()
        pin_mtimes(w)
        files = w.get(PT, 'inactive_target_list_after_deletion', '/api/profile-files').get_json()
        assert 'second' not in {item['name'] for item in files['profiles']} and files['active'] == 'mine'
        gone = patch(w, 'inactive_target_deleted_before_patch', ['plan'], value=PLAN, revision=r, expect=404).get_json()
        assert gone['error'] == 'no such profile: second', gone
    with gen.world('pt_inactive_special') as w:
        target_world(w)
        write_profile(w, 'default', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        write_profile(w, 'mine.draft', {'player': {}})
        starter = patch(w, 'inactive_target_starter_refused', ['plan'], value=PLAN, name='default', revision='any', expect=409).get_json()
        assert starter['starter'] is True
        draft = patch(w, 'inactive_target_draft_refused', ['plan'], value=PLAN, name='mine.draft', revision='any', expect=409).get_json()
        assert 'scan DRAFT' in draft['error']
    with gen.world('pt_inactive_fenced_profile') as w:
        target_world(w)
        e = page_context(w)
        r = source(w).get_json()['revision']
        assert w.plain_post(ACTIVATE, dict(name='second')).status_code == 200
        config(w, 'read_config_after_editor_active_profile_switch')
        refused = patch(w, 'inactive_target_patch_refused_after_active_switch', ['plan'], value=PLAN, context=e, revision=r, expect=409).get_json()
        assert refused['code'] == 'context_changed' and refused['current_context'] == dict(e, profile='second') and source(w).get_json()['revision'] == r
    with gen.world('pt_inactive_fenced_account') as w:
        target_world(w)
        e = page_context(w)
        r = source(w).get_json()['revision']
        switch_to_beta(w)
        refused = patch(w, 'inactive_target_patch_refused_after_account_switch', ['plan'], value=PLAN, context=e, revision=r, expect=409).get_json()
        assert refused['code'] == 'context_changed' and refused['current_context']['account'] == 'beta' and source(w).get_json()['revision'] == r
    with gen.world('pt_inactive_instance') as w:
        target_world(w)

        def other_instance(cfg):
            cfg['instances']['other'] = dict(cfg['instances']['main'], serial='fake:2', account='alpha')
            cfg['active_instance'] = 'other'
        w.edit_config(other_instance)
        got = config(w, 'read_config_after_editor_instance_switch').get_json()
        assert got['active_instance'] == 'other' and got['active_profile'] == 'mine' and got['instances']['other']['account'] == 'alpha'
    with gen.world('pt_inactive_serial') as w:
        target_world(w)
        w.edit_config(lambda cfg: cfg['instances']['main'].update(serial='fake:2'))
        got = config(w, 'read_config_after_editor_serial_change').get_json()
        assert got['instances']['main']['serial'] == 'fake:2' and got['active_profile'] == 'mine'
    with gen.world('pt_inactive_write_failed') as w:
        target_world(w)
        r = source(w).get_json()['revision']
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('fixture patch failed')))
        failed = patch(w, 'inactive_target_write_failed', ['plan'], value=PLAN, revision=r, expect=500).get_json()
        assert failed == {'ok': False, 'error': 'write failed: fixture patch failed'}, failed
        kept = source(w, 'second', 'inactive_target_after_write_failed').get_json()
        assert kept['revision'] == r and kept['profile']['plan'] != PLAN
    with gen.world('pt_inactive_answer_failed') as w:
        target_world(w)
        r = source(w).get_json()['revision']
        real = w.dash._save_yaml_backup

        def written_then_failed(path, data):
            real(path, data)
            raise OSError('fixture patch failed')
        w.mp.setattr(w.dash, '_save_yaml_backup', written_then_failed)
        patch(w, 'inactive_target_written_then_answer_failed', ['plan'], value=PLAN, revision=r, expect=500)
        w.mp.setattr(w.dash, '_save_yaml_backup', real)
        held = source(w, 'second', 'inactive_target_after_written_then_answer_failed').get_json()
        assert held['revision'] != r and held['profile']['plan'] == PLAN


def profile_target_cases(gen):
    unbinding_cases(gen)
    inactive_target_cases(gen)
