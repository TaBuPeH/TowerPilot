"""Tap actuation with jitter, rate cap, dry-run and per-instance gating."""
import random

from device import adbclient
import subprocess
import time

from settings import CONFIG, instance, adb_args, input_args, run_hidden


class TapRefused(RuntimeError):
    pass


_last_tap = 0.0


def _input(*parts) -> None:
    """Send an `input` command straight to the adb server socket.

    Same reason as capture.grab: one adb.exe per tap is process churn the
    long farming runs cannot afford, and taps now fire every ~100ms. Builds
    the same `input -d <display> ...` line input_args() produced, minus the
    executable.
    """
    from device import adbclient
    from settings import instance
    inst = instance()
    disp = inst.get("input_display")
    cmd = "input" + (f" -d {disp}" if disp is not None else "")
    cmd += " " + " ".join(str(p) for p in parts)
    adbclient.shell(inst["serial"], cmd, timeout=8)


def tap(x: int, y: int, reason: str = "", instant: bool = False) -> dict:
    """Send one tap (native coords) honoring safety rails.

    Returns an event dict for the logger. Never raises on dry-run;
    raises TapRefused when a rail blocks a live tap.
    """
    global _last_tap
    inst = instance()
    cfg_tap = CONFIG["tap"]
    dry = CONFIG["loop"]["dry_run"]

    jx = x + random.randint(-cfg_tap["jitter_px"], cfg_tap["jitter_px"])
    jy = y + random.randint(-cfg_tap["jitter_px"], cfg_tap["jitter_px"])
    w, h = CONFIG["screen"]["width"], CONFIG["screen"]["height"]
    if not (0 <= jx < w and 0 <= jy < h):
        raise TapRefused(f"tap ({jx},{jy}) outside screen bounds")

    event = {"type": "tap", "x": jx, "y": jy, "reason": reason, "dry_run": dry}
    if dry:
        return event

    if not inst.get("allow_taps"):
        raise TapRefused(f"taps not allowed on instance {CONFIG['active_instance']}")
    # rate cap PACES rather than refuses: an early tap waits out the interval
    min_interval = 1.0 / CONFIG["tap"]["max_rate_per_sec"]
    wait = min_interval - (time.monotonic() - _last_tap)
    if wait > 0:
        time.sleep(wait)

    delay_ms = random.uniform(*cfg_tap["jitter_ms"])
    time.sleep(delay_ms / 1000.0)
    # adb hiccups (daemon restart, socket exhaustion) must not kill the observe
    # loop: a failed tap is a TapRefused the caller already knows how to handle
    try:
        if instant:
            # some menu widgets (guild milestone boxes) treat the held
            # near-zero-distance swipe as a drag and IGNORE it - plain tap there
            _input("tap", jx, jy)
            dur = 0
        else:
            # press DURATION matters: real recorded human taps hold 60-140ms
            # (mean ~85, sd ~15, measured from the user's own touch recordings).
            # `input tap` is instantaneous; a near-zero-distance swipe gives the
            # tap a natural hold time.
            dur = max(45, min(160, int(random.gauss(85, 15))))
            ex, ey = jx + random.randint(-2, 2), jy + random.randint(-2, 2)
            _input("swipe", jx, jy, ex, ey, dur)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError, adbclient.AdbError) as e:
        raise TapRefused(f"adb input failed: {e}") from e
    _last_tap = time.monotonic()
    event["delay_ms"] = round(delay_ms)
    event["press_ms"] = dur
    return event


def swipe(x0: int, y0: int, x1: int, y1: int, ms: int = 400,
          reason: str = "") -> dict:
    """Deliberate drag - used to scroll inventory grids. Same rails as tap()."""
    global _last_tap
    inst = instance()
    dry = CONFIG["loop"]["dry_run"]
    event = {"type": "swipe", "x": x0, "y": y0, "x1": x1, "y1": y1,
             "ms": ms, "reason": reason, "dry_run": dry}
    if dry:
        return event
    if not inst.get("allow_taps"):
        raise TapRefused(f"taps not allowed on instance {CONFIG['active_instance']}")
    try:
        _input("swipe", x0, y0, x1, y1, ms)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError,
            adbclient.AdbError) as e:
        raise TapRefused(f"adb swipe failed: {e}") from e
    _last_tap = time.monotonic()
    return event
