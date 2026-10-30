"""Offline environment facts and the explicit OCR probe; no device or game pixels."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import urllib.request

from PIL import Image
import pytest

from device import tool_install
from device.preview import scrcpy_package
from runtime import environment


@pytest.fixture(autouse=True)
def unprobed(monkeypatch):
    monkeypatch.setattr(environment, '_windows_ocr', dict(
        state='unknown', basis='not probed', checked_at=None, detail='not probed'))


@pytest.fixture
def local_dashboard(dash, tmp_path, monkeypatch):
    from conftest import seed_root
    backend = tmp_path / 'backend'
    seed_root(backend)
    monkeypatch.setattr(dash, 'ROOT', str(backend))
    monkeypatch.setattr(dash, 'CONFIG_PATH', str(backend / 'config.yaml'))
    return dash


def install_tools(root, receipt=True):
    target = root / 'backend' / 'tools' / 'platform-tools'
    target.mkdir(parents=True)
    for name in tool_install.FILES:
        (target / name).write_bytes(b'synthetic executable')
    if receipt:
        (target / 'source.json').write_text(json.dumps(dict(
            version=tool_install.VERSION, sha256=tool_install.SHA256, url=tool_install.URL)))
    return target


def script_tree(root):
    script = root / 'backend' / 'tools' / 'winocr.ps1'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('# synthetic test script')
    return script


def test_unknown_is_unknown(tmp_path):
    result = environment.report(tmp_path)
    assert result['windows_ocr'] == dict(state='unknown', basis='not probed', checked_at=None, detail='not probed')
    assert result['stream_backends']['mumu']['state'] == 'unknown'
    assert all(row['state'] == 'unknown' for row in result['emulators'].values())
    assert result['app_build']['state'] == result['windows_ocr_script']['state'] == 'missing'
    assert result['release']['kind'] == 'checkout'
    result['windows_ocr']['state'] = 'available'
    assert environment.report(tmp_path)['windows_ocr']['state'] == 'unknown'


def test_local_presence_and_os_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(environment.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(environment.platform, 'release', lambda: '11')
    monkeypatch.setattr(environment.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(environment.platform, 'python_version', lambda: '3.12.10')
    app = tmp_path / 'frontend' / 'webui' / 'app' / 'index.html'
    app.parent.mkdir(parents=True)
    app.write_text('synthetic app')
    script_tree(tmp_path)
    manager = tmp_path / 'manager.exe'
    manager.write_bytes(b'synthetic manager')
    result = environment.report(tmp_path, emulators={'mumu': str(manager)})
    assert result['os'] == dict(system='Windows', release='11', architecture='AMD64')
    assert result['python']['version'] == '3.12.10'
    assert result['emulators']['mumu']['state'] == 'available'
    assert result['emulators']['bluestacks']['state'] == 'unknown'
    assert result['app_build']['state'] == result['windows_ocr_script']['state'] == 'available'
    assert result['windows_ocr']['state'] == 'unknown'  # a script is not a working engine


@pytest.mark.parametrize('portable', [False, True])
def test_portable_kind_detection(tmp_path, monkeypatch, portable):
    executable = tmp_path / 'runtime' / 'python.exe'
    executable.parent.mkdir()
    executable.write_bytes(b'python')
    monkeypatch.setattr(environment.sys, 'executable', str(executable))
    if portable:
        executable.with_name('python312._pth').write_text('Lib/site-packages')
    kind = 'portable-windows-x64' if portable else 'source'
    (tmp_path / 'release_identity.json').write_text(json.dumps({'kind': kind}))
    result = environment.report(tmp_path)
    assert result['python']['kind'] == ('portable' if portable else 'source')
    assert result['release']['kind'] == kind


@pytest.mark.parametrize('identity', ['{broken', '[]', '{"kind":"unrecognized"}'])
def test_invalid_release_identity_is_unknown(tmp_path, identity):
    (tmp_path / 'release_identity.json').write_text(identity)
    assert environment.report(tmp_path)['release']['kind'] == 'unknown'


def test_platform_tools_version_is_from_matching_install_receipt(tmp_path):
    assert environment.platform_tools(tmp_path)['state'] == 'missing'
    target = install_tools(tmp_path)
    assert tool_install.installed(tmp_path / 'backend') == str(target / 'adb.exe')
    result = environment.platform_tools(tmp_path)
    assert result['state'] == 'available' and result['version'] == tool_install.VERSION
    (target / tool_install.FILES[-1]).unlink()
    assert environment.platform_tools(tmp_path)['state'] == 'missing'


@pytest.mark.parametrize('receipt', [None, '{broken', '[]', '{"version":"37.0.1"}',
                                   '{"version":"old", "sha256":"wrong", "url":"wrong"}'])
def test_version_is_not_inferred_from_pin(tmp_path, receipt):
    target = install_tools(tmp_path, receipt=False)
    if receipt is not None:
        (target / 'source.json').write_text(receipt)
    result = environment.platform_tools(tmp_path)
    assert result['state'] == 'available' and result['version'] is None
    assert 'unknown' in result['basis']


def test_unreadable_install_is_unknown(tmp_path, monkeypatch):
    def denied(root):
        raise PermissionError('synthetic inaccessible install')
    monkeypatch.setattr(tool_install, 'installed', denied)
    assert environment.platform_tools(tmp_path)['state'] == 'unknown'
    assert environment.adb_backend(tmp_path)['available'] is False


def test_scrcpy_decoder_missing_is_reported_in_portable(tmp_path, monkeypatch):
    server = tmp_path / 'scrcpy-server'
    server.write_bytes(b'synthetic server')
    monkeypatch.setattr(scrcpy_package, 'default_server_path', lambda: server)
    monkeypatch.setattr(scrcpy_package, 'SERVER_SHA256', hashlib.sha256(server.read_bytes()).hexdigest())
    monkeypatch.setattr(scrcpy_package.importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setattr(environment.sys, 'executable', str(tmp_path / 'python.exe'))
    (tmp_path / 'python312._pth').write_text('Lib/site-packages')
    result = environment.report(tmp_path)
    assert result['python']['kind'] == 'portable'
    assert result['scrcpy_server']['state'] == 'available'
    assert result['scrcpy_decoder']['state'] == result['stream_backends']['scrcpy']['state'] == 'missing'
    server.write_bytes(b'corrupt server')
    assert environment.report(tmp_path)['scrcpy_server']['state'] == 'missing'


def test_adb_is_not_a_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(environment.shutil, 'which', lambda executable: None)
    assert environment.adb_backend(tmp_path, 'nonexistent-adb')['available'] is False
    vendor = tmp_path / 'vendor-adb.exe'
    vendor.write_bytes(b'synthetic vendor binary')
    assert environment.adb_backend(tmp_path, str(vendor))['available'] is True
    vendor.unlink()
    install_tools(tmp_path)
    result = environment.adb_backend(tmp_path)
    assert result['available'] is True and 'device connection not probed' in result['basis']


def test_probe_success(tmp_path, monkeypatch):
    script = script_tree(tmp_path)
    monkeypatch.setattr(environment.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(environment.time, 'time', lambda: 1234.5)
    original_default = environment.ImageFont.load_default

    def pillow_10_default():
        return original_default()

    monkeypatch.setattr(environment.ImageFont, 'load_default', pillow_10_default)
    paths = []

    def run(args, **kwargs):
        assert args[:6] == ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
        assert kwargs['capture_output'] is True and 0 < kwargs['timeout'] <= 10
        path = Path(args[-1])
        paths.append(path)
        with Image.open(path) as image:
            assert image.size == (900, 200) and image.getextrema() != ((255, 255),) * 3
        return SimpleNamespace(returncode=0, stdout=b'20\t20\tHELLO\r\n', stderr=b'')

    result = environment.probe_windows_ocr(tmp_path, run)
    assert result == dict(state='available', basis='on-demand synthetic OCR probe', checked_at=1234.5,
                          detail='Synthetic text recognized by Windows OCR')
    assert paths and not paths[0].exists()
    assert environment.report(tmp_path)['windows_ocr'] == result
    result['state'] = 'missing'
    assert environment.report(tmp_path)['windows_ocr']['state'] == 'available'


@pytest.mark.parametrize('system,script', [('Linux', True), ('Windows', False)])
def test_probe_unavailable(tmp_path, monkeypatch, system, script):
    monkeypatch.setattr(environment.platform, 'system', lambda: system)
    if script:
        script_tree(tmp_path)
    result = environment.probe_windows_ocr(tmp_path, lambda *a, **k: pytest.fail('unexpected spawn'))
    assert result['state'] == 'missing' and result['checked_at'] is not None


@pytest.mark.parametrize('code,stdout,stderr', [(0, b'', b''), (1, b'20\t20\tHELLO', b''),
                                             (0, b'20\t20\tHELLO', b'error'),
                                             (0, b'HELLO', b''), (0, b'20\t20\tother', b'')])
def test_probe_does_not_mistake_exit_zero_for_recognition(tmp_path, monkeypatch, code, stdout, stderr):
    script_tree(tmp_path)
    monkeypatch.setattr(environment.platform, 'system', lambda: 'Windows')
    result = environment.probe_windows_ocr(tmp_path, lambda *a, **k: SimpleNamespace(
        returncode=code, stdout=stdout, stderr=stderr))
    assert result['state'] == 'missing'


@pytest.mark.parametrize('error,state', [(subprocess.TimeoutExpired('powershell', 10), 'timeout'),
                                       (FileNotFoundError('powershell'), 'missing')])
def test_probe_timeout_is_bounded_and_reported(tmp_path, monkeypatch, error, state):
    script_tree(tmp_path)
    monkeypatch.setattr(environment.platform, 'system', lambda: 'Windows')
    paths = []

    def run(args, **kwargs):
        paths.append(Path(args[-1]))
        assert 0 < kwargs['timeout'] <= 10
        raise error

    result = environment.probe_windows_ocr(tmp_path, run)
    assert result['state'] == state and result['checked_at'] is not None
    assert not paths[0].exists()
    assert environment.report(tmp_path)['windows_ocr'] == result


def test_preparation_consumes_the_probe_budget(tmp_path, monkeypatch):
    script_tree(tmp_path)
    monkeypatch.setattr(environment.platform, 'system', lambda: 'Windows')
    clock = iter([0, 11])
    monkeypatch.setattr(environment.time, 'monotonic', lambda: next(clock))
    result = environment.probe_windows_ocr(tmp_path, lambda *a, **k: pytest.fail('deadline already elapsed'))
    assert result['state'] == 'timeout'


def test_probe_runs_only_on_post_and_holds_action_lock(local_dashboard, monkeypatch):
    dash = local_dashboard
    script_tree(Path(dash.ROOT).parent)
    monkeypatch.setattr(environment.platform, 'system', lambda: 'Windows')
    calls = []

    def run(*args, **kwargs):
        assert dash._ACTION_LOCK.locked()
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b'0\t0\tHELLO', stderr=b'')

    monkeypatch.setattr(dash, '_run', run)
    client = dash.app.test_client()
    assert client.get('/api/environment/probe').status_code == 405
    for body in (None, [], {}, {'probe': 'other'}, {'probe': 'windows_ocr', 'extra': True}):
        assert client.post('/api/environment/probe', json=body).status_code == 400
    assert client.post('/api/environment/probe', data='{broken', content_type='application/json').status_code == 400
    assert calls == []
    assert dash._ACTION_LOCK.acquire(blocking=False)
    try:
        locked = client.post('/api/environment/probe', json={'probe': 'windows_ocr'})
        assert locked.status_code == 409 and locked.get_json()['code'] == 'action_lock'
        assert calls == []
    finally:
        dash._ACTION_LOCK.release()
    response = client.post('/api/environment/probe', json={'probe': 'windows_ocr'})
    assert response.status_code == 200 and response.get_json()['windows_ocr']['state'] == 'available'
    assert len(calls) == 1 and not dash._ACTION_LOCK.locked()
    assert client.get('/api/runtime').get_json()['environment']['windows_ocr']['state'] == 'available'
    assert len(calls) == 1


def test_polls_spawn_nothing(local_dashboard, monkeypatch):
    dash = local_dashboard
    def forbidden(*args, **kwargs):
        pytest.fail('poll spawned or accessed network/device')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(dash, '_emulator_instances', forbidden)
    client = dash.app.test_client()
    for _ in range(2):
        result = client.get('/api/runtime')
        assert result.status_code == 200 and result.get_json()['environment']['windows_ocr']['state'] == 'unknown'
        assert client.get('/api/stream/backends').status_code == 200


def test_stream_route_checks_adb_and_preserves_other_fields(local_dashboard, monkeypatch):
    dash = local_dashboard
    monkeypatch.setattr(environment.shutil, 'which', lambda name: None)
    client = dash.app.test_client()
    body = client.get('/api/stream/backends').get_json()
    assert body['ok'] is True and body['instance'] == 'main'
    assert {'settings', 'mumu', 'scrcpy', 'adb'} <= body.keys()
    assert body['adb']['available'] is False
    install_tools(Path(dash.ROOT).parent)
    assert client.get('/api/stream/backends').get_json()['adb']['available'] is True
    cfg = dash.load_config()
    cfg['instances']['main']['preview'] = {'backend': 'invalid'}
    monkeypatch.setattr(dash, 'load_config', lambda: cfg)
    assert client.get('/api/stream/backends').status_code == 400
