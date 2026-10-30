"""A person's tap revocation reaches input that is already authorized.

Accepted workers and delegated scheduler children freeze their configuration
at start, so writing allow_taps=false cannot stop them. Revocation is read at
the one input boundary they all share (runtime.device_lease.input_scope, used
by device.act), and fence_input() proves when no input call is still in flight.
Fake adb socket, temporary lease directory and ledger, no device."""
import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import settings
from control import operations
from device import act, adbclient
from runtime import device_lease
from runtime.operations import restore_worker_context

SERIAL, HOST, PORT = 'fake:1', '127.0.0.1', 5037


@pytest.fixture
def sent(monkeypatch):
    """The adb socket: every input line that would have reached the emulator."""
    lines = []
    monkeypatch.setattr(adbclient, 'shell', lambda serial, cmd, **kwargs: lines.append((serial, cmd)) or b'')
    return lines


@pytest.fixture
def worker(tmp_path, monkeypatch, sent):
    """An accepted 'run' worker as the operation bootstrap installs it: frozen
    configuration restored from the ledger (allow_taps true) and the device
    lease acquired. Everything it touches is restored afterwards (the settings table it replaces by the
    suite-wide conftest._settings_singletons_restored)."""
    monkeypatch.setattr(adbclient, '_HOST', (HOST, PORT))
    frozen = copy.deepcopy(dict(settings.CONFIG))
    frozen.update(active_instance='main')
    frozen['instances'].setdefault('main', {}).update(serial=SERIAL, allow_taps=True)
    frozen['loop']['dry_run'] = False
    frozen['tap']['max_rate_per_sec'] = 1000
    frozen['tap']['jitter_ms'] = [0, 0]
    store = operations.ledger(tmp_path)
    context = operations.context(tmp_path, frozen, {}, None)
    op, _ = store.accept('run', context, {}, 'accepted-run-key')
    store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    restore_worker_context(settings)
    lease = device_lease.acquire(SERIAL, HOST, PORT)
    try:
        yield lease
    finally:
        device_lease.release_current()


def tap():
    return act.tap(100, 100, 'test tap')


def test_revocation_refuses_input_a_frozen_accepted_worker_is_still_permitted_to_send(worker, sent):
    assert settings.instance()['allow_taps'] is True and tap()['dry_run'] is False
    assert len(sent) == 1
    device_lease.revoke_input(SERIAL, HOST, PORT)
    assert settings.instance()['allow_taps'] is True              # its frozen configuration still says yes
    with pytest.raises(act.TapRefused, match='revoked'):
        tap()
    with pytest.raises(act.TapRefused, match='revoked'):
        act.swipe(100, 100, 200, 200, 100, 'test swipe')
    assert len(sent) == 1                                          # nothing reached the socket
    assert device_lease.fence_input(SERIAL, HOST, PORT) is True
    state = device_lease.lease_state(SERIAL, HOST, PORT)
    assert (state['owned'], state['input_gate']) == (True, 'enforced')   # the worker lives on; only its input stopped


def test_an_explicit_enable_restores_input_and_nothing_else_does(worker, sent):
    device_lease.revoke_input(SERIAL, HOST, PORT)
    for _ in range(2):
        with pytest.raises(act.TapRefused):
            tap()
    assert device_lease.is_revoked(SERIAL, HOST, PORT) is True
    device_lease.clear_revocation(SERIAL, HOST, PORT)
    assert device_lease.is_revoked(SERIAL, HOST, PORT) is False
    tap()
    assert len(sent) == 1


def test_a_revocation_marker_that_cannot_be_read_still_revokes(worker, sent):
    device_lease.revoke_input(SERIAL, HOST, PORT)
    marker = device_lease._stem(device_lease._device_key(SERIAL, HOST, PORT)).with_suffix('.revoked')
    marker.write_text('{not json')
    with pytest.raises(act.TapRefused):
        tap()
    assert sent == []


def test_the_fence_is_pending_while_an_input_call_is_in_flight_and_proven_after(worker, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    sent_lines = []

    def slow_shell(serial, cmd, **kwargs):
        sent_lines.append(cmd)
        entered.set()
        assert release.wait(5)
        return b''
    monkeypatch.setattr(adbclient, 'shell', slow_shell)
    result = {}
    thread = threading.Thread(target=lambda: result.update(event=tap()))
    thread.start()
    assert entered.wait(5)
    device_lease.revoke_input(SERIAL, HOST, PORT)
    assert device_lease.fence_input(SERIAL, HOST, PORT, timeout=0.2) is False   # truthfully pending
    release.set()
    thread.join(5)
    assert result['event']['type'] == 'tap' and len(sent_lines) == 1             # the call already past the gate finished
    assert device_lease.fence_input(SERIAL, HOST, PORT) is True
    with pytest.raises(act.TapRefused):
        tap()
    assert len(sent_lines) == 1


def test_an_ads_force_stop_is_not_game_input_but_an_ad_close_tap_is(worker, sent):
    from device import overlays
    import numpy as np
    device_lease.revoke_input(SERIAL, HOST, PORT)
    frame = np.zeros((2560, 1080, 3), np.uint8)
    with pytest.raises(act.TapRefused, match='revoked'):
        overlays._tap(SERIAL, frame, 500, 500, 'ad close button')
    with device_lease.input_scope(game_input=False):
        adbclient.shell(SERIAL, 'am force-stop com.example.ad')
    assert sent == [(SERIAL, 'am force-stop com.example.ad')]


def test_a_worker_that_predates_the_input_gate_cannot_be_reported_fenced(worker):
    path = device_lease._stem(worker.key).with_suffix('.json')
    grant = json.loads(path.read_text())
    grant.pop('input_gate')
    path.write_text(json.dumps(grant))
    assert device_lease.lease_state(SERIAL, HOST, PORT)['input_gate'] == 'unsupported'


def test_a_failed_marker_write_revokes_nothing_and_leaves_no_residue(worker, sent, monkeypatch):
    # H2INPUT-01 at the boundary: a replace that fails is an exception, never a marker, and the very
    # next tap still goes out - which is exactly why nothing above this layer may call it revoked.
    real = os.replace
    monkeypatch.setattr(os, 'replace', lambda src, dst, *a: (_ for _ in ()).throw(PermissionError(13, 'denied'))
                        if str(dst).endswith('.revoked') else real(src, dst, *a))
    with pytest.raises(PermissionError):
        device_lease.revoke_input(SERIAL, HOST, PORT)
    assert device_lease.is_revoked(SERIAL, HOST, PORT) is False
    assert list(device_lease._ROOT.glob('*.tmp')) == []
    tap()
    assert len(sent) == 1


def test_a_marker_that_reads_back_as_someone_elses_is_not_this_revocation(worker, monkeypatch):
    real = os.replace

    def foreign(src, dst, *rest):
        if str(dst).endswith('.revoked'):
            Path(dst).write_text(json.dumps({'token': 'someone-else'}))
        else:
            real(src, dst, *rest)
    monkeypatch.setattr(os, 'replace', foreign)
    with pytest.raises(OSError, match='read back'):
        device_lease.revoke_input(SERIAL, HOST, PORT)


def test_the_dashboard_reports_a_failed_marker_write_as_failed_while_the_frozen_run_still_sends(dash, worker, sent, monkeypatch):
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    real = os.replace
    monkeypatch.setattr(os, 'replace', lambda src, dst, *a: (_ for _ in ()).throw(PermissionError(13, 'denied'))
                        if str(dst).endswith('.revoked') else real(src, dst, *a))
    failed = dash._revoke_taps()
    assert failed['state'] == 'failed' and failed['revoked_at'] is None
    assert (failed['fenced'], failed['latched']) == (False, False)
    assert failed['config_persisted'] is True and [e['step'] for e in failed['errors']] == ['revoke_input']
    assert [owner['input'] for owner in failed['still_owned']] == ['unfenced']   # never 'refused'
    tap()
    assert len(sent) == 1                                   # the claim and the wire agree: input was not stopped
    monkeypatch.setattr(os, 'replace', real)
    stopped = dash._revoke_taps()
    assert stopped['state'] == 'input_stopped' and stopped['fenced'] is True and stopped['latched'] is True
    assert [owner['input'] for owner in stopped['still_owned']] == ['refused']
    with pytest.raises(act.TapRefused):
        tap()
    assert len(sent) == 1


def holder():
    """A second holder of the activity lock, as a scheduler child that predates the gate would be."""
    return device_lease._lock(device_lease._stem(device_lease._device_key(SERIAL, HOST, PORT)).with_suffix('.activity'))


def advertise(path, **grant):
    import psutil
    me = psutil.Process()
    path.write_text(json.dumps(dict(dict(pid=me.pid, creation_time=me.create_time(), input_gate=True), **grant)))


@pytest.mark.parametrize('metadata,gate', [
    pytest.param(None, 'unsupported', id='missing'),
    pytest.param('{not json', 'unsupported', id='corrupt'),
    pytest.param('[]', 'unsupported', id='not-an-object'),
    pytest.param(dict(input_gate=False), 'unsupported', id='pre-gate'),
    pytest.param(dict(creation_time=1.0), 'unsupported', id='stale-process'),
    pytest.param(dict(pid=-1), 'unsupported', id='no-such-process'),
    pytest.param(dict(pid='1'), 'unsupported', id='pid-not-an-integer'),
    pytest.param(dict(), 'enforced', id='positively-advertised-true'),
    pytest.param(dict(input_gate=1), 'enforced', id='positively-advertised-the-producers-token'),
    # H2R2-03: truthiness is not the advertisement
    pytest.param(dict(input_gate='false'), 'unsupported', id='gate-the-string-false'),
    pytest.param(dict(input_gate='1'), 'unsupported', id='gate-the-string-one'),
    pytest.param(dict(input_gate='true'), 'unsupported', id='gate-the-string-true'),
    pytest.param(dict(input_gate=2), 'unsupported', id='gate-another-number'),
    pytest.param(dict(input_gate=1.0), 'unsupported', id='gate-a-float'),
    pytest.param(dict(input_gate=0), 'unsupported', id='gate-zero'),
    pytest.param(dict(input_gate=[1]), 'unsupported', id='gate-a-list'),
    pytest.param(dict(input_gate={'v': 1}), 'unsupported', id='gate-an-object'),
])
def test_only_an_activity_holder_that_advertises_the_gate_proves_enforcement(worker, metadata, gate):
    # H2INPUT-02: a held lock whose metadata cannot be trusted is not an enforced gate.
    path = device_lease._stem(worker.key).with_suffix('.activity.json')
    held = holder()
    try:
        if isinstance(metadata, dict):
            advertise(path, **metadata)
        elif metadata is not None:
            path.write_text(metadata)
        state = device_lease.lease_state(SERIAL, HOST, PORT)
        assert (state['owned'], state['delegated'], state['input_gate']) == (True, True, gate)
    finally:
        device_lease._unlock(held)


# ---- who holds the lease: named only from metadata that names a live, matching process
def test_the_lease_names_the_live_process_its_lock_files_name(worker):
    me = os.getpid()
    assert device_lease.lease_state(SERIAL, HOST, PORT)['holders'] == [dict(pid=me, role='primary')]
    path = device_lease._stem(worker.key).with_suffix('.activity.json')
    held = holder()
    try:
        advertise(path)
        roles = {entry['role']: entry['pid'] for entry in device_lease.lease_state(SERIAL, HOST, PORT)['holders']}
        assert roles == dict(primary=me, delegated=me)
    finally:
        device_lease._unlock(held)


@pytest.mark.parametrize('metadata', [None, '{not json', '[]', dict(creation_time=1.0), dict(pid=-1), dict(pid='1'), dict(pid=10 ** 400),
                                      dict(creation_time=10 ** 400), dict(creation_time=float('nan'))],
                         ids=['missing', 'corrupt', 'not-an-object', 'stale-process', 'no-such-process', 'pid-text', 'pid-huge',
                              'creation-huge', 'creation-nan'])
def test_a_holder_whose_metadata_names_no_live_matching_process_is_still_owned_but_not_named(worker, metadata):
    path = device_lease._stem(worker.key).with_suffix('.activity.json')
    held = holder()
    try:
        if isinstance(metadata, dict):
            advertise(path, **metadata)
        elif metadata is not None:
            path.write_text(metadata)
        state = device_lease.lease_state(SERIAL, HOST, PORT)
        assert state['owned'] is True and state['delegated'] is True
        assert state['holders'] == [dict(pid=os.getpid(), role='primary')]            # only the primary lock's metadata names a live process
    finally:
        device_lease._unlock(held)


def test_a_primary_holder_whose_metadata_is_stale_is_owned_and_not_named(worker):
    path = device_lease._stem(worker.key).with_suffix('.json')
    grant = json.loads(path.read_text())
    path.write_text(json.dumps(dict(grant, creation_time=grant['creation_time'] + 100)))
    state = device_lease.lease_state(SERIAL, HOST, PORT)
    assert state['owned'] is True and state['holders'] == []


def test_a_primary_holder_with_a_malformed_gate_is_not_enforced_either(worker):
    path = device_lease._stem(worker.key).with_suffix('.json')
    grant = json.loads(path.read_text())
    assert grant['input_gate'] == device_lease.INPUT_GATE            # what the producer really writes is accepted
    assert device_lease.lease_state(SERIAL, HOST, PORT)['input_gate'] == 'enforced'
    for malformed in ('false', 'true', 2, 1.0, None, []):
        path.write_text(json.dumps(dict(grant, input_gate=malformed)))
        assert device_lease.lease_state(SERIAL, HOST, PORT)['input_gate'] == 'unsupported', malformed


def test_the_dashboard_will_not_call_a_holder_without_the_gate_input_stopped(dash, worker, monkeypatch):
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    held = holder()                                        # a holder that never wrote the gate advertisement
    try:
        outcome = dash._revoke_taps()
    finally:
        device_lease._unlock(held)
    assert outcome['state'] == 'not_enforced' and outcome['fenced'] is True
    assert [owner['input'] for owner in outcome['still_owned']] == ['unfenced']


def real_lease(dash):
    """The device lease really held by this process (valid gate metadata), nothing in the ledger or the process table."""
    from control import operations as operation_api
    identity = operation_api.device_identity(dash.load_config())
    return device_lease.acquire(identity['serial'], identity['adb_host'], identity['adb_port']), identity


def test_a_lease_scan_that_fails_is_unknown_never_nothing_running(dash, monkeypatch):
    # H2R2-02: with a lease really held and no ledger or process owner, a healthy scan says the input
    # was stopped; the very same situation with an unreadable scan must say it does not know.
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    lease, identity = real_lease(dash)
    healthy = dash._revoke_taps()
    assert (healthy['state'], healthy['fenced'], healthy['latched'], healthy['errors']) == ('input_stopped', True, True, [])
    device_lease.clear_revocation(identity['serial'], identity['adb_host'], identity['adb_port'])
    real = device_lease.lease_state

    def unreadable(*args, **kwargs):
        raise OSError('probe cannot inspect lease')
    monkeypatch.setattr(device_lease, 'lease_state', unreadable)
    failed = dash._revoke_taps()
    monkeypatch.setattr(device_lease, 'lease_state', real)
    assert failed['state'] == 'stopping'
    assert (failed['fenced'], failed['latched']) == (False, False)               # who is covered is unknown
    assert [e['step'] for e in failed['errors']] == ['lease_state'] and 'probe cannot inspect lease' in failed['errors'][0]['message']
    assert failed['config_persisted'] is True and failed['revoked_at'] is not None   # the marker itself is a fact
    assert device_lease.is_revoked(identity['serial'], identity['adb_host'], identity['adb_port']) is True
    assert failed['still_owned'] == []
    retried = dash._revoke_taps()                                                  # the same request again, now readable
    assert (retried['state'], retried['fenced'], retried['latched']) == ('input_stopped', True, True)
    assert lease.handle.closed is False


def test_a_lease_scan_failure_still_reports_a_failed_publication_as_failed(dash, monkeypatch):
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    real_lease(dash)
    monkeypatch.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(OSError('probe cannot inspect lease')))
    monkeypatch.setattr(device_lease, 'revoke_input', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    failed = dash._revoke_taps()
    assert failed['state'] == 'failed' and (failed['fenced'], failed['latched']) == (False, False)


def test_without_an_adb_serial_there_is_no_lease_to_scan_and_nothing_to_stop(dash, monkeypatch):
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    cfg = dash.load_config()
    cfg['instances']['main'].pop('serial')
    dash.save_config(cfg)
    monkeypatch.setattr(device_lease, 'lease_state', lambda *a, **k: (_ for _ in ()).throw(AssertionError('scanned')))
    assert dash._revoke_taps()['state'] == 'nothing_running'


def test_a_free_device_is_proved_free_only_when_no_process_holds_a_lease(tmp_path):
    assert device_lease.lease_free(SERIAL, HOST, PORT) is True
    assert device_lease.lease_state(SERIAL, HOST, PORT) == {'owned': False, 'delegated': False, 'input_gate': None, 'holders': []}
    lease = device_lease.acquire(SERIAL, HOST, PORT)
    try:
        assert device_lease.lease_free(SERIAL, HOST, PORT) is False
    finally:
        lease.release()
    assert device_lease.lease_free(SERIAL, HOST, PORT) is True


CHILD = r"""
import json, sys
from pathlib import Path
backend, lease_root, log_root = sys.argv[1:4]
sys.path.insert(0, backend)
import settings, yaml
settings.CONFIG.clear()
settings.CONFIG.update(yaml.safe_load(settings.CONFIG_EXAMPLE.read_text(encoding='utf-8')))
settings.CONFIG['active_instance'] = 'main'
settings.CONFIG['instances'].setdefault('main', {}).update(serial='fake:1', allow_taps=True)
settings.CONFIG['loop']['dry_run'] = False
settings.CONFIG['tap']['max_rate_per_sec'] = 1000
settings.CONFIG['tap']['jitter_ms'] = [0, 0]
from device import act, adbclient
from runtime import device_lease, logger
device_lease._ROOT = Path(lease_root)
logger._LOG_DIR = Path(log_root)
adbclient._HOST = ('127.0.0.1', 5037)
lines = []
adbclient.shell = lambda serial, cmd, **kwargs: lines.append(cmd) or b''
lease = device_lease.ensure_current()
print(json.dumps({'delegated': lease.delegated}), flush=True)
for command in sys.stdin:
    if command.strip() == 'quit':
        break
    before = len(lines)
    try:
        act.tap(100, 100, 'child tap')
        outcome = 'dispatched'
    except act.TapRefused as exc:
        outcome = 'refused: ' + str(exc)
    print(json.dumps({'outcome': outcome, 'sent': len(lines) - before}), flush=True)
"""


def test_a_delegated_scheduler_child_obeys_a_revocation_it_cannot_see_in_its_config(tmp_path, worker):
    backend = Path(settings.__file__).parent
    script = tmp_path / 'child.py'
    script.write_text(CHILD, encoding='utf-8')
    env = device_lease.child_environment()
    child = subprocess.Popen([sys.executable, str(script), str(backend), str(device_lease._ROOT), str(tmp_path / 'child-logs')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def ask(command='tap'):
        child.stdin.write(command + '\n')
        child.stdin.flush()
        return json.loads(child.stdout.readline())
    try:
        assert json.loads(child.stdout.readline()) == {'delegated': True}
        assert ask() == {'outcome': 'dispatched', 'sent': 1}
        device_lease.revoke_input(SERIAL, HOST, PORT)
        refused = ask()
        assert refused['sent'] == 0 and refused['outcome'].startswith('refused') and 'revoked' in refused['outcome']
        assert device_lease.fence_input(SERIAL, HOST, PORT) is True
        assert device_lease.lease_state(SERIAL, HOST, PORT)['input_gate'] == 'enforced'
        device_lease.clear_revocation(SERIAL, HOST, PORT)
        assert ask() == {'outcome': 'dispatched', 'sent': 1}
    finally:
        child.stdin.write('quit\n')
        child.stdin.flush()
        child.wait(10)
    assert child.returncode == 0, child.stderr.read()