# Windows portable package
> **Status:** Active
> **Type:** Build and release guide
> **Created:** 2026-09-11
> **Updated:** 2026-10-27
> **Tags:** release, portable, environment limits

Users download the `windows-x64.zip`, extract it into a writable folder, and
double-click `Start Tower Pilot.cmd`. No Python installation, pip, PATH changes,
or administrator access is needed. The emulator and game are installed separately.
ADB can be downloaded through Setup after accepting Google's terms. The supported
environment is Windows 11 x64 with current Chrome or Edge; the limits are listed under
"Environment limits" in the release note for the version, for example
`docs/RELEASE_0.6_BETA.md` in the repository.

The private runtime is the official Python 3.12.10 x64 embeddable distribution,
checked against a pinned SHA-256. Dependencies are installed at build time from
`requirements-portable.lock`, not on the user's machine. The `_pth` file isolates
imports from system Python; the dashboard and worker subprocesses use this runtime.
Python and dependency license files remain inside the package.

## Rebuild

On Windows with 64-bit Python 3.12 and Node.js 22.13+ (22.x), 24.x or 26+, build the dashboard
app first; both release scripts refuse to run without it:

```powershell
cd frontend/app; npm ci; npm run build; cd ../..
py -3.12 tools/build_portable.py
```

Output is under `dist/`, with a ZIP SHA-256 sidecar and per-file checksums inside.
Builds use a fresh staging directory and the source allowlist; runtime captures,
personal profiles, account state, ADB and game assets are not collected.

### Clean tree and release identity

A release is the tagged merge, built by the CI run of the tag; the assets that run
publishes are the accepted artifacts. A local build is for rehearsal only.

Both builders refuse to run when the working tree has uncommitted changes, including
untracked files that git does not ignore, and when git cannot say (no `.git` of its own).
`--allow-dirty` skips that refusal for a rehearsal build. Such a build records `dirty` or
`unknown` in its identity, and the archive checker rejects it.

Each ZIP carries `release_identity.json` at its root: the version, the kind
(`source` or `portable-windows-x64`), the source fingerprint, the app build hash, the
lock hash, the Python version and hash (portable only), the build time, the git revision and
the working-tree state. It holds no path, user name or machine name. The dashboard reports it
read-only in `/api/runtime` under `release`, with `matches_source` and `matches_app_build`
(whether the files on disk are still the ones the build recorded). A copy without the file
reports kind `checkout`; one with an unreadable file reports `unknown`.
## Environments and limits

| ID | Environment | What it shows |
|---|---|---|
| E1 | Windows 11 development machine, fresh temporary folder, the published ZIP | The weakest check: system Python and per-user state exist on this machine |
| E2 | GitHub `windows-latest` runner, through `release.yml` | A fresh hosted VM with no emulator. Preinstalled Python and Node are present |
| E3 | A clean extraction of the portable ZIP into a new folder, on E1 and on E2 | The "installs and runs" check |

Windows 10 is not one of these environments; it is untested. The full list of limits for
a version (browsers, emulators, display, the scrcpy preview, Windows text recognition, ad
overlays) is under "Environment limits" in `docs/RELEASE_<VERSION>.md`; the README and the
GitHub release text repeat only the short form. The archive checker requires the note of
the version being built, with that heading, in both ZIPs.

## Release workflow

`release.yml` runs the steps in this order: tag and `VERSION` check, dependency install
(the hash-checked lock, then the pinned `pytest`), the release boundary tests, the
dashboard app checks and build, both builds, the archive check (`tools/check_release_zips.py`),
and the prerelease publication.

There is no scripted check of the installed ZIP yet. For E3, extract the portable ZIP into a new
folder, double-click `Start Tower Pilot.cmd`, and confirm the tray icon, the dashboard in
Chrome or Edge, and that the Emulators page opens. Record the result with the build.

A later run of the workflow for a version that already has a release replaces that release's
assets (`gh release upload --clobber`). Before accepting artifacts, compare their SHA-256 with the
`.sha256` files.

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
