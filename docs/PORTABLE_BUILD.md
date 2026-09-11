# Windows portable package

Users download the `windows-x64.zip`, extract it into a writable folder, and
double-click `Start Tower Pilot.cmd`. No Python installation, pip, PATH changes,
or administrator access is needed. The emulator and game are installed separately.
ADB can be downloaded through Setup after accepting Google's terms.

The private runtime is the official Python 3.12.10 x64 embeddable distribution,
checked against a pinned SHA-256. Dependencies are installed at build time from
`requirements-portable.lock`, not on the user's machine. The `_pth` file isolates
imports from system Python; the dashboard and worker subprocesses use this runtime.
Python and dependency license files remain inside the package.

## Rebuild

On Windows with 64-bit Python 3.12:

```powershell
py -3.12 tools/build_portable.py
```

Output is under `dist/`, with a ZIP SHA-256 sidecar and per-file checksums inside.
Builds use a fresh staging directory and the source allowlist; runtime captures,
personal profiles, account state, ADB and game assets are not collected.

## 0.1-beta validation

- Bundled OpenCV, NumPy, Flask, UnityPy and other dependencies import successfully.
- With system Python removed from PATH, the bundled interpreter launches the
  module-based clicker worker and loads the shipped default configuration.
- Extracted package serves Setup in Chrome and correctly reports no installed
  connection tools or configured emulator. No scan or live-game actions were sent
  from this clean copy; the existing farm was left running.

This smoke test used the development Windows machine, not a pristine Windows VM.
Full clean-machine scanning remains a beta validation task. Keep the whole folder
when upgrading: local account data is stored under its backend directory.
