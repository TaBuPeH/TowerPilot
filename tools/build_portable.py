"""Windows x64 portable build; Python/pip are needed on the build machine only."""
import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterable
from pathlib import Path

from build_release import (APP_BUILD, APP_NOTICES, ROOT, command_line, release_files, require_clean_tree,
                           stage_files, write_identity)
from release_archive_policy import release_notes_path

PYTHON = '3.12.10'
URL = f'https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip'
SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
SOURCE_LAUNCHERS = {'Start-TowerPilot.ps1', 'Start Tower Pilot.cmd'}
SOURCE_BUILD_NOTICE = (
    'Source-build exception: tpk_ar==0.2.4 has no wheel on PyPI; its sdist SHA-256 is pinned.\n'
    'A compiler may be needed on the build machine for source builds.\n'
    'Isolated build dependencies and the locally built wheel are not hash-locked.\n')


def install_dependencies(target: Path, lock: Path = ROOT / 'requirements-portable.lock') -> None:
    """Let pip verify every runtime artifact; only tpk_ar may build from source."""
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--require-hashes',
                    '--only-binary=:all:', '--no-binary=tpk_ar', '--no-cache-dir',
                    '--no-compile', '--target', str(target), '-r', str(lock)], check=True)


def stage_tree(stage: Path, root: Path = ROOT, files: Iterable[str] | None = None) -> None:
    """Copy the release files - full source, tests, agent rules, skills and the
    dashboard app build - into the portable tree, so the extracted folder is
    also a working checkout. Only the source launchers are left out; build()
    writes the portable ones."""
    stage_files(stage, root, release_files(root) if files is None else files, SOURCE_LAUNCHERS)


def write_zip(stage: Path, output: Path, prefix: str) -> Path:
    """ZIP the staged tree under `prefix/` with a SHA256SUMS.txt inventory."""
    inventory = []
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            name = path.relative_to(stage).as_posix()
            data = path.read_bytes()
            archive.writestr(prefix + '/' + name, data)
            inventory.append(hashlib.sha256(data).hexdigest() + '  ' + name)
        archive.writestr(prefix + '/SHA256SUMS.txt', '\n'.join(inventory)+'\n')
    return output


def build(*, allow_dirty: bool = False):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or platform.architecture()[0] != '64bit':
        raise RuntimeError('Build with 64-bit Python 3.12 on Windows')
    tree = require_clean_tree(ROOT, allow_dirty)    # before the app check and any download
    files = release_files(ROOT)          # refuses early when the dashboard app is not built
    version = (ROOT / 'VERSION').read_text().strip()
    dist = ROOT / 'dist'
    cache = dist / 'downloads'
    cache.mkdir(parents=True, exist_ok=True)
    runtime_zip = cache / URL.rsplit('/', 1)[1]
    if not runtime_zip.exists():
        urllib.request.urlretrieve(URL, runtime_zip)
    if hashlib.sha256(runtime_zip.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError('Python download checksum mismatch')
    prefix = f'TowerPilot-{version}-windows-x64'
    # Build in a new directory, never collect local state from a previous run.
    with tempfile.TemporaryDirectory(prefix='portable-build-', dir=dist) as work:
        stage = Path(work) / prefix
        stage.mkdir()
        stage_tree(stage, ROOT, files)
        runtime = stage / 'runtime'
        with zipfile.ZipFile(runtime_zip) as archive:
            archive.extractall(runtime)
        (runtime / 'python312._pth').write_text(
            'python312.zip\n.\nLib/site-packages\n../backend\n../frontend\n..\nimport site\n')
        install_dependencies(runtime / 'Lib/site-packages', ROOT / 'requirements-portable.lock')
        (stage / 'tools').mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'tools/portable_launcher.py', stage / 'tools/portable_launcher.py')
        shutil.copyfile(ROOT / 'requirements-portable.lock', stage / 'requirements-portable.lock')
        # pythonw + start: no console window at all. The app lives in the
        # notification area (tools/portable_launcher.py), so there is no
        # window for the user to close by accident.
        (stage / 'Start Tower Pilot.cmd').write_text(
            '@echo off\ncd /d "%~dp0"\n'
            'start "" "%~dp0runtime\\pythonw.exe" "%~dp0tools\\portable_launcher.py"\n')
        (stage / 'START HERE.txt').write_text(
            'Tower Pilot '+version+' - Windows 11 x64 (Windows 10 is untested)\n\n'
            'Extract the entire ZIP into a writable folder, then double-click Start Tower Pilot.cmd.\n'
            'Python and dependencies are included. No Python, pip or admin install is needed.\n'
            'No console window opens: Tower Pilot sits in the notification area '
            '(near the clock). Click that icon to open the dashboard, right-click it to quit.\n'
            'Your default browser opens the dashboard automatically. Use current Chrome or Edge.\n'
            'Install MuMu Player or BlueStacks and The Tower, then use Setup to install connection tools (internet required).\n'
            'Recommended: 1080x2560 portrait, 360 DPI. Initial scan takes approximately 20-30 minutes.\n'
            'Account data is stored beside the app. Preserve the folder when upgrading.\n'
            'See '+release_notes_path(version)+' for what changed and the environment limits.\n')
        subprocess.run([str(runtime / 'python.exe'), '-c',
            'import cv2,numpy,yaml,psutil,flask,pystray,PIL,UnityPy; print("Bundled imports OK")'],
            cwd=stage, check=True)
        # Capture dependency notices without replacing their bundled licenses.
        (stage / 'THIRD_PARTY.txt').write_text(
            'Python '+PYTHON+' from '+URL+'\nSHA256: '+SHA256+'\n'
            'Python license: runtime/LICENSE.txt\n'
            'Package versions and artifact hashes: requirements-portable.lock\n'
            'Upstream pin evidence: tools/portable_pins.json\n'
            + SOURCE_BUILD_NOTICE +
            'Dependency licenses/notices: runtime/Lib/site-packages/*.dist-info/\n'
            'Dashboard app npm packages and their licenses: '+APP_BUILD+'/'+APP_NOTICES+'\n')
        write_identity(stage, ROOT, 'portable-windows-x64', tree, python=dict(version=PYTHON, sha256=SHA256))
        output = write_zip(stage, dist / (prefix + '.zip'), prefix)
        output.with_suffix('.zip.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
        print(output)
        return output


def main(argv: list[str] | None = None) -> None:
    build(allow_dirty=command_line(__doc__, argv).allow_dirty)


if __name__ == '__main__':
    main()
