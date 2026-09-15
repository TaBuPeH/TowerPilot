# 0.5-beta

Recommended download: **windows-x64 portable ZIP** for Windows 10/11 x64.
Extract the whole ZIP into a writable folder, then double-click **Start Tower
Pilot.cmd**. Python and dependencies are already bundled. No console window
opens: Tower Pilot sits in the notification area near the clock. Click that
icon to open the dashboard, right-click it to quit. Your browser opens Setup
on the first start.

The separate **source ZIP** requires Python 3.12 and installs dependencies on
first launch. Neither package includes an emulator or the game.

Use **1080 × 2560 portrait at 360 DPI**. Allow **20–30 minutes** for mapping,
plus installation/download time. End existing battles and start mapping on Home.
Choose your own farming tier and equipment after mapping.

## Changes since 0.4-beta

- **No console window; an icon in the notification area instead.** The
  launcher was a console that had to stay open, and closing it took the
  dashboard with it. `Start Tower Pilot.cmd` now starts `pythonw`, and the
  launcher places an icon near the clock: click it to open the dashboard,
  right-click it to quit. Quitting closes only the dashboard; a run in
  progress is a separate process and keeps going, as before. Output moves to
  `backend/logs/launcher.log`, a second double-click opens the dashboard
  already running instead of starting another, and a machine with no usable
  tray keeps serving without an icon. The icon is drawn at startup, so no
  image file ships.
- **A new run type: Dissonance.** Farm-style coin runs entered through the
  game's **Dissonant Run** dialog with one workshop tab disabled (Attack,
  Defense, Utility or Ultimate Weapons), for the permanent Dissonant Boost.
  It carries its own equipment, no upgrade shopping, an optional
  every-owned-weapon policy with Chain Lightning always on, and an optional
  wall rescue that also fires the fleet-mark Nuke. The compiler trims what the
  disabled tab makes meaningless: shopping on that tab is dropped, and
  disabling Ultimate Weapons also drops the weapon toggles. Add it from the
  run templates in Control, then capture that tab's dialog images in
  Calibrate.
- **Perk bans per run type.** A coin or tournament run type may carry a list
  of perks to keep banned while it plays, written as fragments of the perk's
  own wording (`coins, but tower max health`); the numbers are ignored because
  they change with lab levels. Before the run is entered the app opens Home,
  Perks, Ban Perks, reads the rows, toggles only rows it has read, verifies
  each toggle and the final banned list, then closes the dialog. Anything
  unexpected aborts with a screenshot and the run still starts. The applied
  list is remembered per account, so the dialog is opened again only when a
  run type wants a different one.
- **One entry path for every coin-kind run.** A person's Start, the restart
  after a death and the day plan's handoff all enter through the same
  function. Two of them previously tapped BATTLE and would have farmed a
  normal run under a dissonance run type.
- **Dissonant dialog images are required only for self-restart.** A run you
  start by hand, which the app adopts, plays without them; an entry refused
  from Home is logged with a screenshot and the app holds on Home instead of
  trying again blindly.

## Carried from 0.4-beta

- The Guild reward flow learns the milestone padlock during play, skips the
  claim pass without it, and never strands a run on the Guild screen; a
  reward flow that raises is dropped and takes the return strip.
- Global behaviour **Fire Demon Mode whenever it is ready**, off by default,
  for the Demon Mode kill quests.
- Fleet spawn waves per tier, picked from the run's own tier
  (`backend/scheduling/fleets.py`, override with `fleet.by_tier`).
- Emulator ad overlays are dismissed on window-list evidence; unknown
  overlays are refused by name.
- Quest CLAIM and END ROUND are learned during play, missing target only,
  never replacing a file.
- Both ZIPs carry the full source with tests, `CLAUDE.md`, `AGENTS.md`, the
  `.claude` skills, agents and hooks, `.codex/config.toml` and the release
  workflow. No captured image ever ships.

## Beta limits

- The portable build is an extracted folder, not an installer. Keep all files
  together in a writable location. Local account data lives in that folder.
- The perk-ban flow has automated tests but has not yet run against a live
  Perks dialog. Watch for a `perk_bans_applied` or `perk_bans_failed` event
  the first time a run type with bans starts.
- A Dissonance run type can only re-enter the mode by itself once that tab's
  dialog images are captured; until then start the mode by hand and let the
  app adopt the run.
- HUD Ad Gems stop appearing once the game's daily allowance is spent, so a
  long day ends with quiet hours. That is the game, not the collector.
- Resolution scaling is experimental. Other resolutions, including
  1080 × 1920, have not completed full validation. Use the reference display.
- MuMu native-resolution coin farming has live validation. BlueStacks has been
  tested but has needed scan retries and targeted repairs.
- Only MuMu Store is a known ad owner. Another emulator's ad shows up as an
  `overlay_unknown` event naming the window; report it so it can be added.
- The fleet table for Tiers 1–13 and 15–24 comes from published data, not from
  runs of this app; report a mismatch and override it meanwhile with
  `fleet.by_tier`.
- Optional or locked features can remain unverified. A Ready run does not mean
  every possible screen or emergency effect has been exercised.
- Stop automation before manually using the emulator. Stop now preserves a live
  battle; it does not surrender it.

The tagged prerelease is built by `.github/workflows/release.yml`. A full scan
on a pristine Windows installation remains a beta validation task.
