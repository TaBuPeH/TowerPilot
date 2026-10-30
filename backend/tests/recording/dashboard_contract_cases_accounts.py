"""Contract cases of the Profiles page: accounts, account tier, profile copy, activation and promotion
(README rows P-01 and P-02).

Families: accounts_write (POST /api/accounts, POST /api/account-tier), profile_files_write (POST /api/profile-copy,
POST /api/profile-activate, POST /api/profile-promote). The reads are `config_state` (GET /api/config), `profile_reads`
(GET /api/profile-files) and the existing `accounts` family of dashboard_contract_v1.json (GET /api/accounts).
The writes are refused while a runner owns the device (the shared owner refusal, one sample per route here); the world's
process table supplies the runner exactly as in the Dashboard contract.
"""
import copy

import yaml

from dashboard_contract_cases_profile import write_profile
from dashboard_contract_pages_world import pin_mtimes, populate, raw_post
from test_dashboard_contract import raising

ACC, FILES = 'accounts_write', 'profile_files_write'


def with_starter(w):
    """The shipped starter (profiles/default.yaml) next to the populated account."""
    profile = yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8'))
    write_profile(w, 'default', profile)


def account(w, case, body, *, expect=None):
    reply = w.post(ACC, case, '/api/accounts', body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def read_accounts(w, case):
    return w.get(ACC, case, '/api/accounts').get_json()


def accounts_cases(gen):
    with gen.world('acw_ok') as w:
        populate(w)
        with_starter(w)
        before = read_accounts(w, 'read_before_any_account')
        assert before['active'] is None and before['accounts'] == []
        created = account(w, 'create_a_fresh_account', dict(id='Alpha', create=True, label='Alpha tower'), expect=200).get_json()
        assert created == {'ok': True, 'active': 'alpha'}
        after = read_accounts(w, 'read_after_create_names_the_account_active')
        assert after['active'] == 'alpha' and after['accounts'] == [{'id': 'alpha', 'label': 'Alpha tower'}]
        pin_mtimes(w)
        w.get(ACC, 'profile_files_after_create_hold_the_new_profile', '/api/profile-files')
        account(w, 'create_an_account_importing_the_current_one', dict(id='beta', create=True, import_current=True), expect=200)
        account(w, 'select_an_existing_account', dict(id='alpha'), expect=200)
        assert read_accounts(w, 'read_after_select')['active'] == 'alpha'
        account(w, 'create_without_a_label_uses_the_id', dict(id='gamma', create=True), expect=200)
    with gen.world('acw_refused') as w:
        populate(w)
        with_starter(w)
        account(w, 'create_alpha', dict(id='alpha', create=True), expect=200)
        for case, body in (('create_a_duplicate_id', dict(id='alpha', create=True)),
                           ('select_an_unknown_account', dict(id='ghost')),
                           ('id_with_a_space', dict(id='a b', create=True)),
                           ('id_empty', dict(id='', create=True)),
                           ('id_missing', dict(create=True)),
                           ('id_not_text', dict(id=5, create=True)),
                           ('id_with_a_path', dict(id='../x', create=True)),
                           ('id_too_long', dict(id='a' * 80, create=True))):
            reply = account(w, case, body, expect=400).get_json()
            assert reply['ok'] is False, case
        write_profile(w, 'account_delta', {'player': {}})
        account(w, 'profile_file_of_the_new_account_already_exists', dict(id='delta', create=True), expect=400)
        raw_post(w, ACC, 'body_not_json', '/api/accounts', 'id=alpha', content_type='text/plain')
        assert read_accounts(w, 'read_after_the_refusals_alpha_is_still_the_only_account')['accounts'] == [{'id': 'alpha', 'label': 'alpha'}]
    with gen.world('acw_import_missing') as w:
        populate(w)
        with_starter(w)
        w.edit_config(lambda cfg: cfg.update(active_profile='ghost'))
        account(w, 'import_current_but_the_current_profile_is_missing', dict(id='eps', create=True, import_current=True), expect=400)
    with gen.world('acw_owner') as w:
        populate(w)
        with_starter(w)
        w.worker(4601, 'orchestrator.py', args=['--preset', 'bp_farm'])
        refused = account(w, 'refused_while_a_runner_owns_the_device', dict(id='alpha', create=True), expect=409).get_json()
        assert refused['code'] == 'device_owned' and refused['owner']
        assert read_accounts(w, 'read_after_the_owner_refusal_nothing_created')['accounts'] == []
    with gen.world('acw_lock') as w:
        populate(w)
        with_starter(w)
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            locked = account(w, 'action_lock_another_action_in_progress', dict(id='alpha', create=True), expect=409).get_json()
        finally:
            w.dash._ACTION_LOCK.release()
        assert locked['code'] == 'action_lock'
    with gen.world('acw_partial') as w:
        populate(w)
        with_starter(w)
        real = w.dash.save_config
        w.mp.setattr(w.dash, 'save_config', raising(OSError('config.yaml could not be written')))
        failed = account(w, 'create_then_the_config_save_failed_leaves_the_profile_file', dict(id='alpha', create=True), expect=400).get_json()
        assert 'could not be written' in failed['error']
        w.mp.setattr(w.dash, 'save_config', real)
        assert read_accounts(w, 'read_after_the_failed_create_no_account')['accounts'] == []
        pin_mtimes(w)
        listed = w.get(ACC, 'profile_files_after_the_failed_create_list_the_orphan', '/api/profile-files').get_json()
        assert 'account_alpha' in {p['name'] for p in listed['profiles']}
        stuck = account(w, 'retry_with_the_same_id_is_refused_because_the_orphan_exists', dict(id='alpha', create=True), expect=400).get_json()
        assert 'already exists' in stuck['error']
        account(w, 'a_different_id_creates_normally', dict(id='alpha2', create=True), expect=200)


def tier(w, case, body, *, expect=None):
    reply = w.post(ACC, case, '/api/account-tier', body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def account_tier_cases(gen):
    with gen.world('tier_ok') as w:
        populate(w)
        read = w.get(ACC, 'read_before_the_tier', '/api/accounts').get_json()
        assert read['max_tier'] == 1
        assert tier(w, 'tier_saved_by_the_operator', dict(max_tier=12), expect=200).get_json() == {'ok': True, 'max_tier': 12}
        assert w.get(ACC, 'read_after_the_tier', '/api/accounts').get_json()['max_tier'] == 12
        src = w.get(ACC, 'profile_source_after_the_tier_records_the_operator_as_provenance', '/api/profile-src/mine').get_json()
        assert src['profile']['player']['max_tier'] == 12 and src['profile']['player']['max_tier_verified_by'] == 'operator'
        tier(w, 'tier_lowest', dict(max_tier=1), expect=200)
        tier(w, 'tier_highest', dict(max_tier=20), expect=200)
    with gen.world('tier_refused') as w:
        populate(w)
        for case, value in (('tier_zero', 0), ('tier_21', 21), ('tier_fraction', 5.5), ('tier_text', '5'), ('tier_true', True), ('tier_null', None)):
            assert tier(w, case, dict(max_tier=value), expect=400).get_json()['error'] == 'Enter an unlocked tier from 1 to 20.'
        tier(w, 'tier_missing', {}, expect=400)
        raw_post(w, ACC, 'tier_body_not_json', '/api/account-tier', 'max_tier=5', content_type='text/plain')
        assert w.get(ACC, 'read_after_the_refusals_tier_unchanged', '/api/accounts').get_json()['max_tier'] == 1
    with gen.world('tier_profiles') as w:
        populate(w)
        with_starter(w)
        w.edit_config(lambda cfg: cfg.update(active_profile='default'))
        assert tier(w, 'tier_of_the_starter_is_refused', dict(max_tier=5), expect=409).get_json()['error'] == 'Create your account first.'
        w.edit_config(lambda cfg: cfg.update(active_profile='ghost'))
        tier(w, 'tier_with_no_profile_file_is_refused', dict(max_tier=5), expect=409)
        w.edit_config(lambda cfg: cfg.update(active_profile=None))
        tier(w, 'tier_with_no_active_profile_is_refused', dict(max_tier=5), expect=409)
    with gen.world('tier_problem') as w:
        populate(w, mutate=lambda profile: profile['blueprints']['farm_deep'].update(loadout='nope'))
        reply = tier(w, 'tier_refused_because_the_profile_has_a_problem', dict(max_tier=5), expect=409).get_json()
        assert 'nope' in reply['error']
    with gen.world('tier_owner') as w:
        populate(w)
        w.worker(4602, 'orchestrator.py', args=['--preset', 'bp_farm'])
        refused = tier(w, 'tier_refused_while_a_runner_owns_the_device', dict(max_tier=5), expect=409).get_json()
        assert refused['code'] == 'device_owned'
    with gen.world('tier_write_failed') as w:
        populate(w)
        w.mp.setattr(w.dash, '_save_yaml_backup', raising(OSError('profiles/mine.yaml could not be written')))
        assert tier(w, 'tier_write_failed_500', dict(max_tier=5)).status_code == 500
        assert w.get(ACC, 'read_after_the_failed_tier_write_unchanged', '/api/accounts').get_json()['max_tier'] == 1
    with gen.world('tier_foreign') as w:
        from dashboard_contract_cases_profile import two_accounts
        populate(w, config=lambda cfg: two_accounts(cfg, active='alpha', profile='theirs'))
        write_profile(w, 'theirs', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        w.edit_config(lambda cfg: cfg['accounts']['beta'].update(active_profile='theirs'))
        assert tier(w, 'tier_of_another_accounts_profile_is_not_refused_by_this_route', dict(max_tier=7), expect=200).get_json()['ok'] is True


def run_accounts_cases(gen):
    accounts_cases(gen)
    account_tier_cases(gen)

# ------------------------------------------------- copy, activate, promote (P-02)
def post(w, case, path, body, *, expect=None):
    reply = w.post(FILES, case, path, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def listing(w, case):
    pin_mtimes(w)
    return w.get(FILES, case, '/api/profile-files').get_json()


def profile_copy_cases(gen):
    url = '/api/profile-copy'
    with gen.world('pf_copy') as w:
        populate(w)
        with_starter(w)
        write_profile(w, 'mine.draft', {'player': {}})
        listing(w, 'files_before_the_copy')
        assert post(w, 'copy_the_starter_to_a_new_profile', url, dict(name='fresh'), expect=200).get_json() == {'ok': True, 'name': 'fresh', 'copied_from': 'default'}
        listed = listing(w, 'files_after_the_copy_list_it_and_copy_activates_nothing')
        assert listed['active'] == 'mine' and 'fresh' in {p['name'] for p in listed['profiles']}
        post(w, 'copy_one_of_your_own_profiles', url, {'from': 'mine', 'name': 'spare'}, expect=200)
        assert post(w, 'copy_onto_an_existing_profile_is_refused', url, {'from': 'mine', 'name': 'fresh'}, expect=409).get_json()['error'].endswith('exists')
        post(w, 'copy_onto_an_existing_profile_with_overwrite', url, {'from': 'mine', 'name': 'fresh', 'overwrite': True}, expect=200)
        assert post(w, 'copy_onto_the_starter_is_refused', url, dict(name='default'), expect=409).get_json()['starter'] is True
        post(w, 'copy_from_a_scan_draft_is_refused', url, {'from': 'mine.draft', 'name': 'x'}, expect=404)
        post(w, 'copy_from_an_unknown_profile', url, {'from': 'ghost', 'name': 'x'}, expect=404)
        post(w, 'copy_to_a_draft_name_is_refused', url, {'name': 'new.draft'}, expect=400)
        post(w, 'copy_to_a_bad_name', url, {'name': '../x'}, expect=400)
        post(w, 'copy_without_a_name', url, {}, expect=400)
        raw_post(w, FILES, 'copy_body_not_json', url, 'name=x', content_type='text/plain')
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            assert post(w, 'copy_action_lock_another_action_in_progress', url, dict(name='y'), expect=409).get_json()['code'] == 'action_lock'
        finally:
            w.dash._ACTION_LOCK.release()


def profile_activate_cases(gen):
    url = '/api/profile-activate'
    with gen.world('pf_activate') as w:
        populate(w)
        with_starter(w)
        write_profile(w, 'mine.draft', {'player': {}})
        write_profile(w, 'broken', dict(yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')), plan={}))
        write_profile(w, 'second', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        done = post(w, 'activate_another_profile', url, dict(name='second'), expect=200).get_json()
        assert done['active'] == 'second' and done['backup']
        assert listing(w, 'files_after_the_activation_name_the_active_profile')['active'] == 'second'
        post(w, 'activate_the_starter', url, dict(name='default'), expect=200)
        unbound = post(w, 'activate_none_unbinds_the_profile', url, dict(name=None), expect=200).get_json()
        assert unbound['active'] is None and 'unbound' in unbound['message']
        assert post(w, 'activate_none_again_says_nothing_was_bound', url, dict(name=None), expect=200).get_json()['message'] == 'no profile was bound'
        assert listing(w, 'files_after_unbinding_have_no_active_profile')['active'] is None
        post(w, 'activate_an_unknown_profile', url, dict(name='ghost'), expect=400)
        post(w, 'activate_a_scan_draft', url, dict(name='mine.draft'), expect=400)
        invalid = post(w, 'activate_a_profile_with_problems_is_refused', url, dict(name='broken'), expect=400).get_json()
        assert invalid['problems'] and 'fix them before activating' in invalid['error']
        post(w, 'activate_a_name_with_a_path', url, dict(name='../x'), expect=400)
        post(w, 'activate_a_name_that_is_not_text', url, dict(name=5), expect=400)
        raw_post(w, FILES, 'activate_body_not_json', url, 'name=second', content_type='text/plain')
        w.worker(4611, 'orchestrator.py', args=['--preset', 'bp_farm'])
        refused = post(w, 'activate_refused_while_a_runner_owns_the_device', url, dict(name='second'), expect=409).get_json()
        assert refused['code'] == 'device_owned'
        assert listing(w, 'files_after_the_owner_refusal_unchanged')['active'] is None
    with gen.world('pf_activate_foreign') as w:
        from dashboard_contract_cases_profile import two_accounts
        populate(w, config=two_accounts)
        write_profile(w, 'theirs', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        refused = post(w, 'activate_another_accounts_profile_is_refused', url, dict(name='theirs'), expect=409)
        assert 'another account' in refused.get_json()['error']
    with gen.world('pf_activate_write_failed') as w:
        populate(w)
        write_profile(w, 'second', yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')))
        w.mp.setattr(w.dash, 'save_config', raising(OSError('config.yaml could not be written')))
        post(w, 'activate_write_failed_500', url, dict(name='second'), expect=500)
        assert listing(w, 'files_after_the_failed_activation_unchanged')['active'] == 'mine'


DRAFT = {'player': {'seeded_from': 'scan', 'card_presets': ['main_farm'], 'abilities': {'nuke': True, 'demon_mode': True}, 'tier_current': 9,
                    'uws': {'chain_lightning': True}}}


def profile_promote_cases(gen):
    url = '/api/profile-promote'
    body = dict(draft='main', name='promoted', base='default')
    with gen.world('pf_promote') as w:
        populate(w)
        with_starter(w)
        write_profile(w, 'main.draft', DRAFT)
        done = post(w, 'promote_a_draft_over_the_starter', url, body, expect=200).get_json()
        assert done['ok'] and done['applied'] and done['name'] == 'promoted' and done['abilities_verified'] is False
        promoted = w.get(FILES, 'profile_source_of_the_promoted_profile_carries_the_draft_player_block', '/api/profile-src/promoted').get_json()
        assert promoted['profile']['player']['max_tier'] == 9 and promoted['profile']['player']['wall'] is False
        assert listing(w, 'files_after_the_promotion_activate_nothing')['active'] == 'mine'
        exists = post(w, 'promote_onto_an_existing_profile_is_refused', url, body, expect=409).get_json()
        assert exists['exists'] is True
        post(w, 'promote_onto_an_existing_profile_with_overwrite', url, dict(body, overwrite=True), expect=200)
        post(w, 'promote_with_the_draft_given_with_its_suffix', url, dict(body, draft='main.draft.yaml', name='promoted2'), expect=200)
        assert post(w, 'promote_to_the_starter_name_is_refused', url, dict(body, name='default'), expect=409).get_json()['starter'] is True
        post(w, 'promote_without_a_draft', url, dict(body, draft='ghost'), expect=400)
        post(w, 'promote_draft_name_missing', url, dict(name='x'), expect=400)
        post(w, 'promote_name_missing', url, dict(draft='main'), expect=400)
        post(w, 'promote_to_a_draft_name', url, dict(body, name='x.draft'), expect=400)
        post(w, 'promote_over_a_base_that_does_not_exist', url, dict(body, name='x', base='ghost'), expect=400)
        post(w, 'promote_over_a_draft_base', url, dict(body, name='x', base='main.draft'), expect=400)
        write_profile(w, 'empty.draft', {'player': {}})
        post(w, 'promote_a_draft_with_an_empty_player_block', url, dict(body, draft='empty', name='x'), expect=400)
        raw_post(w, FILES, 'promote_body_not_json', url, 'draft=main', content_type='text/plain')
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            assert post(w, 'promote_action_lock_another_action_in_progress', url, dict(body, name='locked'), expect=409).get_json()['code'] == 'action_lock'
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('pf_promote_problems') as w:
        populate(w)
        with_starter(w)
        write_profile(w, 'main.draft', DRAFT)
        w.edit_config(lambda cfg: cfg['loadouts'].clear())    # the starter's runs name a loadout this machine does not have
        done = post(w, 'promotion_returns_the_problems_it_does_not_enforce_them', url, body, expect=200).get_json()
        assert done['ok'] is True and done['problems']
    with gen.world('pf_promote_failed') as w:
        populate(w)
        with_starter(w)
        write_profile(w, 'main.draft', DRAFT)
        import runtime.files as files
        w.mp.setattr(files, 'atomic_text', raising(OSError('profiles/promoted.yaml could not be written')))
        failed = post(w, 'promote_write_failed_400_receipt_failed', url, body, expect=400).get_json()
        assert 'could not be written' in failed['error']
        listed = listing(w, 'files_after_the_failed_promotion_have_no_new_profile')
        assert 'promoted' not in {p['name'] for p in listed['profiles']}


def run_profile_files_cases(gen):
    profile_copy_cases(gen)
    profile_activate_cases(gen)
    profile_promote_cases(gen)

def a_finished_scan(w, *, status='succeeded'):
    """A scan operation on the device whose sealed scan_state.json holds the draft (the sealing of runtime.operations)."""
    import hashlib
    import json
    from pathlib import Path
    context = w.context()
    op, _ = w.store.accept('scan', context, {})
    op = w.store.transition(op['id'], op['revision'], 'running')
    document = json.dumps({'operation_id': op['id'], 'player': DRAFT['player']}).encode()
    path = Path(context['artifact_dir']) / 'operations' / op['id'] / 'scan_state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document)
    return w.store.transition(op['id'], op['revision'], status, result_refs={'scan_state.json': {'path': str(path), 'revision': hashlib.sha256(document).hexdigest()}})


def promotion_from_a_scan_cases(gen):
    url = '/api/profile-promote'

    def body(scan, **changes):
        return dict(dict(operation_id=scan['id'], expected_revision=scan['revision'], draft='main', name='promoted', base='default'), **changes)
    with gen.world('pf_scan_promote') as w:
        populate(w)
        with_starter(w)
        scan = a_finished_scan(w)
        done = post(w, 'promote_the_result_of_a_finished_scan', url, body(scan), expect=200).get_json()
        assert done['ok'] and done['operation']['id'] == scan['id'] and done['promotion']['status'] == 'succeeded'
        retry = post(w, 'identical_retry_is_answered_from_the_receipt', url, body(scan), expect=200).get_json()
        assert retry['promotion']['id'] == done['promotion']['id']
        post(w, 'the_same_scan_with_another_name_is_a_different_request', url, body(scan, name='other'), expect=409)
        w.get(FILES, 'the_scan_operation_after_the_promotion', f"/api/operations/{scan['id']}")
    with gen.world('pf_scan_refused') as w:
        populate(w)
        with_starter(w)
        scan = a_finished_scan(w)
        post(w, 'stale_expected_revision', url, body(scan, expected_revision=scan['revision'] - 1), expect=409)
        post(w, 'operation_unknown', url, body(scan, operation_id='no-such-operation'), expect=409)
        post(w, 'operation_id_without_a_revision', url, dict(operation_id=scan['id'], draft='main', name='promoted'), expect=409)
        post(w, 'draft_of_another_account_scan', url, body(scan, draft='someone'), expect=409)
        write_profile(w, 'mine', dict(yaml.safe_load((w.root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8')), note='edited after the scan'))
        post(w, 'profile_changed_after_scanning', url, body(scan), expect=409)
    with gen.world('pf_scan_kinds') as w:
        populate(w)
        with_starter(w)
        other = w.store.accept('calibrate', w.context(), {})[0]
        other = w.store.transition(other['id'], other['revision'], 'failed')
        post(w, 'operation_that_is_not_a_scan', url, dict(operation_id=other['id'], expected_revision=other['revision'], draft='main', name='x'), expect=409)
        running = w.running('scan', {}, args=[])
        post(w, 'scan_still_running_is_not_final', url, dict(operation_id=running['id'], expected_revision=running['revision'], draft='main', name='x'), expect=409)
    with gen.world('pf_scan_owner') as w:
        populate(w)
        with_starter(w)
        scan = a_finished_scan(w)
        w.worker(4621, 'orchestrator.py', args=['--preset', 'bp_farm'])
        refused = post(w, 'promotion_refused_while_a_runner_owns_the_device', url, body(scan), expect=409).get_json()
        assert refused['code'] == 'device_owned'
    with gen.world('pf_scan_failed') as w:
        populate(w)
        with_starter(w)
        scan = a_finished_scan(w)
        import runtime.files as files
        real = files.atomic_text
        w.mp.setattr(files, 'atomic_text', raising(OSError('profiles/promoted.yaml could not be written')))
        failed = post(w, 'apply_failed_the_receipt_is_failed', url, body(scan), expect=400).get_json()
        assert 'could not be written' in failed['error']
        kept = w.get(FILES, 'the_scan_operation_after_the_failed_apply_still_holds_its_capture', f"/api/operations/{scan['id']}").get_json()['operation']
        assert kept['status'] == 'succeeded' and kept['result_refs']['scan_state.json']
        w.mp.setattr(files, 'atomic_text', real)
        again = post(w, 'retry_after_the_failed_apply', url, body(scan))
        assert again.status_code in (200, 409), again.get_json()


def run_profile_files_cases(gen):
    profile_copy_cases(gen)
    profile_activate_cases(gen)
    profile_promote_cases(gen)
    promotion_from_a_scan_cases(gen)