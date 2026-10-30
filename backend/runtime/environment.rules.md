# Environment reporting rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-22
> **Updated:** 2026-10-22
> **Owner:** runtime/environment.py

## Contract

[environment.py](environment.py) owns local capability reporting and the stored
Windows OCR probe result. Routes transport its results. Reports never spawn,
download, contact adb, or access a device. They never read account state.

Each capability has `state` (`available`, `missing`, or `unknown`) and `basis`.
Availability describes the stated local prerequisites; it does not prove an
emulator connection or a successful capture. Emulator locator misses and
unprobed VM mappings remain unknown. Scrcpy server and decoder are separate.
The app build check establishes presence, not integrity; integrity and the release kind belong to
[build_info.py](build_info.py) (`release_identity`, see [build_info.rules.md](build_info.rules.md)). The report asks it
for the kind only and reads no identity file itself.

Platform-tools presence comes from `tool_install.installed`. Its reported
version comes only from a local installer receipt matching the pin's version,
URL, and archive hash. Missing or mismatching receipts leave the version null.
No binary is invoked to discover a version. The receipt does not verify later
edits to installed binaries.

---

## OCR probe

Only `POST /api/environment/probe` with exactly `{"probe":"windows_ocr"}`
starts a probe, under the dashboard's action lock. The probe draws synthetic
text with Pillow and invokes the shipped `backend/tools/winocr.ps1` through
the dashboard's hidden-process boundary. Preparation consumes the same ten
second budget as the child process. The temporary image is always removed.
Success requires the expected recognized text, exit zero, and empty stderr.

The process stores the latest `state` (`available`, `missing`, or `timeout`),
`checked_at` (Unix seconds), `basis`, and `detail`. Before a probe the result is
`unknown` with `basis: not probed`; GET polls return a copy and never run OCR.
The result is local process memory and is reset when the server restarts.
