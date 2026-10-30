"""Frame capture from a MuMu instance via adb screencap (works minimized)."""
import subprocess
import time
import traceback
import uuid
import cv2
import numpy as np

from settings import CONFIG, adb_args, run_hidden
from runtime import logger
from device import adbclient


class CaptureError(RuntimeError):
    pass


_resolved_serial = None

def refresh_display(serial: str | None = None, *,
                    deadline: float | None = None) -> str | None:
    """Re-derive the instance's game display id + input index from dumpsys.

    MuMu recreates its displays whenever the emulator (or just the app's
    display) restarts, and both ids CHANGE. The game lives on the secondary
    mumuscreen (port != 0 in SurfaceFlinger); the logical display wrapping
    it is the input index for taps. Updates the RUNTIME instance config
    only - each process re-derives on demand, so a stale config.yaml costs
    one dumpsys, not a run. Returns the fresh display id, or None for a
    single-display instance (no `display` configured - nothing to derive).
    Raises CaptureError when the screens cannot be parsed: a guessed
    display is a blind tap waiting to happen.
    """
    from device import displays
    from settings import instance as _inst
    adbclient.check_deadline(deadline)
    inst = _inst()
    configured = bool(inst.get("display"))
    serial = serial or inst["serial"]
    options = {"deadline": deadline} if deadline is not None else {}
    sf = adbclient.shell(serial, "dumpsys SurfaceFlinger --display-id",
                         timeout=10, **options)
    adbclient.check_deadline(deadline)
    sf = sf.decode(errors="replace")
    adbclient.check_deadline(deadline)
    screens = displays.parse_screens(sf)
    adbclient.check_deadline(deadline)
    if not screens and not configured:
        return None                     # not MuMu (BlueStacks etc.): one display
    disp = displays.secondary_display(screens)
    adbclient.check_deadline(deadline)
    if disp is None:
        if not configured:
            return None                 # single-screen MuMu: the default display
        raise CaptureError(
            f"display refresh: no secondary mumuscreen in SurfaceFlinger "
            f"({len(screens)} screen(s) listed)")
    dd = adbclient.shell(serial, "dumpsys display", timeout=10, **options)
    adbclient.check_deadline(deadline)
    dd = dd.decode(errors="replace")
    adbclient.check_deadline(deadline)
    logical = displays.logical_display(dd, disp)
    adbclient.check_deadline(deadline)
    if logical is None:
        raise CaptureError(
            f"display refresh: no logical display wraps local:{disp}")
    old = (inst.get("display"), inst.get("input_display"))
    inst["display"] = disp
    inst["input_display"] = logical
    from runtime import logger
    logger.event("display_refreshed", display=disp,
                 input_display=inst["input_display"],
                 was_display=old[0], was_input=old[1])
    adbclient.check_deadline(deadline)
    return disp


def grab(serial: str | None = None, display: str | None = None, *, discover=False,
         deadline: float | None = None) -> np.ndarray:
    """Capture a frame, optionally before an absolute monotonic deadline.

    Deadline calls share the cutoff across discovery, retries and conversion;
    they raise TimeoutError rather than return late evidence. They do not run
    device recovery, whose host queries/settling have no absolute bound.
    Ordinary callers retain recovery and the default resolution lock.
    """
    adbclient.check_deadline(deadline)
    started = time.monotonic_ns()
    diagnostic = {'retries': 0, 'started': started}
    with logger.context(capture_id=uuid.uuid4().hex):
        try:
            frame = _grab(serial, display, discover=discover, diagnostic=diagnostic,
                          deadline=deadline)
            adbclient.check_deadline(deadline)
        except Exception as exc:
            logger.event('capture_failed', serial=serial, display=display,
                         retries=diagnostic['retries'],
                         duration_ms=round((time.monotonic_ns() - started) / 1_000_000, 3),
                         exception_type=type(exc).__name__, exception_message=str(exc),
                         traceback=''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            raise
        if diagnostic['retries']:
            logger.event('capture_recovered', retries=diagnostic['retries'],
                         duration_ms=round((time.monotonic_ns() - started) / 1_000_000, 3))
    adbclient.check_deadline(deadline)
    return frame


def _capture_retry(diagnostic, **fields):
    diagnostic['retries'] += 1
    logger.event('capture_retry', retry=diagnostic['retries'],
                 duration_ms=round((time.monotonic_ns() - diagnostic['started']) / 1_000_000, 3),
                 **fields)


def _grab(serial: str | None = None, display: str | None = None, *, discover=False,
          diagnostic, deadline: float | None = None) -> np.ndarray:
    """Return the current screen as a BGR ndarray at native resolution.

    Uses RAW screencap (no -p): ~344ms vs ~700ms for PNG - the on-device PNG
    encode dominates, the extra ~9MB over loopback TCP is nearly free. Raw
    layout: 4-byte LE width, height, pixel format, (colorspace,) then RGBA.
    display: physical display id for multi-display instances (Main Tower's
    game runs on a secondary display; the default display is the launcher)."""
    adbclient.check_deadline(deadline)
    options = {"deadline": deadline} if deadline is not None else {}
    from_instance = serial is None and display is None
    if from_instance:
        from settings import instance
        global _resolved_serial
        bound_serial = instance()["serial"]
        if _resolved_serial != bound_serial:
            # The launcher may have exactly the game's dimensions. Select the
            # game display before the first frame, not only on a size mismatch.
            refresh_display(bound_serial, **options)
            adbclient.check_deadline(deadline)
            _resolved_serial = bound_serial
        display = instance().get("display")   # active instance's game display
    # Straight to the adb SERVER socket - no adb.exe per frame. Spawning a
    # process ~3x a second is what exhausted Windows socket buffers and then
    # dropped the device mid-run, twice. Same 247ms/frame either way (the 11MB
    # loopback transfer dominates, not the spawn), so this buys stability, not
    # speed. One retry after adbclient.recover (host:connect for a missing
    # device; for an offline one host:disconnect first, but only once this
    # process has had it offline for adbclient._OFFLINE_STALE_S, otherwise
    # just the connect; it never starts a server - no spawn from here),
    # because a dropped device is the failure that actually happens and every
    # caller wants it handled here rather than reimplementing recovery.
    from settings import instance as _inst
    serial = serial or _inst()["serial"]
    cmd = "screencap" + (f" -d {display}" if display else "")
    buf = b""
    for attempt in (1, 2):
        adbclient.check_deadline(deadline)
        try:
            buf = adbclient.exec_out(serial, cmd, timeout=15, **options)
            adbclient.check_deadline(deadline)
            if len(buf) >= 1000:
                break
            if attempt == 1:
                _capture_retry(diagnostic, serial=serial, display=display,
                               reason='short_payload', bytes_received=len(buf))
        except (OSError, adbclient.AdbError) as e:
            adbclient.check_deadline(deadline)
            if deadline is not None and isinstance(e, TimeoutError):
                raise
            if attempt == 2:
                raise CaptureError(f"screencap failed: {e}") from e
            _capture_retry(diagnostic, serial=serial, display=display,
                           exception_type=type(e).__name__, exception_message=str(e),
                           reason='transport_error')
            # Recovery uses its own host-query/settle budgets. Deadline calls
            # may retry the capture, but must not enter that unbounded path.
            if deadline is None:
                adbclient.recover(serial)
    adbclient.check_deadline(deadline)
    if len(buf) < 1000:
        # THE MuMu DISPLAY-CHURN SIGNATURE: the emulator recreates its
        # displays on every restart and the ids change, so a configured
        # display id yields this tiny "Failed to take screenshot" payload
        # while plain adb still works (2026-08-27 twice, 2026-08-28 - it
        # killed the boot pipeline's ad cleaner). Re-derive from dumpsys
        # once and retry; only for the instance's own display, never for
        # an explicitly requested one (wizard probes ask for exact ids).
        if from_instance and display and refresh_display(serial, **options) not in (None, display):
            adbclient.check_deadline(deadline)
            _capture_retry(diagnostic, serial=serial, display=display, reason='display_changed')
            return _grab(discover=discover, diagnostic=diagnostic, deadline=deadline)
        raise CaptureError(f"screencap returned {len(buf)} bytes")
    exp_w, exp_h = CONFIG["screen"]["width"], CONFIG["screen"]["height"]
    # multi-display instances (Main Tower) prepend a text warning line to the
    # binary payload - strip leading lines until the header parses sane
    for _ in range(4):
        adbclient.check_deadline(deadline)
        w = int.from_bytes(buf[0:4], "little")
        h = int.from_bytes(buf[4:8], "little")
        adbclient.check_deadline(deadline)
        if 0 < w <= 10000 and 0 < h <= 10000 and len(buf)-w*h*4 in (12,16):
            break
        nl = buf.find(b"\n", 0, 400)
        if nl < 0:
            break
        buf = buf[nl + 1:]
    adbclient.check_deadline(deadline)
    if (w, h) != (exp_w, exp_h) and not discover:
        # A freshly adopted MuMu instance has serial + adb and NO display
        # configured (the wizard writes nothing else), so this reads MuMu's
        # default screen - the LANDSCAPE launcher - and the lock fires before
        # anything is calibrated (fresh-account exercise, 2026-09-08). The game
        # lives on the secondary mumuscreen: derive it once and retry. Single-
        # display emulators return None from the probe and fall through.
        if from_instance and not display and refresh_display(serial, **options):
            adbclient.check_deadline(deadline)
            _capture_retry(diagnostic, serial=serial, display=display, reason='resolution_display_refresh')
            return _grab(discover=discover, diagnostic=diagnostic, deadline=deadline)
        raise CaptureError(f"unexpected resolution {w}x{h}, expected {exp_w}x{exp_h} "
                           "(resolution lock violated - recalibrate or fix instance)")
    header = len(buf) - w * h * 4        # 12 (Android <12) or 16 bytes
    if header not in (12, 16):
        raise CaptureError(f"unexpected raw screencap size {len(buf)} for {w}x{h}")
    rgba = np.frombuffer(buf, np.uint8, count=w * h * 4, offset=header)
    adbclient.check_deadline(deadline)
    pixels = rgba.reshape(h, w, 4)
    adbclient.check_deadline(deadline)
    frame = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)
    adbclient.check_deadline(deadline)
    return frame


# The bottom HUD (wave box, HP bar, ability row) sits PANEL_SHIFT px lower
# when the upgrade panel is closed. layout_offset tracks the current layout;
# wave_reader auto-detects it by trying both positions.
PANEL_SHIFT = 679
layout_offset = 0
_SHIFTED = {"wave_box", "hp_bar", "ability_row"}


def roi(frame: np.ndarray, name: str) -> np.ndarray:
    """Crop a configured ROI from a frame, layout-aware. Raises if uncalibrated."""
    box = CONFIG["rois"].get(name)
    if not box:
        raise CaptureError(f"ROI '{name}' not calibrated in config.yaml")
    x, y, w, h = box
    if name in _SHIFTED:
        y += layout_offset
    elif name == "field":
        h += layout_offset          # floaters settle lower when panel is closed
    return frame[y:y + h, x:x + w]
