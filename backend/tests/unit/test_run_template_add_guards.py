"""The optional guards of POST /api/run-templates/add: `expected_revision` and `expected_context` (root-approved behaviour change).

Absent, the request is exactly what it always was. `expected_revision` (the profile revision as /api/profile-src publishes it) is judged
under the profile lock BEFORE the recipe and its choices are looked at: a stale one is 409 revision_conflict with the current revision, writing
nothing; a request that carries it also gets the new `revision` back. `expected_context` {instance, account, profile, serial} is the page
fence of the other context-dependent writes: 409 context_changed + current_context, judged first, writing nothing. Every test drives the
real Flask route of backend/dashboard.py over a temporary root (conftest `dash`): no live file, device, ledger or lease.
"""
import hashlib
import os
import threading

import pytest
import yaml

ALPHA = dict(instance='main', account='alpha', profile='mine', serial='fake:1')
BETA = dict(instance='main', account='beta', profile='theirs', serial='fake:1')
URL = '/api/run-templates/add'


def rewrite_config(dash, change):
    with open(dash.CONFIG_PATH, encoding='utf-8') as fh:
        cfg = yaml.safe_load(fh)
    change(cfg)
    with open(dash.CONFIG_PATH, 'w', encoding='utf-8') as fh:
        yaml.safe_dump(cfg, fh)


def two_accounts(cfg, active='alpha'):
    cfg['accounts'] = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {'plain': {}}},
                       'beta': {'label': 'Beta', 'active_profile': 'theirs', 'loadouts': {'plain': {}}}}
    cfg['instances']['main']['account'] = active
    return cfg


@pytest.fixture
def world(dash, tmp_path, monkeypatch):
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_refresh', lambda: None)
    profiles = tmp_path / 'profiles'
    (profiles / 'theirs.yaml').write_bytes((profiles / 'mine.yaml').read_bytes())
    rewrite_config(dash, two_accounts)
    return dash


def path_of(dash, name='mine'):
    return os.path.join(dash.ROOT, 'profiles', f'{name}.yaml')


def revision(dash, name='mine'):
    return hashlib.sha256(open(path_of(dash, name), 'rb').read()).hexdigest()


def runs(dash, name='mine'):
    with open(path_of(dash, name), encoding='utf-8') as fh:
        return set(yaml.safe_load(fh)['blueprints'])


def backups(dash):
    return sorted(name for name in os.listdir(os.path.join(dash.ROOT, 'profiles')) if '.bak-' in name)


def add(dash, run_id='fresh', **extra):
    body = dict(profile='mine', template='farm', run_id=run_id, options={'label': 'Fresh run'})
    body.update(extra)
    return dash.app.test_client().post(URL, json=body)


# ------------------------------------------------------------------ the legacy request is unchanged
def test_a_request_without_the_guards_is_answered_as_it_always_was(world):
    reply = add(world)
    assert reply.status_code == 200
    got = reply.get_json()
    assert set(got) == {'ok', 'profile', 'backup'}, 'the legacy answer gained a member'
    assert 'fresh' in runs(world)


# ------------------------------------------------------------------ expected_revision
def test_the_current_revision_adds_the_run_and_the_answer_names_the_new_revision(world):
    before = revision(world)
    reply = add(world, expected_revision=before)
    assert reply.status_code == 200
    got = reply.get_json()
    assert got['revision'] == revision(world) != before and 'fresh' in runs(world)


def test_the_returned_revision_chains_the_next_add_without_a_read(world):
    first = add(world, 'one', expected_revision=revision(world)).get_json()
    second = add(world, 'two', options={'label': 'Second run'}, expected_revision=first['revision'])
    assert second.status_code == 200 and {'one', 'two'} <= runs(world)


def test_a_stale_revision_is_409_with_the_current_revision_and_writes_nothing(world):
    stale = revision(world)
    assert add(world, 'one', expected_revision=stale).status_code == 200
    files, current = backups(world), revision(world)
    reply = add(world, 'two', options={'label': 'Second run'}, expected_revision=stale)
    got = reply.get_json()
    assert reply.status_code == 409 and got['code'] == 'revision_conflict' and got['revision'] == current and got['ok'] is False
    assert revision(world) == current and 'two' not in runs(world) and backups(world) == files


@pytest.mark.parametrize('bad', [None, '', 5, True, 'abc'])
def test_a_revision_that_is_not_the_current_one_is_stale_whatever_it_is(world, bad):
    reply = add(world, expected_revision=bad)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'revision_conflict'
    assert 'fresh' not in runs(world)


def test_the_revision_is_judged_before_the_recipe_and_its_choices(world):
    current = revision(world)
    invalid = dict(template='ghost', run_id='Not A Valid Id', options={'speed': 1})
    assert add(world, expected_revision='0' * 64, **invalid).status_code == 409
    assert add(world, expected_revision=current, **invalid).status_code == 400


def test_the_resend_of_a_committed_add_is_stale_with_the_revision_and_refused_by_the_run_without_it(world):
    first = revision(world)
    assert add(world, expected_revision=first).status_code == 200
    assert add(world, expected_revision=first).status_code == 409
    resend = add(world)
    assert resend.status_code == 400 and 'already exists' in resend.get_json()['error']


def test_the_revision_is_compared_under_the_profile_lock_so_a_save_that_lands_while_the_request_waits_is_seen(world, monkeypatch):
    """The compare-and-set is only one if the revision is read INSIDE the lock the write holds: a request that read it first, then waited
    for another client's save to finish, would add on top of a profile it never saw. Deterministic: this thread holds the profile lock, the
    request arrives and waits for it, another client's save lands, then the lock is released."""
    path = path_of(world)
    stale = revision(world)
    real_lock = world._profile_lock
    arrived, answer = threading.Event(), {}

    def announcing(target):
        if threading.current_thread() is not threading.main_thread():
            arrived.set()                          # the request has read whatever it reads before the lock and now waits for it
        return real_lock(target)
    monkeypatch.setattr(world, '_profile_lock', announcing)
    with real_lock(path):
        worker = threading.Thread(target=lambda: answer.update(reply=add(world, 'second', options={'label': 'Second run'}, expected_revision=stale)))
        worker.start()
        assert arrived.wait(10), 'the request never reached the profile lock'
        with open(path, encoding='utf-8') as fh:
            profile = yaml.safe_load(fh)
        profile['blueprints']['farm']['label'] = 'Saved by another client'
        with open(path, 'w', encoding='utf-8') as fh:
            yaml.safe_dump(profile, fh)
    worker.join(10)
    assert not worker.is_alive()
    assert answer['reply'].status_code == 409 and answer['reply'].get_json()['code'] == 'revision_conflict', answer['reply'].get_json()
    assert 'second' not in runs(world)


# ------------------------------------------------------------------ expected_context
def test_a_matching_context_adds_and_the_answer_has_no_revision_unless_one_was_asked_for(world):
    reply = add(world, expected_context=ALPHA)
    assert reply.status_code == 200 and 'revision' not in reply.get_json() and 'fresh' in runs(world)


def test_a_request_composed_for_alpha_is_refused_after_the_switch_and_writes_nothing(world):
    rewrite_config(world, lambda cfg: two_accounts(cfg, active='beta'))
    before, files = revision(world), backups(world)
    reply = add(world, expected_context=ALPHA, expected_revision=before)
    got = reply.get_json()
    assert reply.status_code == 409 and got['code'] == 'context_changed' and got['current_context'] == BETA and got['ok'] is False
    assert revision(world) == before and 'fresh' not in runs(world) and backups(world) == files


def test_the_context_is_judged_before_the_revision(world):
    rewrite_config(world, lambda cfg: two_accounts(cfg, active='beta'))
    assert add(world, expected_context=ALPHA, expected_revision='0' * 64).get_json()['code'] == 'context_changed'
    assert add(world, expected_context=BETA, expected_revision='0' * 64).get_json()['code'] == 'revision_conflict'


@pytest.mark.parametrize('bad', [None, 'alpha', [], {}, dict(ALPHA, extra=1), {k: v for k, v in ALPHA.items() if k != 'serial'}, dict(ALPHA, serial=5)])
def test_a_malformed_context_is_a_coded_400_that_writes_nothing(world, bad):
    before = revision(world)
    reply = add(world, expected_context=bad)
    assert reply.status_code == 400 and reply.get_json()['code'] == 'invalid_request', reply.get_json()
    assert revision(world) == before


def test_a_missing_configuration_names_no_context_and_creates_nothing(world):
    os.remove(world.CONFIG_PATH)
    reply = add(world, expected_context=ALPHA)
    assert reply.status_code == 409 and reply.get_json()['current_context'] is None and not os.path.exists(world.CONFIG_PATH)


# ------------------------------------------------------------------ the profile kinds the route refuses whatever the guards say
def test_a_starter_profile_is_refused_with_the_guards(world):
    with open(path_of(world, 'default'), 'w', encoding='utf-8') as fh:
        fh.write(open(path_of(world), encoding='utf-8').read())
    reply = add(world, profile='default', expected_revision='any', expected_context=ALPHA)
    assert reply.status_code == 409 and 'Create a personal profile' in reply.get_json()['error']


# ------------------------------------------------------------------ held through the write
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


def paused_add(world, monkeypatch, **extra):
    inside, release = threading.Event(), threading.Event()
    real = world._save_yaml_backup

    def paused(*args, **kwargs):
        inside.set()
        assert release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(world, '_save_yaml_backup', paused)
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(reply=add(world, **extra)))
    worker.start()
    try:
        assert inside.wait(10), 'the write was never reached'
        free = config_lock_is_free_for_another_thread(world)
    finally:
        release.set()
        worker.join()
    assert answer['reply'].status_code == 200
    return free


def test_the_config_lock_is_held_through_the_write_of_a_fenced_add_and_not_taken_by_a_legacy_one(world, monkeypatch):
    assert paused_add(world, monkeypatch, expected_context=ALPHA) is False
    assert paused_add(world, monkeypatch, run_id='second', options={'label': 'Second run'}) is True
