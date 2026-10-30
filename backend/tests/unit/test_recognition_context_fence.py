"""The optional context fence of the Recognition page's "Apply saved observations": `expected_context` on POST /api/calibrate/apply.

The route writes the profile IN FORCE, and the page composes the request from a configuration read made at click time, so a switch of
account or profile between that read and the write put the observations on the wrong profile. A request that carries `expected_context`
{instance, account, profile, serial} is refused 409 context_changed (with current_context), writing nothing and recording no operation,
when the configuration now names another; a request without it behaves exactly as before. Every test drives the real Flask route of
backend/dashboard.py over a temporary root (conftest `dash`): no live file, device, ledger or lease.
"""

import paths as test_paths
import hashlib
import json
import os
import sqlite3
import threading

import pytest
import yaml

from control import operations

ALPHA = dict(instance='main', account='alpha', profile='mine', serial='fake:1')
BETA = dict(instance='main', account='beta', profile='theirs', serial='fake:1')
APPLY = '/api/calibrate/apply'
SAVED = dict(saved_observations=True)


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


def observe(dash, tier):
    """The saved observations of the account in force (calibrate_state.json of its own calibration folder, no operation)."""
    folder = dash._calibration_dir(dash.load_config())
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'calibrate_state.json'), 'w', encoding='utf-8') as fh:
        json.dump({'player': {'max_tier': tier}}, fh)


@pytest.fixture
def world(dash, tmp_path, monkeypatch):
    """Alpha (profile `mine`) is the page's context and has observed tier 19; Beta (profile `theirs`) is who it becomes and has observed tier 7."""
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_refresh', lambda: None)
    profiles = tmp_path / 'profiles'
    (profiles / 'theirs.yaml').write_bytes((profiles / 'mine.yaml').read_bytes())
    rewrite_config(dash, two_accounts)
    observe(dash, 19)
    rewrite_config(dash, lambda cfg: two_accounts(cfg, active='beta'))
    observe(dash, 7)
    rewrite_config(dash, lambda cfg: two_accounts(cfg, active='alpha'))
    return dash


def switch_to_beta(dash):
    rewrite_config(dash, lambda cfg: two_accounts(cfg, active='beta'))


def tier_of(dash, name):
    with open(os.path.join(dash.ROOT, 'profiles', f'{name}.yaml'), encoding='utf-8') as fh:
        return (yaml.safe_load(fh).get('player') or {}).get('max_tier')


def snapshot(dash):
    """Every byte an apply could change: the config and every profile file."""
    digest = hashlib.sha256()
    for folder, _, names in sorted(os.walk(dash.ROOT)):
        for name in sorted(names):
            if name.endswith('.yaml') or '.bak-' in name:
                path = os.path.join(folder, name)
                digest.update(os.path.relpath(path, dash.ROOT).encode() + open(path, 'rb').read())
    return digest.hexdigest()


def recorded(dash, kind):
    path = operations.ledger_path(dash.ROOT)
    if not os.path.exists(path):
        return 0
    db = sqlite3.connect(path)
    try:
        return db.execute('SELECT COUNT(*) FROM operations WHERE kind=?', (kind,)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        db.close()


def apply(dash, body):
    return dash.app.test_client().post(APPLY, json=body)


# ------------------------------------------------------------------ the fence
def test_a_matching_context_applies_to_the_profile_in_force(world):
    reply = apply(world, dict(SAVED, expected_context=ALPHA))
    assert reply.status_code == 200 and reply.get_json()['applied'] is True and reply.get_json()['profile'] == 'mine'
    assert tier_of(world, 'mine') == 19 and tier_of(world, 'theirs') != 19
    assert recorded(world, 'profile_write') == 1


def test_a_request_composed_for_alpha_is_refused_after_the_switch_and_writes_nothing(world):
    switch_to_beta(world)
    before = snapshot(world)
    reply = apply(world, dict(SAVED, expected_context=ALPHA))
    assert reply.status_code == 409
    got = reply.get_json()
    assert got['code'] == 'context_changed' and got['ok'] is False and got['current_context'] == BETA and 'operation' not in got
    assert snapshot(world) == before
    assert recorded(world, 'profile_write') == 0
    assert tier_of(world, 'mine') != 19 and tier_of(world, 'theirs') != 7


def test_the_same_request_without_the_field_still_lands_on_the_current_profile(world):
    """The legacy page: exactly today's behaviour (this is what the fence exists to prevent for a page that sends it)."""
    switch_to_beta(world)
    reply = apply(world, dict(SAVED))
    assert reply.status_code == 200 and reply.get_json()['profile'] == 'theirs'
    assert tier_of(world, 'theirs') == 7 and tier_of(world, 'mine') != 19


@pytest.mark.parametrize('bad', [None, 'alpha', [], {}, dict(ALPHA, extra=1), {k: v for k, v in ALPHA.items() if k != 'serial'},
                                 dict(ALPHA, serial=5), dict(ALPHA, instance=3)])
def test_a_malformed_field_is_a_coded_400_that_writes_nothing(world, bad):
    before = snapshot(world)
    reply = apply(world, dict(SAVED, expected_context=bad))
    assert reply.status_code == 400 and reply.get_json()['code'] == 'invalid_request', reply.get_json()
    assert snapshot(world) == before and recorded(world, 'profile_write') == 0


def test_a_resend_after_a_committed_apply_and_a_switch_is_refused_not_applied_twice(world):
    assert apply(world, dict(SAVED, expected_context=ALPHA)).status_code == 200
    switch_to_beta(world)
    after = snapshot(world)
    retry = apply(world, dict(SAVED, expected_context=ALPHA))
    assert retry.status_code == 409 and retry.get_json()['code'] == 'context_changed' and retry.get_json()['current_context'] == BETA
    assert snapshot(world) == after and recorded(world, 'profile_write') == 1


# ------------------------------------------------------------------ order
def test_the_context_is_judged_before_the_owner_refusal(world, monkeypatch):
    """A stale request is told its context changed, not 'a runner owns the device': the page must review before it retries."""
    def owned(message, *args, **kwargs):
        raise world.DeviceOwned(dict(ok=False, code='device_owned', error=message, owner={'recovery': 'x'}, recovery='x', retry='x'))
    monkeypatch.setattr(world, '_require_idle', owned)
    switch_to_beta(world)
    assert apply(world, dict(SAVED, expected_context=ALPHA)).get_json()['code'] == 'context_changed'
    assert apply(world, dict(SAVED, expected_context=BETA)).get_json()['code'] == 'device_owned'


def test_the_context_is_judged_before_the_request_is_understood(world):
    """No operation and no saved_observations: the legacy answer is operation_required; a stale context is told first."""
    switch_to_beta(world)
    assert apply(world, dict(expected_context=ALPHA)).get_json()['code'] == 'context_changed'
    assert apply(world, dict(expected_context=BETA)).get_json()['code'] == 'operation_required'


def test_a_missing_or_unreadable_configuration_names_no_context_and_creates_nothing(world):
    os.remove(world.CONFIG_PATH)
    reply = apply(world, dict(SAVED, expected_context=ALPHA))
    assert reply.status_code == 409 and reply.get_json()['code'] == 'context_changed' and reply.get_json()['current_context'] is None
    assert not os.path.exists(world.CONFIG_PATH)


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


def test_the_config_lock_is_held_from_the_check_until_the_profile_is_written(world, monkeypatch):
    inside, release = threading.Event(), threading.Event()
    real = world._save_yaml_backup

    def paused(*args, **kwargs):
        inside.set()
        assert release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(world, '_save_yaml_backup', paused)
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(reply=apply(world, dict(SAVED, expected_context=ALPHA))))
    worker.start()
    try:
        assert inside.wait(10), 'the write was never reached'
        assert config_lock_is_free_for_another_thread(world) is False, 'the config lock was free while the write was under way'
    finally:
        release.set()
        worker.join()
    assert answer['reply'].status_code == 200


# ------------------------------------------------------------------ the other Recognition writes
def test_stop_is_a_revocation_and_is_never_refused_for_a_context(world):
    """A person's Stop must always be reachable: a stale expected_context on it is ignored, never a 409 context_changed."""
    switch_to_beta(world)
    reply = world.app.test_client().post('/api/calibrate/stop', json=dict(expected_context=ALPHA))
    assert (reply.get_json() or {}).get('code') not in ('context_changed', 'invalid_request'), reply.get_json()


def test_the_fence_is_declared_on_the_apply_route():
    """The decorator is what makes the route refuse; the behaviour tests above fail without it, this names why (source check of the route's decorators)."""
    import ast
    import pathlib
    source = (test_paths.DASHBOARD).read_text(encoding='utf-8')
    tree = ast.parse(source)
    route = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'api_calibrate_apply')
    assert '_fenced_by_page_context' in {getattr(item, 'id', None) for item in route.decorator_list}
    stop = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'api_calibrate_stop')
    assert '_fenced_by_page_context' not in {getattr(item, 'id', None) for item in stop.decorator_list}
