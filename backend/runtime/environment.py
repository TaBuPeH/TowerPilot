"""Local environment facts and an explicit, synthetic Windows OCR probe.

Reports never spawn, download, contact adb, or inspect a device. Availability
describes local prerequisites, not a successful capture or emulator connection.
See environment.rules.md for the reporting and probe contract.
"""
from collections.abc import Callable, Mapping
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

from device import tool_install
from device.preview import scrcpy_package
from device.preview.common import PreviewUnavailable, options as preview_options
from device.preview.mumu import discover_target
from runtime import build_info


OCR_TIMEOUT = 10
_OCR_TEXT = 'HELLO'
_windows_ocr = dict(state='unknown', basis='not probed', checked_at=None, detail='not probed')


def _capability(present: bool, basis: str) -> dict:
    return dict(state='available' if present else 'missing', basis=basis)


def _file_capability(path: Path, basis: str) -> dict:
    try:
        return _capability(path.is_file(), basis)
    except OSError as exc:
        return dict(state='unknown', basis=f'{basis}: {type(exc).__name__}')


def platform_tools(root: Path) -> dict:
    """Report the local installer receipt, never `adb version` or a pin as fact."""
    try:
        installed = tool_install.installed(Path(root) / 'backend')
    except OSError as exc:
        return dict(state='unknown', basis=f'local tools check: {type(exc).__name__}', version=None)
    result = dict(_capability(bool(installed), 'tool_install.installed: local executable and required DLLs'),
                  version=None)
    if not installed:
        return result
    try:
        receipt = json.loads((Path(installed).parent / 'source.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        result['basis'] += '; installed version unknown (no readable receipt)'
        return result
    if (isinstance(receipt, dict) and receipt.get('version') == tool_install.VERSION
            and receipt.get('sha256') == tool_install.SHA256 and receipt.get('url') == tool_install.URL):
        result.update(version=receipt['version'], basis=result['basis'] + '; pinned installer receipt matches')
    else:
        result['basis'] += '; installed version unknown (receipt does not match pin)'
    return result


def adb_backend(root: Path, executable: str = '') -> dict:
    """Local screenshot prerequisites; device readiness is deliberately unprobed."""
    tools = platform_tools(root)
    configured = False
    try:
        if executable:
            configured = Path(executable).is_file() or shutil.which(executable) is not None
    except OSError as exc:
        if tools['state'] != 'available':
            return dict(available=False, state='unknown', basis=f'configured adb check: {type(exc).__name__}')
    state = 'available' if configured else tools['state']
    basis = 'configured adb executable found locally' if configured else tools['basis']
    return dict(available=state == 'available', state=state,
                basis=basis + '; device connection not probed')


def stream_backends(root: Path, cfg: dict) -> dict:
    """The existing stream package contract, with checked adb prerequisites."""
    instance = cfg.get('active_instance', 'main')
    inst = cfg['instances'][instance]
    settings = preview_options(inst.get('preview'))
    executable = cfg.get('adb', {}).get('exe', '')
    try:
        target = discover_target(inst.get('serial', ''), executable, settings['mumu'])
        native = dict(available=True, target=target, reason=None)
    except PreviewUnavailable as exc:
        native = dict(available=False, reason=str(exc))
    return dict(ok=True, instance=instance, settings=settings, mumu=native,
                scrcpy=scrcpy_package.status(), adb=adb_backend(root, executable))


def _release_kind(root: Path) -> str:
    """The kind the build recorded, read by build_info (the one reader); the hash checks are /api/runtime.release."""
    return build_info.release_identity(root, verify=False)['kind']


def report(root: Path, *, emulators: Mapping[str, str | None] | None = None) -> dict:
    """Read only local facts. Emulator paths come from the wizard's locators.

    A locator miss says unknown: alternate installations may exist. MuMu native
    capture also needs a configured VM mapping, which only /stream/backends checks.
    """
    root = Path(root)
    scrcpy = scrcpy_package.status()
    server = dict(_capability(bool(scrcpy['installed']), 'scrcpy_package.status: pinned server checksum'),
                  version=scrcpy['version'])
    decoder = _capability(bool(scrcpy['decoder_available']), 'scrcpy_package.status: PyAV module discovery')
    emulator_rows = {}
    for name in ('mumu', 'bluestacks'):
        path = (emulators or {}).get(name)
        emulator_rows[name] = (_file_capability(Path(path), 'wizard locator: local executable; not launched')
                               if path else dict(state='unknown', basis='not found by wizard locator; alternate installs not checked'))
    return dict(
        os=dict(system=platform.system(), release=platform.release(), architecture=platform.machine()),
        python=dict(version=platform.python_version(),
                    kind='portable' if Path(sys.executable).with_name('python312._pth').is_file() else 'source'),
        release=dict(kind=_release_kind(root)), platform_tools=platform_tools(root),
        emulators=emulator_rows, scrcpy_server=server, scrcpy_decoder=decoder,
        stream_backends=dict(adb=adb_backend(root),
                             scrcpy=_capability(bool(scrcpy['available']), 'scrcpy server and decoder prerequisites'),
                             mumu=dict(state='unknown', basis='configured VM mapping not checked; see /api/stream/backends')),
        app_build=_file_capability(root / 'frontend' / 'webui' / 'app' / 'index.html', 'app index.html exists; integrity not checked'),
        windows_ocr_script=_file_capability(root / 'backend' / 'tools' / 'winocr.ps1', 'local Windows OCR script'),
        windows_ocr=dict(_windows_ocr))


def probe_windows_ocr(root: Path, run: Callable) -> dict:
    """Probe the real script once with synthetic pixels, under the caller's lock.

    `run` is the dashboard's window-suppressed subprocess boundary. No settings
    or account state is loaded. The temporary PNG is removed on every outcome.
    """
    global _windows_ocr
    deadline = time.monotonic() + OCR_TIMEOUT
    script = Path(root) / 'backend' / 'tools' / 'winocr.ps1'
    state, detail = 'missing', 'Windows OCR requires Windows'
    try:
        if platform.system() == 'Windows':
            if not script.is_file():
                detail = 'Windows OCR script is missing'
            else:
                with tempfile.TemporaryDirectory(prefix='tp_environment_ocr_') as directory:
                    path = Path(directory) / 'probe.png'
                    # Pillow 10.0 is supported; load_default(size=...) arrived later.
                    image = Image.new('RGB', (180, 40), 'white')
                    ImageDraw.Draw(image).text((10, 10), _OCR_TEXT, fill='black', font=ImageFont.load_default())
                    image = image.resize((900, 200), Image.Resampling.LANCZOS)
                    image.save(path)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired('Windows OCR preparation', OCR_TIMEOUT)
                    completed = run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(script), str(path)], capture_output=True, timeout=remaining)
                    # Only the expected recognized line is proof; exit 0 alone is not.
                    text = completed.stdout.decode('utf-8', errors='replace')
                    recognized = any(len(parts := line.split('\t', 2)) == 3 and parts[2].strip() == _OCR_TEXT
                                     for line in text.splitlines())
                    if completed.returncode == 0 and not completed.stderr.strip() and recognized:
                        state, detail = 'available', 'Synthetic text recognized by Windows OCR'
                    else:
                        detail = f'Windows OCR did not recognize the synthetic text (exit {completed.returncode})'
    except subprocess.TimeoutExpired:
        state, detail = 'timeout', f'Windows OCR exceeded the {OCR_TIMEOUT} s probe deadline'
    except (OSError, subprocess.SubprocessError) as exc:
        detail = f'Windows OCR probe failed: {type(exc).__name__}'
    _windows_ocr = dict(state=state, basis='on-demand synthetic OCR probe', checked_at=time.time(), detail=detail)
    return dict(_windows_ocr)
