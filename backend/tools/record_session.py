"""Record a HUMAN play session: touches + the screen they happened on.

    python tools/record_session.py --instance main --name tournament
    (stop it with Ctrl-C, or by deleting/creating the stop file it prints)

Captures three things into recordings/<instance>/<stamp>/:
  getevent.log   raw `getevent -lt` stream (every touch device)
  frames/*.jpg   a screenshot taken as each gesture STARTS - i.e. the screen
                 the human was looking at when they decided to tap
  marks.jsonl    {t_wall, t_kernel, device, kind, frame} per detected gesture

Why screenshots at gesture start: the tap coordinates alone are useless for
replay, because the same pixel means different things on different screens.
Pairing each tap with the screen it landed on is what makes the session
learnable - it shows both WHERE and WHAT they clicked.

The autopilot must NOT be running on the same instance while recording, or
its taps get mixed into the log as if they were the human's.
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings                                    # noqa: E402
from settings import ROOT, adb_args                # noqa: E402
from device import capture                                     # noqa: E402

LINE = re.compile(r"\[\s*(?P<t>[\d.]+)\]\s+(?P<dev>\S+):\s+(?P<type>\S+)\s+"
                  r"(?P<code>\S+)\s+(?P<val>\S+)")
MIN_SHOT_GAP = 0.45        # seconds; rapid taps share the newest screenshot
IDLE_SHOT_EVERY = 15.0     # also grab context while nothing is happening
SETTLE_AFTER = 0.9         # after a burst of taps, capture the RESULT


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", default="main")
    ap.add_argument("--name", default="session")
    args = ap.parse_args()

    settings.select_instance(args.instance)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "recordings" / args.instance / f"{stamp}_{args.name}"
    (out / "frames").mkdir(parents=True, exist_ok=True)
    stop_file = out / "STOP"

    raw = (out / "getevent.log").open("w", encoding="utf-8")
    marks = (out / "marks.jsonl").open("w", encoding="utf-8")

    print(f"recording -> {out}")
    print(f"stop with Ctrl-C, or:  echo x > {stop_file}")

    proc = subprocess.Popen(
        adb_args() + ["shell", "getevent", "-lt"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1, creationflags=settings.NO_WINDOW)

    state = {"n": 0, "last_shot": 0.0, "last_tap": 0.0, "frame": None,
             "stop": False}
    lock = threading.Lock()

    def shoot(reason: str) -> str | None:
        """Screenshot, throttled. Returns the frame filename."""
        now = time.time()
        if now - state["last_shot"] < MIN_SHOT_GAP:
            return state["frame"]
        with lock:
            state["last_shot"] = now
            try:
                img = capture.grab()
            except Exception as e:                  # keep recording regardless
                print("capture failed:", e)
                return state["frame"]
            state["n"] += 1
            name = f"{state['n']:04d}_{reason}.jpg"
            cv2.imwrite(str(out / "frames" / name), img,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
            state["frame"] = name
            return name

    def idle_shots():
        """Two jobs: a periodic context frame, and - more important - a
        SETTLE frame shortly after a burst of taps finishes. A gesture-start
        screenshot shows what was on screen BEFORE the tap; without an after
        shot there is no record of what the tap actually did, which is the
        half that matters for learning a menu flow."""
        while not state["stop"]:
            time.sleep(0.3)
            now = time.time()
            last_tap = state["last_tap"]
            if last_tap and now - last_tap > SETTLE_AFTER \
                    and state["last_shot"] < last_tap + SETTLE_AFTER:
                state["last_shot"] = 0.0        # bypass the throttle once
                shoot("after")
                state["last_tap"] = 0.0
            elif now - state["last_shot"] > IDLE_SHOT_EVERY:
                shoot("idle")

    threading.Thread(target=idle_shots, daemon=True).start()

    shoot("start")
    counts = {"gestures": 0, "lines": 0}

    def reader():
        """Reading runs in its own thread: getevent blocks on readline during
        idle periods, so a single-threaded loop could neither notice the STOP
        file nor flush what it had already written."""
        for line in proc.stdout:
            if state["stop"]:
                break
            raw.write(line)
            counts["lines"] += 1
            raw.flush()                 # so the log is inspectable live
            m = LINE.match(line)
            if not m:
                continue
            # finger DOWN: a tracking id that is not the 'lifted' sentinel
            if m["code"] == "ABS_MT_TRACKING_ID" and m["val"] != "ffffffff":
                state["last_tap"] = time.time()
                frame = shoot("tap")
                counts["gestures"] += 1
                marks.write(json.dumps({
                    "t_wall": round(time.time(), 3),
                    "t_kernel": float(m["t"]),
                    "device": m["dev"].rstrip(":"),
                    "kind": "down",
                    "frame": frame,
                }) + "\n")
                marks.flush()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        while not stop_file.exists() and proc.poll() is None:
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        state["stop"] = True
        proc.terminate()
        t.join(timeout=2)
        raw.close()
        marks.close()
        print(f"\nstopped. {counts['gestures']} gestures, {counts['lines']} raw "
              f"lines, {state['n']} frames -> {out}")
        print(f"decode with: python tools/decode_touches.py {out / 'getevent.log'}")


if __name__ == "__main__":
    main()
