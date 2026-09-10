"""Install connection tools locally; no PATH edits or administrator access."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile

URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
MAX_BYTES = 128 * 1024 * 1024
FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")


def ensure_server(root, executable, run):
    """Start the daemon once on a cold machine; never replace a live server."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 5037), timeout=1):
            return
    except OSError:
        pass
    if not executable or not Path(executable).is_file():
        executable = installed(root)
    if not executable:
        raise RuntimeError("Install connection tools in Setup first")
    result = run([executable, "start-server"], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Could not start Android connection tools")


def installed(root):
    folder = Path(root) / "tools" / "platform-tools"
    return str(folder / "adb.exe") if all((folder / n).is_file() for n in FILES) else None


def unpack(archive, target):
    """Extract only the three required files; never follow archive paths."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in FILES:
            info = z.getinfo("platform-tools/" + name)
            if info.file_size > MAX_BYTES or info.file_size == 0:
                raise ValueError("Invalid platform-tools archive")
            with z.open(info) as src, (target / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
        if (target / "adb.exe").read_bytes()[:2] != b"MZ":
            raise ValueError("The download does not contain a Windows ADB executable")


def install(root, progress):
    existing = installed(root)
    if existing:
        progress("ready", "Connection tools are already installed", 100)
        return existing
    parent = Path(root) / "tools"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adb-install-", dir=parent) as work:
        archive = Path(work) / "download.zip"
        progress("downloading", "Downloading Android connection tools from Google", 0)
        digest = hashlib.sha256()
        with urllib.request.urlopen(URL, timeout=30) as response, archive.open("wb") as dst:
            if not response.url.startswith("https://dl.google.com/"):
                raise ValueError("Unexpected download destination")
            total = int(response.headers.get("Content-Length", 0))
            if total > MAX_BYTES:
                raise ValueError("Connection tools download is too large")
            received = 0
            while chunk := response.read(256 * 1024):
                received += len(chunk)
                if received > MAX_BYTES:
                    raise ValueError("Connection tools download is too large")
                dst.write(chunk)
                digest.update(chunk)
                progress("downloading", f"Downloaded {received // 1048576} MB",
                         min(90, int(received / total * 90)) if total else None)
        progress("installing", "Installing connection tools locally", 95)
        staged = Path(work) / "platform-tools"
        unpack(archive, staged)
        (staged / "source.json").write_text(json.dumps({"url": URL, "sha256": digest.hexdigest()}))
        dest = parent / "platform-tools"
        if dest.exists():
            raise RuntimeError("An incomplete tools folder exists; rename it before retrying")
        os.replace(staged, dest)
    progress("ready", "Connection tools installed", 100)
    return installed(root)
