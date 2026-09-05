"""JSONL event log + trigger screenshots.

The log file is named LAZILY (on the first event) because the process binds
its instance after import: settings.select_instance() runs in __main__, by
which time this module is already imported. Naming at import time would stamp
every instance's log with whatever `active_instance` happened to be in the
config file rather than the one this process actually drives.
"""
import json
import time
from pathlib import Path
import cv2

from settings import ROOT, CONFIG

_LOG_DIR = ROOT / CONFIG["logging"]["dir"]
_LOG_DIR.mkdir(exist_ok=True)
_run_file: Path | None = None
_started = time.strftime("%Y%m%d_%H%M%S")


def run_file() -> Path:
    """Per-instance log path, fixed on first use: logs/<instance>/events_*.jsonl.
    The tray app tails the newest file in an instance's directory."""
    global _run_file
    if _run_file is None:
        d = _LOG_DIR / CONFIG["active_instance"]
        d.mkdir(parents=True, exist_ok=True)
        _run_file = d / f"events_{_started}.jsonl"
    return _run_file


def event(kind: str, **fields):
    rec = {"t": round(time.time(), 3), "kind": kind, **fields}
    with run_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def shot(frame, tag: str):
    if not CONFIG["logging"]["screenshot_on_trigger"]:
        return None
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
    path = run_file().parent / name
    cv2.imwrite(str(path), frame)
    return name
