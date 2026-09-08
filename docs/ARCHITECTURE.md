# Architecture

How the pieces of Tower Pilot connect end to end. Verified against the code on
2026-09-08. This page is the map; the details live in the linked docs:

- [README.md](../README.md) - install, folder layout, house rules
- [CLAUDE.md](../CLAUDE.md) - the hard rules every change must respect
- [ACCOUNT_SETUP.md](ACCOUNT_SETUP.md) - the operator's setup path
- [SCAN_PLAN.md](SCAN_PLAN.md) - obtaining recognition images from zero
- [BLUESTACKS.md](BLUESTACKS.md) - BlueStacks specifics
- [backend/BEHAVIORS.md](../backend/BEHAVIORS.md) - in-run behaviors
- [backend/flows/README.md](../backend/flows/README.md) - the FLOW contract
- [backend/profiles/SCHEMA.md](../backend/profiles/SCHEMA.md) - profile schema

Two trees: `frontend/` (the Flask control panel) and `backend/` (everything that
touches the emulator). Coordinates everywhere are native 1080x2560 @ 360 dpi.

## 1. Big picture

```
 browser  --HTTP-->  frontend/dashboard.py  --pythonw spawn-->  backend process
 (webui/index.html)      Flask :8620              |  calibrate.py / scan.py / orchestrator.py
                     (TOWER_PILOT_PORT)          |  flows/*.py / scheduling/combo.py / device/boot.py
                                                 v
                                     device/adbclient.py (socket to adb server :5037)
                                                 |
                                            emulator (BlueStacks / MuMu)
```

The dashboard never imports the runner stack. It spawns one backend process per
job (`pythonw`, cwd `backend/`, no console window) and reads state back from
files: `logs/<instance>/events_*.jsonl`, `calibrate_state.json`, `scan_state.json`.
Runners never see code edits: **deploy = kill + relaunch**.

## 2. Layers (bottom up)

### Device - `backend/device/`
- `adbclient.py` - the ONLY adb transport: a socket client to the adb server
  (`exec_out`, `shell`, `reconnect`, streamed `exec_to`). Never spawns adb per
  command (hard rule 1). Daemon lifecycle (`connect`, `start-server`) goes
  through `settings.run_hidden` / the dashboard's `_run`.
- `capture.py` - `grab()` (raw `screencap`, resolution enforced, MuMu display
  churn handled by `refresh_display`), `roi(frame, name)` with the panel-open
  `layout_offset`.
- `displays.py` - pure parsing of `dumpsys SurfaceFlinger` / `dumpsys display`:
  which MuMu display holds the game (`game_display`) and `strip_warning` for
  the text MuMu prints ahead of a PNG. Shared by `refresh_display` and the
  dashboard's own captures (`/api/frame.png`, `/api/stream.mjpg`,
  `/api/wizard/resolution`), which derive the display on demand and cache it
  per serial - the id changes on every emulator restart and adopt records
  only serial + adb.
- `act.py` - `tap` / `swipe` with jitter, rate cap, human-like hold and the
  per-instance `allow_taps` gate (`TapRefused`).
- `boot.py` - boot pipeline `adb -> android -> overlays.clean -> am start ->
  known screen`; logs `boot_stage` / `boot_done`. `overlays.py` closes known ad
  windows only (unknown windows are logged, never blind-tapped).

### Settings + accounts - `backend/settings.py`, `backend/player/accounts.py`
- `settings.select_instance(name, preset)` binds ONE instance per process from
  `backend/config.yaml` (machine file, git-ignored; template
  `config.example.yaml`). `CONFIG` is the shared module-global dict.
- `accounts.py` is a pure resolver. A bound account keeps its calibration under
  `backend/accounts/<id>/calibration/<instance>-<sha(serial|adb)[:10]>/` with its
  own `templates/`; an unbound install uses `backend/logs/<instance>/` and
  `backend/templates/`. Every image read/write goes through
  `settings.template_path` / `accounts.template_path` - never a hard-coded
  fallback to the legacy dir.

### Vision - `backend/vision/`
- `wave_reader.py` - reads the wave counter from `digits/0-9.png` (all ten or
  `RuntimeError`; the number is the "tower on screen" proof every runner rests
  on). `WaveTracker` sanity-checks jumps.
- `screen.py` - `identify(frame)`: overlays first (`OVERLAYS` table: game
  stats, exit/end-round dialogs, tournament, welcome-back...), then the menu
  header word (`screens/hdr_*.png` in `HEADER_BAND`), else battle vs home.
- `textocr.py` - Windows OCR (`tools/winocr.ps1`), `read_lines` / `read_text`.
- `pills.py` - template-free detection of the game's outlined controls.
- `detect.py` - in-run state (sprint, second wind, buttons, HP/wall bars...).

### Setup pipeline - `backend/player/`
```
POST /api/calibrate/start {bootstrap:true, flows:true}
  -> calibrate.py --bootstrap --flows
     -> bootstrap.py  Scanner.run(): Home -> manifest routes (menus) -> _run_flows() -> verify Home
        -> flow_capture.py  run_flows(): capture_menu_extras, capture_battle_flow
           -> battle_capture.py  OCR/pixel primitives (digit_glyphs, capture_by_text ...)
```
- `bootstrap_manifest.json` is TEXT ONLY: screen anchors, target rects, routes,
  `dynamic_targets`. `bootstrap_layout.writable_targets()` derives the
  **allowlist** of what the consented setup may write.
- `calibrate.py::Calibration.cut` is the gate: a rel must be account-specific
  (`is_account_rel`) or on the bootstrap allowlist; a cut must self-match,
  be unique (unless `unique=False` sibling buttons), and it never replaces a
  file without `--overwrite`. `calibrate_report.json` records every verified cut.
- `flow_capture.py` is the ONLY setup code that starts and ends a run (hard
  rule 3), consented via the dashboard popup and gated on a clean Home
  (`can_start_battle`). Its passes:
  - `_hud_digit_pass` - Tier 1 (the tower survives): reads the climbing wave
    counter to cut `digits/0-9` (the one digit source that needs no digits)
    and the owned Ultimate Weapon labels. No-op once known.
  - `_battle_pass` - the account's top tier: intro-sprint dialog, then
    surrender immediately for END ROUND / exit dialog, GAME STATS, RETRY / HOME,
    reward skip.
  - `capture_menu_extras` - Events / Store / Guild controls.
  - `_uw_lottery_pass` (opt-in) - the unowned UW labels via Tier-1 perk grants.
- Ownership evidence: the in-run UW panel lists owned weapons only, so a
  verified `uw/<name>.png` cut proves ownership. `calibration_report.owned_uws`
  turns the entries into `player.uws` facts; the Scanner records them in the
  calibrate state and `POST /api/calibrate/apply` ("Use discovered presets in
  my profile") merges them into the profile - the fact the compiler gates on
  when a blueprint binds a Chain Lightning plan. The run editor greys out
  plans the account cannot bind yet (`uwPlanBlocked`) with the reason.
- Unknown is not knowledge (2026-09-08). The starter profile seeds
  `max_tier: 1`, `wall: false`, `abilities_verified: false`, `uws: all false`
  for an account nobody has looked at, and none of those refuse a binding
  any more. What a run REALLY needs is a per-run readiness requirement that
  greys it out with the item named: `buttons/<ability>.png` for every ability
  a rescue fires, `config: rois.wall_bar` for a wall watch (`capture.roi`
  raises without it), `uw/*.png` + toggles for a weapon plan. Setup proves
  what it can - UW labels off the panel (`owned_uws`), the wall bar off the
  HUD (`flow_capture.wall_bar_present`, positive-only, writes the region via
  Apply), the top tier off the climb - and the compiler turns the rest into
  `warnings()` advisories. Verified knowledge that says NO still refuses
  (`abilities_verified: true` with `demon_mode: false`).
- No max tier. `player.max_tier` is a hint only - the highest tier seen
  unlocked, written by the setup's top-tier climb (`_battle_pass`, per
  `setup`) or typed by the operator - and a run tier above it is a
  `warnings()` advisory, never a `validate()` refusal (user ruling 2026-09-08).
- Artwork finds controls (2026-09-08): the manifest's `asset_bindings` name
  the installed game's own sprites for a target, `asset_verify.locate` finds
  them in a live frame (SIFT + affine, two frames must agree), and
  `Scanner.verify_asset_rels` cuts the on-screen pixels - menu screens via
  `verify_assets`, the HUD ability buttons and UW switches via
  `flow_capture.capture_artwork_targets` inside the Tier-1 battle. The
  dashboard shows the same artwork beside every target (`/api/artwork/<rel>`,
  `template_docs` names it) next to the cut it made (`/api/template/<rel>`),
  green when identified, red when failing.
- Where artwork cannot work, position does (2026-09-08, after the user hand-
  cropped four targets setup had missed): the in-run HUD's cart tile and
  hamburger are `icon: true` targets in the manifest's `hud` section (screens
  `battle` / `battle_menu`, read by `bootstrap_layout.screen_targets`), cut at
  their fixed rect by `icon_present` - hue red/cyan/purple/gold/green, or
  `bars` = three interior white bands for the hamburger (a plain "white"
  check once accepted Home's chevron: Home's top-right is a chevron and the
  Missions box, the HUD tiles exist only during a run). The in-run side menu
  (`flow_capture.capture_side_menu`, manifest `side_menu`) runs inside the
  Tier-1 digit run: found shut it taps the hamburger, cuts the column's
  artwork-bound tiles (`icons/tile_quests.png` = MissionsIcon, screen
  `battle_menu`) and the green X, and taps it shut again; found open it reads
  and leaves it. `capture_hud_targets` cuts the HUD icons in both battle
  passes. The Second Wind badge is bound to the
  `SecondWind` sprite over `detect.SW_BAND` (`HUD_STATE_TARGETS`): both battle
  passes try it, and `bootstrap.observe(p, watch=N)` (`--observe-watch`,
  `/api/calibrate/observe {watch}`, "Watch the screen for 2 min") keeps
  looking - about once a second, no taps - through the person's own run. The
  Primary/Assist slot prompt cuts its own buttons when the Modules scan's
  restore step meets it (`module_roundtrip._cut_slot_buttons`); v29 equips by
  category, so readiness no longer requires them. Every cropper cut leaves a
  `cropper` report entry with its native rect and frame size
  (`calibrate.record_manual_cut`) - provenance the four hand crops lacked.
- The learned manifest (`player/learned.py`, `learned_manifest.json` beside
  `calibrate_state.json`, account-local): "we build the MANIFEST ALWAYS and
  the screen we are on is part of the manifest - navigate to screen, crop,
  that is all" (user, 2026-09-08). Every verified cut whose `extra` carries a
  native `rect` and a `screen` is recorded there by `Calibration.cut` - setup
  cuts (`Scanner.cut` stamps `self.current`), artwork hits (the binding's
  screen), dashboard crops (`bootstrap.recognize_screen` names the frame's
  screen by the manifest anchors, `battle` by a readable wave, `battle_menu`
  by the wave plus the side menu's green X). From then on `Scanner.learned_cuts(screen, frame,
  follow)` cuts every learned target still missing at the same rect and size
  whenever the scan stands on that screen (`harvest`, `side_menu`, the battle
  passes via `flow_capture.capture_learned_targets`, and the observe pass
  after recognizing the screen it sees - which also cuts the shipped
  manifest's own targets for that screen); a shipped colour or text check
  for the target must pass too, otherwise the proven screen plus the earlier
  crop at that spot is the evidence. The scan plan and readiness cards show
  the learned spot, and the cropper pre-draws the box there.
- Template writers, exactly three (hard rule 6): the dashboard cropper
  (`api_template_save`), `calibrate.write_template` (every calibrate phase,
  bootstrap and flow cut), `battle_capture.write_template` (digits, and the
  runner-side MISSING-only UW subroutine `capture_missing_uw_labels`).
- `scan_plan.py` - the human acquisition plan (`STEPS`), `scan_targets.json`
  (text-only `{rel, reference_size}`), `missing_navigation` (may setup tap at
  all?) and `control_gate` (what `/api/status.calibration` shows).
- `readiness.py` - per compiled run: `CORE` recognition guards (menu headers,
  battle dialogs, digits - blocking for every run) + `ADVISORY` guards for
  screens setup cannot produce on demand (tournament, welcome-back, reward
  skip - listed, blocking only for the `tournament` kind) + what the run's own
  features need (flow templates, gather, shopping, UW, rules, presets).
  `check()` returns `ready` / `missing` / `advisory`; it drives
  `/api/readiness`, the per-run `readiness` map in `/api/runs` (the Control
  picker greys out runs that lack images and names them in a tooltip), and
  refuses `/api/control start`. The Control tab itself opens once a scan has
  finished (`scan_plan.control_gate`), attention flags or not.
- `scan.py` - read-only account survey (cards / modules / guardians / battle)
  that seeds `profiles/<name>.draft.yaml`.

### Runtime - `backend/orchestrator.py`, `flows/`, `interactions/`, `scheduling/`
- `orchestrator.py` - the observe-decide-act engine: `watch_frame`, compiled
  rule interpreter, restart / sprint / death handling.
- `flows/__init__.py` - registry of run kinds. Each `flows/<name>.py` declares a
  literal `FLOW` spec (templates it needs, handoff mode, argv). Kinds: `coin`,
  `shard`, `tournament`, `cycle_quest` (quest_ilm), `uw_grant_quest` (quest_sm).
- `interactions/` - one module per game surface: `tourney.py` (tournament
  entry + the shared `ensure_home` / `end_round` guards), `loadout.py`,
  `presets.py`, `shopper.py`, `store.py`, `missions.py`, `inventory.py`.
- `scheduling/combo.py` - the day scheduler; `runflag.py` - phase switches at
  run boundaries only (hard rule 5); `chores.py`, `daystate.py`.
- `player/playerprofile.py` - the blueprint compiler: `profiles/<name>.yaml`
  -> `bp_<name>` presets with `runner` / `runner_args`. The dashboard mirrors
  it in `_compiled_runs`.
- `runtime/logger.py` - `event()` / `shot()` into the newest
  `logs/<instance>/events_<stamp>.jsonl` (one file per process start).

### Frontend - `frontend/`
- `dashboard.py` - Flask, port `TOWER_PILOT_PORT` (default 8620). Process
  control (`/api/control`, `/api/scan/start`, `/api/calibrate/start`), status
  (`/api/status`, `/api/readiness`, `/api/runs`), the emulator wizard
  (`/api/wizard/emulators|launch|prepare|adopt|reconnect|resolution`), accounts
  (`/api/accounts`), the cropper (`/api/frame.png`, `POST /api/template/<rel>`),
  profile editing (`/api/profile*`).
- `webui/index.html` - the whole SPA (setup -> calibrate -> control, gated).
  `startBootstrap()` shows the consent popup; Cancel means `flows:false`
  (menus only, no battle). Uses vendored Pico CSS, never a CDN.

## 3. Emulator wizard
`/api/wizard/emulators` finds installed adb binaries (MuMu, BlueStacks, LD) and
instances - MuMu via `MuMuManager info`, BlueStacks by parsing
`%ProgramData%\BlueStacks_nxt\bluestacks.conf`. **Launching is done from the
UI**: `/api/wizard/launch` runs `MuMuManager control -v <i> launch` or
`HD-Player.exe --instance <key>`, then hands off to `device/boot.py`.
`/api/wizard/bluestacks/prepare` writes the ADB switch and the 2560x1080 @ 360
panel into `bluestacks.conf` (see BLUESTACKS.md). Foreign adb binaries are
never run against each other (protocol mismatch kills the daemon).

## 4. State on disk
| Path | What |
|---|---|
| `backend/config.yaml` | machine config: adb, instances, bound accounts, loadouts |
| `backend/accounts/<id>/calibration/<inst>-<hash>/` | the account's templates, `calibrate_state/report.json`, `scan_state.json`, evidence |
| `backend/logs/<instance>/events_*.jsonl` | one event log per process start (+ screenshots) |
| `backend/logs/daily_state.json`, `setup_done`, `wizard_state.json` | day counters, setup gate, wizard sections |
| `backend/runs/<instance>/<stamp>/` | per-run stats |
| `backend/profiles/*.yaml` | player profiles (`default.yaml` ships owning nothing) |

Nothing under `accounts/`, `templates/`, `logs/`, `runs/` or `config.yaml` is
tracked; `tests/test_release_clean.py` fails if account data slips into git.

## 5. Tests
`python -m pytest` from the repo root (offline). `backend/tests/_fakes.py`
fakes every screen-touching module; `settings` stays real. Notable:
`test_flow_capture.py` (consented capture decisions), `test_bootstrap_manifest.py`
(manifest <-> allowlist), `test_release_clean.py`, `test_ui_contract.py`.
Frontend: `node --test frontend/tests/`.
