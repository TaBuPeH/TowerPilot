"""Atomic local reports tolerant of short-lived Windows reader locks."""
import json
import os
from pathlib import Path
import tempfile
import time


def atomic_json(path, value):
    """atomic_text of `value` as indented JSON."""
    atomic_text(path, json.dumps(value, indent=1))


def atomic_text(path, text):
    """Replace `path` with `text` (UTF-8, text mode) so a reader sees the whole
    old file or the whole new one: written to a temp file in the same folder,
    then os.replace'd over `path`. A PermissionError from replace (a Windows
    reader holding the file) is retried 5 times with 0.05-0.8 s backoff, then
    raised with `path` untouched. The temp file never outlives the call."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
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
