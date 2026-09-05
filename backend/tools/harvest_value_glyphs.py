"""Harvest value-font glyph templates from captures with KNOWN amounts.

Auto-labels by matching contour order against the expected string; a frame
is only used if its contour count matches, so mislabeling is impossible.
Writes templates/valuefont/<char>.png  ('.' saved as 'dot.png', '$' as 'dollar.png')
"""
import cv2
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settings import ROOT

SAMPLES = [
    # (file, native crop [y0,y1,x0,x1], expected chars in x-order)
    ("captures/snap_105002_000.png", (38, 90, 30, 200), "$160"),
    ("captures/diag_now.png",        (38, 92, 30, 290), "$1.97M"),
    ("captures/smoke.png",           (38, 94, 30, 345), "$235.22K"),
    ("captures/smoke.png",           (120, 176, 100, 250), "1.71B"),
]

out = ROOT / "templates" / "valuefont"
out.mkdir(parents=True, exist_ok=True)
saved = {}

for path, (y0, y1, x0, x1), expected in SAMPLES:
    img = cv2.imread(str(ROOT / path))
    if img is None:
        print(f"SKIP {path}: unreadable")
        continue
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = sorted([cv2.boundingRect(c) for c in contours
                    if cv2.boundingRect(c)[3] >= 5 and cv2.boundingRect(c)[2] >= 3],
                   key=lambda b: b[0])
    if len(boxes) != len(expected):
        print(f"SKIP {path} {expected!r}: {len(boxes)} contours vs {len(expected)} chars")
        continue
    for (x, y, w, h), ch in zip(boxes, expected):
        name = {".": "dot", "$": "dollar"}.get(ch, ch)
        if name in saved:
            continue
        glyph = bw[y:y + h, x:x + w]
        cv2.imwrite(str(out / f"{name}.png"), glyph)
        saved[name] = (w, h)
        print(f"saved {name}: {w}x{h} from {expected!r}")

print("\ntotal glyphs:", sorted(saved))
