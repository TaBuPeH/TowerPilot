# 0.6-beta
> **Status:** Draft. The version number and the change list are confirmed when the release is tagged.
> **Type:** Release notes
> **Created:** 2026-10-24
> **Updated:** 2026-10-25
> **Tags:** release, environment limits, portable

> [!warning] Draft until the Windows 11 checks are recorded
> Do not publish this note until the checks of the tagged build (see
> [the portable build guide](PORTABLE_BUILD.md)) are recorded and the limits below still match them.
> The release builders do not yet package this file; it is added to the allowed docs when the version is raised to 0.6-beta.

Recommended download: **windows-x64 portable ZIP** for Windows 11 x64. Extract the
whole ZIP into a writable folder, then double-click **Start Tower Pilot.cmd**. Python
and dependencies are already bundled. No console window opens: Tower Pilot sits in
the notification area near the clock. Click that icon to open the dashboard,
right-click it to quit. The launcher opens the dashboard's Emulators page in your
default browser; use current Chrome or Edge.

The separate **source ZIP** requires Python 3.12 and installs dependencies on first
launch. Neither package includes an emulator or the game.

Use **1080 x 2560 portrait at 360 DPI**. Allow **20-30 minutes** for mapping, plus
installation/download time. End existing battles and start mapping on Home. Choose
your own farming tier and equipment after mapping.

---

## Changes since 0.5-beta

> [!note] The user-facing change list is added when the release is tagged.
> This section lists only release and packaging changes that can be checked in the repository.

- **Pinned runtime packages.** The portable runtime installs from
  `requirements-portable.lock`: 26 packages, each with a SHA-256 that pip checks.
  `tpk_ar` has no wheel; it is built from its pinned source archive, and the build
  notice (`THIRD_PARTY.txt`) says so.
- **Pinned connection tools.** Setup downloads Android platform-tools 37.0.1 from
  Google and checks its SHA-256 before it unpacks anything. Nothing is downloaded
  until you accept the Android SDK terms and choose **Install connection tools**.
- **Release ZIPs are checked before they are published.** The check refuses local
  state (config, accounts, logs, runs, captures, templates, other profiles), personal
  paths, e-mail addresses outside dependency licence files, credentials in URLs, and
  dependency images that are not on the pinned list. It also requires the release note
  of the version being built, with an Environment limits section.
- **Capability reports say what was checked.** `/api/runtime` carries an
  `environment` block. Each capability is available, missing, timeout or unknown, with the
  check it rests on. Windows text recognition stays unknown until the on-demand probe
  has run (see the limits below).
- **The old Dashboard pages are gone.** The dashboard app at `/ui/app/` is the only
  interface. `/ui/index.html` only redirects old bookmarks to the matching app page.

---

## Environment limits

These are the limits of this build. Nothing outside them is claimed.

### Operating system and browser

- **Windows 11 x64 is the supported environment.** The checks are described in
  [the portable build guide](PORTABLE_BUILD.md). **Windows 10 is untested.**
- **Browsers: current Chrome and Edge.** Other browsers are not supported. The launcher
  opens your default browser; if that is another one, open
  `http://127.0.0.1:8620/ui/app/#emulators` in Chrome or Edge. The port is 8620 unless
  `TOWER_PILOT_PORT` is set.
- **The portable build is an extracted folder, not an installer.** Keep all files
  together in a writable location. Local account data lives in that folder.

### Emulators and display

- **MuMu Player is the reference emulator.** Native-resolution coin farming on MuMu has
  live validation.
- **BlueStacks is tested.** It has needed scan retries and targeted repairs.
- **No other emulator is claimed.** LDPlayer is not supported. The Emulators page
  launches MuMu and BlueStacks only.
- **Reference display: 1080 x 2560 portrait at 360 DPI.** BlueStacks keeps a 2560 x 1080
  landscape panel that the game rotates to the same frame. Other resolutions, including
  1080 x 1920, are unvalidated, and resolution scaling is experimental. Use the
  reference display.
- **Ad overlays are known for MuMu only.** Only MuMu Store is a known ad owner. Another
  emulator's ad shows up as an `overlay_unknown` event naming the window; report it so it
  can be added.

### Downloads, components and Windows features

- **Platform tools are downloaded at Setup and need internet.** Setup downloads Android
  platform-tools 37.0.1 from Google after you accept the Android SDK terms. The download
  is checked against a pinned SHA-256 and refused if it differs.
- **The scrcpy preview is unavailable in the portable build.** The decoder it needs
  (PyAV, listed in `requirements-scrcpy.txt`) is not part of the portable runtime. The
  preview uses MuMu native capture or screenshots over adb instead.
- **Windows text recognition is required.** Setup and several run flows read game text
  with the Windows built-in OCR engine. Capability reports show it as unknown until the
  on-demand probe has run. The probe is a POST request to `/api/environment/probe` with
  the body `{"probe": "windows_ocr"}`. It recognizes a synthetic image within 10 seconds
  and reports available, missing or timeout. It does not touch an emulator or the game,
  and its result is kept in memory until the dashboard restarts.

### Carried from 0.5-beta

- The perk-ban flow has automated tests but has not yet run against a live Perks dialog.
  Watch for a `perk_bans_applied` or `perk_bans_failed` event the first time a run type
  with bans starts.
- A Dissonance run type can only re-enter the mode by itself once that tab's dialog
  images are captured; until then start the mode by hand and let the app adopt the run.
- HUD Ad Gems stop appearing once the game's daily allowance is spent, so a long day ends
  with quiet hours. That is the game, not the collector.
- The fleet table for Tiers 1-13 and 15-24 comes from published data, not from runs of
  this app; report a mismatch and override it meanwhile with `fleet.by_tier`.
- Optional or locked features can remain unverified. A Ready run does not mean every
  possible screen or emergency effect has been exercised.
- Stop automation before manually using the emulator. Stop now preserves a live battle;
  it does not surrender it.

The tagged prerelease is built by `.github/workflows/release.yml`. That workflow does not
start the extracted app, run Setup, an emulator or the game, so a full Setup scan on a
pristine Windows installation remains a beta validation task.
