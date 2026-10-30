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
import base64
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import cv2

import settings
from runtime import priority
from runtime.stage_clock import clock

WINOCR = settings.ROOT / "tools" / "winocr.ps1"
# One class above normal for the warm reader: CPU-bound work at normal
# priority (builds, test suites) otherwise starves a short-deadline read.
URGENT_PRIORITY = priority.ABOVE_NORMAL
# An abandoned warm reader gets this long to exit on its stdin EOF. One still
# running after it (a WinRT call that never returns) is terminated, the bound
# subprocess.run gives a cold read. No new warm reader starts while an
# abandoned one is alive: that read takes the cold path instead.
ABANDON_GRACE_SEC = 1.0

# Where the time of the latest read_texts call went, for a caller that has to say why a read ran long:
# path ('warm', or 'cold_after_warm_unavailable' / 'cold'), warm_sec (everything the warm reader took, its lock and
# any restart included), reclaim_sec (terminating an abandoned reader, inside warm_sec) and cold_sec (the
# PowerShell run). Recorded only; nothing here changes how a read is done. The collector reads on one thread, so
# one dict is enough: read_texts clears it, take_timing() hands it over once.
_TIMING = {}


def _stage(name, started):
    elapsed = time.monotonic() - started
    _TIMING[name] = round(_TIMING.get(name, 0.0) + elapsed, 3)
    clock.add('ocr.' + name, elapsed)       # the collector's per-pass timers (runtime/stage_clock.py)


def take_timing():
    """The stage timers of the latest read_texts call, once ({} when none was recorded since the last take)."""
    taken = dict(_TIMING)
    _TIMING.clear()
    return taken


class _WarmUnavailable(Exception):
    """No warm reader may start now; the caller reads cold."""


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def read_lines(bgr, scale: float = 3.0, *, timeout: float = 120,
               raise_on_error: bool = False) -> list[tuple[int, int, str]]:
    """OCR one crop, upscaled by `scale`. Rows (y, x, text) in reading order;
    empty when the engine finds nothing or is unavailable.
    `raise_on_error` lets deadline-sensitive callers distinguish incomplete
    recognition from an empty reading; existing callers keep their behavior.
    """
    deadline = time.monotonic()+timeout if raise_on_error else None
    img = bgr
    if scale != 1.0:
        img = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    fd, path = tempfile.mkstemp(suffix=".png", prefix="tp_ocr_")
    os.close(fd)
    try:
        written = cv2.imwrite(path, img)
        if raise_on_error and not written:
            raise OSError('Windows OCR crop write failed')
        if deadline is not None:
            timeout = deadline-time.monotonic()
            if timeout <= 0:
                raise subprocess.TimeoutExpired('Windows OCR preparation', 0)
        p = settings.run_hidden(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(WINOCR), path],
            capture_output=True, timeout=timeout)
        if raise_on_error and (p.returncode != 0 or p.stderr.strip()):
            raise subprocess.CalledProcessError(p.returncode, p.args,
                                                output=p.stdout, stderr=p.stderr)
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
    except subprocess.TimeoutExpired:
        if raise_on_error:
            raise
        return []
    except (OSError, subprocess.SubprocessError):
        if raise_on_error:
            raise
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
    return _majority_text(readings)


def _majority_text(readings):
    if not readings:
        return ""
    counts = Counter(readings)
    best = max(counts.items(), key=lambda kv: (kv[1], sum(c.isdigit() for c in kv[0]), len(kv[0])))
    return fix_l1(best[0])


class _WarmReader:
    """One long-lived hidden `winocr.ps1 -Serve` process for short-deadline reads.

    A cold read starts PowerShell and loads the WinRT OCR types on every call
    (about 1 s of a 2 s budget under load); this process pays that once. It
    recognizes with the same Read-Image function as a cold read. A reply that
    misses its deadline, or any malformed reply, abandons the process: its
    stdin is closed, so it exits after its current image, and the next read
    starts a fresh one; a late reply is never read. An abandoned reader still
    running after ABANDON_GRACE_SEC is terminated through runtime.process_handle
    (our own held Popen). A new reader starts only when no abandoned one is
    alive, so at most one abandoned reader exists at a time; otherwise
    recognize raises _WarmUnavailable and read_texts reads cold.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._lines = None
        self._next_id = 0
        self._abandoned = []          # (Popen, monotonic time it was abandoned)

    def _reclaim(self):
        """True when no abandoned reader is alive (terminating any past its grace)."""
        from runtime import process_handle
        alive = []
        for proc, since in self._abandoned:
            if proc.poll() is None and time.monotonic() - since >= ABANDON_GRACE_SEC:
                process_handle.terminate_owned(proc.pid, timeout=1.0)
                try:
                    proc.wait(timeout=.5)      # termination is asynchronous; reap it before counting
                except subprocess.TimeoutExpired:
                    pass
            if proc.poll() is None:
                alive.append((proc, since))
        self._abandoned = alive
        return not alive

    def _start(self):
        proc = settings.spawn_hidden(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(WINOCR), "-Serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=settings.NO_WINDOW | URGENT_PRIORITY)
        lines = queue.Queue()

        def drain():            # keeps the pipe empty even after the process is abandoned
            for raw in iter(proc.stdout.readline, b""):
                lines.put(_decode(raw).rstrip("\r\n"))
            lines.put(None)
        threading.Thread(target=drain, name="winocr-warm", daemon=True).start()
        self._proc, self._lines = proc, lines

    def abandon(self):
        proc, self._proc, self._lines = self._proc, None, None
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        self._abandoned.append((proc, time.monotonic()))

    def recognize(self, entries, deadline):
        """stdout lines of one reply, in the cold batch's TP_OCR_IMAGE framing."""
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise subprocess.TimeoutExpired("Windows OCR warm reader (busy)", 0)
        try:
            try:
                if self._proc is None or self._proc.poll() is not None:
                    self.abandon()
                    reclaim_started = time.monotonic()
                    reclaimed = self._reclaim()
                    _stage('reclaim_sec', reclaim_started)
                    if not reclaimed:
                        raise _WarmUnavailable()
                    self._start()
                self._next_id += 1
                request_id = str(self._next_id)
                self._proc.stdin.write((json.dumps({"id": request_id, "images": entries}) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
                reply, begun = [], False
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired("Windows OCR warm reader", 0)
                    try:
                        line = self._lines.get(timeout=remaining)
                    except queue.Empty:
                        raise subprocess.TimeoutExpired("Windows OCR warm reader", remaining) from None
                    if line is None:
                        raise subprocess.SubprocessError("Windows OCR warm reader exited")
                    if line == "TP_OCR_BEGIN\t" + request_id and not begun:
                        begun = True
                    elif line == "TP_OCR_DONE\t" + request_id and begun:
                        return reply
                    elif begun and not line.startswith(("TP_OCR_BEGIN", "TP_OCR_DONE", "TP_OCR_ERROR")):
                        reply.append(line)
                    else:
                        raise subprocess.SubprocessError(f"Windows OCR warm reader replied {line!r}")
            except _WarmUnavailable:
                raise
            except BaseException:
                self.abandon()
                raise
        finally:
            self._lock.release()


_WARM = _WarmReader()


def _batch_rows(lines, count):
    """Rows per image from TP_OCR_IMAGE-framed reader output (cold or warm)."""
    rows = [[] for _ in range(count)]
    active = None
    markers = []
    for line in lines:
        if line.startswith('TP_OCR_IMAGE\t'):
            active = int(line.split('\t', 1)[1])
            if active != len(markers) or active >= len(rows):
                raise ValueError('Windows OCR returned an invalid image index')
            markers.append(active)
            continue
        parts = line.split('\t', 2)
        if active is not None and len(parts) == 3:
            rows[active].append((int(parts[0]), int(parts[1]), parts[2].strip()))
    if len(markers) != count:
        raise ValueError('Windows OCR batch ended before all images were recognized')
    return rows


def read_texts(crops, scales=(2.0, 3.0, 4.0), *, timeout=120,
               raise_on_error=False, urgent=False):
    """OCR independent crops/scales with one hidden PowerShell invocation.

    Every image is recognized separately by the same winocr.ps1 reader used
    by read_lines. This preserves crop context and wrapped text without OCR
    merging adjacent labels in a contact sheet. Only process startup is shared;
    each crop still votes across all requested scales exactly as read_text does.
    `urgent` sends the batch to the warm reader (_WarmReader, URGENT_PRIORITY)
    for a read whose deadline is a few seconds; the reading itself is the same.
    """
    crops, scales = list(crops), tuple(scales)
    if not crops:
        return []
    if not scales or any(scale <= 0 for scale in scales):
        raise ValueError('OCR scales must be positive')
    if any(crop is None or not crop.size for crop in crops):
        raise ValueError('Cannot batch an empty OCR crop')
    deadline = time.monotonic() + timeout if raise_on_error else None
    _TIMING.clear()
    try:
        # An abandoned warm reader may still hold its current image for a moment;
        # its leftover crop must not turn a timeout into a different failure.
        with tempfile.TemporaryDirectory(prefix='tp_ocr_batch_', ignore_cleanup_errors=urgent) as directory:
            entries = []
            for index, crop in enumerate(crops):
                for scale_index, scale in enumerate(scales):
                    image = crop if scale == 1.0 else cv2.resize(
                        crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    path = Path(directory) / f'{index}_{scale_index}.png'
                    if not cv2.imwrite(str(path), image):
                        raise OSError('Windows OCR crop write failed')
                    entries.append({'path': str(path), 'index': len(entries)})
            if urgent:
                remaining = timeout if deadline is None else deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired('Windows OCR preparation', 0)
                _TIMING['path'] = 'warm'
                warm_started = time.monotonic()
                try:
                    try:
                        lines = _WARM.recognize(entries, time.monotonic() + remaining)
                    finally:
                        _stage('warm_sec', warm_started)
                    rows = _batch_rows(lines, len(entries))
                    return _vote(rows, len(crops), scales)
                except _WarmUnavailable:
                    _TIMING['path'] = 'cold_after_warm_unavailable'    # an abandoned reader is still alive: read cold, as before r15d
            manifest = Path(directory) / 'images.json'
            manifest.write_text(json.dumps(entries), encoding='utf-8')
            # EncodedCommand keeps native paths out of shell interpolation.
            # PowerShell string literals escape only a single quote by doubling it.
            literal = lambda value: "'" + str(value).replace("'", "''") + "'"
            script = (
                "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
                f"$images = Get-Content -Raw -LiteralPath {literal(manifest)} | ConvertFrom-Json; "
                "foreach ($entry in $images) { "
                "Write-Output ('TP_OCR_IMAGE' + [char]9 + $entry.index); "
                f"& {literal(WINOCR)} $entry.path; "
                "}"
            )
            encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
            remaining = timeout if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('Windows OCR preparation', 0)
            _TIMING.setdefault('path', 'cold')
            cold_started = time.monotonic()
            try:
                p = settings.run_hidden(
                    ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                     '-EncodedCommand', encoded], capture_output=True, timeout=remaining)
            finally:
                _stage('cold_sec', cold_started)
            if p.returncode != 0 or p.stderr.strip():
                raise subprocess.CalledProcessError(p.returncode, p.args,
                                                    output=p.stdout, stderr=p.stderr)
            return _vote(_batch_rows(_decode(p.stdout).splitlines(), len(entries)), len(crops), scales)
    except (OSError, ValueError, subprocess.SubprocessError):
        if raise_on_error:
            raise
        return [''] * len(crops)


def _vote(rows, count, scales):
    result = []
    for index in range(count):
        readings = []
        for scale_index in range(len(scales)):
            text = ' '.join(text for _, _, text in sorted(rows[index * len(scales) + scale_index]))
            text = ' '.join(text.split())
            if text:
                readings.append(text)
        result.append(_majority_text(readings))
    return result


def fix_l1(text: str) -> str:
    """'Tourney Pl' -> 'Tourney P1': a two-character token of a capital and
    an l/I is the engine's reading of a capital followed by the digit one
    (seen 2026-09-06); no real preset name is spelt that way."""
    return re.sub(r"\b([A-Z])[lI]\b", r"\g<1>1", text)


def available() -> bool:
    return Path(WINOCR).exists() and os.name == "nt"
