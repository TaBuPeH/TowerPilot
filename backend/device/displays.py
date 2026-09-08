"""Which display holds the game on a multi-display emulator (MuMu).

MuMu Player runs the launcher on its default display and the game on a
secondary "mumuscreen" (SurfaceFlinger port != 0). Without `-d <id>` a
screencap answers for the default display, prefixed by a text warning
("[Warning] Multiple displays were found ...") - so the dashboard's preview
read a landscape launcher, the screen check parsed "[War" x "ning" as the
resolution, and the live stream never saw a PNG (2026-09-08). The ids change
on every emulator restart, so nothing here is persisted: every consumer
derives on demand through `game_display` and re-derives when a capture
stops making sense. Pure text parsing over the adb-server socket; no
settings, no capture, so the dashboard can import it.
"""
import re

PNG_MAGIC = b"\x89PNG"
WARNING_PREFIX = b"[Warning]"


def parse_screens(surfaceflinger_text: str) -> list[tuple[str, int, str]]:
    """(display id, port, name) for every mumuscreen in
    `dumpsys SurfaceFlinger --display-id`; empty for a single-display emulator."""
    return [(did, int(port), name) for did, port, name in re.findall(
        r'Display (\d+) .*?port=(\d+).*?displayName="(mumuscreen\d+)"',
        surfaceflinger_text)]


def secondary_display(screens) -> str | None:
    """The game's physical display: the first mumuscreen whose port is not 0."""
    for did, port, _name in screens:
        if port != 0:
            return did
    return None


def logical_display(dumpsys_display_text: str, physical_id: str) -> int | None:
    """The logical display id (the `input -d` index) wrapping a physical one."""
    m = re.search(r"mDisplayId=(\d+)\s*\n\s*mPrimaryDisplayDevice="
                  rf"[^\n(]*\(local:{physical_id}\)", dumpsys_display_text)
    return int(m.group(1)) if m else None


def game_display(shell) -> tuple[str | None, int | None]:
    """(physical display id, logical input index) of the game display, or
    (None, None) for an emulator with one display. `shell(cmd) -> str` runs
    a shell command on the device (the caller owns the transport)."""
    screens = parse_screens(shell("dumpsys SurfaceFlinger --display-id"))
    disp = secondary_display(screens)
    if disp is None:
        return None, None
    return disp, logical_display(shell("dumpsys display"), disp)


def strip_warning(raw: bytes, limit: int = 512) -> bytes:
    """Drop the text warning some emulators print ahead of a PNG payload."""
    if raw.startswith(PNG_MAGIC):
        return raw
    at = raw.find(PNG_MAGIC, 0, limit)
    return raw[at:] if at > 0 else raw
