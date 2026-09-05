"""Save native frames for calibration/template cropping.

    python tools/snap.py [count] [interval_sec]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from device import capture
from settings import ROOT

count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
out = ROOT / "captures"
out.mkdir(exist_ok=True)

for i in range(count):
    frame = capture.grab()
    name = out / f"snap_{time.strftime('%H%M%S')}_{i:03d}.png"
    cv2.imwrite(str(name), frame)
    print(name)
    if i + 1 < count:
        time.sleep(interval)
