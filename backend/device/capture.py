"""Frame capture from a MuMu instance via adb screencap (works minimized)."""
import subprocess
import cv2
import numpy as np

from settings import CONFIG, adb_args, run_hidden


class CaptureError(RuntimeError):
    pass


_resolved_serial = None

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
    from device import adbclient, displays, overlays
    from settings import instance as _inst
    inst = _inst()
    configured = bool(inst.get("display"))
    serial = serial or inst["serial"]
    # The display the GAME's window is on, not merely the first secondary:
    # another app tab (a Play Store page) takes a mumuscreen of its own and
    # the game then lives on the next one (displays.game_display).
    disp, logical = displays.game_display(
        lambda cmd: adbclient.shell(serial, cmd, timeout=10).decode(
            errors="replace"), overlays.GAME_PKG)
    if disp is None:
        if not configured:
            return None     # BlueStacks / single-screen MuMu: the default display
        raise CaptureError(
            "display refresh: no secondary mumuscreen in SurfaceFlinger")
    if logical is None:
        raise CaptureError(
            f"display refresh: no logical display wraps local:{disp}")
    old = (inst.get("display"), inst.get("input_display"))
    inst["display"] = disp
    inst["input_display"] = logical
    if old != (disp, logical):
        # only a CHANGE is news: the observe loop re-derives once a minute
        # while it cannot name the screen (follow_game)
        from runtime import logger
        logger.event("display_refreshed", display=disp,
                     input_display=inst["input_display"],
                     was_display=old[0], was_input=old[1])
    return disp


def follow_game(serial: str | None = None) -> bool:
    """Re-derive the game display; True when it MOVED since the last frame.

    The game restarting (an Android package update killed it, 2026-11-18)
    can land it on a different mumuscreen than the one this process is
    capturing, and nothing about the old display's frames says so - they
    just stop being the game. The observe loop calls this while the screen
    reads `unknown`. Never raises: a failed probe is "did not move"."""
    from settings import instance as _inst
    before = _inst().get("display")
    try:
        after = refresh_display(serial)
    except Exception:                   # noqa: BLE001 - adb hiccup / mid-boot
        return False
    return after is not None and after != before


def grab(serial: str | None = None, display: str | None = None, *, discover=False) -> np.ndarray:
    """Return the current screen as a BGR ndarray at native resolution.

    Uses RAW screencap (no -p): ~344ms vs ~700ms for PNG - the on-device PNG
    encode dominates, the extra ~9MB over loopback TCP is nearly free. Raw
    layout: 4-byte LE width, height, pixel format, (colorspace,) then RGBA.
    display: physical display id for multi-display instances (Main Tower's
    game runs on a secondary display; the default display is the launcher)."""
    from_instance = serial is None and display is None
    if from_instance:
        from settings import instance
        global _resolved_serial
        bound_serial = instance()["serial"]
        if _resolved_serial != bound_serial:
            # The launcher may have exactly the game's dimensions. Select the
            # game display before the first frame, not only on a size mismatch.
            refresh_display(bound_serial)
            _resolved_serial = bound_serial
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
            return grab(discover=discover)
        raise CaptureError(f"screencap returned {len(buf)} bytes")
    exp_w, exp_h = CONFIG["screen"]["width"], CONFIG["screen"]["height"]
    # multi-display instances (Main Tower) prepend a text warning line to the
    # binary payload - strip leading lines until the header parses sane
    for _ in range(4):
        w = int.from_bytes(buf[0:4], "little")
        h = int.from_bytes(buf[4:8], "little")
        if 0 < w <= 10000 and 0 < h <= 10000 and len(buf)-w*h*4 in (12,16):
            break
        nl = buf.find(b"\n", 0, 400)
        if nl < 0:
            break
        buf = buf[nl + 1:]
    if (w, h) != (exp_w, exp_h) and not discover:
        # A freshly adopted MuMu instance has serial + adb and NO display
        # configured (the wizard writes nothing else), so this reads MuMu's
        # default screen - the LANDSCAPE launcher - and the lock fires before
        # anything is calibrated (fresh-account exercise, 2026-09-08). The game
        # lives on the secondary mumuscreen: derive it once and retry. Single-
        # display emulators return None from the probe and fall through.
        if from_instance and not display and refresh_display(serial):
            return grab(discover=discover)
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
