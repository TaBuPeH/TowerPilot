# Tower Pilot

Vision-driven autopilot for "The Tower" in an Android emulator: adb
screencaps in, human-like taps out. No memory reading, no modded APK.
Layout and setup: [README.md](README.md). Coordinates are native 1080x2560
portrait at 360 dpi.

## Hard rules (violating these has lost real runs)

1. **adb via socket only.** Every screencap, tap and shell command goes
   through `backend/device/adbclient.py` (socket client to the adb server on
   5037), wrapped by `capture.grab` / `act.tap` / `act.swipe`. Never spawn
   the adb binary per command: pythonw parents pop a console window per
   spawn and mass spawns exhausted Windows socket buffers and dropped the
   device mid-run. The one sanctioned spawn is daemon lifecycle
   (`kill-server` / `start-server` / `connect`) through `settings.run_hidden`
   or the dashboard's `_run`.
2. **Never cancel a tournament run.** Ticket purchase auto-starts the run
   and the gem cost escalates 10 -> 20 -> 30. Guards: `tourney.end_round`,
   `tourney.ensure_home`.
3. **Never end a live run, whoever started it.** `tourney.ensure_home`
   holds on a live battle until it ends by itself; the shard loop
   surrenders only runs it started; the scheduler spawns runners in adopt
   mode over a battle its own handoff put on screen. The one place setup
   ends a run is `player/flow_capture.py`: the Full-setup starter scan
   starts its OWN normal run to capture the intro-sprint/exit/GAME STATS
   screens and surrenders that same run - human-started, consented via the
   dashboard popup, gated on a clean Home (`can_start_battle`), and refused
   over any run it did not start or any tournament.
4. **Tower on screen or hands off.** No taps while a human owns the screen;
   recovery taps only in menus the bot itself opened (`rs.bot_left_battle`
   in the orchestrator, `_hold_while_unknown` in the scheduler).
5. **Phase switches at run boundaries only** - the runflag contract
   (`backend/scheduling/runflag.py`): write the flag, the runner leaves at
   its death handler. Never kill a live run to switch activities.
6. **Detectors never overwrite their own templates.** The only template
   writers are the dashboard cropper and `player/calibrate.py`, both
   started by a person; the calibrator writes account-specific names or the explicit text-only
   bootstrap manifest allowlist, and never replaces a file unless told to
   (`--overwrite`). All resulting images remain local. Human-started setup may also extract
   installed APK artwork into its local asset library; these source images never
   become runtime templates until verified on the emulator screen.
   The one runner-side writer is the opt-in Ultimate-Weapon-label subroutine
   (`flow_capture.capture_missing_uw_labels`, called from `flows/quest_sm.py`):
   the two unowned UW render only while a Tier-1 perk grant holds them, so rather
   than block setup on that lottery, each grant scan captures any still-MISSING
   UW label from the panel it already has open. It writes account-local,
   git-ignored templates through the sanctioned writer, MISSING targets only,
   and NEVER replaces a file - a near-free no-op once every UW is known.
7. **An Abort means the screen was not what the code expected.** Stop and
   log; never blind-tap into an unknown screen.
8. **Machine and account state stay out of git**: `backend/config.yaml`,
   `backend/profiles/<name>.yaml`, logs, runs, captures, and the player's
   own template cuts (see `.gitignore`). **Shipped files carry no account
   data**: no preset names, no module icons or equipped/inventory tables,
   no tiers above 1, no deck tweaks - `config.example.yaml` loadouts are
   empty, `backend/profiles/default.yaml` owns nothing, and
   `backend/tests/test_release_clean.py` fails when something slips back.
   The Calibrate cropper, the scan and the config editor produce all of it
   per install. The compiler regression fixture is
   `backend/tests/fixtures/golden_profile.yaml`.

## Ops knowledge

- Bound accounts keep calibration under `backend/accounts/<id>/calibration/`
  (git-ignored), separate for each emulator connection. Use `settings.template_path`
  / `settings.template_files` or the pure `player.accounts` resolver for every
  image read/write. Never add a direct personal-template fallback to the legacy
  directory. Account setup and remaining generic-bootstrap work are documented
  in `docs/ACCOUNT_SETUP.md`.

- The learned manifest `backend/accounts/<id>/calibration/<inst>/learned_manifest.json`
  (git-ignored) records the screen and native rect of every verified cut, hand
  crops included; setup and the observe pass cut missing targets from it on
  that screen at the same size (`player/learned.py`, `Scanner.learned_cuts`).

- **Deploy = kill + relaunch.** Runners never see code edits. Every runner
  adopts in-progress state (live battle -> straight to the observe loop).
- Detached spawns on Windows: `Start-Process pythonw -WorkingDirectory
  backend`.
- Logs: `backend/logs/<instance>/events_*.jsonl`, one file per process
  start; a monitor must re-resolve the newest file. Screenshots land next
  to them. Run stats: `backend/runs/<instance>/<stamp>/`.
- Templates are account- and rarity-specific for card preset tabs, preset
  picker rows and module icons (`templates/cards/preset_*`,
  `templates/presets/{gp,modules,guardians,workshop,bots}_*`,
  `templates/modules/<slug>.png`, `templates/modules/equipped/`) and are
  NOT shipped: the Calibrate button cuts them from the player's own screens
  (structural pill detection in `vision/pills.py`, names by Windows OCR in
  `vision/textocr.py`) and git ignores them. Missing ones surface as
  `template_missing` events and on that page.
- Ownership is validated where a blueprint BINDS a policy, not where the
  policy is defined: the policy library ships in full for an account that
  has scanned nothing.
- Per-machine rendering differences are handled by variant tuples
  (`presets.PICKER_ICONS`, `tourney.BATTLE_BUTTONS`) and the
  `templates/floaters/gem_*.png` glob, never by lowering thresholds.

## Verification habits

- After edits: `python -m py_compile <files>` before any relaunch.
- `python -m pytest` from the repo root (offline, 790+ tests).
- Template work: self-match must score ~1.0, next-best clearly below.
- New behavior: watch the first live occurrence in the events log before
  trusting it.

## Image-free release boundary

No game images ship: buttons, screen headers, digits, fonts, module art and
screenshots all come from the user running the game. There is no generic-image
fallback. Keep all captured pixels local and ignored. `player/scan_targets.json`
contains text-only capture metadata; `player/scan_plan.py` defines acquisition.
Tests generate synthetic fixtures; never restore game PNGs to make tests pass.
