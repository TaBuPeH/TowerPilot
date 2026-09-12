---
name: opencv-image-processing
description: OpenCV decision tables — which blur/filter for which noise, which edge detector, Global vs Otsu vs Adaptive thresholding, CLAHE tuning, and the parameter gotchas (odd kernels, grayscale-first, uint8 wraparound).
---

<!-- imported 2026-08-18 from https://github.com/aeren23/image-processing-skills (02-preprocessing-decisions + 03-thresholding-strategy), scrubbed and distilled for this project -->

*Project note: the autopilot's detectors run template matching and color checks on emulator screenshots (OpenCV + numpy). Use these tables when building or debugging detector preprocessing.*

# OpenCV Decision Tables

## Filter selection by noise type

| Noise seen | Filter | Call |
|---|---|---|
| Salt & pepper (random black/white dots) | Median — nothing else comes close | `cv2.medianBlur(img, 5)` |
| Gaussian grain (sensor heat, low light) | Gaussian | `cv2.GaussianBlur(img, (5,5), 0)` |
| Unknown noise, must preserve edges | Bilateral (slow — not for per-frame) | `cv2.bilateralFilter(img, 9, 75, 75)` |
| General smoothing | Mean — simplest/fastest | `cv2.blur(img, (5,5))` |

Rule of thumb: don't know the noise → Gaussian. Edges matter → Bilateral. Random black/white dots → Median.

| Filter | Speed | Edge preservation | Best for |
|---|---|---|---|
| Mean | fastest | poor | general smoothing |
| Gaussian | fast | fair | gaussian noise, pre-Canny |
| Median | medium | good | salt & pepper |
| Bilateral | slow | excellent | edge-aware denoising |

## Edge detector choice

| Need | Detector |
|---|---|
| General edges (most cases) | `cv2.Canny(blurred, 50, 150)` — thresholds at 1:2–1:3 ratio |
| Directional (horizontal OR vertical) | `cv2.Sobel` with dx/dy |
| Finest detail + corners (noise-sensitive) | `cv2.Laplacian` |

**Always `GaussianBlur` before Canny** — without it, noise becomes edges.

## Thresholding method choice

| Lighting | Method |
|---|---|
| Uniform, known cutoff | `cv2.threshold(gray, t, 255, cv2.THRESH_BINARY)` |
| Uniform, unknown cutoff | Otsu: `cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)` |
| Uneven / shadows | `cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 3)` (MEAN_C general, GAUSSIAN_C better for text) |
| Very low contrast | Enhance first (CLAHE), then threshold |

Threshold types: `BINARY` (fg white), `BINARY_INV` (dark objects on light bg), `TRUNC`/`TOZERO`/`TOZERO_INV` (clamp / keep-bright / keep-dark).

## Contrast enhancement

| Method | Call | Character |
|---|---|---|
| Histogram stretch | `cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)` | natural, linear |
| Equalization | `cv2.equalizeHist(gray)` | aggressive, artificial |
| CLAHE (default choice) | `cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8,8)).apply(gray)` | local, artifact-free |

CLAHE tuning: `clipLimit` 2.0–3.0 (higher = more contrast, more noise); `tileGridSize` (4,4)–(16,16) (smaller tiles = more local adaptation, artifact risk).

Histogram diagnostics (`cv2.calcHist([gray],[0],None,[256],[0,256])`):

| Shape | Diagnosis | Action |
|---|---|---|
| Clustered left / right | Under- / over-exposed | CLAHE or normalize |
| Narrow center peak | Low contrast | stretch or CLAHE |
| Two distinct peaks | Clear fg/bg | Otsu will work perfectly |

## Gotchas

- **Kernel sizes must be odd** — `(4,4)` crashes; median takes a single int (`medianBlur(img, 5)`, not a tuple).
- **Threshold/equalize on grayscale only** — `cvtColor(img, cv2.COLOR_BGR2GRAY)` first. Color images: equalize L/Y channel in LAB/YCrCb, convert back.
- **Adaptive `blockSize` must be odd** (11, not 10).
- **uint8 arithmetic:** numpy `img1 + img2` wraps (200+100=44); `cv2.add` saturates at 255.
- OpenCV loads **BGR**, not RGB; array indexing is `[y, x]` but drawing/resize take `(x, y)` — the #1 coordinate-bug source.
- HSV in OpenCV: H is 0–179 (not 0–360); red wraps the hue circle → needs two masks OR'd together.
- `ddepth=-1` in filters = "same depth as input"; pass `cv2.CV_64F` for float precision.
