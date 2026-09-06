# Tower Pilot

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-08-24
> **Updated:** 2026-09-06
> **Tags:** autopilot, the-tower, distribution

Vision-driven autopilot for **The Tower** running in an Android emulator
(MuMu Player, BlueStacks, LDPlayer): screenshots in over adb, human-like
taps out. No memory reading, no modded APK, everything stays on your
machine.

## Quick start

1. Install [Python 3.12](https://www.python.org/downloads/) (3.10 is the
   floor) and an Android emulator with The Tower installed. The frame the
   autopilot sees must be **1080 x 2560 portrait at 360 dpi** - every
   template and coordinate assumes that layout. On MuMu Player (the
   reference setup) that is the display setting itself; BlueStacks keeps a
   2560 x 1080 landscape panel that the game rotates, and the Setup page's
   **Prepare** button writes it - step by step in
   [docs/BLUESTACKS.md](docs/BLUESTACKS.md).
2. `pip install -r requirements.txt` (add `-r requirements-dev.txt` to run
   the tests).
3. `python frontend/dashboard.py` and open <http://127.0.0.1:8620/>.
   The first start copies `backend/config.example.yaml` to
   `backend/config.yaml` - that file is your machine and is git-ignored.
4. **Setup** page: the wizard lists the emulators it finds. **Start it**
   launches the instance (MuMu through MuMuManager, BlueStacks through
   `HD-Player.exe`), points the config at it on a fresh install, and runs
   the boot pipeline that waits for Android, clears overlays and starts
   the game. Then check the display resolution.
5. **Calibrate** page, with the game on its home screen: press **Calibrate
   now**. The bot walks Cards, the preset picker, Modules, Guild, Event and
   Workshop, finds every preset control by its outline, reads the name you
   gave it, cuts the template and verifies it (`player/calibrate.py`).
   Those pictures - card preset tabs, global and category preset rows, your
   modules at your rarity - are the game's art and yours, so the repo never
   ships them; the generic buttons and screens do ship. The cropper on the
   same page is the manual fallback for a cut that came out weak.
6. **Analyze the account**: *Scan account* on the Setup page reads the
   guardians, card presets and modules (add the battle phase for ultimate
   weapons and abilities) into `profiles/<instance>.draft.yaml`, then
   **Promote** it on the Home page into a runnable
   `profiles/<name>.yaml` and activate it.
7. Everything starts **read-only**: `allow_taps: false` in `config.yaml`
   makes every runner refuse every tap. Run one read-only pass, check the
   screen names and template scores in `backend/logs/<instance>/`, then
   flip `allow_taps` in the Configuration page.

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
| [backend/profiles/](backend/profiles/) | Player profiles ([schema](backend/profiles/SCHEMA.md)). `default.yaml` is the generic starter; yours is written by *Promote* (or *Copy the starter* on the Home page) and git-ignored; the dashboard refuses to edit the starter in place. |
| [backend/templates/](backend/templates/) | Image templates the vision layer matches against, cut at native 1080x2560. `cards/preset_*`, `presets/*` and `modules/*` are account-specific. |
| [docs/](docs/) | Emulator setup guides. |

---

## What is yours and stays out of git

| Path | Content |
|---|---|
| `backend/config.yaml` (+ `.bak-*`) | adb path, emulator serial, preset names |
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
5. **Detectors never overwrite their own templates.** The only template
   writer is the cropper on the Calibrate page, driven by a person.
6. **Deploy = restart.** Runners never see code edits; kill the process
   and relaunch. Every runner adopts a run already in progress.

---

## Verifying changes

```bash
python -m pytest
```

The suite (790+ tests) runs entirely offline from the repo root - every
screen-touching module is faked, and the compiler regression locks run
against the frozen account in `backend/tests/fixtures/`. Watch the first
live occurrence of any new behaviour in
`backend/logs/<instance>/events_*.jsonl` before trusting it.
