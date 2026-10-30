"""Contract cases of the context fence (review finding F6PE-P02): `expected_context` on the context-dependent writes.

Family context_fence. The routes name a profile or an account, not the context the page composed the write in, so a
switch between the page's last read and its write sent the write to whatever was current. A request that carries
`expected_context` {instance, account, profile, serial} - read the way the page reads it, from GET /api/config - is
refused 409 context_changed, writing nothing, when the configuration now names another. A request without the field is
the legacy page's and is not fenced. The scenarios use two accounts: Alpha (profile `mine`) and Beta (profile `theirs`).
"""
import copy
import os

import yaml

from dashboard_contract_cases_editors import LOADOUTS, PLAYER
from dashboard_contract_cases_profile import two_accounts, write_profile
from dashboard_contract_pages_world import pin_mtimes, populate

CF = 'context_fence'

# the body each fenced route is sent in these scenarios (no field: the legacy request)
ROUTE_BODIES = (
    ('accounts_create', '/api/accounts', dict(id='gamma', create=True)),
    ('accounts_select', '/api/accounts', dict(id='alpha')),
    ('account_tier', '/api/account-tier', dict(max_tier=7)),
    ('profile_activate', '/api/profile-activate', dict(name='mine')),
    ('profile_copy', '/api/profile-copy', {'from': 'mine', 'name': 'fresh'}),
    ('profile_promote', '/api/profile-promote', dict(draft='alpha', name='promoted')),
    ('profile_patch', '/api/profile-patch', dict(name='mine', path=['player', 'max_tier'], value=5)),
    ('loadout_patch', '/api/loadout-patch', dict(name='plain', body={'cards': 'x'})),
)


def page_context(w):
    """The context as the page derives it from GET /api/config (active_instance, that instance's account and serial, active_profile)."""
    served = w.plain_get('/api/config').get_json()
    instance = served['active_instance']
    device = served['instances'][instance]
    return dict(instance=instance, account=device.get('account'), profile=served['active_profile'], serial=device.get('serial'))


def fenced(w, case, path, body, context, *, expect):
    reply = w.post(CF, case, path, dict(body, expected_context=context))
    assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply.get_json()


def two_account_world(w):
    populate(w, config=two_accounts)
    mine = yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8'))
    write_profile(w, 'theirs', mine)


def equipment_world(w):
    """Two accounts that both own the Equipment page's inventory (the editors' PLAYER) and its loadouts, so the real validator accepts a loadout."""
    def configure(cfg):
        two_accounts(cfg)
        for account in cfg['accounts'].values():
            account['loadouts'] = copy.deepcopy(LOADOUTS)

    def inventory(profile):
        profile['player'] = copy.deepcopy(PLAYER)
    populate(w, mutate=inventory, config=configure)
    write_profile(w, 'theirs', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))


def loadout(w, case, body, context, *, expect):
    """POST /api/loadout-patch with `expected_context` as given (None sends an explicit null)."""
    reply = w.post(CF, case, '/api/loadout-patch', dict(body, expected_context=context))
    assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply.get_json()


def switch_to_beta(w):
    w.edit_config(lambda cfg: two_accounts(cfg, active='beta'))


def context_fence_cases(gen):
    with gen.world('cf_switch') as w:
        two_account_world(w)
        alpha = page_context(w)
        assert alpha['account'] == 'alpha' and alpha['profile'] == 'mine'
        w.get(CF, 'read_config_the_page_context_comes_from', '/api/config')
        assert fenced(w, 'tier_with_the_matching_context_is_written', '/api/account-tier', dict(max_tier=7), alpha, expect=200) == {'ok': True, 'max_tier': 7}
        assert w.get(CF, 'read_after_the_matching_tier', '/api/accounts').get_json()['max_tier'] == 7
        switch_to_beta(w)
        beta = page_context(w)
        assert beta['account'] == 'beta' and beta['profile'] == 'theirs'
        w.get(CF, 'read_config_after_another_client_switched_the_account', '/api/config')
        for name, path, body in ROUTE_BODIES:
            refused = fenced(w, f'{name}_composed_for_alpha_is_refused_after_the_switch', path, body, alpha, expect=409)
            assert refused['code'] == 'context_changed' and refused['current_context'] == beta and refused['ok'] is False, name
        assert w.get(CF, 'read_after_the_refusals_beta_tier_unchanged', '/api/accounts').get_json()['max_tier'] == 1
        pin_mtimes(w)
        listed = w.get(CF, 'profile_files_after_the_refusals_nothing_was_created', '/api/profile-files').get_json()
        names = {p['name'] for p in listed['profiles']}   # the listing hides the other account's profile (`mine`) once Beta is active
        assert 'theirs' in names and not names & {'fresh', 'promoted', 'account_gamma'}, names
        assert w.get(CF, 'read_after_the_refusals_no_account_was_created', '/api/accounts').get_json()['accounts'] == [
            {'id': 'alpha', 'label': 'Alpha'}, {'id': 'beta', 'label': 'Beta'}]
        for case, bad in (('expected_context_null_is_refused_not_read_as_absent', None),
                          ('expected_context_not_an_object', 'alpha'),
                          ('expected_context_missing_the_serial', {k: v for k, v in beta.items() if k != 'serial'}),
                          ('expected_context_with_an_unknown_key', dict(beta, rendering=None)),
                          ('expected_context_serial_not_text', dict(beta, serial=5))):
            refused = fenced(w, f'tier_{case}', '/api/account-tier', dict(max_tier=7), bad, expect=400)
            assert refused['code'] == 'invalid_request', case
        assert fenced(w, 'accounts_expected_context_null_is_refused', '/api/accounts', dict(id='alpha'), None, expect=400)['code'] == 'invalid_request'
        assert w.get(CF, 'read_after_the_malformed_requests_beta_tier_unchanged', '/api/accounts').get_json()['max_tier'] == 1
        assert w.post(CF, 'tier_without_the_field_is_not_fenced_the_legacy_page', '/api/account-tier', dict(max_tier=9)).status_code == 200
        assert w.get(CF, 'read_after_the_legacy_tier_beta_holds_it', '/api/accounts').get_json()['max_tier'] == 9
    with gen.world('cf_loadout') as w:
        # POST /api/loadout-patch (the Equipment page): the same fence, with the page's own expected_revision next to it
        equipment_world(w)
        alpha = page_context(w)
        first = w.get(CF, 'read_config_before_the_loadout_save', '/api/config').get_json()
        revision = first['config_revision']
        body = {'global_preset': 'Farm Run'}
        saved = loadout(w, 'loadout_with_the_matching_context_is_written', dict(name='plain', body=body, expected_revision=revision), alpha, expect=200)
        assert saved['ok'] and saved['loadouts']['plain'] == body and saved['config_revision'] != revision
        revision = saved['config_revision']
        read = w.get(CF, 'read_config_after_the_matching_loadout_holds_it', '/api/config').get_json()
        assert read['loadouts']['plain'] == body and read['config_revision'] == revision
        for case, bad in (('loadout_expected_context_null_is_refused_not_read_as_absent', None),
                          ('loadout_expected_context_not_an_object', 'alpha'),
                          ('loadout_expected_context_missing_the_serial', {k: v for k, v in alpha.items() if k != 'serial'}),
                          ('loadout_expected_context_with_an_unknown_key', dict(alpha, rendering=None)),
                          ('loadout_expected_context_serial_not_text', dict(alpha, serial=5))):
            refused = loadout(w, case, dict(name='plain', body={'cards': 'main_farm'}, expected_revision=revision), bad, expect=400)
            assert refused['code'] == 'invalid_request', case
        assert w.get(CF, 'read_config_after_the_malformed_loadout_requests_unchanged', '/api/config').get_json()['config_revision'] == revision
        w.edit_config(lambda cfg: cfg['instances']['main'].update(account='beta'))   # only the account moves: both keep the same loadouts
        beta = page_context(w)
        before = w.get(CF, 'read_config_after_another_client_switched_the_account_for_the_loadout', '/api/config').get_json()
        refused = loadout(w, 'loadout_composed_for_alpha_is_refused_with_its_expected_revision', dict(name='plain', body={'cards': 'main_farm'}, expected_revision=revision), alpha, expect=409)
        assert refused['code'] == 'context_changed' and refused['current_context'] == beta and refused['ok'] is False
        stale = loadout(w, 'loadout_with_a_stale_context_and_a_stale_revision_is_told_the_context_changed', dict(name='plain', body={'cards': 'main_farm'}, expected_revision='0' * 64), alpha, expect=409)
        assert stale['code'] == 'context_changed'
        current = loadout(w, 'loadout_for_the_current_context_with_a_stale_revision_is_a_revision_conflict', dict(name='plain', body={'cards': 'main_farm'}, expected_revision='0' * 64), beta, expect=409)
        assert current['code'] == 'revision_conflict' and 'current_context' not in current
        after = w.get(CF, 'read_config_after_the_loadout_refusals_nothing_was_written', '/api/config').get_json()
        assert after['config_revision'] == before['config_revision'] and after['loadouts'] == before['loadouts']
        legacy = w.post(CF, 'loadout_without_the_field_is_not_fenced_the_legacy_page', '/api/loadout-patch', dict(name='plain', body={'cards': 'main_farm'}))
        assert legacy.status_code == 200 and legacy.get_json()['ok'] is True
        assert w.get(CF, 'read_config_after_the_legacy_loadout_beta_holds_it', '/api/config').get_json()['loadouts']['plain'] == {'cards': 'main_farm'}
    with gen.world('cf_owner') as w:
        two_account_world(w)
        alpha = page_context(w)
        switch_to_beta(w)
        beta = page_context(w)
        w.worker(4701, 'orchestrator.py', args=['--preset', 'bp_farm'])
        stale = fenced(w, 'stale_tier_while_a_runner_owns_the_device_is_told_the_context_changed', '/api/account-tier', dict(max_tier=7), alpha, expect=409)
        assert stale['code'] == 'context_changed'
        current = fenced(w, 'current_tier_while_a_runner_owns_the_device_is_refused_by_the_owner', '/api/account-tier', dict(max_tier=7), beta, expect=409)
        assert current['code'] == 'device_owned'
    with gen.world('cf_unresolved') as w:
        two_account_world(w)
        alpha = page_context(w)
        w.edit_config(lambda cfg: cfg['instances']['main'].update(account='ghost'))
        refused = fenced(w, 'tier_when_the_configuration_names_an_unknown_account_is_refused', '/api/account-tier', dict(max_tier=7), alpha, expect=409)
        assert refused['code'] == 'context_changed' and refused['current_context'] is None
    with gen.world('cf_retry') as w:
        two_account_world(w)
        alpha = page_context(w)
        assert fenced(w, 'account_switch_composed_for_alpha_is_committed', '/api/accounts', dict(id='beta'), alpha, expect=200) == {'ok': True, 'active': 'beta'}
        retried = fenced(w, 'the_same_request_resent_after_a_lost_answer_is_refused', '/api/accounts', dict(id='beta'), alpha, expect=409)
        assert retried['code'] == 'context_changed' and retried['current_context']['account'] == 'beta'
        assert w.get(CF, 'read_after_the_retry_beta_is_active', '/api/accounts').get_json()['active'] == 'beta'
    with gen.world('cf_noaccount') as w:
        populate(w)
        context = page_context(w)
        assert context['account'] is None and context['profile'] == 'mine'
        assert fenced(w, 'copy_with_a_null_account_in_the_context_is_written', '/api/profile-copy', {'from': 'mine', 'name': 'spare'}, context, expect=200)['ok'] is True
        stale = fenced(w, 'copy_for_an_account_that_is_not_bound_is_refused', '/api/profile-copy', {'from': 'mine', 'name': 'other'}, dict(context, account='alpha'), expect=409)
        assert stale['code'] == 'context_changed' and stale['current_context']['account'] is None
    # ---- a refusal reads, it never seeds (review R10-P01) and a stored shape that is no configuration names no context (R10-P03).
    # These worlds come after every earlier scenario of the family, so the ids and hashes of the cases above do not move.
    with gen.world('cf_noconfig') as w:
        two_account_world(w)
        alpha = page_context(w)
        (w.root / 'config.example.yaml').write_text('active_instance: main\ninstances: {main: {serial: "example:1"}}\n', encoding='utf-8')   # what a legacy request would seed from
        os.remove(w.root / 'config.yaml')
        refused = fenced(w, 'tier_when_config_yaml_is_missing_is_refused_and_creates_nothing', '/api/account-tier', dict(max_tier=7), alpha, expect=409)
        assert refused['code'] == 'context_changed' and refused['current_context'] is None
        assert not (w.root / 'config.yaml').exists(), 'the refusal seeded config.yaml'
    for name, text in (('empty', ''), ('a_list', '- main\n- other\n'), ('a_selected_instance_that_is_text', 'active_instance: main\ninstances:\n  main: fake:1\n')):
        with gen.world(f'cf_shape_{name}') as w:
            two_account_world(w)
            alpha = page_context(w)
            (w.root / 'config.yaml').write_text(text, encoding='utf-8', newline='')
            refused = fenced(w, f'tier_when_config_yaml_is_{name}_is_refused', '/api/account-tier', dict(max_tier=7), alpha, expect=409)
            assert refused['code'] == 'context_changed' and refused['current_context'] is None
            assert (w.root / 'config.yaml').read_text(encoding='utf-8') == text

