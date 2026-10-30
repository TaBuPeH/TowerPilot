"""HTTP-level scaffolding for the harvesting tests: the real Flask app over a
temporary root, one fake clock, a controllable process table and the psutil/adb
boundaries faked. Shared by the route tests and the published-contract test."""
import os
from types import SimpleNamespace

from control import battles, harvest, operations
from harvest_support import NOW, Clock, fake_inspect, make_psutil
from towerpilot_workflows import battles as ledger_battles
from towerpilot_workflows import harvest as ledger_h
from towerpilot_workflows import operations as ledger_ops

KEY = iter(range(1, 100_000))


def make_app(dash, launches, monkeypatch):
    """The dashboard with one clock for the ledger modules and the host, a controllable
    process table, and the fake psutil/adb boundaries."""
    clock = Clock(NOW)
    for module in (ledger_ops, ledger_battles, ledger_h, harvest, battles, dash):
        monkeypatch.setattr(module, 'time', clock)
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: worker is not None))
    rows = []
    monkeypatch.setattr(dash, '_procs', lambda: list(rows))
    monkeypatch.setattr(dash, '_procs_cached', lambda: list(rows))
    monkeypatch.setattr(dash, '_device_state',
                        lambda cfg: {'device_state': 'device', 'port_open': True, 'serial': 'fake:1'})
    monkeypatch.setattr(dash, '_TRANSFER_WAIT_SEC', 0.3)
    return SimpleNamespace(dash=dash, client=dash.app.test_client(), rows=rows, clock=clock,
                           psutil=make_psutil(monkeypatch), spawned=launches, monkeypatch=monkeypatch)


def identity(app):
    return harvest.identity_for(app.dash.load_config())


def body(app, enabled=True, preset='bp_farm', *, key=None, expected=None):
    ident = identity(app)
    result = dict(enabled=enabled, idempotency_key=key or f'http-{next(KEY):06d}',
                  expected_context={name: ident[name] for name in
                                    (('instance', 'account', 'serial', 'profile') if enabled else ('instance', 'serial'))})
    if enabled:
        result.update(preset=preset, expected_revision=revision(app) if expected is None else expected)
    return result


def revision(app):
    with ledger_h.readonly(operations.ledger_path(app.dash.ROOT)) as db:
        choice = None if db is None else ledger_h.read_choice(db, identity(app)['device_key'])
    return choice['revision'] if choice else 0


def enable(app, **kwargs):
    response = app.client.post('/api/harvest', json=body(app, **kwargs))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def disable(app, **kwargs):
    response = app.client.post('/api/harvest', json=body(app, enabled=False, **kwargs))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def ops(app):
    return operations.ledger(app.dash.ROOT).active(identity(app)['device_key'])


def running_collector(app):
    """The launched collector as its worker would leave it: running, with a pid."""
    app.dash._harvest_tick()
    [op] = ops(app)
    store = operations.ledger(app.dash.ROOT)
    return store.transition(op['id'], op['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})


def stub_status(dash, monkeypatch):
    import player.scan_plan
    monkeypatch.setattr(player.scan_plan, 'control_gate', lambda root, cfg: {})
    monkeypatch.setattr(dash, '_procs_status', lambda: {'state': 'fresh'})
    monkeypatch.setattr(dash, '_setup_complete', lambda: True)
    monkeypatch.setattr(dash, '_calibrate_missing', lambda: [])
    monkeypatch.setattr(dash, '_reconnect_status', lambda serial: {'state': 'idle'})


def collector_row(op):
    """The scan row of a collector worker: a real scan pins the creation time of the process it walked, and the
    projection attaches the row to the worker only when that is the worker's recorded creation time."""
    return {'pid': os.getpid(), 'runner': 'gem_collector', 'cmdline': 'python gem_collector.py',
            'created': (op.get('worker') or {}).get('creation_time', 1.0)}   # 1.0 is the fake workers' recorded creation time


def start_run(app, **extra):
    return app.client.post('/api/control', json=dict(action='start', preset='bp_farm',
                                                     idempotency_key=f'run-{next(KEY):06d}', **extra))


def _battle_for(app, op):
    app.monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: worker is not None))
    import harvest_support
    env = SimpleNamespace(root=app.dash.ROOT, cfg=app.dash.load_config(), keys=iter(range(1, 100)))
    return harvest_support._battle(env, op, published=NOW - 1)


def revoke(app):
    response = app.client.post('/api/automation-access', json={'enabled': False})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['revocation']


def taps_allowed(app):
    return app.dash.load_config()['instances']['main']['allow_taps']


