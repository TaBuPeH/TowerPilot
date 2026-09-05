"""Pre-start overlay cleaner: name and dismiss ad overlays before a runner.

Emulators draw promotional ads over the launcher (and sometimes over the
game) after boot - MuMu's com.mumu.store was seen 2026-08-19 as a
fullscreen APPLICATION_OVERLAY covering the game, and 2026-08-21 as a
transparent "Launch Day" ad with a close button. A runner that starts
under one of these correctly aborts on sight, so the fix belongs BEFORE
the runner: run `python overlays.py --instance <name>` (or call clean()
from a preflight) after the VM boots.

Design rules, in rank order:
1. Presence is decided by EVIDENCE, not vision: `dumpsys window windows`
   lists every window by owner. A transparent ad barely changes the frame;
   the window list cannot lie about it. This also generalizes to other
   emulators: ANY window that is neither system UI, the launcher, nor the
   game is flagged - known ad owners get dismissed, unknown ones get
   logged + screenshotted and REFUSED (never a blind tap, hard rule 6).
   A new emulator's ad package earns its AD_PACKAGES row from that log.
2. Dismissal prefers the ad's own close button (template
   `overlays/ad_close_x.png`, the emulator's standard rounded-square X),
   found on the live frame at >= 0.90; `am force-stop <pkg>` is the
   fallback (proven on com.mumu.store). Every dismissal is verified
   against the window list before it counts.
3. Taps here do NOT go through act.tap: its bounds rail is locked to the
   configured portrait screen and the boot launcher is landscape. The
   local helper keeps act's other rails (allow_taps, dry_run, jitter,
   event logging) and bounds-checks against the live frame instead.
"""

import argparse
import random
import re
import time

from device import adbclient

GAME_PKG = "com.TechTreeGames.TheTower"

# Window titles that belong on a healthy screen (prefix match). Everything
# else is an overlay: dismissable if its owner is in AD_PACKAGES, a logged
# refusal otherwise.
EXPECTED_PREFIXES = (
    GAME_PKG,                       # the game itself (any activity)
    "Splash Screen",                # transient app-launch window
    "app.lawnchair",                # MuMu 12's stock launcher
    "com.android.launcher",         # AOSP launcher family (other emulators)
    "com.android.systemui",         # wallpaper, decor
    "StatusBar",
    "NotificationShade",
    "ScreenDecorOverlay",           # covers ...Bottom too
    "ShellDropTarget",
    "InputMethod",
)

# Overlay owners we KNOW are ads and know how to dismiss. Grown by
# sighting: an overlay_unknown event names the exact window to add here.
AD_PACKAGES = ("com.mumu.store",)

CLOSE_X = "overlays/ad_close_x.png"
CLOSE_THRESHOLD = 0.90


def windows(serial: str) -> list[str]:
    out = adbclient.shell(serial, "dumpsys window windows").decode(
        errors="replace")
    return re.findall(r"Window #\d+ Window\{[0-9a-f]+ u\d+ ([^}]+)\}", out)


def offending(wins: list[str]) -> tuple[list[str], list[str]]:
    """Split the window list into (known ad windows, unknown overlays)."""
    ads, unknown = [], []
    for w in wins:
        if any(w.startswith(p) for p in EXPECTED_PREFIXES):
            continue
        if any(w.startswith(p) for p in AD_PACKAGES):
            ads.append(w)
        else:
            unknown.append(w)
    return ads, unknown


def _grab(serial: str):
    """Frame in whatever orientation the screen is in right now.

    capture.grab's portrait resolution lock is a safety rail for the GAME
    and stays untouched - but the boot launcher is landscape, which is
    exactly when this module runs. PNG screencap + imdecode sidesteps the
    raw-header orientation question; ~700ms is irrelevant pre-start."""
    import cv2
    import numpy as np
    buf = adbclient.exec_out(serial, "screencap -p", timeout=15)
    # A multi-display instance prepends "[Warning] Multiple displays were
    # found..." BEFORE the PNG magic (347 text bytes, seen 2026-08-28) and
    # imdecode refuses the whole payload - decode from the magic instead.
    # (The raw-capture path in capture.grab strips the same warning.)
    start = buf.find(b"\x89PNG")
    if start > 0:
        buf = buf[start:]
    frame = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or min(frame.shape[:2]) < 400:
        raise RuntimeError(f"screencap -p unusable ({len(buf)} bytes, "
                           f"png_magic_at={start})")
    return frame


def _gone(serial: str, pkg: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(w.startswith(pkg) for w in windows(serial)):
            return True
        time.sleep(0.5)
    return False


def _tap(serial: str, frame, x: int, y: int, reason: str) -> dict:
    """act.tap's rails minus the portrait-locked bounds check (see module
    docstring). Returns the event dict; raises act.TapRefused on a rail."""
    from device import act
    from settings import CONFIG, instance
    if not instance().get("allow_taps", False):
        raise act.TapRefused("allow_taps is off for this instance")
    j = CONFIG["tap"]["jitter_px"]
    jx, jy = x + random.randint(-j, j), y + random.randint(-j, j)
    h, w = frame.shape[:2]
    if not (0 <= jx < w and 0 <= jy < h):
        raise act.TapRefused(f"({jx},{jy}) outside the live frame {w}x{h}")
    ev = {"type": "tap", "x": jx, "y": jy, "reason": reason,
          "dry_run": bool(CONFIG["loop"]["dry_run"])}
    if not ev["dry_run"]:
        adbclient.shell(serial, f"input tap {jx} {jy}")
    return ev


def clean(rounds: int = 3, settle: float = 4.0) -> bool:
    """Dismiss every known ad overlay; True when the screen is clean.

    False means a human (or a new AD_PACKAGES row) is needed: an overlay
    we cannot name, or one that survived both the close button and
    force-stop. Callers should refuse to start a runner on False."""
    from device import act
    from vision import detect
    from runtime import logger
    from settings import instance
    serial = instance()["serial"]
    for _ in range(rounds):
        ads, unknown = offending(windows(serial))
        if unknown:
            frame = _grab(serial)
            logger.event("overlay_unknown", windows=unknown,
                         shot=logger.shot(frame, "overlay_unknown"))
            return False
        if not ads:
            return True
        win = ads[0]
        pkg = win.split("/")[0].strip()
        frame = _grab(serial)
        hit, score, loc = detect._match(frame, CLOSE_X, CLOSE_THRESHOLD)
        if hit:
            tpl = detect._tpl(CLOSE_X)
            try:
                ev = _tap(serial, frame, loc[0] + tpl.shape[1] // 2,
                          loc[1] + tpl.shape[0] // 2, "ad close button")
                logger.event("overlay_close", window=win, via="close_button",
                             score=round(score, 3), **ev)
            except act.TapRefused as e:
                logger.event("tap_refused", button="ad_close", error=str(e))
                hit = False
        if hit and _gone(serial, pkg, settle):
            continue
        # No close button on screen (ad art without one, or the match is
        # below threshold) or the tap did not take: kill the owner. Data
        # loss is not a concern for a store/ad process.
        adbclient.shell(serial, f"am force-stop {pkg}")
        logger.event("overlay_close", window=win, via="force_stop",
                     close_button_seen=bool(hit))
        if not _gone(serial, pkg, settle):
            frame = _grab(serial)
            logger.event("overlay_stuck", window=win,
                         shot=logger.shot(frame, "overlay_stuck"))
            return False
    ads, unknown = offending(windows(serial))
    if ads or unknown:
        logger.event("overlay_stuck", windows=ads + unknown, rounds=rounds)
        return False
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--instance", required=True)
    a = ap.parse_args()
    import settings
    settings.select_instance(a.instance)
    from runtime import logger  # noqa: F401  (binds the event log to the instance)
    ok = clean()
    print("clean" if ok else "NOT CLEAN - see events log")
    raise SystemExit(0 if ok else 1)
