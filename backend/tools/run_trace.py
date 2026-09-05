"""Read-only flight recorder for a run.

Every frame: wave, wall/hp fill, the Second Wind badge score, the Demon Mode
button's template score and border reading, and the identified screen. Written
as one JSON object per line.

This exists because the wave-1120 tournament failure had to be reconstructed
after the fact from three screenshots and a sampler started too late. The
diagnosis was right but it cost a whole entry to get. With this running
alongside, the next failure is answerable from one file.

NEVER taps. Run it next to the orchestrator, not instead of it.

    python tools/run_trace.py --instance main
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings                                     # noqa: E402
from settings import ROOT                           # noqa: E402
from device import capture                                      # noqa: E402
from vision import detect                                       # noqa: E402
from vision import screen                                       # noqa: E402
from vision import wave_reader                                  # noqa: E402


def sample(frame) -> dict:
    row = {}
    row["wave"] = wave_reader.read_wave(frame)
    row["wall"] = round(detect.wall_fill(frame), 4)
    row["hp"] = round(detect.hp_fill(frame), 4)
    row["wall_state"] = detect.wall_state(frame)
    sw, sw_score = detect.second_wind_badge(frame)
    row["sw"] = bool(sw)
    row["sw_score"] = round(sw_score, 4)
    st = detect.button_state(frame, "demon_mode")
    row["dm_present"] = st.present
    row["dm_ready"] = st.ready
    row["dm_score"] = round(st.score, 4)
    row["dm_center"] = st.center
    bv = detect.button_border_val(frame, "demon_mode")
    row["dm_border"] = round(bv, 1) if bv is not None else None
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--screen-every", type=int, default=6,
                    help="identify the screen every N samples (it costs "
                         "~113ms, too much to do on every one)")
    args = ap.parse_args()

    settings.select_instance(args.instance)
    out = ROOT / "logs" / args.instance / f"trace_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    stop = out.with_suffix(".stop")
    print(f"tracing {args.instance} -> {out}\nstop with:  echo x > {stop}")

    n = 0
    t0 = time.time()
    last_screen = None
    with out.open("w") as f:
        while not stop.exists():
            t = time.time()
            try:
                frame = capture.grab()
            except Exception as e:                   # emulator hiccup
                f.write(json.dumps({"t": round(t - t0, 2),
                                    "error": str(e)}) + "\n")
                f.flush()
                time.sleep(1.0)
                continue
            row = {"t": round(t - t0, 2)}
            if n % args.screen_every == 0:
                last_screen = screen.identify(frame).name
            row["screen"] = last_screen
            row.update(sample(frame))
            f.write(json.dumps(row) + "\n")
            f.flush()
            n += 1
            time.sleep(max(0.0, args.interval - (time.time() - t)))
    print(f"stopped after {n} samples -> {out}")


if __name__ == "__main__":
    main()
