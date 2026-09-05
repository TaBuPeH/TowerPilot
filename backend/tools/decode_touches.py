"""Decode a `getevent -lt` log into gestures (taps/drags) with timing stats.

    python tools/decode_touches.py captures/clickrec/getevent_main.log

Raw units are native pixels on MuMu (X 0-1080, Y 0-2560, verified via
getevent -p). Repeat taps at an unchanged position do NOT re-send X/Y, so
last-known position is carried across gestures.
"""
import json
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

LINE = re.compile(
    r"\[\s*(?P<t>[\d.]+)\]\s+(?P<dev>\S+):\s+(?P<type>\S+)\s+(?P<code>\S+)\s+(?P<val>\S+)")

path = Path(sys.argv[1])
gestures = []          # {dev, t0, t1, x0, y0, x1, y1, path_px, samples}
state = defaultdict(lambda: {"down": False, "x": None, "y": None,
                             "t0": None, "sx": None, "sy": None,
                             "path": 0.0, "px": None, "py": None, "n": 0})

for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    m = LINE.match(line)
    if not m:
        continue
    t, dev, code, val = float(m["t"]), m["dev"], m["code"], m["val"]
    s = state[dev]
    if code == "ABS_MT_POSITION_X":
        v = int(val, 16)
        if v < 2000:                              # drop 0xFFFFFFxx overflow
            s["x"] = v
    elif code == "ABS_MT_POSITION_Y":
        v = int(val, 16)
        if v < 3000:
            s["y"] = v
    elif code == "ABS_MT_TRACKING_ID":
        if val != "ffffffff":                     # finger down
            s["down"] = True
            s["t0"] = t
            # DO NOT lock the start position here: this touch's X/Y arrive
            # after TRACKING_ID; carried values are the PREVIOUS touch's end.
            s["sx"] = s["sy"] = None
            s["path"], s["n"] = 0.0, 0
            s["px"] = s["py"] = None
        else:                                     # finger up
            if s["down"] and s["t0"] is not None and s["sx"] is not None:
                gestures.append({
                    "dev": dev, "t0": s["t0"], "t1": t,
                    "dur_ms": round((t - s["t0"]) * 1000, 1),
                    "x0": s["sx"], "y0": s["sy"],
                    "x1": s["x"], "y1": s["y"],
                    "path_px": round(s["path"], 1), "moves": s["n"],
                })
            s["down"] = False
    elif code == "SYN_REPORT" and s["down"]:
        if s["sx"] is None:
            # first sync of this touch: NOW the coordinates are this touch's.
            # If no X/Y arrived (repeat tap at unchanged spot), carry stands.
            s["sx"], s["sy"] = s["x"], s["y"]
            s["px"], s["py"] = s["x"], s["y"]
            time_first = True
        elif s["px"] is not None and s["x"] is not None and s["py"] is not None and s["y"] is not None:
            dx, dy = s["x"] - s["px"], s["y"] - s["py"]
            if dx or dy:
                s["path"] += (dx * dx + dy * dy) ** 0.5
                s["n"] += 1
            s["px"], s["py"] = s["x"], s["y"]

taps = [g for g in gestures if g["path_px"] < 12 and g["x0"] is not None]
drags = [g for g in gestures if g["path_px"] >= 12]

print(f"gestures: {len(gestures)}  taps: {len(taps)}  drags: {len(drags)}\n")

# --- tap clusters (40px grid)
clusters = defaultdict(list)
for g in taps:
    clusters[(g["x0"] // 40 * 40, g["y0"] // 40 * 40)].append(g)
print("TAP CLUSTERS (grid 40px):")
for (cx, cy), gs in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
    durs = [g["dur_ms"] for g in gs]
    xs = [g["x0"] for g in gs]; ys = [g["y0"] for g in gs]
    print(f"  ({min(xs)}-{max(xs)}, {min(ys)}-{max(ys)})  n={len(gs)}  "
          f"press {round(st.mean(durs),1)}ms "
          f"(sd {round(st.stdev(durs),1) if len(durs)>1 else 0}, "
          f"{round(min(durs),1)}-{round(max(durs),1)})")

# --- inter-tap intervals within bursts (<2s apart)
ivals = []
for a, b in zip(taps, taps[1:]):
    gap = (b["t0"] - a["t1"]) * 1000
    if 0 < gap < 2000:
        ivals.append(gap)
if ivals:
    print(f"\nINTER-TAP GAPS (<2s): n={len(ivals)}  mean {round(st.mean(ivals),1)}ms  "
          f"sd {round(st.stdev(ivals),1)}ms  min {round(min(ivals),1)}  max {round(max(ivals),1)}")

# --- drags
if drags:
    print("\nDRAGS:")
    for g in drags:
        v = g["path_px"] / max(1e-3, (g["t1"] - g["t0"]))
        print(f"  ({g['x0']},{g['y0']}) -> ({g['x1']},{g['y1']})  "
              f"len {g['path_px']}px  dur {g['dur_ms']}ms  avg {round(v)}px/s")

out = path.with_suffix(".gestures.jsonl")
out.write_text("\n".join(json.dumps(g) for g in gestures), encoding="utf-8")
print(f"\nsaved: {out}")
