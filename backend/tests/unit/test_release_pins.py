"""Offline pin enforcement with local wheels and a synthetic platform-tools ZIP.

portable_pins.json records upstream evidence; these tests never query a registry.
"""

import paths as test_paths
import hashlib
import importlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
from device import tool_install

REPO = test_paths.REPO


@pytest.fixture()
def portable(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(REPO / 'tools'))
    return importlib.import_module('build_portable')


def test_every_runtime_pin_matches_recorded_pypi_evidence() -> None:
    evidence = json.loads((REPO / 'tools/portable_pins.json').read_text())
    text = (REPO / 'requirements-portable.lock').read_text()
    lines = [line for line in text.replace('\\\n', '').splitlines()
             if line.strip() and not line.startswith('#')]
    pins = {}
    for line in lines:
        match = re.fullmatch(r'(\S+==\S+)\s+--hash=sha256:([0-9a-f]{64})', line)
        assert match is not None, f'Missing or malformed pin: {line}'
        requirement, digest = match.groups()
        assert requirement not in pins
        pins[requirement] = digest
    assert set(pins) == {row['requirement'] for row in evidence['packages']}
    for row in evidence['packages']:
        artifact, = row['artifacts']
        name, version = row['requirement'].split('==')
        assert row['source'] == f'https://pypi.org/pypi/{name}/{version}/json'
        assert pins[row['requirement']] == artifact['sha256']
        if name == 'tpk_ar':
            assert row['wheel_count'] == 0
            assert artifact['filename'].endswith('.tar.gz')
        else:
            assert row['wheel_count'] > 0
            assert artifact['filename'].endswith('.whl')


@pytest.fixture()
def local_wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A valid wheel pip can install without network or build dependencies."""
    wheelhouse = tmp_path / 'wheelhouse'
    wheelhouse.mkdir()
    wheel = wheelhouse / 'release_pin_fixture-1.0-py3-none-any.whl'
    metadata = 'release_pin_fixture-1.0.dist-info/'
    files = {
        'release_pin_fixture.py': b'VALUE = 42\n',
        metadata + 'METADATA': b'Metadata-Version: 2.1\nName: release-pin-fixture\nVersion: 1.0\n',
        metadata + 'WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    files[metadata + 'RECORD'] = ''.join(f'{name},,\n' for name in [*files, metadata + 'RECORD']).encode()
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    monkeypatch.setenv('PIP_CONFIG_FILE', os.devnull)
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    monkeypatch.setenv('PIP_FIND_LINKS', str(wheelhouse))
    monkeypatch.setenv('PIP_DISABLE_PIP_VERSION_CHECK', '1')
    return wheel


@pytest.mark.parametrize('pin', ['right', 'wrong', 'missing'])
def test_pip_enforces_wheel_hash(
        portable: ModuleType, local_wheel: Path, tmp_path: Path, pin: str,
        capfd: pytest.CaptureFixture[str]) -> None:
    lock = tmp_path / 'requirements.lock'
    digest = hashlib.sha256(local_wheel.read_bytes()).hexdigest() if pin == 'right' else '0' * 64
    suffix = '' if pin == 'missing' else f' --hash=sha256:{digest}'
    lock.write_text('release-pin-fixture==1.0' + suffix + '\n')
    target = tmp_path / 'packages'
    if pin == 'right':
        portable.install_dependencies(target, lock)
        assert (target / 'release_pin_fixture.py').read_bytes() == b'VALUE = 42\n'
    else:
        with pytest.raises(subprocess.CalledProcessError):
            portable.install_dependencies(target, lock)
        assert not (target / 'release_pin_fixture.py').exists()
        error = capfd.readouterr().err
        assert ('DO NOT MATCH THE HASHES' if pin == 'wrong' else 'Hashes are required') in error


@pytest.fixture()
def platform_archive(monkeypatch: pytest.MonkeyPatch) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name in tool_install.FILES:
            archive.writestr('platform-tools/' + name, b'MZsynthetic')
    body = data.getvalue()

    def download(url: str, timeout: int) -> io.BytesIO:
        assert url == tool_install.URL
        assert timeout == 30
        response = io.BytesIO(body)
        response.url = url
        response.headers = {'Content-Length': str(len(body))}
        return response

    monkeypatch.setattr(tool_install.urllib.request, 'urlopen', download)
    return body


def test_platform_tools_pin_matches_google_evidence() -> None:
    pin = json.loads((REPO / 'tools/portable_pins.json').read_text())['platform_tools']
    assert tool_install.VERSION == pin['version']
    assert tool_install.URL == pin['url']
    assert tool_install.SHA256 == pin['sha256']
    assert tool_install.URL == (
        f'https://dl.google.com/android/repository/platform-tools_r{tool_install.VERSION}-win.zip')
    assert re.fullmatch('[0-9a-f]{64}', tool_install.SHA256)
    assert pin['repository'] == 'https://dl.google.com/android/repository/repository2-3.xml'


@pytest.mark.parametrize('digest', ['wrong', 'missing'])
def test_bad_platform_tools_hash_fails_before_unpacking(
        platform_archive: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        digest: str) -> None:
    monkeypatch.setattr(tool_install, 'SHA256', '0' * 64 if digest == 'wrong' else '')
    monkeypatch.setattr(tool_install, 'unpack', lambda *args: pytest.fail('Unpacked unverified bytes'))
    progress = []
    with pytest.raises(ValueError, match='checksum mismatch'):
        tool_install.install(tmp_path, lambda *args: progress.append(args))
    assert tool_install.installed(tmp_path) is None
    assert not (tmp_path / 'tools/platform-tools').exists()
    assert not list((tmp_path / 'tools').iterdir())
    assert not any(row[0] in {'installing', 'ready'} for row in progress)


def test_right_platform_tools_hash_passes_and_records_pin(
        platform_archive: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    digest = hashlib.sha256(platform_archive).hexdigest()
    monkeypatch.setattr(tool_install, 'SHA256', digest)
    executable = tool_install.install(tmp_path, lambda *args: None)
    assert executable == str(tmp_path / 'tools/platform-tools/adb.exe')
    assert json.loads((tmp_path / 'tools/platform-tools/source.json').read_text()) == {
        'version': tool_install.VERSION, 'url': tool_install.URL, 'sha256': digest}


def test_platform_tools_redirect_still_requires_google(
        platform_archive: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tool_install.urllib.request.urlopen

    def redirected(url: str, timeout: int) -> io.BytesIO:
        response = original(url, timeout=timeout)
        response.url = 'https://example.invalid/platform-tools.zip'
        return response

    monkeypatch.setattr(tool_install.urllib.request, 'urlopen', redirected)
    with pytest.raises(ValueError, match='Unexpected download destination'):
        tool_install.install(tmp_path, lambda *args: None)
    assert tool_install.installed(tmp_path) is None


def test_build_uses_hash_mode_and_records_source_exception(
        portable: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real build orchestration, replacing only network/process boundaries."""
    monkeypatch.setattr(portable, 'ROOT', tmp_path)
    monkeypatch.setattr(portable.sys, 'platform', 'win32')
    monkeypatch.setattr(portable.sys, 'version_info', (3, 12))
    monkeypatch.setattr(portable.platform, 'architecture', lambda: ('64bit', ''))
    staged = ['VERSION', 'requirements-portable.lock', 'tools/portable_pins.json', 'frontend/dashboard.py', 'frontend/webui/app/BUILD_INVENTORY.txt']
    monkeypatch.setattr(portable, 'release_files', lambda root: staged)
    # The git boundary: this folder is not a repository (the clean-tree rule has its own tests, test_release_identity.py).
    monkeypatch.setattr(portable, 'require_clean_tree', lambda root, allow_dirty=False: 'clean')
    (tmp_path / 'VERSION').write_text('9.9.9\n')
    (tmp_path / 'requirements-portable.lock').write_text('# fixture\n')
    for name in staged[3:]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text('# fixture\n')
    (tmp_path / 'tools').mkdir(exist_ok=True)
    for name in ('portable_pins.json', 'portable_launcher.py'):
        (tmp_path / 'tools' / name).write_text('{}')
    cache = tmp_path / 'dist/downloads'
    cache.mkdir(parents=True)
    runtime_zip = cache / portable.URL.rsplit('/', 1)[1]
    with zipfile.ZipFile(runtime_zip, 'w') as archive:
        archive.writestr('LICENSE.txt', 'synthetic runtime')
    monkeypatch.setattr(portable, 'SHA256', hashlib.sha256(runtime_zip.read_bytes()).hexdigest())
    calls = []
    monkeypatch.setattr(portable.subprocess, 'run', lambda args, **kwargs: calls.append((args, kwargs)))
    output = portable.build()
    pip, options = calls[0]
    assert pip[:4] == [sys.executable, '-m', 'pip', 'install']
    assert {'--require-hashes', '--only-binary=:all:', '--no-binary=tpk_ar'} <= set(pip)
    assert options['check'] is True
    assert pip[pip.index('-r') + 1] == str(tmp_path / 'requirements-portable.lock')
    with zipfile.ZipFile(output) as archive:
        prefix = 'TowerPilot-9.9.9-windows-x64/'
        notices = archive.read(prefix + 'THIRD_PARTY.txt').decode().replace('\r\n', '\n')
        assert portable.SOURCE_BUILD_NOTICE in notices
        assert 'tpk_ar==0.2.4 has no wheel on PyPI' in notices
        assert 'compiler may be needed' in notices
        assert archive.read(prefix + 'tools/portable_pins.json') == b'{}'
        identity = json.loads(archive.read(prefix + 'release_identity.json'))
        assert (identity['kind'], identity['working_tree'], identity['python']) == (
            'portable-windows-x64', 'clean', dict(version=portable.PYTHON, sha256=portable.SHA256))
        assert prefix + 'release_identity.json' in archive.namelist()
    assert importlib.import_module('build_release').allowed('tools/portable_pins.json')
