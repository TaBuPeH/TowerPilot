"""Catch a Second Wind proc on camera so its border can be templated.

    python tools/sw_hunt.py --instance main

The problem this solves: Demon Mode is held until a Second Wind proc is seen,
and there is no template for one, so the proc is never seen and DM never
fires. That is what killed the first tournament run at wave 1100. A template
cannot be cropped from a screenshot nobody has, and the proc lasts 8 seconds of
real time in the middle of a fight - there is no pausing to go and take one.

So: hold a RING BUFFER of recent frames, watch the rescue bar, and when it
craters, dump the seconds either side to disk. The frames before the trigger
are the point - they show the tower with no border, which is what makes the
border identifiable by differencing rather than by eye.

Read-only. This never taps; run it alongside a human-played run, or alongside
the orchestrator.
"""
import argparse
import collections
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings                                     # noqa: E402
from settings import ROOT                           # noqa: E402
from device import capture                                      # noqa: E402
from vision import detect                                       # noqa: E402
from vision import wave_reader                                  # noqa: E402

PRE_SEC = 6.0        # kept before the trigger: the "no border" reference
MANUAL_PRE_SEC = 75.0   # manual mode keeps far more history: the human says
                        # "it procced" seconds after the fact, and the reply
                        # itself costs time. 25s was not enough - a whole dump
                        # came back as nothing but the game-stats screen.
JPEG_Q = 92
POST_SEC = 14.0      # kept after: covers the whole 8s immunity and its end
TRIGGER = 0.06       # bar fill that counts as "about to die"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--bar", choices=["wall", "hp", "both"], default="both")
    ap.add_argument("--trigger", type=float, default=TRIGGER)
    ap.add_argument("--manual", action="store_true",
                    help="ignore the bars; dump the ring buffer when a file "
                         "named NOW appears in the output directory")
    args = ap.parse_args()

    settings.select_instance(args.instance)
    out = ROOT / "captures" / f"sw_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    stop = out / "STOP"
    print(f"hunting Second Wind on {args.instance}; bar={args.bar} "
          f"trigger<{args.trigger}")
    print(f"dumps -> {out}\nstop with:  echo x > {stop}")

    ring = collections.deque()          # (t, frame)
    dumping_until = 0.0
    n_dump = 0
    seq = 0
    low_streak = 0

    while not stop.exists():
        t = time.time()
        try:
            frame = capture.grab()
        except Exception as e:
            print("capture failed:", e)
            time.sleep(0.5)
            continue
        # A Second Wind proc happens when the tower would DIE, and which bar
        # gets there first depends on the build - the wall can be gone while
        # HP is still up, or the reverse. Watching the LOWER of the two costs
        # nothing and cannot miss the moment.
        wall, hp = detect.wall_fill(frame), detect.hp_fill(frame)
        fill = {"wall": wall, "hp": hp, "both": min(wall, hp)}[args.bar]
        wave = wave_reader.read_wave(frame)

        if dumping_until:
            seq += 1
            cv2.imwrite(str(out / f"d{n_dump:02d}_{seq:04d}_"
                              f"{int((t % 1000) * 10):05d}_f{fill:.3f}.jpg"),
                        frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if t > dumping_until:
                print(f"  dump {n_dump} complete ({seq} frames)")
                dumping_until = 0.0
            continue

        # Buffer JPEG BYTES, not decoded frames. A raw 1080x2560 BGR frame is
        # 8.3MB, so 75 seconds at ~3.4fps would be ~2GB of RAM; encoded it is
        # ~250KB each, ~65MB for the same window.
        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        if ok:
            ring.append((t, enc.tobytes(), fill))
        keep = MANUAL_PRE_SEC if args.manual else PRE_SEC
        while ring and t - ring[0][0] > keep:
            ring.popleft()

        # MANUAL mode: the human is the detector. On a high tier the tower can
        # go from full to dead between two frames, and once dead the wave is
        # unreadable so a bar-based trigger is gated off entirely - which is
        # exactly why the automatic one caught nothing. A person saying "that
        # was one" is slower but never misses.
        if args.manual:
            if (out / "NOW").exists():
                (out / "NOW").unlink()
                low_streak = 2
            else:
                low_streak = 0
        else:
            # a run only dies once, so trigger on a SUSTAINED low reading rather
            # than a single frame - the bar flickers during heavy hits
            low_streak = low_streak + 1 if (wave is not None and
                                            fill < args.trigger) else 0
        if low_streak >= 2:
            n_dump += 1
            seq = 0
            print(f"TRIGGER dump {n_dump} at wave {wave} fill={fill:.3f} "
                  f"({len(ring)} pre-frames)")
            t_last = ring[-1][0] if ring else t
            for i, (rt, rbytes, rfill) in enumerate(ring):
                seq += 1
                # name carries seconds-before-trigger: which frame to look at
                # is the whole question, and -3.4s is a far better hint than
                # an index
                (out / f"d{n_dump:02d}_pre{i:03d}_t-{t_last - rt:05.1f}s_"
                       f"f{rfill:.3f}.jpg").write_bytes(rbytes)
            ring.clear()
            dumping_until = t + POST_SEC
            low_streak = 0

    print(f"stopped. {n_dump} dump(s) -> {out}")


if __name__ == "__main__":
    main()
