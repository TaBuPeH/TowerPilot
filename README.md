# Tower Pilot

**0.4-beta — Windows x64 portable release.** Download the `windows-x64` ZIP,
extract the whole folder, and double-click **Start Tower Pilot.cmd**. Python and
dependencies are bundled; no separate Python installation is needed. The browser
opens Setup automatically. The separate `source` ZIP requires Python 3.12.
See [what changed and the beta limits](docs/RELEASE_0.4_BETA.md).

[Download 0.4-beta](https://github.com/TaBuPeH/TowerPilot/releases/tag/v0.4-beta)
— choose **windows-x64.zip** for the app with Python included, or **source.zip**
for development. Both now carry the full source, the agent rule book
(`CLAUDE.md`, `AGENTS.md`) and the `.claude/` skills and hooks.

Licensed under [MIT](LICENSE): extend the app, add configs and manifests, and
share your improvements. Third-party libraries retain their own licenses.
The license does not grant rights to The Tower's artwork or other game assets.

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-08-24
> **Updated:** 2026-09-10
> **Tags:** autopilot, the-tower, distribution

Vision-driven autopilot for **The Tower** running in an Android emulator
(MuMu Player, BlueStacks, LDPlayer): screenshots in over adb, human-like
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

Use Windows 10/11 x64. Setup uses Windows text recognition. Install an
emulator and The Tower yourself, and sign into your game account first. MuMu and
BlueStacks have been tested. You do not need a separate ADB installation.

For the portable ZIP, launch **Start Tower Pilot.cmd** and continue at step 4.
Steps 1–3 below apply only to the source distribution.

1. Install [Python 3.12](https://www.python.org/downloads/)
   and an Android emulator with The Tower installed. The frame the
   recommended for this beta is **1080 x 2560 portrait at 360 dpi**. On MuMu Player (the
   reference setup) that is the display setting itself; BlueStacks keeps a
   2560 x 1080 landscape panel that the game rotates, and the Setup page's
   **Prepare** button writes it - step by step in
   [docs/BLUESTACKS.md](docs/BLUESTACKS.md).
2. Open PowerShell in the project folder and run `py -3.12 -m pip install -r requirements.txt`.
3. Run `py -3.12 frontend/dashboard.py` and open
   [Setup](http://127.0.0.1:8620/ui/index.html#setup). Keep the Python process running.
   The first start copies `backend/config.example.yaml` to
   `backend/config.yaml` - that file is your machine and is git-ignored.
4. **Setup** page: choose **Install connection tools** to let the Python app
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
py -3.12 frontend/dashboard.py
```

Open [Control on port 8621](http://127.0.0.1:8621/ui/index.html#home).

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
| [frontend/](frontend/) | The control panel: `dashboard.py` (Flask, port 8620, `TOWER_PILOT_PORT` overrides) + `webui/`. Never taps the game except the human-driven template cropper. |
| [backend/orchestrator.py](backend/orchestrator.py) | The engine: the observe-decide-act loop that schedules everything below. Deliberately the one root-level module. |
| [backend/flows/](backend/flows/) | **One file per type of run** (coin, tournament, shard, quests). Each declares a `FLOW` spec; the registry makes it schedulable everywhere ([guide](backend/flows/README.md)). |
| [backend/device/](backend/device/) | Talking to the emulator: adb socket client, screen capture, taps/swipes, boot pipeline, ad-overlay cleanup. |
| [backend/vision/](backend/vision/) | Reading pixels: template matching, screen identification, wave-counter OCR, structural pill detection (`pills.py`) and Windows text OCR (`textocr.py`) for the calibrator. |
| [backend/interactions/](backend/interactions/) | Scripted menu flows: reward missions, guild store, workshop shopping, loadout equipping, v29 preset selection, tournament navigation, module shattering. |
| [backend/scheduling/](backend/scheduling/) | Time: the day scheduler (combo), between-run chores, daily counters, the stop-flag contract. |
| [backend/player/](backend/player/) | The account: profile compiler + validator, account scanner (`scan.py`), module/card catalogue. |
| [backend/runtime/](backend/runtime/) | Process plumbing: event log, run-stats collector, tray launcher, test harness. |
| [backend/BEHAVIORS.md](backend/BEHAVIORS.md) | **The in-run behavior map**: every click family (rescue, gems, rewards, UW toggles, sprint, chores), why it exists, and which knob configures it. |
| [backend/config.example.yaml](backend/config.example.yaml) | The machine config template. Copied to `config.yaml` (git-ignored) on first start; the dashboard edits it with timestamped backups. |
| [backend/profiles/](backend/profiles/) | Player profiles ([schema](backend/profiles/SCHEMA.md)). `default.yaml` is the generic starter; account creation writes a local profile and scanning updates its discoveries. |
| [backend/templates/](backend/templates/) | Image templates the vision layer matches against, cut at native 1080x2560. `cards/preset_*`, `presets/*` and `modules/*` are account-specific. |
| [docs/](docs/) | Emulator setup guides. |

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

## Verifying changes

```bash
python -m pytest
```

The automated suite runs offline from the repo root - every
screen-touching module is faked, and the compiler regression locks run
against the frozen account in `backend/tests/fixtures/`. Watch the first
live occurrence of any new behaviour in
`backend/logs/<instance>/events_*.jsonl` before trusting it.

Run recipes: [Farm, Tournament and Shard farming](docs/RUN_TEMPLATES.md).

Recent live results and remaining limits:
[BlueStacks and MuMu validation notes](docs/BLUESTACKS_SETUP_TEST.md).
Preserve profiles and run settings when resetting recognition images.
