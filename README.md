# Tower Pilot

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-08-24
> **Updated:** 2026-09-05
> **Tags:** autopilot, the-tower, distribution

Vision-driven autopilot for **The Tower** running in an Android emulator
(MuMu Player, BlueStacks, LDPlayer): screenshots in over adb, human-like
taps out. No memory reading, no modded APK, everything stays on your
machine.

## Quick start

1. Install [Python 3.12](https://www.python.org/downloads/) (3.10 is the
   floor) and an Android emulator with The Tower installed. The emulator
   display must be **1080 x 2560 portrait at 360 dpi** - every template and
   coordinate assumes that layout. MuMu Player is the reference setup;
   BlueStacks is covered step by step in [docs/BLUESTACKS.md](docs/BLUESTACKS.md).
2. `pip install -r requirements.txt` (add `-r requirements-dev.txt` to run
   the tests).
3. `python frontend/dashboard.py` and open <http://127.0.0.1:8620/>.
   The first start copies `backend/config.example.yaml` to
   `backend/config.yaml` - that file is your machine and is git-ignored.
4. **Setup** page: the wizard finds your emulator's adb, connects, writes
   the serial into `config.yaml`, and checks the display resolution.
5. **Calibrate** page: the *Required for your account* list names every
   picture only your account can provide (card presets, global and
   category presets as you named them, your modules at your rarity). Open
   the screen in the game, drag a box on the live frame, save. The generic
   buttons and screens ship with the repo.
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
| [backend/vision/](backend/vision/) | Reading pixels: template matching, screen identification, wave-counter OCR. |
| [backend/interactions/](backend/interactions/) | Scripted menu flows: reward missions, guild store, workshop shopping, loadout equipping, v29 preset selection, tournament navigation, module shattering. |
| [backend/scheduling/](backend/scheduling/) | Time: the day scheduler (combo), between-run chores, daily counters, the stop-flag contract. |
| [backend/player/](backend/player/) | The account: profile compiler + validator, account scanner (`scan.py`), module/card catalogue. |
| [backend/runtime/](backend/runtime/) | Process plumbing: event log, run-stats collector, tray launcher, test harness. |
| [backend/BEHAVIORS.md](backend/BEHAVIORS.md) | **The in-run behavior map**: every click family (rescue, gems, rewards, UW toggles, sprint, chores), why it exists, and which knob configures it. |
| [backend/config.example.yaml](backend/config.example.yaml) | The machine config template. Copied to `config.yaml` (git-ignored) on first start; the dashboard edits it with timestamped backups. |
| [backend/profiles/](backend/profiles/) | Player profiles ([schema](backend/profiles/SCHEMA.md)). `default.yaml` is the generic starter; yours is written by *Promote* and git-ignored. |
| [backend/templates/](backend/templates/) | Image templates the vision layer matches against, cut at native 1080x2560. `cards/preset_*`, `presets/*` and `modules/*` are account-specific. |
| [docs/](docs/) | Emulator setup guides. |

---

## What is yours and stays out of git

| Path | Content |
|---|---|
| `backend/config.yaml` (+ `.bak-*`) | adb path, emulator serial, preset names |
| `backend/profiles/<name>.yaml`, `*.draft.yaml` | what your account owns, your run types |
| `backend/logs/` | event logs, screenshots, daily counters |
| `backend/runs/`, `backend/captures/` | run statistics, calibration captures |

The only shipped profile is `backend/profiles/default.yaml`, the starter.
Its `player:` block is placeholder data so the blueprints validate; no
blueprint in it binds a rescue policy, because the account's abilities are
unverified until the scan's battle phase has seen the buttons work.

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
