"""Passive enemy-HP sampler for the Smart Missiles falloff watch.

Every SAMPLE_SEC it grabs a frame, reads the wave, and saves a crop of the
enemy-HP figure from the battle HUD (the number is on screen permanently -
no menus, NO TAPS EVER). The supervising agent reads the crops in batches,
fits the HP growth curve and projects the wave where HP crosses Smart
Missiles damage (12.54q) - the ride exit point (user, 2026-08-16: "the
growth is not linear").

Crops land in logs/<instance>/hp_probe/w<wave>_<hhmmss>.png; a jsonl row
per sample keeps the pairing.
"""
import json
import os
import time

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python runtime/hp_probe.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import settings


SAMPLE_SEC = 120.0
HP_CROP = (700, 1670, 1040, 1735)     # x0, y0, x1, y1: HP value + heart icon


def main() -> None:
    settings.select_instance("main", "quest_smart_missiles")
    from device import capture
    import cv2
    from vision import wave_reader
    from settings import CONFIG
    out = os.path.join("logs", CONFIG.get("active_instance", "main"),
                       "hp_probe")
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "samples.jsonl")
    x0, y0, x1, y1 = HP_CROP
    while True:
        frame = capture.grab()
        w = wave_reader.read_wave(frame)
        if w is not None:
            stamp = time.strftime("%H%M%S")
            name = f"w{w}_{stamp}.png"
            cv2.imwrite(os.path.join(out, name), frame[y0:y1, x0:x1])
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.time(), "wave": w,
                                     "crop": name}) + "\n")
        time.sleep(SAMPLE_SEC)


if __name__ == "__main__":
    main()
