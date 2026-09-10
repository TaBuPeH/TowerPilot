"""Atomic local reports tolerant of short-lived Windows reader locks."""
import json
import os
from pathlib import Path
import tempfile
import time


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=1)
        for attempt in range(6):
            try:
                os.replace(name, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.05 * 2**attempt)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
