"""JSONL event log + trigger screenshots.

The log file is named LAZILY (on the first event) because the process binds
its instance after import: settings.select_instance() runs in __main__, by
which time this module is already imported. Naming at import time would stamp
every instance's log with whatever `active_instance` happened to be in the
config file rather than the one this process actually drives.
"""
import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import cv2

from settings import ROOT, CONFIG
from runtime import diagnostics

_LOG_DIR = ROOT / CONFIG["logging"]["dir"]
_run_file: Path | None = None
_battle_context: dict = {}
_destination = ContextVar('diagnostic_destination', default=None)
context = diagnostics.context


def current_context():
    return {**_battle_context, **diagnostics.current_context()}


@contextmanager
def destination(directory):
    """Route this scope to a relative subdirectory of the existing log root."""
    directory = Path(directory)
    if directory.is_absolute() or '..' in directory.parts:
        raise ValueError('Log destination must be a relative logs subdirectory')
    token = _destination.set(directory)
    try:
        yield
    finally:
        _destination.reset(token)


def set_battle_context(context: dict | None):
    """Attach immutable battle identity to subsequent driver events."""
    global _battle_context
    allowed = {'battle_id', 'driver_segment', 'envelope_hash'}
    if context is not None and set(context) - allowed:
        raise ValueError('Unsupported battle log context')
    _battle_context = dict(context or {})


def run_file() -> Path:
    """Per-instance log path, fixed on first use: logs/<instance>/events_*.jsonl.
    The tray app tails the newest file in an instance's directory."""
    global _run_file
    directory = _destination.get()
    if directory is not None:
        d = _LOG_DIR / directory
        d.mkdir(parents=True, exist_ok=True)
        return d / diagnostics.file_name()
    if _run_file is None:
        d = _LOG_DIR / CONFIG["active_instance"]
        d.mkdir(parents=True, exist_ok=True)
        _run_file = d / diagnostics.file_name()
    return _run_file


def event(kind: str, **fields):
    """Preserve the driver log destination and battle identity on shared events."""
    values = {'instance': CONFIG.get('active_instance'), **current_context(), **fields}
    return diagnostics.write_event(run_file, kind, **values)


@contextmanager
def attempt(name, **fields):
    """Record an invocation through the same driver destination as event()."""
    with diagnostics.attempt(name, emit=event, **fields) as handle:
        yield handle


def shot(frame, tag: str):
    if not CONFIG["logging"]["screenshot_on_trigger"]:
        return None
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}_{uuid.uuid4().hex[:8]}.png"
    try:
        path = run_file().parent / name
        if not cv2.imwrite(str(path), frame):
            raise OSError('cv2.imwrite returned False')
    except Exception as exc:
        event('shot_failed', tag=tag, filename=name, exception_type=type(exc).__name__,
              exception_message=str(exc), traceback=traceback.format_exc())
        return None
    return name
