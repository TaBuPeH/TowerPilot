"""The context fence of the writes the Profiles (and Equipment) pages send: `expected_context`.

A page composes a write for one emulator, account and profile. The routes below name a profile or an account, not
that context, so a switch between the page's last read and its write sent the write to whatever was current. A
request that carries `expected_context` {instance, account, profile, serial} is refused 409 context_changed, writing
nothing, when the configuration now names another; a request without it behaves exactly as before. Every test
drives the real Flask routes of backend/dashboard.py over a temporary root (conftest `dash`): no live file,
device, ledger or lease.
"""
import copy
import hashlib
import os
import threading

import pytest
import yaml

import settings

ALPHA = dict(instance='main', account='alpha', profile='mine', serial='fake:1')
BETA = dict(instance='main', account='beta', profile='theirs', serial='fake:1')
ROUTES = ('/api/accounts', '/api/account-tier', '/api/profile-activate', '/api/profile-copy', '/api/profile-promote',
          '/api/profile-patch', '/api/loadout-patch')


def two_accounts(cfg, active='alpha'):
    cfg['accounts'] = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {'plain': {}}},
                       'beta': {'label': 'Beta', 'active_profile': 'theirs', 'loadouts': {'plain': {}}}}
    cfg['instances']['main']['account'] = active
    return cfg


def rewrite_config(dash, change):
    with open(dash.CONFIG_PATH, encoding='utf-8') as fh:
        cfg = yaml.safe_load(fh)
    change(cfg)
    with open(dash.CONFIG_PATH, 'w', encoding='utf-8') as fh:
        yaml.safe_dump(cfg, fh)


@pytest.fixture
def world(dash, tmp_path, monkeypatch):
    """Alpha (profile `mine`) is the page's context; Beta (profile `theirs`) is who it becomes. No process table is read."""
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_refresh', lambda: None)
    profiles = tmp_path / 'profiles'
    (profiles / 'theirs.yaml').write_bytes((profiles / 'mine.yaml').read_bytes())
    rewrite_config(dash, two_accounts)
    return dash


def switch_to_beta(dash):
    rewrite_config(dash, lambda cfg: two_accounts(cfg, active='beta'))


def snapshot(dash):
    """Every byte a fenced write could change: the config and every profile file."""
    root, digest = dash.ROOT, hashlib.sha256()
    import os
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            if name.endswith('.yaml') or '.bak-' in name:
                path = os.path.join(folder, name)
                digest.update(os.path.relpath(path, root).encode() + open(path, 'rb').read())
    return digest.hexdigest()


def body_for(route, context=None):
    body = {'/api/accounts': dict(id='gamma', create=True),
            '/api/account-tier': dict(max_tier=7),
            '/api/profile-activate': dict(name='mine'),
            '/api/profile-copy': {'from': 'mine', 'name': 'fresh'},
            '/api/profile-promote': dict(draft='alpha', name='promoted'),
            '/api/profile-patch': dict(name='mine', path=['player', 'max_tier'], value=5),
            '/api/loadout-patch': dict(name='plain', body={'cards': 'x'})}[route]
    return body if context is None else dict(body, expected_context=context)


# ------------------------------------------------------------------ the helpers
def test_the_page_context_is_what_get_config_names(world):
    from control import operations
    client = world.app.test_client()
    served = client.get('/api/config').get_json()
    inst = served['active_instance']
    named = dict(instance=inst, account=served['instances'][inst].get('account'), profile=served['active_profile'],
                 serial=served['instances'][inst].get('serial'))
    assert operations.page_context(world.load_config()) == named == ALPHA


def test_the_page_context_of_an_installation_with_no_account_or_profile(world):
    from control import operations
    rewrite_config(world, lambda cfg: (cfg['instances']['main'].pop('account'), cfg.pop('accounts'), cfg.pop('active_profile')))
    assert operations.page_context(world.load_config()) == dict(instance='main', account=None, profile=None, serial='fake:1')


@pytest.mark.parametrize('body', [None, [], 'text', 5, {}, {'name': 'x'}])
def test_a_request_without_the_field_is_not_fenced(body):
    from control import operations
    assert operations.expected_page_context(body) is None


@pytest.mark.parametrize('bad', [
    None, [], 'alpha', 7, {},
    dict(ALPHA, extra='x'), {k: v for k, v in ALPHA.items() if k != 'serial'}, {k: v for k, v in ALPHA.items() if k != 'account'},
    dict(ALPHA, instance=3), dict(ALPHA, account=['alpha']), dict(ALPHA, profile=True), dict(ALPHA, serial={'a': 1})])
def test_a_malformed_field_is_refused_never_read_as_absent(bad):
    from control import operations
    with pytest.raises(ValueError):
        operations.expected_page_context({'expected_context': bad})


def test_null_account_and_profile_are_legal():
    from control import operations
    ctx = dict(instance='main', account=None, profile=None, serial='fake:1')
    assert operations.expected_page_context({'expected_context': ctx}) == ctx


# ------------------------------------------------- the tier write of the review (F6PE-P02)
def tier_of(dash, profile):
    return yaml.safe_load((__import__('pathlib').Path(dash.ROOT) / 'profiles' / f'{profile}.yaml').read_text(encoding='utf-8')).get('player', {}).get('max_tier')


def test_a_tier_composed_for_alpha_is_refused_after_the_switch_to_beta_and_writes_nothing(world):
    client = world.app.test_client()
    switch_to_beta(world)
    before = snapshot(world)
    reply = client.post('/api/account-tier', json=dict(max_tier=7, expected_context=ALPHA))
    assert reply.status_code == 409
    assert reply.get_json() == dict(ok=False, code='context_changed', current_context=BETA, error=reply.get_json()['error'])
    assert snapshot(world) == before
    assert tier_of(world, 'theirs') != 7 and tier_of(world, 'mine') != 7


def test_the_same_tier_without_the_field_still_goes_to_the_current_account(world):
    """The legacy page: exactly today's behaviour (this is what the fence exists to prevent for a page that sends it)."""
    client = world.app.test_client()
    switch_to_beta(world)
    assert client.post('/api/account-tier', json=dict(max_tier=7)).status_code == 200
    assert tier_of(world, 'theirs') == 7 and tier_of(world, 'mine') != 7


def test_a_matching_context_writes_exactly_as_before(world):
    client = world.app.test_client()
    served = client.get('/api/config').get_json()
    inst = served['active_instance']
    context = dict(instance=inst, account=served['instances'][inst].get('account'), profile=served['active_profile'],
                   serial=served['instances'][inst].get('serial'))
    reply = client.post('/api/account-tier', json=dict(max_tier=7, expected_context=context))
    assert reply.status_code == 200 and reply.get_json() == {'ok': True, 'max_tier': 7}
    assert tier_of(world, 'mine') == 7 and tier_of(world, 'theirs') != 7


# --------------------------------------------------------- every fenced route
@pytest.mark.parametrize('route', ROUTES)
def test_every_context_dependent_write_is_refused_after_a_switch_and_changes_nothing(world, route):
    client = world.app.test_client()
    switch_to_beta(world)
    before = snapshot(world)
    reply = client.post(route, json=body_for(route, ALPHA))
    assert reply.status_code == 409, (route, reply.get_json())
    got = reply.get_json()
    assert got['code'] == 'context_changed' and got['ok'] is False and got['current_context'] == BETA
    assert snapshot(world) == before
    import os
    assert not os.path.exists(os.path.join(world.ROOT, 'profiles', 'fresh.yaml'))
    assert not os.path.exists(os.path.join(world.ROOT, 'profiles', 'account_gamma.yaml'))


@pytest.mark.parametrize('route', ROUTES)
def test_each_route_without_the_field_answers_as_it_did(world, route):
    """Absent field: the legacy page's answer, which never carried a code on these routes (the fence adds none)."""
    reply = world.app.test_client().post(route, json=body_for(route))
    assert 'code' not in reply.get_json(), (route, reply.get_json())


@pytest.mark.parametrize('route', ROUTES)
def test_a_malformed_field_is_a_coded_400_that_writes_nothing(world, route):
    client = world.app.test_client()
    before = snapshot(world)
    for bad in (None, 'alpha', dict(ALPHA, extra=1), {'instance': 'main'}):
        reply = client.post(route, json=dict(body_for(route), expected_context=bad))
        assert reply.status_code == 400 and reply.get_json()['code'] == 'invalid_request', (route, bad, reply.get_json())
    assert snapshot(world) == before


def test_a_field_sent_as_plain_text_json_is_still_judged(world):
    """The handlers read the body with force=True; the fence must read it the same way, or a text/plain body skips it."""
    client = world.app.test_client()
    switch_to_beta(world)
    before = snapshot(world)
    import json
    reply = client.post('/api/account-tier', data=json.dumps(dict(max_tier=7, expected_context=ALPHA)), content_type='text/plain')
    assert reply.status_code == 409 and reply.get_json()['code'] == 'context_changed'
    assert snapshot(world) == before


def test_a_configuration_that_names_no_context_refuses_the_request_and_writes_nothing(world):
    """An account the library does not hold: the configuration resolves to no context at all. The page had a readable
    one, so the answer is the same refusal (current_context null), not a server error and not a write."""
    rewrite_config(world, lambda cfg: cfg['instances']['main'].update(account='ghost'))
    before = snapshot(world)
    reply = world.app.test_client().post('/api/account-tier', json=dict(max_tier=7, expected_context=ALPHA))
    assert reply.status_code == 409
    assert reply.get_json()['code'] == 'context_changed' and reply.get_json()['current_context'] is None
    assert snapshot(world) == before


# ----------------------------------------------- the Equipment page's loadout write
def config_revision(dash):
    return dash.app.test_client().get('/api/config').get_json()['config_revision']


def test_a_loadout_save_with_the_matching_context_and_its_revision_is_written(world):
    client = world.app.test_client()
    reply = client.post('/api/loadout-patch', json=dict(name='plain', body={'cards': 'main_farm'}, expected_revision=config_revision(world),
                                                         expected_context=ALPHA))
    assert reply.status_code == 200 and reply.get_json()['loadouts']['plain'] == {'cards': 'main_farm'}
    assert client.get('/api/config').get_json()['loadouts']['plain'] == {'cards': 'main_farm'}


def test_a_loadout_save_composed_for_alpha_is_refused_after_the_switch_even_with_a_current_revision(world):
    """The revision the page read belongs to the config file, which an account switch also changes; but a switch back and forth
    can restore it. The context decides, whatever the revision says."""
    client = world.app.test_client()
    switch_to_beta(world)
    before = snapshot(world)
    reply = client.post('/api/loadout-patch', json=dict(name='plain', body={'cards': 'main_farm'}, expected_revision=config_revision(world),
                                                         expected_context=ALPHA))
    assert reply.status_code == 409 and reply.get_json()['code'] == 'context_changed' and reply.get_json()['current_context'] == BETA
    assert snapshot(world) == before


def test_the_loadout_context_is_judged_before_the_revision(world):
    client = world.app.test_client()
    switch_to_beta(world)
    stale = '0' * 64
    assert client.post('/api/loadout-patch', json=dict(name='plain', body={'cards': 'x'}, expected_revision=stale,
                                                       expected_context=ALPHA)).get_json()['code'] == 'context_changed'
    assert client.post('/api/loadout-patch', json=dict(name='plain', body={'cards': 'x'}, expected_revision=stale,
                                                       expected_context=BETA)).get_json()['code'] == 'revision_conflict'


# ---------------------------------------------------------------------- order
def test_the_context_is_judged_before_the_owner_refusal(world, monkeypatch):
    """A stale request is told its context changed, not 'a runner owns the device': the page must review before retrying."""
    def owned(message, *args, **kwargs):
        raise world.DeviceOwned(dict(ok=False, code='device_owned', error=message, owner={'recovery': 'x'}, recovery='x', retry='x'))
    monkeypatch.setattr(world, '_require_idle', owned)
    client = world.app.test_client()
    switch_to_beta(world)
    assert client.post('/api/account-tier', json=dict(max_tier=7, expected_context=ALPHA)).get_json()['code'] == 'context_changed'
    assert client.post('/api/account-tier', json=dict(max_tier=7, expected_context=BETA)).get_json()['code'] == 'device_owned'


def test_the_context_is_judged_before_the_field_validators(world):
    client = world.app.test_client()
    switch_to_beta(world)
    assert client.post('/api/account-tier', json=dict(max_tier=99, expected_context=ALPHA)).get_json()['code'] == 'context_changed'
    assert client.post('/api/account-tier', json=dict(max_tier=99, expected_context=BETA)).status_code == 400


# ------------------------------------------------------------- lost reply, retry
def test_the_retry_of_a_committed_switch_is_refused_not_applied_twice(world):
    """The reply of an account switch was lost; the page resends the SAME request. The configuration has moved on, so the
    fence refuses it: nothing is written a second time."""
    client = world.app.test_client()
    first = client.post('/api/accounts', json=dict(id='beta', expected_context=ALPHA))
    assert first.status_code == 200 and first.get_json() == {'ok': True, 'active': 'beta'}
    after = snapshot(world)
    retry = client.post('/api/accounts', json=dict(id='beta', expected_context=ALPHA))
    assert retry.status_code == 409 and retry.get_json()['code'] == 'context_changed'
    assert retry.get_json()['current_context'] == BETA
    assert snapshot(world) == after


# --------------------------------------------------------- held through the write
def config_lock_is_free_for_another_thread(dash):
    taken = []

    def probe():
        free = dash._CONFIG_WRITE_LOCK.acquire(blocking=False)
        taken.append(free)
        if free:
            dash._CONFIG_WRITE_LOCK.release()
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return taken[0]


@pytest.mark.parametrize('route', ['/api/account-tier', '/api/profile-patch', '/api/profile-copy'])
def test_the_config_lock_is_held_from_the_check_until_the_write_is_done(world, monkeypatch, route):
    """Pause the write itself; while it is paused nothing else may take the lock every context switch holds."""
    inside, release = threading.Event(), threading.Event()
    real_backup, real_copy, real_save = world._save_yaml_backup, world.shutil.copyfile, world.save_config

    def hold(real):
        def paused(*args, **kwargs):
            inside.set()
            assert release.wait(10)
            return real(*args, **kwargs)
        return paused
    monkeypatch.setattr(world, '_save_yaml_backup', hold(real_backup))
    monkeypatch.setattr(world.shutil, 'copyfile', hold(real_copy))
    monkeypatch.setattr(world, 'save_config', hold(real_save))
    answer = {}

    def send():
        answer['reply'] = world.app.test_client().post(route, json=body_for(route, ALPHA))
    worker = threading.Thread(target=send)
    worker.start()
    try:
        assert inside.wait(10), 'the write was never reached'
        assert config_lock_is_free_for_another_thread(world) is False, 'the config lock was free while the write was under way'
    finally:
        release.set()
        worker.join()
    assert answer['reply'].status_code == 200, answer['reply'].get_json()


def test_the_legacy_request_does_not_take_the_config_lock_it_never_took(world, monkeypatch):
    """No field, no new locking: the legacy path is untouched."""
    inside, release = threading.Event(), threading.Event()
    real = world._save_yaml_backup

    def paused(*args, **kwargs):
        inside.set()
        assert release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(world, '_save_yaml_backup', paused)
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(reply=world.app.test_client().post('/api/account-tier', json=dict(max_tier=7))))
    worker.start()
    try:
        assert inside.wait(10)
        assert config_lock_is_free_for_another_thread(world) is True
    finally:
        release.set()
        worker.join()
    assert answer['reply'].status_code == 200


# ----------------------------------------------- a refusal writes nothing, not even the file a legacy read would seed (R10-P01)
def files_under(root):
    """Every file of the backend root except logs/: the dashboard's own per-request diagnostics land there for any request, refused or not."""
    return sorted(os.path.relpath(os.path.join(folder, name), root) for folder, _, names in os.walk(root) for name in names
                  if os.path.relpath(folder, root).split(os.sep)[0] != 'logs')


@pytest.mark.parametrize('route', ROUTES)
def test_a_refusal_with_no_saved_configuration_creates_no_file_and_names_no_context(world, route):
    """config.yaml is absent: the legacy resolver would seed it from the shipped example. The fence only READS what is saved, so a
    stale request is refused (current_context null) and the directory is exactly as it was."""
    os.remove(world.CONFIG_PATH)
    before = files_under(world.ROOT)
    reply = world.app.test_client().post(route, json=body_for(route, ALPHA))
    assert reply.status_code == 409, (route, reply.get_json())
    assert reply.get_json()['code'] == 'context_changed' and reply.get_json()['current_context'] is None
    assert not os.path.exists(world.CONFIG_PATH), 'the refusal seeded config.yaml'
    assert files_under(world.ROOT) == before


def test_a_request_without_the_field_still_bootstraps_a_missing_configuration(world):
    """Only the fence is read-only: the legacy request keeps the seeding it always had."""
    os.remove(world.CONFIG_PATH)
    world.app.test_client().post('/api/account-tier', json=dict(max_tier=7))
    assert os.path.exists(world.CONFIG_PATH)


# ----------------------------------------------- stored shapes that resolve to no context are the refusal, not a 500 (R10-P03)
SHAPES = {
    'empty_file': '',
    'null_document': 'null\n',
    'root_list': '- main\n- other\n',
    'selected_instance_is_text': 'active_instance: main\ninstances:\n  main: fake:1\n',
    'instances_is_a_list': 'active_instance: main\ninstances:\n  - main\n',
    'account_record_is_null': 'active_instance: main\ninstances:\n  main:\n    serial: fake:1\n    account: alpha\naccounts:\n  alpha:\n',
    'accounts_is_a_list': 'active_instance: main\ninstances:\n  main:\n    serial: fake:1\n    account: alpha\naccounts:\n  - alpha\n',
}


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_a_stored_configuration_of_an_unexpected_shape_is_the_coded_refusal_and_stays_untouched(world, shape):
    with open(world.CONFIG_PATH, 'w', encoding='utf-8', newline='') as fh:
        fh.write(SHAPES[shape])
    before = files_under(world.ROOT), open(world.CONFIG_PATH, 'rb').read()
    for route in ('/api/account-tier', '/api/loadout-patch'):
        reply = world.app.test_client().post(route, json=body_for(route, ALPHA))
        assert reply.status_code == 409, (shape, route, reply.status_code, reply.get_data(as_text=True)[:200])
        assert reply.get_json()['code'] == 'context_changed' and reply.get_json()['current_context'] is None
    assert (files_under(world.ROOT), open(world.CONFIG_PATH, 'rb').read()) == before


# ----------------------------------------------- this file leaves the settings singleton as it found it (R10-P02)
def test_generating_the_context_fence_contract_family_leaves_no_trace_in_the_settings_singleton(dash, tmp_path, monkeypatch):
    """The contract family saves loadouts through the real handler too (its Equipment cases). Its world undoes that (the base world of
    test_dashboard_contract.py): generating the family alone, which this test does, must leave the live settings table as it was.
    Asserted inside the test, so the suite-wide restoring fixture cannot hide a world that does not undo it."""
    from dashboard_contract_cases_context import context_fence_cases
    from dashboard_contract_pages_world import PagesGenerator
    before = copy.deepcopy(settings.CONFIG)
    gen = PagesGenerator(dash, tmp_path / 'family', monkeypatch)
    context_fence_cases(gen)
    assert settings.CONFIG == before
    assert any(name.startswith('loadout_with_the_matching_context') for name in gen.cases['context_fence']), 'the family did not save a loadout'
