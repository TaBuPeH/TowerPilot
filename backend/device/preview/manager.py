"""One preview session, with explicit native → scrcpy → screenshot fallback.

This is deliberately not used by automation: a package-specific native capture
can see the game behind a human-owned foreground app. Input guards continue to
use the existing device.capture screenshot path.
"""
from __future__ import annotations

from collections.abc import Callable
import re

import numpy as np

from device.preview.common import PreviewCleanupError, PreviewError, PreviewSource, PreviewUnavailable, options


class _ScreenshotPreview:
    def __init__(self, capture: Callable[[], np.ndarray]):
        self._capture = capture

    def read(self, timeout: float = 2.0) -> np.ndarray:
        return self._capture()

    def close(self) -> None:
        return None


class PreviewCapture:
    """Own and close only this response's source; never queue historical frames.

Failed accelerated sources are not retried until reconnect. This bounds startup
probes and keeps a broken DLL or optional package out of the hot screenshot loop.
The last screenshot source may retry on the route's existing offline backoff.
"""

    def __init__(self, *, serial: str, adb_exe: str, settings: dict | None,
                 raw_capture: Callable[[], np.ndarray], physical_display: Callable[[], str | None],
                 requested_backend: str | None = None, fps: float | None = None,
                 explicit_display: bool = False, event: Callable[..., None] | None = None):
        self.options = options(settings)
        self.requested = requested_backend if requested_backend is not None else self.options["backend"]
        if self.requested not in ("auto", "mumu", "scrcpy", "adb"):
            raise ValueError("backend must be auto, mumu, scrcpy, or adb")
        self.fps = self.options["fps"] if fps is None else fps
        self.serial = serial
        self.adb_exe = adb_exe
        self._raw_capture = raw_capture
        self._physical_display = physical_display
        self._explicit_display = explicit_display
        self._event = event or (lambda *args, **kwargs: None)
        self._candidates = {
            "auto": ("mumu", "scrcpy", "adb"), "mumu": ("mumu", "scrcpy", "adb"),
            "scrcpy": ("scrcpy", "adb"), "adb": ("adb",),
        }[self.requested]
        self._position = 0
        self._source: PreviewSource | None = None
        self.backend: str | None = None
        self.failures: list[dict[str, str]] = []
        self._closed = False
        self._cleanup_pending = False

    @property
    def effective_fps(self) -> float:
        return min(self.fps, 4) if self.backend == "adb" else self.fps

    def _logical_display(self) -> int:
        from device import adbclient, displays
        physical = self._physical_display()
        if physical is None:
            return 0
        if not re.fullmatch(r"[0-9]+", str(physical)):
            raise PreviewUnavailable("Configured physical display must contain only digits")
        text = adbclient.shell(self.serial, "dumpsys display", timeout=2).decode(errors="replace")
        logical = displays.logical_display(text, str(physical))
        if logical is None:
            raise PreviewUnavailable("Cannot resolve the game's logical display for scrcpy")
        return logical

    def _open(self, backend: str) -> PreviewSource:
        if backend == "mumu":
            if self._explicit_display:
                raise PreviewUnavailable("An explicit physical display requires scrcpy or screenshots")
            from device.preview.mumu import MuMuPreview, discover_target
            from device.overlays import GAME_PKG
            target = discover_target(self.serial, self.adb_exe, self.options["mumu"])
            return MuMuPreview(target["install_dir"], target["index"], GAME_PKG)
        if backend == "scrcpy":
            from device.preview.scrcpy_package import status
            available = status()
            if not available["available"]:
                raise PreviewUnavailable(available["reason"])
            from device.preview.scrcpy import ScrcpyPreview
            return ScrcpyPreview(self.serial, self._logical_display(), available["server_path"],
                                 fps=self.fps, bitrate=self.options["scrcpy"]["bitrate"])
        return _ScreenshotPreview(self._raw_capture)

    def _failed(self, backend: str, exc: Exception, stage: str = "capture") -> None:
        row = {"backend": backend, "reason": str(exc), "stage": stage}
        if row not in self.failures:
            self.failures = [r for r in self.failures if r["backend"] != backend or r["stage"] != stage]
            self.failures.append(row)
            self._event("preview_backend_failed", backend=backend, reason=str(exc),
                        error_type=type(exc).__name__, stage=stage)

    def _close_source(self) -> None:
        source = self._source
        if source is not None:
            try:
                source.close()
            except Exception as exc:
                self._failed(self.backend or "unknown", exc, "cleanup")
                raise PreviewError(f"Preview cleanup failed: {exc}") from exc
            self._source = None

    def _discard_failed_source(self) -> None:
        # Retain ownership until cleanup succeeds. A later read may retry
        # cleanup, but must never read from or overlap the failed source.
        self._close_source()
        self._cleanup_pending = False
        if self.backend != "adb":
            self._position += 1

    def read(self, timeout: float = 2.0) -> np.ndarray:
        if self._closed:
            raise PreviewError("Preview is closed")
        if self._cleanup_pending:
            self._discard_failed_source()
        while self._position < len(self._candidates):
            name = self._candidates[self._position]
            try:
                if self._source is None:
                    self.backend = name
                    self._source = self._open(name)
                frame = self._source.read(timeout=timeout)
                if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
                    raise PreviewError("Preview source returned invalid BGR pixels")
                if getattr(self, "_announced", None) != name:
                    self._announced = name
                    self._event("preview_backend_selected", backend=name, requested_backend=self.requested,
                                effective_fps=self.effective_fps, fallback=bool(self.failures))
                return frame
            except Exception as exc:
                if isinstance(exc, PreviewCleanupError):
                    self._source = exc.source
                self._failed(name, exc)
                self._cleanup_pending = True
                self._discard_failed_source()
                if name == "adb":
                    raise PreviewError(f"Screenshot fallback failed: {exc}") from exc
        raise PreviewError("No preview source available")

    def status(self) -> dict:
        return {"backend": self.backend, "requested_backend": self.requested,
                "effective_fps": self.effective_fps, "backend_failures": list(self.failures),
                "frame_age_ms": getattr(self._source, "frame_age_ms", None)}

    def close(self) -> None:
        self._closed = True
        self._close_source()
