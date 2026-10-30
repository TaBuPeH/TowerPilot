"""Runs the REAL takeover worker (orchestrator.main, T3b) on an operation the REAL takeover route (T3c) accepted, with the screen readers and every input call
replaced by recorders. Offline: no device, no socket. Test support only; depends on T3b's tests/support/takeover_world.py (a pinned dependency)."""
from types import SimpleNamespace

from takeover_world import frame, readers


class Stop(BaseException):
    """The test's own end of the observe loop."""


def run_worker(app, operation_id, frames, monkeypatch, tmp_path, *, revoked=False):
    """`orchestrator.main()` on `operation_id` with scripted `frames`; returns (outcome, taps). The configuration is the one the route froze into the envelope."""
    import orchestrator as orch
    import settings
    from control import operations
    from player import readiness
    from runtime import device_lease, logger
    taps, store = [], operations.ledger(app.dash.ROOT)
    envelope = store.get(operation_id)['request']['battle_envelope']
    cfg = dict(envelope['config'], screen={'width': 1080, 'height': 2560}, loop={'fps': 10, 'dry_run': True}, logging={})
    monkeypatch.setenv('TOWER_OPERATION_ID', operation_id)
    monkeypatch.setenv('TOWER_OPERATION_DB', str(operations.ledger_path(app.dash.ROOT)))
    for name in ('TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    from player import playerprofile
    # a bound profile with a non-empty `player` section: the capability gate fails closed without one (the synthetic world's own seed is empty)
    monkeypatch.setattr(playerprofile, 'PROFILE', {'player': {'abilities': {}, 'abilities_verified': False}}, raising=False)
    monkeypatch.setattr(orch, 'CONFIG', cfg)
    for name in ('_attach', '_battle_session', '_action_session', '_rewards_history'):
        monkeypatch.setattr(orch, name, None)
    state, Exhausted = readers(monkeypatch, frames, taps)
    monkeypatch.setattr(device_lease, 'ensure_current', lambda: SimpleNamespace(key='lease-key'))
    monkeypatch.setattr(device_lease, 'revocation', lambda key: 'revoked' if revoked else None)
    monkeypatch.setattr(readiness, 'require', lambda *a, **k: None)
    monkeypatch.setattr(logger, 'event', lambda kind, **fields: None)
    monkeypatch.setattr(logger, 'shot', lambda *a, **k: 'shot')

    def watch(rs, observed, observation=None):
        raise Stop
    monkeypatch.setattr(orch, 'watch_frame', watch)
    monkeypatch.setattr(orch.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(orch.psutil, 'Process', lambda: SimpleNamespace(cpu_percent=lambda: 0))
    monkeypatch.setattr(orch.detect, '_match', lambda *a, **k: (True, None))
    try:
        orch.main()
        outcome = 'returned'
    except Stop:
        outcome = 'looping'
    except Exhausted:
        outcome = 'exhausted'
    return outcome, taps
