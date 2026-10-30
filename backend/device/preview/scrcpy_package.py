"""Explicit, optional installation of the pinned official scrcpy server.

Importing this module and checking status never download anything.
Run from backend: python -m device.preview.scrcpy_package install
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import urllib.request

from .common import PreviewUnavailable

VERSION = "5.0.1"
SERVER_SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"
SERVER_URL = f"https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/scrcpy-server-v{VERSION}"
_MAX_SERVER_BYTES = 16 * 1024 * 1024


def default_server_path() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "scrcpy" / VERSION / f"scrcpy-server-v{VERSION}"


def verify_server(server_path: str | Path) -> Path:
    """Validate a local jar before any device operation."""
    path = Path(server_path)
    try:
        if path.stat().st_size > _MAX_SERVER_BYTES:
            raise PreviewUnavailable("scrcpy server exceeds the expected size limit")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PreviewUnavailable(f"scrcpy server missing or unreadable: {path}") from exc
    if actual != SERVER_SHA256:
        raise PreviewUnavailable(f"scrcpy {VERSION} server checksum mismatch: {path}")
    return path


def status(server_path: str | Path | None = None) -> dict[str, object]:
    path = default_server_path() if server_path is None else Path(server_path)
    try:
        verify_server(path)
    except PreviewUnavailable as exc:
        installed, reason = False, str(exc)
    else:
        installed, reason = True, ""
    try:
        decoder = importlib.util.find_spec("av") is not None
    except (ImportError, ValueError):
        decoder = False
    if not decoder:
        reason = "; ".join(filter(None, (reason, "PyAV missing; install requirements-scrcpy.txt")))
    return {"installed": installed, "decoder_available": decoder,
            "available": installed and decoder, "reason": reason,
            "version": VERSION, "server_path": str(path)}


def install(server_path: str | Path | None = None) -> Path:
    """Download only on explicit invocation; publish verified bytes atomically."""
    target = default_server_path() if server_path is None else Path(server_path)
    if target.is_file():
        try:
            return verify_server(target)
        except PreviewUnavailable:
            pass  # An explicit install repairs an invalid local package.
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".scrcpy-", delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(SERVER_URL, timeout=30) as response:
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > _MAX_SERVER_BYTES:
                        raise PreviewUnavailable("scrcpy download exceeds the expected size limit")
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        verify_server(temporary)
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status"))
    args = parser.parse_args()
    try:
        if args.command == "install":
            install()
        result = status()
    except (OSError, PreviewUnavailable) as exc:
        parser.exit(1, f"scrcpy package: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
