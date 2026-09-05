"""Benchmark the autopilot's resource cost against the live instance.

    python tools/bench.py [iterations] [fps]

Measures per-stage wall latency (adb capture, decode included; each detector
that has calibrated ROI/templates), process CPU%% and RSS, and prints a
summary. Writes logs/bench_<ts>.json.
"""
import json
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psutil

from device import capture
from vision import detect
from vision import wave_reader
from settings import ROOT, CONFIG

iters = int(sys.argv[1]) if len(sys.argv) > 1 else 60
fps = float(sys.argv[2]) if len(sys.argv) > 2 else CONFIG["loop"]["fps"]
period = 1.0 / fps

proc = psutil.Process()
proc.cpu_percent()                      # prime the counter
rss_start = proc.memory_info().rss

stages = {"capture": [], "wave": [], "detect": [], "loop": []}
cpu_samples, rss_samples = [], []

for i in range(iters):
    t0 = time.perf_counter()
    frame = capture.grab()
    stages["capture"].append(time.perf_counter() - t0)

    t1 = time.perf_counter()
    try:
        wave_reader.read_wave(frame)
    except Exception:
        pass
    stages["wave"].append(time.perf_counter() - t1)

    t2 = time.perf_counter()
    try:
        detect.intro_sprint_active(frame)
        detect.second_wind_floater(frame)
        detect.floating_gem(frame)
        detect.death_screen(frame)
    except Exception:
        pass
    stages["detect"].append(time.perf_counter() - t2)

    stages["loop"].append(time.perf_counter() - t0)
    cpu_samples.append(proc.cpu_percent())
    rss_samples.append(proc.memory_info().rss)
    time.sleep(max(0.0, period - (time.perf_counter() - t0)))

def ms(xs):
    return {"mean_ms": round(st.mean(xs) * 1000, 1),
            "p95_ms": round(sorted(xs)[int(len(xs) * 0.95)] * 1000, 1),
            "max_ms": round(max(xs) * 1000, 1)}

report = {
    "iterations": iters,
    "target_fps": fps,
    "effective_fps": round(iters / sum(stages["loop"]) if sum(stages["loop"]) > iters * period * 0.99 else fps, 2),
    "stages": {k: ms(v) for k, v in stages.items()},
    "cpu_percent": {"mean": round(st.mean(cpu_samples[1:]), 1),
                    "max": round(max(cpu_samples[1:]), 1)},
    "rss_mb": {"start": round(rss_start / 1e6, 1),
               "mean": round(st.mean(rss_samples) / 1e6, 1),
               "max": round(max(rss_samples) / 1e6, 1)},
    "note": ("cpu_percent is THIS python process (one core = 100). Transient "
             "adb.exe capture processes are included in capture wall time but "
             "their CPU is separate and small. MuMu itself is not counted."),
}
print(json.dumps(report, indent=2))
out = ROOT / "logs" / f"bench_{time.strftime('%Y%m%d_%H%M%S')}.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report, indent=2))
print("saved:", out)
