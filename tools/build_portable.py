"""Windows x64 portable build; Python/pip are needed on the build machine only."""
import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from build_release import ROOT, allowed

PYTHON = '3.12.10'
URL = f'https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip'
SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'


def build():
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or platform.architecture()[0] != '64bit':
        raise RuntimeError('Build with 64-bit Python 3.12 on Windows')
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
        names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
        # The portable tree carries the full source, tests, agent rules and
        # skills included (0.2-beta), so the extracted folder is also a
        # working checkout. Only the source launchers are replaced below.
        for name in sorted(set(names)):
            if not name or not allowed(name) or not (ROOT / name).is_file():
                continue
            if name in {'Start-TowerPilot.ps1', 'Start Tower Pilot.cmd'}:
                continue
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        runtime = stage / 'runtime'
        with zipfile.ZipFile(runtime_zip) as archive:
            archive.extractall(runtime)
        (runtime / 'python312._pth').write_text(
            'python312.zip\n.\nLib/site-packages\n../backend\n../frontend\n..\nimport site\n')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-binary=tpk_ar',
                        '--no-compile', '--target', str(runtime / 'Lib/site-packages'),
                        '-r', str(ROOT / 'requirements-portable.lock')], check=True)
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
            'Tower Pilot '+version+' - Windows 10/11 x64\n\n'
            'Extract the entire ZIP into a writable folder, then double-click Start Tower Pilot.cmd.\n'
            'Python and dependencies are included. No Python, pip or admin install is needed.\n'
            'No console window opens: Tower Pilot sits in the notification area '
            '(near the clock). Click that icon to open the dashboard, right-click it to quit.\n'
            'Your browser opens Setup automatically on the first start.\n'
            'Install an emulator and The Tower, then use Setup to install connection tools (internet required).\n'
            'Recommended: 1080x2560 portrait, 360 DPI. Initial scan takes approximately 20-30 minutes.\n'
            'Account data is stored beside the app. Preserve the folder when upgrading.\n'
            'See docs/RELEASE_0.5_BETA.md for what changed and the beta limitations.\n')
        subprocess.run([str(runtime / 'python.exe'), '-c',
            'import cv2,numpy,yaml,psutil,flask,pystray,PIL,UnityPy; print("Bundled imports OK")'],
            cwd=stage, check=True)
        # Capture dependency notices without replacing their bundled licenses.
        (stage / 'THIRD_PARTY.txt').write_text(
            'Python '+PYTHON+' from '+URL+'\nSHA256: '+SHA256+'\n'
            'Python license: runtime/LICENSE.txt\n'
            'Package versions: requirements-portable.lock\n'
            'Dependency licenses/notices: runtime/Lib/site-packages/*.dist-info/\n')
        output = dist / (prefix + '.zip')
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
        output.with_suffix('.zip.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
        print(output)
        return output


if __name__ == '__main__':
    build()
