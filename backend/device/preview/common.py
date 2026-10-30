"""Shared preview contracts, defaults, and raw screenshot decoding."""
from __future__ import annotations

import copy
import math
import struct
from typing import Protocol

import numpy as np


class PreviewError(RuntimeError):
    """A preview source failed; callers must not reuse its previous frame."""


class PreviewUnavailable(PreviewError):
    """This source cannot be used with the current installation or device."""


class PreviewSource(Protocol):
    def read(self, timeout: float = 2.0) -> np.ndarray: ...
    def close(self) -> None: ...


class PreviewCleanupError(PreviewError):
    """Failed construction whose owned resources still require cleanup."""

    def __init__(self, message: str, source: PreviewSource) -> None:
        super().__init__(message)
        self.source = source


BACKENDS = ("auto", "mumu", "scrcpy", "adb")
DEFAULTS = {
    "backend": "auto",
    "fps": 15,
    "mumu": {"install_dir": "", "index": -1},
    "scrcpy": {"bitrate": 8_000_000},
}


def options(value: dict | None = None) -> dict:
    """Merge only known settings; reject malformed machine-file values early."""
    result = copy.deepcopy(DEFAULTS)
    if value is not None:
        if not isinstance(value, dict):
            raise ValueError("preview must be an object")
        for key in ("backend", "fps"):
            if key in value:
                result[key] = value[key]
        for key in ("mumu", "scrcpy"):
            if key in value:
                if not isinstance(value[key], dict):
                    raise ValueError(f"preview.{key} must be an object")
                result[key].update(value[key])
    if result["backend"] not in BACKENDS:
        raise ValueError("preview.backend must be auto, mumu, scrcpy, or adb")
    fps = result["fps"]
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0.5 <= fps <= 30:
        raise ValueError("preview.fps must be between 0.5 and 30")
    index = result["mumu"]["index"]
    if isinstance(index, bool) or not isinstance(index, int) or not -1 <= index <= 1024:
        raise ValueError("preview.mumu.index must be -1 (automatic) or 0–1024")
    path = result["mumu"]["install_dir"]
    if not isinstance(path, str) or "\x00" in path or len(path) > 4096:
        raise ValueError("preview.mumu.install_dir must be a folder path")
    bitrate = result["scrcpy"]["bitrate"]
    if isinstance(bitrate, bool) or not isinstance(bitrate, int) or not 1_000_000 <= bitrate <= 40_000_000:
        raise ValueError("preview.scrcpy.bitrate must be between 1000000 and 40000000")
    return result


def decode_raw(raw: bytes) -> np.ndarray:
    """Decode Android RGBA screenshots, including its optional warning prefix."""
    import cv2
    for _ in range(4):
        if len(raw) >= 12:
            width, height, pixel_format = struct.unpack_from("<III", raw)
            header = len(raw) - width * height * 4
            if 0 < width <= 10000 and 0 < height <= 10000 and header in (12, 16):
                if pixel_format not in (1, 2):
                    raise ValueError(f"unsupported screencap pixel format: {pixel_format}")
                rgba = np.frombuffer(raw, np.uint8, offset=header).reshape(height, width, 4)
                return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        if not raw.startswith(b"[Warning]"):
            break
        newline = raw.find(b"\n", 0, 512)
        if newline < 0:
            break
        raw = raw[newline + 1:]
    raise ValueError("invalid raw screencap (device offline or display changed?)")
