# Video preview backends
> **Status:** Active
> **Type:** Configuration guide
> **Created:** 2026-10-19
> **Updated:** 2026-10-24

## Choosing a source

Open **Emulators → Video preview** to save defaults for the selected emulator.
The Overview source and FPS controls provide a saved browser override. Choose
**Auto** in the browser to follow the configured source. Changes reconnect the
preview when the dashboard reloads its configuration.

| Source | Selection and fallback |
| --- | --- |
| Auto | Verified local MuMu native capture, then installed scrcpy, then ADB screenshots |
| MuMu native | Native capture first, then installed scrcpy, then ADB screenshots |
| scrcpy | Optional scrcpy package first, then ADB screenshots |
| ADB screenshots | The existing socket screenshot path |

Native and scrcpy preview targets allow 0.5–30 FPS. Screenshots are capped at
4 FPS even when a higher target is requested. The default target is 15 FPS.
Existing saved browser FPS preferences are preserved.

The status line names the active backend, server-delivered FPS, effective cap,
capture/encoding time, and fallback reasons. Delivered FPS is measured at the
server; it is not a measurement of browser presentation or end-to-end latency.
Backend selection/failure and cleanup errors also enter dashboard diagnostics.
Scrcpy's frame-age value estimates age from local packet arrival and relative
video timestamps; it does not measure absolute end-to-end latency. Native
capture delivers a frame on demand and leaves this estimate unset.

An accelerated backend that fails is skipped for the remainder of that stream.
Toggle the preview off and on to retry after repairing the installation. ADB
capture continues to retry with the stream's offline backoff.
If a failed source cannot be cleaned up, the stream retains its ownership and
retries cleanup before opening any replacement source.

---

## MuMu native

The preview uses the renderer DLL from the user's MuMu installation. No vendor
DLL is shipped. Discovery derives the install directory from the configured
MuMu ADB executable and verifies the selected serial against MuMu's VM port
configuration. It never selects an instance just because it is the first one.

The optional install-directory and instance-index settings can resolve custom
installations. Index `-1` means automatic discovery. An explicit index still
has to match the selected serial. Unverifiable aliases, absent SDK files, and
ambiguous mappings cause a visible fallback.

DLL calls run in a hidden helper process, isolated from the web server. Frame
requests and cleanup have deadlines. Full-size BGR pixels are converted to JPEG
using the Overview quality preset; there is no queue of old preview frames.

Native capture is **preview-only**. Farming, calibration, click verification,
and foreground ownership checks retain their existing screenshot path. Native
package capture must not be treated as proof that the game owns the foreground.

---

## Optional scrcpy package

The optional backend uses the pinned official scrcpy server and PyAV. It sends
only video: audio, control, clipboard synchronization, touch indicators, screen
power changes, and new-display creation are disabled. Device transport uses
Tower Pilot's socket ADB client, never an ADB executable per command.

For a source checkout, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-scrcpy.txt
cd backend
..\.venv\Scripts\python.exe -m device.preview.scrcpy_package install
..\.venv\Scripts\python.exe -m device.preview.scrcpy_package status
cd ..
```

The installer verifies the pinned server's SHA-256 before publishing it under
`backend/tools/scrcpy/`. This machine-local folder is ignored by Git. Opening
the dashboard or starting a preview does not download packages. Missing or
broken dependencies leave native MuMu and socket screenshots usable.

The source release includes the optional requirements and installer. The
standard portable dependency set does not include PyAV; a portable distributor
can install the optional requirements into `runtime/Lib/site-packages` with a
matching Python interpreter and run the installer using the bundled runtime.

Scrcpy resolves the selected game's logical Android display; physical
SurfaceFlinger IDs are not reused as logical display IDs. The decoder consumes
all reference packets and exposes only the newest unconsumed, sufficiently
recent frame. Each connection owns a unique server session and temporary JAR;
cleanup is limited to that session.

Live verification for this change uses MuMu. Other-emulator compatibility is
provided as an optional path and remains unverified here.

---

## Configuration and diagnostics

Settings live under `instances.<name>.preview` in the machine-local config:

```yaml
preview:
  backend: auto
  fps: 15
  mumu:
    install_dir: ''
    index: -1
  scrcpy:
    bitrate: 8000000
```

`GET /api/stream/backends` reports local package/discovery readiness without
starting a device capture. `GET /api/stream/status?stream_id=…` reports the
specific stream's active backend, fallback reasons, rate and errors. Changing
quality still controls the JPEG sent by the dashboard, including when scrcpy
provides the source pixels.

Each part of `GET /api/stream.mjpg` carries four headers made with that part's own
pixels: `X-Preview-Frame-Kind` (`frame`, or `error` for the marker drawn when a
capture fails), `X-Preview-Sequence` (successful frames so far; an error marker
repeats the last number and never advances it), `X-Preview-Backend` (`mumu`,
`scrcpy`, `adb` or `unknown`) and `X-Preview-Source-Age-Ms` (the source's own
age estimate in milliseconds, or `unknown`; only scrcpy estimates one). They
identify the picture on screen; the status record is a separate snapshot that
can race a fallback and cannot say which part is displayed.

Implementation: [preview package](../backend/device/preview/),
[settings catalog](../backend/control/settings_catalog.py), and
[stream endpoint](../backend/dashboard.py).
