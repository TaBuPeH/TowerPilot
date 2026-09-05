"""Record every frame for N seconds. No triggers, no detection, no cleverness.

    python tools/flat_capture.py --instance main --seconds 120

Used when something has to be caught that no detector can be trusted to spot -
the Second Wind border, for instance. Read-only: never taps.
"""
import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings                                     # noqa: E402
from settings import ROOT                           # noqa: E402
from device import capture                                      # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--quality", type=int, default=92)
    a = ap.parse_args()

    settings.select_instance(a.instance)
    out = ROOT / "captures" / f"flat_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"recording {a.seconds:.0f}s -> {out}", flush=True)

    t0 = time.time()
    n = 0
    while time.time() - t0 < a.seconds:
        try:
            frame = capture.grab()
        except Exception as e:
            print("capture failed:", e, flush=True)
            continue
        n += 1
        # elapsed seconds in the filename: the only index that matters when
        # hunting for a moment someone described in wall-clock terms
        cv2.imwrite(str(out / f"{n:04d}_t{time.time() - t0:06.2f}.jpg"), frame,
                    [cv2.IMWRITE_JPEG_QUALITY, a.quality])
        if n % 25 == 0:
            print(f"  {time.time() - t0:5.1f}s  {n} frames", flush=True)
    print(f"done: {n} frames over {time.time() - t0:.1f}s -> {out}", flush=True)


if __name__ == "__main__":
    main()
