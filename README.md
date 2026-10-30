# Tower Pilot
> **Status:** Active
> **Type:** Project guide
> **Created:** 2026-08-24
> **Updated:** 2026-10-25
> **Tags:** autopilot, the-tower, distribution

**0.5-beta — Windows x64 portable release.** Download the `windows-x64` ZIP,
extract the whole folder, and double-click **Start Tower Pilot.cmd**. Python and
dependencies are bundled; no separate Python installation is needed. The default
browser opens Setup automatically; use current Chrome or Edge. The server runs in
the background: Tower Pilot sits in the notification area near the clock (click
to open the dashboard, right-click to quit). The separate `source` ZIP requires
Python 3.12.
See [what changed and the beta limits](docs/RELEASE_0.5_BETA.md).

The development preview prefers native MuMu capture, with an optional scrcpy
package and socket screenshots as fallbacks. See [video preview setup and
configuration](docs/VIDEO_PREVIEW.md). The portable ZIP does not bundle the
scrcpy decoder, so the scrcpy preview is unavailable there.

[Download 0.5-beta](https://github.com/TaBuPeH/TowerPilot/releases/tag/v0.5-beta)
— choose **windows-x64.zip** for the app with Python included, or **source.zip**
for development. Both now carry the full source, the agent rule book
(`CLAUDE.md`, `AGENTS.md`) and the `.claude/` skills and hooks.

Licensed under [MIT](LICENSE): extend the app, add configs and manifests, and
share your improvements. Third-party libraries retain their own licenses.
The license does not grant rights to The Tower's artwork or other game assets.

Vision-driven autopilot for **The Tower** running in an Android emulator
(MuMu Player or BlueStacks): screenshots in over adb, human-like
taps out. No memory reading, no modded APK, everything stays on your
machine.

## Setup overview and timing

Use **Setup → Remap this game → Control**. Fresh accounts receive the shipped
Farm, Tournament and Shard farming recipes. No game images ship with the app:
setup extracts artwork from the installed game and verifies it against screenshots
using the text manifest. Captures and account discoveries stay local.

Allow **20–30 minutes for initial mapping and validation**, excluding installation
and downloads. Live measurements from 10 September 2026:

| Test | Duration | Coverage |
|---|---|---|
| MuMu rescan | About **17 minutes** | 24 screens; existing battle captures reused |
| BlueStacks full scan, successful retry | About **21 minutes** | Menu/inventory mapping and battle verification |
| BlueStacks targeted repair | About **4 minutes** | Missing upgrade label; no inventory rescan |

These accounts had 31 cards, six equipped modules and roughly 100 inventory
modules. BlueStacks required a retry and repair; these durations are not a
guarantee of first-pass completion. Inventory size and emulator speed matter.
Ten-minute full setup is not yet a reliable expectation. The vertical progress
history shows the current action and step durations.

## Recommended emulator display

| Emulator | Display setting | Density | Captured game frame |
|---|---|---|---|
| MuMu Player | **1080 × 2560 portrait** | **360 DPI** | 1080 × 2560 |
| BlueStacks | **2560 × 1080 landscape** | **360 DPI** | 1080 × 2560 after the game rotates it |

Use these reference settings. Manifest scaling and per-resolution overrides are
experimental; other resolutions have not completed full validation. On BlueStacks,
**Prepare** configures the display and enables Android
Debug Bridge while the emulator is closed, keeping a backup. Restart after display
changes. Run **Check now** with The Tower in front, not the Android launcher.
The browser window size does not determine the captured game resolution.

## Quick start

Use Windows 11 x64 (Windows 10 is untested) and a current Chrome or Edge
browser. Setup uses Windows text recognition. Install an emulator and The Tower
yourself, and sign into your game account first. MuMu is the reference emulator;
BlueStacks is tested and has needed retries; no other emulator is claimed. You do
not need a separate ADB installation. Every limit of the 0.6-beta draft is listed
under [Environment limits](https://github.com/TaBuPeH/TowerPilot/blob/master/docs/RELEASE_0.6_BETA.md#environment-limits)
in the repository (the release ZIPs carry the limits of their own version in `docs/RELEASE_<VERSION>.md`).

For the portable ZIP, launch **Start Tower Pilot.cmd** and continue at step 4.
Steps 1–3 below apply only to the source distribution.

1. Install [Python 3.12](https://www.python.org/downloads/)
   and an Android emulator with The Tower installed. The frame the
   recommended for this beta is **1080 x 2560 portrait at 360 dpi**. On MuMu Player (the
   reference setup) that is the display setting itself; BlueStacks keeps a
   2560 x 1080 landscape panel that the game rotates, and the Setup page's
   **Prepare** button writes it - step by step in
   [docs/BLUESTACKS.md](docs/BLUESTACKS.md).
2. Double-click **Start Tower Pilot.cmd**. The source launcher creates its local
   Python environment and installs dependencies. Its setup console closes after
   launch; the server runs without a terminal window.
3. Use the Tower Pilot notification-area icon to open
   [Emulators](http://127.0.0.1:8620/ui/app/#emulators), or right-click it to quit
   the dashboard. Startup failures appear in a dialog, with details in
   `backend/logs/launcher.log`. Quitting the dashboard leaves detached runners alive.
   The first start copies `backend/config.example.yaml` to
   `backend/config.yaml` - that file is your machine and is git-ignored.
4. **Emulators** page: choose **Install connection tools** to let the Python app
   download ADB from Google into its own local tools directory. No separate
   ADB installation, administrator access or PATH changes are needed.
   The wizard lists the emulators it finds, including closed instances. **Start it**
   launches the instance (MuMu through MuMuManager, BlueStacks through
   `HD-Player.exe`), points the config at it on a fresh install, and runs
   the boot pipeline that waits for Android, clears overlays and starts
   the game. Then check the display resolution.
5. Choose or create your account in Setup. Import current settings and calibration
   only when you deliberately want to reuse them. If the emulator is running,
   click **Use this one** to select it, and **Open The Tower** to open the game.
   Resolve any login/cloud-save dialog. **End every existing battle, then open
   Home in the game**—going Home alone can leave a battle active. Do not scan
   over a tournament. Click **Continue to map my game**, then
   **Calibrate → Remap this game** and confirm the preparation dialog. The app
   extracts artwork from your installed game, verifies Home, follows the shipped
   screen manifest and reads your cards, modules and presets. Keep the emulator
   free while the vertical progress history shows each step. You do not need to
   draw crops for this menu scan. **No game images ship**: extracted artwork,
   screen captures and learned positions stay local to this account and emulator.
   When needed, setup starts a short normal battle at the highest unlocked tier
   it detects. A bounded Tier 1 pass captures missing digits, upgrade labels and
   battle controls. It ends only its own test battles and restores the original
   tier. It does not enter tournaments or purchase items to unlock screens.
   Optional effects such as Second Wind never require an indefinite wait.
6. Keep the dashboard open and the emulator untouched until completion.
   Discoveries are saved automatically. A separate legacy account scan, manual
   crops and draft-profile promotion are not part of normal setup.
7. Open **Control**, choose **Coin farming**, and edit its equipment and farming
   tier. The generic recipe starts at Tier 1; the highest unlocked tier is not
   automatically your best farming tier. When the run shows **Ready**, enable
   **Allow automation to tap this emulator**, then click **Start**.

The default dashboard port is **8620**; our development/test instance uses
**8621**. To use that port in PowerShell:

```powershell
$env:TOWER_PILOT_PORT = "8621"
.\Start-TowerPilot.ps1
```

Open [the dashboard on port 8621](http://127.0.0.1:8621/ui/app/).

### Keeping the dashboard running

The dashboard supervises the gem collector, and nothing else restarts the collector, so a dead dashboard
means no harvesting. Three parts keep it up:

- **One dashboard per port.** `backend/dashboard.py` binds its port exclusively before it starts any
  background thread (refresher, harvest tick, reconnect). A second start while another dashboard serves the
  port logs `dashboard_start_refused` (`reason: port_taken`) to `backend/logs/_dashboard/events_*.jsonl` and
  exits with code 1. It also refuses a port that an older dashboard bound.
- **Landing hold.** While the file `backend/logs/_dashboard/HOLD` exists, a dashboard start logs
  `dashboard_start_refused` (`reason: landing_hold`, plus the file's text) and exits with code 3 without binding the
  port. Create it before copying runtime files and delete it afterwards so the restart task below cannot start a
  half-landed tree. The file is git-ignored; a forgotten one keeps every start refused.
- **Restart task.** `tools/register_dashboard_task.ps1` (re)registers the Scheduled Task "TowerPilot Dashboard":
  it runs `conhost.exe --headless <repo>\.venv\Scripts\python.exe <repo>\tools\portable_launcher.py --background` in the repo folder
  (the dashboard behind Start Tower Pilot's notification-area icon, but never opening a browser tab or a dialog; output in `backend\logs\launcher.log`; Quit on the icon lasts until the next repetition),
  starts at logon and repeats every 5 minutes with `MultipleInstances = IgnoreNew` and `-Priority 4` (Normal; the Task Scheduler default of 7 is BelowNormal, which the dashboard, its workers and the gem collector all inherit and which starves them under other CPU load; the collector also raises itself to AboveNormal at startup). A running dashboard is left
  alone, and a dead one comes back within 5 minutes (conhost reports exit code 0 to the Task whatever Python returned, so read refusals in the `_dashboard` event log, not in the Task result). Re-running the script replaces the definition without stopping
  a running dashboard. Preview with `-WhatIf`; test on a scratch task with
  `-TaskName "Scratch" -Execute cmd.exe -Argument "/c exit 0"` and delete it with `-TaskName "Scratch" -Remove`.

### Putting new code live in the gem collector

A running collector keeps the code it started with. Do not kill it to deploy: ask it to leave at a safe point.

```
python tools/collector_restart.py --wait 180 --reason "orbit fix"   # ask, then wait until it has taken the request
python tools/collector_restart.py --status                          # is a request pending, and how old is it
python tools/collector_restart.py --cancel                          # withdraw a request nobody took
```

The command only writes the file `backend/logs/<instance>/collector_exit` (`scheduling/runflag.py` owns it, next to
the run-boundary flag `stop_after_run`, which it does not touch). No process is killed or signalled, and nothing talks
to the dashboard, the emulator or adb.

- **Safe point.** The collector checks the file between polls, never inside one. It waits while an ad claim is being
  attempted or an offer's claim is scheduled, and while a floating-gem attempt window is open, and logs
  `gem_collector_exit_deferred` with the reasons once per change. It then closes its battle record as `interrupted`
  (reason `collector_restart`), deletes the file, logs `gem_collector_stopped` (`planned: true`) and exits normally.
  It never ends the battle on screen. A request written before a collector started is ignored by it.
- **Relaunch.** The dashboard's harvest tick starts a new collector as it does after any exit (at least
  `MIN_SPACING_SEC` after the previous launch). The exit is recorded as `succeeded`, so it adds nothing to the failure
  count, the backoff or the circuit. A collector started by hand is not relaunched. The `--wait` timeout (exit code
  2) means the request is still pending: the collector is busy, not running, or not harvest-managed.
- **On the Dashboard.** The collector's battle record is split: the new collector opens a new record, and the cause line
  ("Collector restarted during this battle") only reads an exit that happened after the record being shown began, so
  it stays empty for the new record. The old record shows as interrupted.

## Readiness, stopping and repair

- **Prepare this run** reuses saved discoveries and can capture missing battle
  controls. It does not require a second full inventory scan for battle repair.
- A failed full Remap can still repeat inventory work. Persistent resume remains
  an improvement; do not delete all saved data just because one step failed.
- A run showing Ready can start while optional images remain. Tournament-only
  screens can be mapped when available; setup does not spend a ticket for them.
- **Stop now** stops automation without ending the game battle. **Finish current
  run, then stop** lets automation finish its current run.
- Stop automation before switching accounts/emulators or changing display settings.
  A different emulator connection has separate calibration and needs mapping.

Gathering, policies, repeat counts and timing remain visible in the advanced run
settings and behavior library. Global behaviors separately control Free Store
gems, Daily Missions, Guild progress and Event Missions. Store gems are distinct
from Ad Gems and rotating diamonds; see [Global rewards](docs/GLOBAL_REWARDS.md).

For collection timing evidence, raw event filters/export and measured stream FPS,
see [Observability](docs/OBSERVABILITY.md).

> [!warning] Hands off while it drives
> The autopilot owns the screen while a run is live. If you need the
> emulator, stop the runner first - it never fights a human for the mouse,
> it just aborts. It also never ends a run it did not start, and never
> cancels a tournament run.

---

## Layout

One file per concern, one folder per type - a new capability is a new file
in the folder it belongs to, never a patch inside a monolith.

| Path | What lives there |
|---|---|
| [frontend/](frontend/) | The dashboard app's sources in [frontend/app/](frontend/app/), built into the git-ignored `webui/app/` and served at `/ui/app/` by `backend/dashboard.py`. Its generated endpoint contract is `app/src/contract/generated/` (written by the generators, never edited by hand). `webui/index.html` only redirects old `/ui/index.html#...` bookmarks to the matching app page. |
| [backend/](backend/) root modules | Four loose modules, each for one reason. [settings.py](backend/settings.py) is the import anchor: config loading, paths and the hidden-window subprocess helper that the packages import. The other three are entry points started by path, not imported as libraries: [orchestrator.py](backend/orchestrator.py) (the engine: the observe-decide-act loop that schedules everything below), [gem_collector.py](backend/gem_collector.py) (the diamond collector that harvesting launches) and [dashboard.py](backend/dashboard.py) (the control panel: Flask, port 8620, `TOWER_PILOT_PORT` overrides; it serves the app built from `frontend/`). |
| [backend/flows/](backend/flows/) | **One file per type of run** (coin, tournament, shard, quests). Each declares a `FLOW` spec; the registry makes it schedulable everywhere ([guide](backend/flows/README.md)). |
| [backend/device/](backend/device/) | Talking to the emulator: adb socket client, screen capture, taps/swipes, boot pipeline, ad-overlay cleanup. |
| [backend/vision/](backend/vision/) | Reading pixels: template matching, screen identification, wave-counter OCR, structural pill detection (`pills.py`) and Windows text OCR (`textocr.py`) for the calibrator. |
| [backend/interactions/](backend/interactions/) | Scripted menu flows: reward missions, guild store, workshop shopping, loadout equipping, v29 preset selection, tournament navigation, module shattering. |
| [backend/scheduling/](backend/scheduling/) | Time: the day scheduler (combo), between-run chores, daily counters, the stop-flag contract. |
| [backend/player/](backend/player/) | The account: profile compiler + validator, account scanner (`scan.py`), module/card catalogue. |
| [backend/runtime/](backend/runtime/) | Process plumbing: event log, run-stats collector, tray launcher, test harness. |
| [docs/BEHAVIORS.md](docs/BEHAVIORS.md) | **The in-run behavior map**: every click family (rescue, gems, rewards, UW toggles, sprint, chores), why it exists, and which knob configures it. |
| [backend/config.example.yaml](backend/config.example.yaml) | The machine config template. Copied to `config.yaml` (git-ignored) on first start; the dashboard edits it with timestamped backups. |
| [backend/profiles/](backend/profiles/) | Player profiles. [default.yaml](backend/profiles/default.yaml) is the generic starter; account creation writes a local profile and scanning updates its discoveries. |
| [backend/templates/](backend/templates/) | Image templates the vision layer matches against, cut at native 1080x2560. `cards/preset_*`, `presets/*` and `modules/*` are account-specific. |
| [backend/tests/](backend/tests/) | Backend tests, in separate folders by role. `unit/` holds the test modules (pytest's default path): unit tests plus in-process route tests that run the Flask app over temporary roots with no device. `support/` holds helpers and fakes the tests import (`paths.py` owns the checkout roots). `recording/` holds the generators that write the recorded files in `fixtures/` and the frontend's `tests/fixtures/`. `fixtures/` holds the recorded data, golden profile and frozen traces; `conftest.py` stays at the tests root. |
| [frontend/app/tests/](frontend/app/tests/) | Dashboard app tests, split the same way: `unit/` (Vitest), `e2e/` (Playwright specs, helpers and configs), `support/` (readers and the fake backend shared by both) and `fixtures/` (recorded samples). See [the app README](frontend/app/README.md#test-layout). |
| [docs/](docs/) | Emulator setup guides and the in-run behavior map. |

---

## What is yours and stays out of git

| Path | Content |
|---|---|
| `backend/config.yaml` (+ `.bak-*`) | adb path, emulator serial, preset names |
| `backend/accounts/<id>/calibration/` | Extracted artwork, templates, scan evidence and learned positions, separated by emulator connection |
| `backend/tools/` | Connection tools installed by the app |
| `backend/profiles/<name>.yaml`, `*.draft.yaml` | what your account owns, your run types |
| `backend/catalogue_local.yaml` | module names the calibrator read off the game that the shipped catalogue lacks |
| `backend/logs/` | event logs, screenshots, daily counters |
| `backend/runs/`, `backend/captures/` | run statistics, calibration captures |
| `backend/templates/cards/preset_*`, `presets/{gp,modules,guardians,workshop,bots}_*`, `modules/<slug>.png`, `modules/equipped/` | your card tabs, preset rows and module icons, cut by the cropper |

The only shipped profile is `backend/profiles/default.yaml`, the starter.
Its `player:` block owns nothing - no preset names, no modules, no weapons,
no wall, tier 1 - and every loadout in `config.example.yaml` is empty. The
validator gates ownership where a blueprint *binds* a policy, so the full
policy library ships unbound and the starter still validates; you bind the
Chain Lightning choreography and the rescues after the scan has shown what
the account has. Nothing shipped assumes how you named anything.

---

## Extending: add your own run type

Drop a new file into [backend/flows/](backend/flows/) that declares a
`FLOW` spec - the scheduler, the profile compiler, the tray and the
dashboard all discover it from that one file. No other code changes.
The full contract with a worked example: [backend/flows/README.md](backend/flows/README.md).

The division of labour: **the flow declares, the orchestrator executes.**
A flow whose behaviour fits the generic engine sets `"runner": None` and
puts all its variance in the preset/blueprint the engine reads; a flow
with its own choreography ships its own script in the same file.

---

## House rules (learned the hard way)

1. **Never cancel a tournament run** - the ticket escalates in gem cost.
   Guards live in `backend/interactions/tourney.py`; keep them.
2. **Never end a live run, whoever started it.** A handoff that meets a
   live battle holds until the run ends by itself.
3. **Tower on screen or hands off** - the bot only recovers inside menus
   it opened itself.
4. **All adb goes through `backend/device/adbclient.py`** (socket client).
   Never spawn `adb.exe` per command - window storms and socket exhaustion
   have both lost real runs. Never mix two emulators' adb builds: each
   kills the other's daemon.
5. **Detectors never overwrite their own templates.** User-started setup and
   repair write verified account-local captures through the calibration writer.
6. **Deploy = restart.** Runners never see code edits; kill the process
   and relaunch. Every runner adopts a run already in progress.

---

## Development

Work from a source checkout. In PowerShell, from the project folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
cd frontend/app; npm ci; npm run typecheck; npm run lint; npm test; npm run build; npm run e2e
```

`requirements-dev.txt` pulls in `requirements.txt` and adds pytest;
`requirements.txt` alone has no test runner. Run pytest from the repo root
with no path: `pytest.ini` points it at `backend/tests/unit`, puts `backend` and
the `backend/tests` folders (`support`, `recording`, `unit`) on the import path and
adds `-q -p no:cacheprovider`. To run it explicitly:
`.venv\Scripts\python.exe -m pytest backend/tests/unit`.

The dashboard app needs [Node.js](https://nodejs.org/) 22.13+ (22.x), 24.x or
26+; the release workflow runs its checks on Node 22. `npm test` runs the unit
tests (Vitest), `npm run build` writes `frontend/webui/app/`, and `npm run e2e`
runs the browser suite against a fixture backend (no device, no live
dashboard). On Windows the browser suite runs under Node 22: `npm run e2e`
switches to it when the current Node is another version.

The pure operation ledger is independently installable from the root
`pyproject.toml`. See [local workflow boundaries and package verification](docs/WORKFLOW_ARCHITECTURE_PROPOSAL.md#implementation-boundaries)
for its build command and dependency boundary. The application retains one
local launcher and release.

The backend suite runs offline and needs no emulator.
The runner tests use `backend/tests/support/_fakes.py`, which replaces the
screen-touching modules, so they capture and tap nothing; download paths are
monkeypatched.
`backend/tests/conftest.py` swaps the in-memory `settings.CONFIG` for the
shipped `config.example.yaml` before any test module reads it, and sends event
logs and screenshots to each test's temporary directory instead of
`backend/logs/`. Device lease roots and operation ledgers also use temporary
directories, including legacy dashboard fixtures that retain the checkout root.
The suite still touches the checkout: importing `settings`
creates `backend/config.yaml` from the example when it is missing, dashboard
tests that do not patch the dashboard's `CONFIG_PATH` read this machine's
`backend/config.yaml`, and a few dashboard tests create and then delete
`zz_*.yaml` profiles in `backend/profiles/`. The compiler regression locks run
against the frozen account in `backend/tests/fixtures/`.

Watch the first live occurrence of any new behaviour in
`backend/logs/<instance>/events_*.jsonl` before trusting it.

Run recipes: [Farm, Tournament and Shard farming](docs/RUN_TEMPLATES.md).

Setup and recognition guidance: [Account setup](docs/ACCOUNT_SETUP.md).
Current run controls and verification limits:
[Run lifecycle](docs/RUN_LIFECYCLE_PLAN.md#verification-result).
Preserve profiles and run settings when resetting recognition images.
