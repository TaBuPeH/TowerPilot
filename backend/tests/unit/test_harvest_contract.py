"""The published v1 harvesting contract, generated from the real code.

backend/tests/fixtures/harvest_status_v1.json is what the UI binds to: one
GET /api/status.harvest sample per state, the POST /api/harvest answers, the
shared owner refusal, the tap-revocation answers and the run takeover. It is
GENERATED here from real temporary ledgers and the real Flask app (fixed clock,
ids aliased in order of appearance), never edited by hand. A normal run only
compares: any change in a field name, a code or a state fails until a developer
regenerates it on purpose and the UI side re-reads it:

    python -m pytest backend/tests/unit/test_harvest_contract.py --regenerate-harvest-fixture

Offline: temporary ledgers and leases, fake process and device boundaries."""

import paths as test_paths
from fixture_json import render_fixture_json
import copy
import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

from control import harvest, operations
from harvest_http import body, collector_row, enable, make_app, running_collector, stub_status
from control.operations import inspect_worker as real_inspect_worker
from harvest_support import (
    FULL, NOW, OFF_ROUTINES, _battle, _collector, _run, ad_window, choose, fake_inspect, fake_platform, finish_last,
    identity, make_env, routine, sealed_unresolved,
)

FIXTURE = test_paths.FIXTURES / 'harvest_status_v1.json'
HOST = dict(reconciler='active', last_tick_at=NOW - 1, last_error=None)
ON_ROUTINES = {'ad_gems': routine(True), 'floating_gems': routine(False)}
EVIDENCE = dict(generation='g1', started_at=NOW - 300, coverage='sampled_driver_observations',
                ad_claim_confirmations=2, orbit_balance_confirmations=0, orbit_recorded_increase=0,
                latest_confirmations={'ad_gems': dict(id='claim-2', observed_at=NOW - 40)}, observed_through_at=NOW - 1)


# ----------------------------------------------------------- status samples
def sample_unset(h):
    return h.status(host=HOST)


def found_at_start(h, rows=()):
    """The host of a dashboard that has just started: what it found of a collector from before it (change 2b)."""
    report = harvest.collector_at_start(h.root, h.cfg, processes=list(rows), resolve_source=h.resolve_source, now=NOW)
    return dict(HOST, collector_at_start=report)


def sample_off(h):
    choose(h)
    choose(h, enabled=False)
    return h.status(host=HOST)


def sample_reconciler_inactive(h):
    choose(h)
    row = dict(pid=4711, runner='gem_collector', cmdline='gem_collector.py', created=NOW - 600)
    host = dict(found_at_start(h, [row]), reconciler='inactive', last_tick_at=None)   # just started: no tick yet, and a collector it cannot take over
    return h.status(host=host)


def sample_reconciler_failing(h):
    choose(h)
    return h.status(host=dict(reconciler='active', last_tick_at=NOW - 1,
                              last_error=dict(type='OSError', message='adb server vanished', at=NOW - 3)))


def sample_starting_due(h):
    choose(h)
    return h.status(host=HOST)


def sample_starting_collector(h):
    choose(h)
    h.tick()
    return h.status(host=HOST)


def counted_routines(missed):
    return {'ad_gems': routine(True, counters=dict(checks=9, seen=7, skips=0, attempts=8, confirmed=2, windows_missed=missed)),
            'floating_gems': routine(False)}


def sample_collecting(h):
    """Counts and the cause of the latest miss; one missed window is not a warning."""
    choose(h)
    _, battle = _battle(h, _collector(h), routines=counted_routines(1), evidence=EVIDENCE)
    ad_window(h, battle, 'succeeded', 'offer_disappeared', at=NOW - 200)
    ad_window(h, battle, 'no_op', 'no_offer_seen', at=NOW - 100, attempts=0)
    return h.status(host=HOST)


def sample_working_collector_claims_failing(h):
    """Four ad windows in a row sent a tap and confirmed nothing: collecting is a stated warning, not 'Collecting gems'."""
    choose(h)
    _, battle = _battle(h, _collector(h), routines=counted_routines(4), evidence=EVIDENCE)
    for index in range(4):
        ad_window(h, battle, 'failed', 'offer_moved', at=NOW - 400 + 90 * index)
    return h.status(host=HOST)


def sample_working_collector_finder_suspected(h):
    """The orbit finder searched frames, recognized none of the recent ones and kept seeing gem-shaped near misses: the status
    says so beside the evidence it comes from; the state is still plain collecting (the Dashboard line is a later step)."""
    choose(h)
    routines = dict(counted_routines(0), floating_gems=routine(True))
    sensing = dict(floating_gems=dict(frames=240, recognized=0, near_miss_frames=9, reasons=dict(glyph_rejected=9, no_candidate=231),
                                      best_near_miss=0.71, recent=dict(window=120, frames=120, recognized=0, near_miss_frames=9)))
    _battle(h, _collector(h), routines=routines, evidence=dict(EVIDENCE, sensing=sensing))
    return h.status(host=HOST)


def sample_waiting_for_battle(h):
    choose(h)
    _collector(h)
    return h.status(host=found_at_start(h))        # the collector the dashboard found at its start, and kept


def sample_live_paused(h):
    choose(h)
    _battle(h, _collector(h), routines={'ad_gems': routine(False), 'floating_gems': routine(False)})
    return h.status(host=HOST)


def sample_stale_collector(h):
    choose(h)
    _battle(h, _collector(h), routines=ON_ROUTINES, published=NOW - 120)
    return h.status(host=HOST)


def sample_worker_not_running(h):
    choose(h)
    _battle(h, _collector(h), routines=ON_ROUTINES)
    h.alive['value'] = False
    return h.status(host=found_at_start(h))        # found at start with its worker gone: reported, not adopted


def sample_worker_unverified(h):
    choose(h)
    _battle(h, _collector(h), routines=ON_ROUTINES)
    h.alive['value'] = None   # the process cannot be inspected from here: not dead, not alive
    return h.status(host=HOST)


def sample_stopping(h, psutil_fake=None):
    choose(h)
    op = _collector(h)
    operations.ledger(h.root).transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
    return h.status(host=HOST)


def sample_manual_collector(h):
    _battle(h, _collector(h, harvest_owned=False), routines=ON_ROUTINES, evidence=EVIDENCE)
    return h.status(host=HOST)


def sample_owned_by_run_gathers(h):
    """A full driver owns the emulator: its confirmations and its latest miss are published too."""
    choose(h)
    _, battle = _battle(h, _run(h), controls=FULL, routines=counted_routines(2), gather={'ad_gems': True}, evidence=EVIDENCE)
    ad_window(h, battle, 'failed', 'timeout', at=NOW - 60)
    return h.status(host=HOST)


def sample_owned_by_run_not_gathering(h):
    choose(h)
    _battle(h, _run(h), controls=FULL, routines=OFF_ROUTINES, gather={'ad_gems': False})
    return h.status(host=HOST)


def sample_owned_by_run_not_reported(h):
    choose(h)
    _run(h)
    return h.status(host=HOST)


def sample_owned_by_run_stale(h):
    choose(h)
    _battle(h, _run(h), controls=FULL, routines=ON_ROUTINES, published=NOW - 600)
    return h.status(host=HOST)


def sample_foreign_process(h):
    choose(h)
    h.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    return h.status(host=HOST)


def sample_overlay_waiting(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    _battle(h, _run(h), controls=FULL, routines=OFF_ROUTINES, gather={'ad_gems': False})
    return h.status(host=HOST)


def sample_overlay_requested(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    _battle(h, _run(h), controls=FULL, routines=OFF_ROUTINES, gather={'ad_gems': False})
    h.tick()
    return h.status(host=HOST)


def sample_overlay_applied(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    store, battle = _battle(h, _run(h), controls=FULL, routines=OFF_ROUTINES, gather={'ad_gems': False})
    h.tick()
    [command] = store.commands(battle['id'])
    store.acknowledge(battle['id'], 'g1', command['id'], applied_paths=['routines.ad_gems.enabled'], state='applied')
    return h.status(host=HOST)


def sample_overlay_off_pending(h):
    choose(h)
    store, battle = _battle(h, _run(h), controls=FULL, routines=ON_ROUTINES, gather={'ad_gems': True})
    choose(h, enabled=False)
    h.tick()
    return h.status(host=HOST)


def sample_overlay_live_choice(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, routines=ON_ROUTINES, gather={'ad_gems': False})
    person = store.request_controls(battle['id'], run['id'], 'g1', 0, {'routines': {'ad_gems': {'enabled': False}}}, 'person-live-1')
    store.acknowledge(battle['id'], 'g1', person['id'], applied_paths=['routines.ad_gems.enabled'], state='applied')
    return h.status(host=HOST)


def sample_overlay_unsupported(h):
    choose(h)
    _battle(h, _run(h), controls=[], routines=OFF_ROUTINES, gather={'ad_gems': False})
    return h.status(host=HOST)


def sample_policy_invalid(h):
    choose(h)
    h.source['valid'] = False
    return h.status(host=HOST)


def sample_source_changed(h):
    choose(h)
    h.source['edit'] = 1
    return h.status(host=HOST)


def sample_taps_disabled(h):
    choose(h)
    cfg = copy.deepcopy(h.cfg)
    cfg['instances']['main']['allow_taps'] = False
    return h.status(cfg_=cfg, host=HOST)


def sample_input_revoked(h):
    from runtime import device_lease
    choose(h)
    ident = identity(h)
    device_lease.revoke_input(ident['serial'], ident['adb_host'], ident['adb_port'])
    return h.status(host=HOST)


def sample_context_changed(h):
    choose(h)
    cfg = copy.deepcopy(h.cfg)
    cfg['active_profile'] = 'another'
    return h.status(cfg_=cfg, host=HOST)


def sample_setup_guard_block(h):
    choose(h)
    sealed_unresolved(h)
    return h.status(host=HOST)


def sample_emulator_offline(h):
    choose(h)
    h.adb['state'] = 'offline'
    h.tick()
    return h.status(host=HOST)


def sample_run_settings_refused(h):
    choose(h)
    h.refusal = ('digits_missing', 'Learn the wave digits before collecting gems.')
    h.tick()
    return h.status(host=HOST)


def sample_backing_off(h):
    choose(h)
    h.tick()
    finish_last(h, 'failed', ran=5, now=NOW)
    return h.status(NOW + 1, host=HOST)


def sample_circuit_open(h):
    choose(h)
    now = NOW
    for attempt in range(harvest.CIRCUIT):
        h.tick(now)
        finish_last(h, 'failed', ran=5, now=now)
        now += harvest.backoff(attempt + 1)
    return h.status(now, host=HOST)


def sample_transferring(h):
    choose(h)
    harvest.begin_transfer(h.root, identity(h), 'run_start', now=NOW)
    return h.status(host=HOST)


def sample_state_unreadable(h):
    choose(h)
    with operations.ledger(h.root)._db() as db:
        db.execute('UPDATE harvest_choices SET body=?', ('not json',))
    return h.status(host=HOST)


SAMPLES = [value for name, value in sorted(globals().items()) if name.startswith('sample_')]


# --------------------------------------------------------- HTTP scenarios
class Scenarios:
    """Each scenario gets a fresh root, ledger, lease directory, clock and process table."""

    def __init__(self, dash, launches, monkeypatch, tmp_path):
        self.dash, self.launches, self.monkeypatch, self.tmp_path = dash, launches, monkeypatch, tmp_path

    def app(self, name):
        from conftest import seed_root
        from runtime import device_lease
        root = self.tmp_path / 'http' / name
        seed_root(root)
        self.monkeypatch.setattr(self.dash, 'ROOT', str(root))
        self.monkeypatch.setattr(self.dash, 'CONFIG_PATH', str(root / 'config.yaml'))
        self.monkeypatch.setattr(device_lease, '_ROOT', root / 'leases')
        self.dash._HARVEST_SOURCES.clear()
        self.dash._HARVEST_HOST.update(last_tick_at=None, last_error=None)
        self.launches.clear()
        app = make_app(self.dash, self.launches, self.monkeypatch)
        self.monkeypatch.setattr(device_lease, 'time', app.clock)
        return app


def answer(response):
    return dict(status=response.status_code, body=response.get_json())


def trimmed(op):
    return {key: op[key] for key in ('id', 'kind', 'status', 'revision') if key in op}


def post(app, url, payload=None):
    return answer(app.client.post(url, json=payload if payload is not None else {}))


def key(n):
    return f'contract-{n:04d}'


def post_harvest_samples(make):
    out = {}
    app = make('enable')
    out['enable_ok'] = answer(app.client.post('/api/harvest', json=body(app, key=key(1))))
    out['enable_replayed'] = answer(app.client.post('/api/harvest', json=body(app, key=key(1), expected=0)))
    app = make('off_stopped')
    enable(app, key=key(2))
    running_collector(app)
    out['off_ok_stopped'] = answer(app.client.post('/api/harvest', json=body(app, enabled=False, key=key(3))))
    out['off_ok_replayed'] = answer(app.client.post('/api/harvest', json=body(app, enabled=False, key=key(3))))
    app = make('off_stopping')
    enable(app, key=key(4))
    running_collector(app)
    app.psutil.lingering = True
    out['off_ok_stopping'] = answer(app.client.post('/api/harvest', json=body(app, enabled=False, key=key(5))))
    app = make('refused')
    enable(app, key=key(6))
    bad = body(app, key=key(7), expected=0)
    out['harvest_revision_changed'] = answer(app.client.post('/api/harvest', json=bad))
    out['context_changed'] = answer(app.client.post('/api/harvest', json=dict(
        body(app, key=key(8)), expected_context=dict(body(app)['expected_context'], serial='127.0.0.1:9999'))))
    out['idempotency_key_reused'] = answer(app.client.post('/api/harvest', json=dict(body(app, key=key(6)), preset='bp_other')))
    out['invalid_request'] = answer(app.client.post('/api/harvest', json={'enabled': True}))
    import yaml
    path = Path(app.dash.ROOT) / 'profiles' / 'mine.yaml'
    profile = yaml.safe_load(path.read_text(encoding='utf-8'))
    profile['policies']['gather']['shared']['ad_gems'] = False
    path.write_text(yaml.safe_dump(profile), encoding='utf-8')
    app.dash._HARVEST_SOURCES.clear()
    out['policy_invalid'] = answer(app.client.post('/api/harvest', json=body(app, key=key(9), expected=1)))
    app = make('locked')
    assert app.dash._ACTION_LOCK.acquire(blocking=False)
    try:
        out['action_lock'] = answer(app.client.post('/api/harvest', json=body(app, key=key(10))))
        out['off_during_action_lock'] = answer(app.client.post('/api/harvest', json=body(app, enabled=False, key=key(11))))
    finally:
        app.dash._ACTION_LOCK.release()
    app = make('unknown')
    real = harvest.apply_choice

    def commit_then_fail(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError('connection reset after the commit')
    with app.monkeypatch.context() as patched:
        patched.setattr(harvest, 'apply_choice', commit_then_fail)
        out['outcome_unknown'] = answer(app.client.post('/api/harvest', json=body(app, key=key(12))))
    return out


def refusal_samples(make):
    out = {}
    process = {'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'}
    app = make('owner_run')
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    run, _ = store.accept('run', context, {}, key(20))
    store.transition(run['id'], run['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})
    app.rows.append({'pid': os.getpid(), 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py', 'created': 1.0})
    out['device_owned_run'] = post(app, '/api/accounts', dict(id='someone'))
    app = make('owner_process')
    app.rows.append(process)
    out['device_owned_process'] = post(app, '/api/wizard/reconnect')
    app = make('owner_collector')
    enable(app, key=key(21))
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    out['device_owned_collector'] = post(app, '/api/accounts', dict(id='someone'))
    out['collector_running'] = post(app, '/api/control', dict(action='start_gems', preset='bp_farm', idempotency_key=key(22)))
    app = make('owner_manual_collector')
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {'preset': 'bp_farm'}, None)
    manual, _ = store.accept('gems', context, {'preset': 'bp_farm'}, key(23))
    store.transition(manual['id'], manual['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})
    app.rows.append(collector_row(manual))
    out['collector_running_manual'] = post(app, '/api/control', dict(action='start_gems', preset='bp_farm', idempotency_key=key(24)))
    app = make('owner_pre_spawn')
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    store.accept('run', context, {}, key(29))   # accepted, its worker not spawned yet: no process row exists
    out['device_owned_pre_spawn'] = post(app, '/api/accounts', dict(id='someone'))
    app = make('owner_lease')
    from runtime import device_lease
    ident = harvest.identity_for(app.dash.load_config())
    lease = device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port'])   # held, nothing in the ledger or process table
    try:
        out['device_owned_lease'] = post(app, '/api/accounts', dict(id='someone'))
    finally:
        lease.release()
    app = make('owner_unreadable')
    with app.monkeypatch.context() as patched:
        patched.setattr(operations.Operations, 'active_readonly',
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked'))))
        out['device_owner_unreadable'] = post(app, '/api/accounts', dict(id='someone'))
    app = make('transfer_lagging')
    enable(app, key=key(25))
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    app.psutil.lingering = True
    out['collector_stopping'] = post(app, '/api/control', dict(action='start', preset='bp_farm', idempotency_key=key(26)))
    app = make('setup_guard')
    sealed_unresolved(make_env_for(app))
    out['setup_battle_unresolved'] = post(app, '/api/control', dict(action='start', preset='bp_farm', idempotency_key=key(27)))
    app = make('reserved')
    out['reserved_fields'] = post(app, '/api/control', dict(action='start_gems', preset='bp_farm', harvest=True,
                                                          idempotency_key=key(28)))
    app = make('locked_other')
    assert app.dash._ACTION_LOCK.acquire(blocking=False)
    try:
        out['action_lock'] = post(app, '/api/control', dict(action='clear_flag'))
    finally:
        app.dash._ACTION_LOCK.release()
    return out


def make_env_for(app):
    """Just enough of the lifecycle fixture's shape for the sealed-record builder."""
    from types import SimpleNamespace
    return SimpleNamespace(root=app.dash.ROOT, cfg=app.dash.load_config())


def automation_samples(make):
    out = {}
    app = make('taps_idle')
    out['disable_nothing_running'] = post(app, '/api/automation-access', dict(enabled=False))
    out['enable_ok'] = post(app, '/api/automation-access', dict(enabled=True))
    app = make('taps_collector')
    enable(app, key=key(30))
    running_collector(app)
    out['disable_input_stopped'] = post(app, '/api/automation-access', dict(enabled=False))
    app = make('taps_lingering')
    enable(app, key=key(31))
    running_collector(app)
    app.psutil.lingering = True
    out['disable_stopping'] = post(app, '/api/automation-access', dict(enabled=False))
    app = make('taps_driver')
    store = operations.ledger(app.dash.ROOT)
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    run, _ = store.accept('run', context, {}, key(32))
    store.transition(run['id'], run['revision'], 'running', worker={'pid': 1, 'creation_time': 1.0})
    out['disable_not_enforced'] = post(app, '/api/automation-access', dict(enabled=False))
    from runtime import device_lease
    with app.monkeypatch.context() as patched:
        patched.setattr(device_lease, 'lease_state',
                        lambda *a, **k: dict(owned=True, delegated=False, input_gate='enforced'))
        out['disable_driver_input_refused'] = post(app, '/api/automation-access', dict(enabled=False))
    app.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    out['enable_refused_owner'] = post(app, '/api/automation-access', dict(enabled=True))
    app = make('taps_marker_failed')
    enable(app, key=key(33))
    running_collector(app)
    with app.monkeypatch.context() as patched:
        patched.setattr(device_lease, 'revoke_input',
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
        out['disable_marker_write_failed'] = post(app, '/api/automation-access', dict(enabled=False))
    app = make('taps_lease_scan_failed')
    ident = harvest.identity_for(app.dash.load_config())
    lease = device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port'])
    try:
        with app.monkeypatch.context() as patched:
            patched.setattr(device_lease, 'lease_state',
                            lambda *a, **k: (_ for _ in ()).throw(OSError('probe cannot inspect lease')))
            out['disable_lease_scan_failed'] = post(app, '/api/automation-access', dict(enabled=False))
    finally:
        lease.release()
    app = make('taps_locked')
    assert app.dash._ACTION_LOCK.acquire(blocking=False)
    try:
        out['disable_during_action_lock'] = post(app, '/api/automation-access', dict(enabled=False))
        out['enable_during_action_lock'] = post(app, '/api/automation-access', dict(enabled=True))
    finally:
        app.dash._ACTION_LOCK.release()
    return out


def transfer_samples(make):
    out = {}
    app = make('transfer_ok')
    enable(app, key=key(40))
    collector = running_collector(app)
    app.rows.append(collector_row(collector))
    answered = post(app, '/api/control', dict(action='start', preset='bp_farm', idempotency_key=key(41)))
    answered['body']['operation'] = trimmed(answered['body']['operation'])
    out['run_takes_over_collector'] = answered
    return out


def stop_trim(reply):
    """A stop reply with its operation reduced to what the person is told (the full body carries machine paths)."""
    reply = copy.deepcopy(reply)
    operation = reply['body'].get('operation')
    if operation is not None:
        reply['body']['operation'] = {key: operation[key] for key in ('id', 'kind', 'status', 'revision', 'step', 'error')
                                      if key in operation}
    return reply


def stop_operation_samples(make):
    """How a person's Stop (POST /api/operations/<id>/cancel, /api/scan/stop, /api/calibrate/stop) and the runner
    kill (POST /api/control action=kill) answer, every sample from the real routes (the two races are driven
    deterministically between the steps of the real route). Stop answers `stop`: {state, reason, holder};
    kill answers {ok, gone} or a coded refusal. Nothing is signalled to a real process: a FakeKernel stands
    in for the Windows kernel."""
    from runtime import device_lease, process_handle
    out = dict(cancel={}, conflicts={}, repeat={}, kill={}, processes=None)

    def worker_of(app, n, inspect=None, pid=4242, created=1.0):
        """A standalone diamond collector a person started: running, with a recorded worker."""
        if inspect is not None:
            app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(inspect))
        store = operations.ledger(app.dash.ROOT)
        context = operations.context(app.dash.ROOT, app.dash.load_config(), {'preset': 'bp_farm'}, None)
        op, _ = store.accept('gems', context, {'preset': 'bp_farm'}, key(n))
        return store.transition(op['id'], op['revision'], 'running', worker={'pid': pid, 'creation_time': created})

    def stop(app, op):
        return stop_trim(post(app, f"/api/operations/{op['id']}/cancel", dict(expected_revision=op['revision'])))

    def hold(app):
        ident = harvest.identity_for(app.dash.load_config())
        return device_lease.acquire(ident['serial'], ident['adb_host'], ident['adb_port'])

    def survivor_app(name, n):
        """A worker that is stopped (or gone) while something outside its verified family holds the lease. The real
        liveness check runs over the fake process table, so the worker is gone once it has been terminated."""
        app = make(name)
        app.platform = fake_platform(app.monkeypatch)
        app.monkeypatch.setattr(operations, 'inspect_worker', real_inspect_worker)
        app.platform.start(910001, 1_790_000_000.123456, ppid=100)
        app.platform.start(910003, 1_790_000_002.123456, ppid=9999)   # a descendant whose parent is gone: the walk cannot reach it
        return app, worker_of(app, n, pid=910001, created=1_790_000_000.123456), hold(app)

    cancel = out['cancel']
    app = make('stop_stopped')
    cancel['stopped'] = stop(app, worker_of(app, 50))
    assert app.psutil.terminated == [4242]
    app = make('stop_stopped_family')
    app.psutil.os.start(4242, 1.0, ppid=100)
    app.psutil.os.start(4243, 2.0, ppid=4242)            # the delegated driver child ...
    lease = hold(app)                                    # ... which holds the device lease, and loses it when it is terminated
    app.psutil.os.watch(lambda event, pid: lease.release() if event == 'terminate' and pid == 4243 else None)
    cancel['stopped_family'] = stop(app, worker_of(app, 60))
    assert app.psutil.terminated == [4243, 4242]
    app, op, lease = survivor_app('stop_held_after_stop', 61)
    try:
        cancel['unverified_lease_held_after_stop'] = stop(app, op)
        assert [killed for killed, _ in app.platform.kills] == [910001]
    finally:
        lease.release()
    app, op, lease = survivor_app('stop_unreadable_after_stop', 62)
    lease.release()
    with app.monkeypatch.context() as patched:
        patched.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(OSError('cannot probe')))
        cancel['unverified_lease_unreadable_after_stop'] = stop(app, op)
    app = make('stop_stopping')
    app.psutil.lingering = True
    cancel['stopping'] = stop(app, worker_of(app, 51))
    app = make('stop_released')
    cancel['released'] = stop(app, worker_of(app, 52, inspect=lambda worker: None))
    assert app.psutil.terminated == []                 # released never signals a process
    app = make('stop_lease_held')
    op = worker_of(app, 53, inspect=lambda worker: None)
    lease = hold(app)
    try:
        cancel['unverified_lease_held'] = stop(app, op)
    finally:
        lease.release()
    app = make('stop_lease_unreadable')
    op = worker_of(app, 54, inspect=lambda worker: None)
    with app.monkeypatch.context() as patched:
        patched.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(OSError('cannot probe')))
        cancel['unverified_lease_unreadable'] = stop(app, op)
    app = make('stop_lease_free')
    op = worker_of(app, 55, inspect=lambda worker: None)
    lease = hold(app)
    real_release = operations.release_unverified_worker

    def release_then_the_holder_lets_go(root, operation_id, **kwargs):
        settled = real_release(root, operation_id, **kwargs)       # the lease is still held here, so it is not released ...
        lease.release()                                  # ... and the holder lets go before the reply reads the lease
        return settled
    with app.monkeypatch.context() as patched:
        patched.setattr(operations, 'release_unverified_worker', release_then_the_holder_lets_go)
        cancel['unverified_lease_free'] = stop(app, op)
    app = make('stop_ended')
    op = worker_of(app, 56)
    real_stop = operations.stop_driver

    def another_writer_settles_first(root, operation_id, **kwargs):
        current = operations.ledger(root).get(operation_id)    # the refresher saw the worker die while the stop was running
        operations.ledger(root).transition(operation_id, current['revision'], 'interrupted', error={
            'code': 'worker_lost', 'message': 'Worker exited without a confirmed outcome'})
        return real_stop(root, operation_id, **kwargs)
    with app.monkeypatch.context() as patched:
        patched.setattr(operations, 'stop_driver', another_writer_settles_first)
        cancel['ended'] = stop(app, op)

    app = make('stop_ended_after_termination')
    op = worker_of(app, 64)
    real_terminate = process_handle.terminate_tree

    def terminate_then_another_writer_settles(pid, created, **kwargs):
        stopped = real_terminate(pid, created, **kwargs)                          # THIS request terminated the worker ...
        current = operations.ledger(app.dash.ROOT).get(op['id'])                  # ... and the refresher retired the operation before the final write
        operations.ledger(app.dash.ROOT).transition(op['id'], current['revision'], 'interrupted', error={
            'code': 'worker_lost', 'message': 'Worker exited without a confirmed outcome'})
        return stopped
    with app.monkeypatch.context() as patched:
        patched.setattr(process_handle, 'terminate_tree', terminate_then_another_writer_settles)
        cancel['ended_after_termination'] = stop(app, op)
    assert app.psutil.terminated == [4242]

    conflicts = out['conflicts']
    app = make('stop_conflicts')
    op = worker_of(app, 70)
    conflicts['operation_changed'] = stop(app, dict(op, revision=op['revision'] - 1))
    conflicts['operation_unknown'] = stop_trim(post(app, '/api/operations/00000000-0000-4000-8000-0000000000aa/cancel',
                                                    dict(expected_revision=1)))
    conflicts['operation_required'] = stop_trim(post(app, '/api/scan/stop', {}))
    assert app.psutil.terminated == []                                            # a refusal that did nothing signals nothing
    app = make('stop_not_cancellable')
    context = operations.context(app.dash.ROOT, app.dash.load_config(), {}, None)
    persistence, _ = operations.ledger(app.dash.ROOT).accept('profile_write', context, {}, key(71))
    conflicts['not_cancellable'] = stop(app, persistence)
    assert app.psutil.terminated == []
    app = make('stop_partial')
    enable(app, key=key(72))
    op = worker_of(app, 73)
    real_pause = harvest.pause

    def pause_then_the_operation_moves(*args, **kwargs):
        paused = real_pause(*args, **kwargs)                                      # harvesting is paused for good ...
        current = operations.ledger(app.dash.ROOT).get(op['id'])                  # ... and another writer moves the operation before the stop is recorded
        operations.ledger(app.dash.ROOT).transition(op['id'], current['revision'], 'running', progress={'moved': True})
        return paused
    with app.monkeypatch.context() as patched:
        patched.setattr(harvest, 'pause', pause_then_the_operation_moves)
        conflicts['stop_outcome_partial'] = stop(app, op)
    assert app.psutil.terminated == []

    app = make('stop_repeat_stopping')
    app.psutil.lingering = True
    op = worker_of(app, 58)
    first = stop(app, op)
    app.psutil.lingering = False
    out['repeat']['stopping_then_stopped'] = dict(first=first, second=stop(app, first['body']['operation']))
    app = make('stop_repeat_unverified')
    op = worker_of(app, 59, inspect=lambda worker: None)
    lease = hold(app)
    try:
        first = stop(app, op)
    finally:
        lease.release()
    out['repeat']['unverified_then_released'] = dict(first=first, second=stop(app, first['body']['operation']))
    app, op, lease = survivor_app('stop_repeat_survivor', 63)
    try:
        first = stop(app, op)
        again = stop(app, first['body']['operation'])          # still held: nothing new to signal
    finally:
        lease.release()
    settled = operations.resolve(app.dash.ROOT, op['id'])      # the lease is free: the next read-write path settles it
    out['repeat']['held_after_stop_then_the_survivor_leaves'] = dict(
        first=first, still_held=again, after_release=dict(status=settled['status'], error=settled['error'],
                                                           stop=operations.stop_state(settled)),
        stale_stop=stop(app, first['body']['operation']))

    pid, created = 910001, 1_790_000_000.123456
    row = dict(pid=pid, runner='orchestrator', cmdline='python orchestrator.py', created=created)

    def runner(name, *, alive=created, listed=created):
        app = make(name)
        app.platform = fake_platform(app.monkeypatch)
        if alive is not None:
            app.platform.start(pid, alive)
        app.rows[:] = [] if listed is None else [dict(row, created=listed)]
        return app

    def kill(app, **fields):
        return post(app, '/api/control', dict(action='kill', **fields))

    kills = out['kill']
    app = runner('kill_ok')
    kills['stopped'] = kill(app, pid=pid, created=created)
    assert [killed for killed, _ in app.platform.kills] == [pid]
    app = runner('kill_gone', alive=None, listed=None)
    kills['gone'] = kill(app, pid=pid, created=created)
    app = runner('kill_other_runner', alive=created + 100, listed=created + 100)
    kills['pid_passed_to_another_runner'] = kill(app, pid=pid, created=created)
    app = runner('kill_without_created')
    kills['created_missing'] = kill(app, pid=pid)
    app = runner('kill_not_a_runner', listed=None)
    kills['not_a_runner'] = kill(app, pid=pid, created=created)
    app = runner('kill_invalid')
    kills['invalid_request'] = kill(app, created=created)
    assert app.platform.kills == []
    app = runner('kill_listing')
    app.rows[:] = [dict(row), dict(row, pid=pid + 1, created=None)]
    out['processes'] = operations.project_processes(app.dash.ROOT, app.rows, reconcile=False)
    return out


def status_failed_sample(make):
    app = make('status_failed')
    stub_status(app.dash, app.monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError('boom')
    with app.monkeypatch.context() as patched:
        patched.setattr(harvest, 'status', broken)
        return app.dash._harvest_status(app.dash.load_config(), [])


# ------------------------------------------------------------ normalization
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
DIGEST = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')


def alias_ids(value):
    """Replace every uuid (and its 8-character display prefix) by an alias numbered
    in order of first appearance, so regeneration is stable."""
    text = json.dumps(value, sort_keys=False)
    aliases = {}
    for found in UUID.findall(text):
        aliases.setdefault(found, f'{len(aliases) + 1:08x}-0000-4000-8000-000000000000')
    for found, alias in aliases.items():
        text = text.replace(found, alias)
    for found, alias in aliases.items():
        text = re.sub(r'(?<![0-9a-f])' + found[:8] + r'(?![0-9a-f])', alias[:8], text)
    digests = {}
    for found in DIGEST.findall(text):   # sha256 of sealed records that embed a random operation id
        digests.setdefault(found, f'{len(digests) + 1:064x}')
    return json.loads(DIGEST.sub(lambda match: digests[match.group(0)], text))


def pin_processes(value):
    if isinstance(value, dict):
        return {k: (4242 if k == 'pid' and v == os.getpid() else pin_processes(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [pin_processes(v) for v in value]
    return value


def build(tmp_path, monkeypatch, dash, launches):
    import secrets

    from runtime import device_lease
    monkeypatch.setattr(secrets, 'token_hex', lambda n=32: 'ab' * n)
    status = {}
    for sample in SAMPLES:
        name = sample.__name__[len('sample_'):]
        env = make_env(tmp_path / 'status' / name, monkeypatch)
        monkeypatch.setattr(device_lease, '_ROOT', tmp_path / 'status' / name / 'leases')
        monkeypatch.setattr(device_lease, 'time', env.clock)
        status[name] = sample(env)
    scenarios = Scenarios(dash, launches, monkeypatch, tmp_path)
    status['status_failed'] = status_failed_sample(scenarios.app)
    waits = dict(now=0.0)

    def sleep(seconds):
        waits['now'] += seconds
    real_wait = harvest.wait_for_release
    monkeypatch.setattr(harvest, 'wait_for_release',
                        lambda *a, **k: real_wait(*a, sleep=sleep, clock=lambda: waits['now'], **k))
    document = dict(
        version=1,
        generated_by='backend/tests/test_harvest_contract.py',
        notes=dict(
            status='GET /api/status.harvest, one sample per state or condition. server_time is the fixed test clock.',
            post_harvest='POST /api/harvest answers as {status, body}. Off is never refused by the global action lock.',
            refusals='The shared 409 body of every route that needs an idle emulator ({status, body}).',
            automation_access='POST /api/automation-access answers; revocation.state tells input_stopped from stopping.',
            start_transfer='POST /api/control start while only the standalone collector runs; operation is trimmed here.',
            stop_operation=('A person\'s Stop and the runner kill, every sample from the real route. cancel: POST /api/operations/<id>/cancel '
                            '(also /api/scan/stop and /api/calibrate/stop) answers {ok, operation, stop:{state, reason, holder, by_this_request}}. '
                            'state: stopped (the verified family exited AND the device lease is free) | released (a person released an '
                            'unverifiable worker; nothing was signalled) | stopping (signalled, a member has not exited) | unverified '
                            '(the stop cannot be confirmed; the operation stays the owner) | ended (it ended on its own or another writer '
                            'settled it) | active (accepted/running/applying: defensive, the routes cannot return it after a cancel). '
                            'reason, for unverified: worker_not_verifiable_lease_free|_lease_held|_lease_unreadable (the worker\'s '
                            'identity cannot be proven) and lease_held_after_stop|lease_unreadable_after_stop (the worker is gone or was '
                            'stopped but something outside the verified family still holds the lease, or the lease cannot be read). '
                            'holder: the device_owned lease owner body while the reason names a held lease; owner pid and script stay null '
                            '(the lease is not a process row) and lease.holders lists the live processes its lock files name. '
                            'unverified_lease_free, ended and ended_after_termination are real-route races driven deterministically between '
                            'the route\'s steps (the lease holder lets go after the release check; another writer settles the operation '
                            'first, or after this request terminated the worker). by_this_request is what THIS request did, always present: '
                            '{paused: harvesting was turned off by this call (a replay of another request\'s pause is false), requested: the '
                            'stop was recorded on the operation, signalled: the pids TerminateProcess was called on, family: how the '
                            'verified family ended (stopped|stopping|unverified|gone|null), released: an unverifiable worker was released}. '
                            'state ended with signalled non-empty means this request stopped the worker and another writer settled the '
                            'operation in the same moment: it was stopped, not found already ended. A Harvest Off never releases, only a '
                            'person\'s Stop does. conflicts: a Stop that is answered 409 {ok:false, code, error, operation}. Before anything '
                            'was done the code is operation_changed (stale revision or an operation that already ended; operation is the '
                            'current one) | operation_unknown | operation_required (no operation_id / expected_revision) | not_cancellable; '
                            'nothing was changed. Once anything was done (harvesting paused, the stop recorded, a process signalled, a worker '
                            'released) a conflict is stop_outcome_partial: {conflict, error, operation (current), stop (how it stands now, '
                            'with by_this_request)}, never a plain refusal; the client reads the operation again before stopping again. '
                            'These routes are never refused with action_lock. repeat: a second Stop (repeat.*.after_release is the operation '
                            'as the next read-write path settles it plus operations.stop_state(), not a route reply). kill: POST /api/control action=kill '
                            'with {pid, created} as the list drew them; processes is the /api/status.processes projection, created is '
                            'null when the scan could not pin the row.')),
        status=status,
        post_harvest=post_harvest_samples(scenarios.app),
        refusals=refusal_samples(scenarios.app),
        automation_access=automation_samples(scenarios.app),
        start_transfer=transfer_samples(scenarios.app),
        stop_operation=stop_operation_samples(scenarios.app))
    return alias_ids(pin_processes(document))


def test_the_published_fixture_matches_what_the_code_produces(tmp_path, monkeypatch, dash, launches, request):
    generated = build(tmp_path, monkeypatch, dash, launches)
    text = render_fixture_json(generated)
    assert str(tmp_path) not in text and str(Path(dash.__file__).parents[1]) not in text, 'a machine path leaked into the contract'
    assert 'NaN' not in text and 'Infinity' not in text
    if request.config.getoption('--regenerate-harvest-fixture'):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes(text.encode('utf-8'))
        pytest.skip('fixture regenerated: review the diff and tell the UI author')
    assert FIXTURE.exists(), 'run with --regenerate-harvest-fixture to publish it'
    assert json.loads(FIXTURE.read_text(encoding='utf-8')) == generated


def test_the_fixture_covers_every_published_state_and_every_documented_code():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    states = {sample['state'] for sample in fixture['status'].values()}
    assert states == {'off', 'starting', 'collecting', 'waiting_for_battle', 'live_paused', 'owned_by_driver',
                      'transferring', 'stopping', 'blocked', 'backing_off', 'unavailable'}
    assert all(sample['version'] == 1 and 'server_time' in sample for sample in fixture['status'].values())
    codes = {reply['body'].get('code') for reply in fixture['post_harvest'].values()}
    assert {'harvest_revision_changed', 'context_changed', 'policy_invalid', 'idempotency_key_reused',
            'invalid_request', 'action_lock', 'harvest_outcome_unknown'} <= codes
    owner_codes = {reply['body'].get('code') for reply in fixture['refusals'].values()}
    assert {'device_owned', 'collector_running', 'collector_stopping', 'setup_battle_unresolved',
            'device_owner_unreadable'} <= owner_codes
    assert fixture['refusals']['device_owned_pre_spawn']['body']['owner']['pid'] is None
    lease_owner = fixture['refusals']['device_owned_lease']['body']
    assert (lease_owner['code'], lease_owner['owner']['kind'], lease_owner['owner']['lease']['held']) == ('device_owned', 'process', True)
    assert lease_owner['recovery'] is None
    assert {sample['code'] for sample in fixture['status'].values()} >= {'worker_not_running', 'worker_unverified'}
    recoveries = {(reply['body'].get('recovery') or {}).get('action') for reply in fixture['refusals'].values()}
    assert {'pause_harvesting', 'stop_operation', None} <= recoveries
    revocations = {reply['body'].get('revocation', {}).get('state') for reply in fixture['automation_access'].values()}
    assert {'nothing_running', 'input_stopped', 'stopping', 'not_enforced', 'failed'} <= revocations
    failed = fixture['automation_access']['disable_marker_write_failed']
    assert failed['status'] == 500 and failed['body']['code'] == 'input_revocation_failed'
    assert (failed['body']['revocation']['fenced'], failed['body']['revocation']['latched']) == (False, False)
    unknown = fixture['automation_access']['disable_lease_scan_failed']['body']['revocation']
    assert (unknown['state'], unknown['fenced'], unknown['latched']) == ('stopping', False, False)
    assert [error['step'] for error in unknown['errors']] == ['lease_state']
    assert fixture['start_transfer']['run_takes_over_collector']['body']['transfer']['stopped']

    stop = fixture['stop_operation']
    cancel = {name: reply['body']['stop'] for name, reply in stop['cancel'].items()}
    assert {s['state'] for s in cancel.values()} == {'stopped', 'released', 'stopping', 'unverified', 'ended'}   # `active` is not producible by a route
    for name, state in cancel.items():
        assert set(state['by_this_request']) == {'paused', 'requested', 'signalled', 'family', 'released'}, name
    after = cancel['ended_after_termination']
    assert (after['state'], after['by_this_request']['signalled'], after['by_this_request']['family']) == ('ended', [4242], 'stopped')
    assert cancel['ended']['by_this_request']['signalled'] == [] and cancel['released']['by_this_request']['released'] is True
    assert {s['reason'] for s in cancel.values()} == {None, 'worker_not_verifiable_lease_free', 'worker_not_verifiable_lease_held',
                                                      'worker_not_verifiable_lease_unreadable', 'lease_held_after_stop',
                                                      'lease_unreadable_after_stop'}
    for name in ('unverified_lease_held', 'unverified_lease_held_after_stop'):
        holder = cancel[name]['holder']
        assert (holder['kind'], holder['lease']['held'], holder['recovery']) == ('process', True, None)
        assert holder['lease']['holders'] == [dict(pid=4242, role='primary')]            # the live process the lock file names (the test process, pinned to 4242)
    assert sorted(name for name, s in cancel.items() if s['holder'] is not None) == ['unverified_lease_held', 'unverified_lease_held_after_stop']
    bodies = {name: reply['body'] for name, reply in stop['cancel'].items()}
    assert bodies['stopped_family']['operation']['status'] == 'cancelled'
    assert bodies['unverified_lease_held_after_stop']['operation']['status'] == 'cancelling'   # a survivor keeps the operation the owner
    assert bodies['unverified_lease_unreadable_after_stop']['operation']['status'] == 'cancelling'
    assert stop['cancel']['released']['body']['operation']['error']['code'] == 'worker_released'
    again = stop['repeat']
    stopping, unverified = again['stopping_then_stopped'], again['unverified_then_released']
    assert (stopping['first']['body']['stop']['state'], stopping['second']['body']['stop']['state']) == ('stopping', 'stopped')
    assert (unverified['first']['body']['stop']['state'], unverified['second']['body']['stop']['state']) == ('unverified', 'released')
    survivor = again['held_after_stop_then_the_survivor_leaves']
    assert survivor['first']['body']['stop']['reason'] == survivor['still_held']['body']['stop']['reason'] == 'lease_held_after_stop'
    assert survivor['after_release'] == dict(status='interrupted', error=survivor['after_release']['error'], stop=dict(state='ended', reason=None))
    assert survivor['after_release']['error']['code'] == 'worker_lost' and survivor['stale_stop']['status'] == 409
    assert survivor['stale_stop']['body']['code'] == 'operation_changed'
    conflicts = {name: (reply['status'], reply['body']['code']) for name, reply in stop['conflicts'].items()}
    assert conflicts == dict(operation_changed=(409, 'operation_changed'), operation_unknown=(409, 'operation_unknown'),
                             operation_required=(409, 'operation_required'), not_cancellable=(409, 'not_cancellable'),
                             stop_outcome_partial=(409, 'stop_outcome_partial'))
    assert stop['conflicts']['operation_changed']['body']['operation']['status'] == 'running'
    assert stop['conflicts']['operation_unknown']['body']['operation'] is None and 'stop' not in stop['conflicts']['operation_changed']['body']
    partial = stop['conflicts']['stop_outcome_partial']['body']
    assert partial['stop']['by_this_request'] == dict(paused=True, requested=False, signalled=[], family=None, released=False)
    assert (partial['operation']['status'], partial['stop']['state']) == ('running', 'active') and partial['conflict'] and partial['error']
    kills = {name: (reply['status'], reply['body'].get('code')) for name, reply in stop['kill'].items()}
    assert kills == dict(stopped=(200, None), gone=(200, None), pid_passed_to_another_runner=(409, 'process_identity_unverified'),
                         created_missing=(409, 'process_identity_unverified'), not_a_runner=(400, 'not_a_runner'),
                         invalid_request=(400, 'invalid_request'))
    assert [type(row['created']) for row in stop['processes']] == [float, type(None)]
