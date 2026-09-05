"""Frame capture from a MuMu instance via adb screencap (works minimized)."""
import subprocess
import cv2
import numpy as np

from settings import CONFIG, adb_args, run_hidden


class CaptureError(RuntimeError):
    pass


def refresh_display(serial: str | None = None) -> str | None:
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
    import re
    from device import adbclient
    from settings import instance as _inst
    inst = _inst()
    if not inst.get("display"):
        return None
    serial = serial or inst["serial"]
    sf = adbclient.shell(serial, "dumpsys SurfaceFlinger --display-id",
                         timeout=10).decode(errors="replace")
    screens = re.findall(
        r'Display (\d+) .*?port=(\d+).*?displayName="(mumuscreen\d+)"', sf)
    secondary = [did for did, port, _n in screens if int(port) != 0]
    if not secondary:
        raise CaptureError(
            f"display refresh: no secondary mumuscreen in SurfaceFlinger "
            f"({len(screens)} screen(s) listed)")
    disp = secondary[0]
    dd = adbclient.shell(serial, "dumpsys display",
                         timeout=10).decode(errors="replace")
    m = re.search(r"mDisplayId=(\d+)\s*\n\s*mPrimaryDisplayDevice="
                  rf"[^\n(]*\(local:{disp}\)", dd)
    if not m:
        raise CaptureError(
            f"display refresh: no logical display wraps local:{disp}")
    old = (inst.get("display"), inst.get("input_display"))
    inst["display"] = disp
    inst["input_display"] = int(m.group(1))
    from runtime import logger
    logger.event("display_refreshed", display=disp,
                 input_display=inst["input_display"],
                 was_display=old[0], was_input=old[1])
    return disp


def grab(serial: str | None = None, display: str | None = None) -> np.ndarray:
    """Return the current screen as a BGR ndarray at native resolution.

    Uses RAW screencap (no -p): ~344ms vs ~700ms for PNG - the on-device PNG
    encode dominates, the extra ~9MB over loopback TCP is nearly free. Raw
    layout: 4-byte LE width, height, pixel format, (colorspace,) then RGBA.
    display: physical display id for multi-display instances (Main Tower's
    game runs on a secondary display; the default display is the launcher)."""
    from_instance = serial is None and display is None
    if from_instance:
        from settings import instance
        display = instance().get("display")   # active instance's game display
    # Straight to the adb SERVER socket - no adb.exe per frame. Spawning a
    # process ~3x a second is what exhausted Windows socket buffers and then
    # dropped the device mid-run, twice. Same 247ms/frame either way (the 11MB
    # loopback transfer dominates, not the spawn), so this buys stability, not
    # speed. One retry with a reconnect, because a dropped device is the
    # failure that actually happens and every caller wants it handled here
    # rather than reimplementing recovery.
    from device import adbclient
    from settings import instance as _inst
    serial = serial or _inst()["serial"]
    cmd = "screencap" + (f" -d {display}" if display else "")
    buf = b""
    for attempt in (1, 2):
        try:
            buf = adbclient.exec_out(serial, cmd, timeout=15)
            if len(buf) >= 1000:
                break
        except (OSError, adbclient.AdbError) as e:
            if attempt == 2:
                raise CaptureError(f"screencap failed: {e}") from e
            adbclient.reconnect(serial)
    if len(buf) < 1000:
        # THE MuMu DISPLAY-CHURN SIGNATURE: the emulator recreates its
        # displays on every restart and the ids change, so a configured
        # display id yields this tiny "Failed to take screenshot" payload
        # while plain adb still works (2026-08-27 twice, 2026-08-28 - it
        # killed the boot pipeline's ad cleaner). Re-derive from dumpsys
        # once and retry; only for the instance's own display, never for
        # an explicitly requested one (wizard probes ask for exact ids).
        if from_instance and display and refresh_display(serial) not in (None, display):
            return grab()
        raise CaptureError(f"screencap returned {len(buf)} bytes")
    exp_w, exp_h = CONFIG["screen"]["width"], CONFIG["screen"]["height"]
    # multi-display instances (Main Tower) prepend a text warning line to the
    # binary payload - strip leading lines until the header parses sane
    for _ in range(4):
        w = int.from_bytes(buf[0:4], "little")
        h = int.from_bytes(buf[4:8], "little")
        if (w, h) == (exp_w, exp_h):
            break
        nl = buf.find(b"\n", 0, 400)
        if nl < 0:
            break
        buf = buf[nl + 1:]
    if (w, h) != (exp_w, exp_h):
        raise CaptureError(f"unexpected resolution {w}x{h}, expected {exp_w}x{exp_h} "
                           "(resolution lock violated - recalibrate or fix instance)")
    header = len(buf) - w * h * 4        # 12 (Android <12) or 16 bytes
    if header not in (12, 16):
        raise CaptureError(f"unexpected raw screencap size {len(buf)} for {w}x{h}")
    rgba = np.frombuffer(buf, np.uint8, count=w * h * 4, offset=header)
    return cv2.cvtColor(rgba.reshape(h, w, 4), cv2.COLOR_RGBA2BGR)


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
