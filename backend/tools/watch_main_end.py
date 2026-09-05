"""Passive watcher: prints one line when the run on an instance ends (GAME
STATS dialog visible). Capture-only - no taps.

    python tools/watch_main_end.py --instance main
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings                                             # noqa: E402
from device import capture                                  # noqa: E402
from vision import detect                                   # noqa: E402
import cv2                                                  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default=None,
                    help="instance key from config.yaml (default: active_instance)")
    a = ap.parse_args()
    settings.select_instance(a.instance or settings.CONFIG["active_instance"])
    while True:
        try:
            f = capture.grab()
            hit, score, _ = detect._match(f, "icons/game_stats.png", 0.75)
            if hit:
                cv2.imwrite("captures/main_run_end.png", f)
                print(f"RUN ENDED - GAME STATS dialog visible (score {score:.2f})",
                      flush=True)
                break
        except Exception as e:                              # noqa: BLE001
            print(f"watch error (retrying): {e}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
