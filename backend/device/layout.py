"""Native display contract shared by setup and calibration."""
import re

WIDTH, HEIGHT, DPI = 1080, 2560, 360


def density(text):
    matches = re.findall(r"(?:Physical|Override) density:\s*(\d+)", text)
    return int(matches[-1]) if matches else None


def require_native(width, height, dpi):
    if (width, height, dpi) != (WIDTH, HEIGHT, DPI):
        raise ValueError(f"Display is {width}×{height} at {dpi or 'unknown'} dpi. "
                         "Use Setup to configure 1080×2560 portrait at 360 dpi "
                         "(BlueStacks panel: 2560×1080 landscape). No calibration was started.")
