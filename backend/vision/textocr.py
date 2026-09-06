"""Read the player's own text off a crop with Windows' built-in OCR.

The names the game lets a player type - card preset tabs, preset picker
rows, category preset tabs - cannot ship with the code and cannot be matched
by template before their template exists. tools/winocr.ps1 drives the WinRT
OCR engine (Windows PowerShell 5.1, present on every Windows 10/11); this
wraps it for one crop at a time, always window-suppressed (a pythonw parent
would otherwise flash a console per call).

Module detail panels and the picker read cleanly at 2-3x; the only
ambiguity seen is l/1 in a label such as "Tourney P1" (2026-09-06), so
`read_text` runs several scales and keeps the majority reading.
"""
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import cv2

import settings

WINOCR = settings.ROOT / "tools" / "winocr.ps1"


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def read_lines(bgr, scale: float = 3.0) -> list[tuple[int, int, str]]:
    """OCR one crop, upscaled by `scale`. Rows (y, x, text) in reading order;
    empty when the engine finds nothing or is unavailable."""
    img = bgr
    if scale != 1.0:
        img = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    fd, path = tempfile.mkstemp(suffix=".png", prefix="tp_ocr_")
    os.close(fd)
    try:
        cv2.imwrite(path, img)
        p = settings.run_hidden(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(WINOCR), path],
            capture_output=True, timeout=120)
        rows = []
        for line in _decode(p.stdout).splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                try:
                    rows.append((int(parts[0]), int(parts[1]), parts[2].strip()))
                except ValueError:
                    pass
        rows.sort()
        return rows
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def read_text(bgr, scales=(2.0, 3.0, 4.0)) -> str:
    """The crop's text as one line: the majority reading across `scales`
    (ties go to the reading with the most digits, then the longest)."""
    readings = []
    for sc in scales:
        text = " ".join(t for _, _, t in read_lines(bgr, sc)).strip()
        if text:
            readings.append(" ".join(text.split()))
    if not readings:
        return ""
    counts = Counter(readings)
    best = max(counts.items(), key=lambda kv: (kv[1], sum(c.isdigit() for c in kv[0]), len(kv[0])))
    return fix_l1(best[0])


def fix_l1(text: str) -> str:
    """'Tourney Pl' -> 'Tourney P1': a two-character token of a capital and
    an l/I is the engine's reading of a capital followed by the digit one
    (seen 2026-09-06); no real preset name is spelt that way."""
    return re.sub(r"\b([A-Z])[lI]\b", r"\g<1>1", text)


def available() -> bool:
    return Path(WINOCR).exists() and os.name == "nt"
