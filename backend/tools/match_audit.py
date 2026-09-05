"""Matching-engine audit: score every template against a corpus of real
frames and report the separation between confident hits and background.

The margin (best true score minus the highest score on frames where the
template is NOT present) is the fuzziness headroom: a template whose
background max creeps toward its threshold is one lighting change or one
floating popup away from a false negative/positive.

Usage: python tools/match_audit.py [--frames N] [--min 0.85]
"""
import argparse
import glob
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--min", type=float, default=0.85,
                    help="only report templates whose best score >= this")
    a = ap.parse_args()
    os.chdir(ROOT)
    files = glob.glob("logs/main/scan_evidence/*.png")
    files += sorted(glob.glob("logs/main/2026*_*.png"),
                    key=os.path.getmtime)[-a.frames:]
    frames = {}
    for f in files:
        im = cv2.imread(f)
        if im is not None and im.shape[:2] == (2560, 1080):
            frames[os.path.basename(f)] = im
    print(f"{len(frames)} full-size frames")
    rows = []
    for t in glob.glob("templates/**/*.png", recursive=True):
        tp = cv2.imread(t)
        if tp is None or tp.shape[0] > 600 or tp.shape[1] > 1080:
            continue
        scores = []
        for im in frames.values():
            r = cv2.matchTemplate(im, tp, cv2.TM_CCOEFF_NORMED)
            scores.append(float(r.max()))
        if not scores:
            continue
        scores.sort(reverse=True)
        best = scores[0]
        hits = [s for s in scores if s >= 0.90]
        bg = [s for s in scores if s < 0.90]
        bgmax = max(bg) if bg else 0.0
        rel = os.path.relpath(t, "templates").replace("\\", "/")
        rows.append((rel, best, len(hits), bgmax, tp.shape[1], tp.shape[0]))
    rows.sort(key=lambda r: r[1] - r[3])
    print(f"\n{'template':42s} {'best':>6s} {'hits':>4s} {'bg_max':>6s} "
          f"{'margin':>6s}  size")
    for rel, best, nh, bgmax, w, h in rows:
        if best >= a.min:
            flag = " <-- THIN" if best - bgmax < 0.15 else ""
            print(f"{rel:42s} {best:6.3f} {nh:4d} {bgmax:6.3f} "
                  f"{best - bgmax:6.3f}  {w}x{h}{flag}")


if __name__ == "__main__":
    main()
