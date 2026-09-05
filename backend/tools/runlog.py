"""Post-battle run logger: record GAME STATS / More Stats / Perks screens
and parse them into a per-run .md for later analysis.

Usage:
  python tools/runlog.py record --account main
      Watches the account's screen (~1 fps) and saves every DISTINCT frame
      while you navigate: GAME STATS dialog -> More Stats (all pages) ->
      Perks (all pages). Ctrl+C (or 90s of no new frames) to stop.
  python tools/runlog.py parse --account main [--dir runs/main/<stamp>]
      OCRs the recorded frames (Windows built-in OCR) and writes run.md.

record+parse can be chained:  python tools/runlog.py auto --account main
"""
import argparse
import datetime
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settings import CONFIG, ROOT
from device import capture

WINOCR = Path(__file__).parent / "winocr.ps1"


def _inst(account):
    inst = CONFIG["instances"][account]
    return inst["serial"], inst.get("display")


def record(account: str) -> Path:
    serial, display = _inst(account)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / account / stamp
    out.mkdir(parents=True, exist_ok=True)
    print(f"recording distinct frames from {account} -> {out}")
    print("navigate: GAME STATS -> MORE STATS (scroll all) -> PERKS (scroll)")
    prev = None
    idx = 0
    last_new = time.monotonic()
    try:
        while time.monotonic() - last_new < 90:
            f = capture.grab(serial, display)
            g = cv2.cvtColor(cv2.resize(f, (270, 640)), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                diff = float(np.mean(cv2.absdiff(prev, g)))
                if diff < 2.0:
                    time.sleep(0.6)
                    continue
            prev = g
            idx += 1
            last_new = time.monotonic()
            cv2.imwrite(str(out / f"frame_{idx:03d}.png"), f)
            print(f"  frame_{idx:03d}.png")
            time.sleep(0.6)
    except KeyboardInterrupt:
        pass
    print(f"recorded {idx} frames")
    return out


def ocr_file(png: Path) -> list[tuple[int, int, str]]:
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(WINOCR), str(png)],
        capture_output=True, text=True, timeout=120)
    rows = []
    for line in p.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            try:
                rows.append((int(parts[0]), int(parts[1]), parts[2]))
            except ValueError:
                pass
    rows.sort()
    return rows


def parse(run_dir: Path, account: str):
    frames = sorted(run_dir.glob("frame_*.png"))
    if not frames:
        print("no frames to parse")
        return
    md = [f"# Run log - {account} - {run_dir.name}", ""]
    seen_lines: set[str] = set()
    for png in frames:
        rows = ocr_file(png)
        text = [t for _, _, t in rows]
        joined = " ".join(text)
        if "GAME STATS" in joined:
            section = "Game Stats"
        elif "PERK" in joined.upper():
            section = "Perks"
        else:
            section = "More Stats"
        md.append(f"## {section}  ({png.name})")
        for y, x, t in rows:
            key = t.strip()
            if not key or key in seen_lines:
                continue
            seen_lines.add(key)
            md.append(f"- {key}")
        md.append("")
    out = run_dir / "run.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["record", "parse", "auto"])
    ap.add_argument("--account", default="main")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()
    if args.mode in ("record", "auto"):
        d = record(args.account)
        if args.mode == "auto":
            parse(d, args.account)
    else:
        d = Path(args.dir) if args.dir else \
            sorted((ROOT / "runs" / args.account).glob("*"))[-1]
        parse(d, args.account)
