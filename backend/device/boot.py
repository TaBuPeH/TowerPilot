"""Bring a VM's screen to the game, clean: adb up -> Android booted ->
ad overlays dismissed -> The Tower running -> a screen we recognize.

Spawned detached by the dashboard wizard's launch button (right after
MuMuManager starts the VM) and runnable by hand:

    python boot.py --instance acct2

Every stage is verified before the next and logged as a boot_stage
event; a stall exits nonzero without tapping into an unknown screen
(hard rule 6). The whole pipeline is idempotent - on an already-booted
VM with the game up it just verifies through in a few seconds."""

import argparse
import sys
import time

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python device/boot.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from device import adbclient
from device import overlays

# Cold MuMu boot to adb is the long pole; the rest are short.
STAGE_TIMEOUT = {"adb": 240, "android": 120, "game": 60, "screen": 90}


def _wait(timeout: float, fn, poll: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(poll)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--instance", required=True)
    a = ap.parse_args()
    import settings
    settings.select_instance(a.instance)
    from runtime import logger
    from settings import instance
    serial = instance()["serial"]

    def adb_up() -> bool:
        try:
            return adbclient.reconnect(serial) and adbclient.alive(serial)
        except Exception:               # noqa: BLE001 - port refuses mid-boot
            return False

    def android_up() -> bool:
        try:
            return adbclient.shell(
                serial, "getprop sys.boot_completed").decode().strip() == "1"
        except Exception:               # noqa: BLE001
            return False

    def game_window() -> bool:
        try:
            return any(w.startswith(overlays.GAME_PKG)
                       for w in overlays.windows(serial))
        except Exception:               # noqa: BLE001
            return False

    for stage, fn in (("adb", adb_up), ("android", android_up)):
        t0 = time.monotonic()
        ok = _wait(STAGE_TIMEOUT[stage], fn)
        logger.event("boot_stage", stage=stage, ok=ok,
                     waited=round(time.monotonic() - t0, 1))
        if not ok:
            return 1

    # Ads pop over the launcher right after boot; sweep BEFORE touching
    # the game so its icon/window are not under an overlay.
    if not overlays.clean():
        logger.event("boot_stage", stage="overlays", ok=False)
        return 1
    logger.event("boot_stage", stage="overlays", ok=True)

    if not game_window():
        act_line = adbclient.shell(
            serial, "cmd package resolve-activity --brief "
                    f"{overlays.GAME_PKG} | tail -1").decode().strip()
        if "/" not in act_line:
            logger.event("boot_stage", stage="game", ok=False,
                         error=f"no launcher activity: {act_line!r}")
            return 1
        adbclient.shell(serial, f"am start -W -n {act_line}", timeout=30)
    ok = _wait(STAGE_TIMEOUT["game"], game_window)
    logger.event("boot_stage", stage="game", ok=ok)
    if not ok:
        return 1

    # The game loads, rotates to portrait, and lands somewhere the
    # runners know how to adopt: home, or a live battle it resumed.
    from device import capture
    from vision import screen
    seen = {"name": None, "score": 0.0}

    def known_screen() -> bool:
        try:
            frame = capture.grab()
            sc = screen.identify(frame)
        except Exception:               # noqa: BLE001 - still loading/rotating
            return False
        seen["name"], seen["score"] = sc.name, round(float(sc.score), 3)
        if sc.name == "welcome_back":
            # "Resume previous round?" after a restart with a run live -
            # the user's standing ruling (2026-08-28): ALWAYS resume. The
            # tap is template-located on THIS frame (the button y shifts
            # with the dialog); the loop then waits for the battle.
            from device import act
            from vision import detect
            hit, _score, loc = detect._match(
                frame, "home/welcome_back_resume.png", 0.90)
            if hit:
                tpl = detect._tpl("home/welcome_back_resume.png")
                act.tap(loc[0] + tpl.shape[1] // 2,
                        loc[1] + tpl.shape[0] // 2,
                        reason="welcome back: Resume")
            return False                # keep waiting for battle/home
        return sc.name in ("home", "battle")

    ok = _wait(STAGE_TIMEOUT["screen"], known_screen)
    if not ok:
        logger.event("boot_stage", stage="screen", ok=False, last=seen,
                     shot=logger.shot(capture.grab(), "boot_screen_unknown"))
        return 1
    logger.event("boot_stage", stage="screen", ok=True, **seen)

    # Ads can also draw ABOVE the game (seen 2026-08-19); one last sweep.
    ok = overlays.clean()
    logger.event("boot_done", ok=ok, screen=seen["name"])
    return 0 if ok else 1


if __name__ == "__main__":
    # Same terminal-logging contract as the flow runners: under pythonw an
    # unhandled traceback has no console, and a boot that dies silently
    # reads as "the emulator started but nothing else happened" (the
    # display-churn CaptureError inside overlays.clean, 2026-08-28).
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:      # noqa: BLE001 - logged, then re-raised
        try:
            from runtime import logger
            logger.event("runner_crashed", flow="boot",
                         error=f"{type(e).__name__}: {e}")
        except Exception:           # noqa: BLE001 - never mask the original
            pass
        raise
