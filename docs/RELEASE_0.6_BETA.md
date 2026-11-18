# 0.6-beta
> **Status:** Released
> **Type:** Release notes
> **Created:** 2026-11-18
> **Updated:** 2026-11-18

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

## Changes since 0.5-beta

- **Leaving a menu no longer touches the battle screen (game 29.2).** Every
  reward flow left its menu by tapping the "Tap To Return To Game" position
  and tapping it again if the menu still looked open. On a busy emulator that
  second tap arrived after the menu had closed and landed on the upgrade
  panel, where game 29.2 opens an Ultimate Weapon's info popup when its card
  is touched. The popup then covered the battle with the run untended, once
  for 53 minutes. The strip is now tapped only where a frame taken for that
  tap shows it, and never twice within two seconds. Quests, guild, the guild
  store, free gems, events, the quest scan and stuck recovery all leave this
  way.
- **The free-gems Store visit stops once the day is spent.** A free-gems card
  already on cooldown, claimed by hand for example, never closed the day, so
  the Store was reopened every five minutes until the UTC date changed (92
  visits on one day, 155 on another). A countdown on the card now closes the
  day. The Store also reopens where it was last scrolled; a card that is not
  on the opening frame is looked for from the top instead of further down.
- **The app follows the game to its own emulator display.** MuMu gives every
  app tab a display of its own. With a Play Store tab open, a game that
  Android had restarted came up on the next display while captures and taps
  kept going to the store page. The game display is now the one the game's
  window is on, and a running farm re-checks once a minute while it cannot
  name the screen.
- **A sighted fleet is nuked on sight.** Three Tier 15 runs died within a few
  waves of a fleet spawning while the Nuke was being held to see whether the
  fleet was dying. The fleet is now found by shape and colour, with no shipped
  image, and the Nuke fires as soon as it is sighted inside the fleet window
  (`fleet.sighting.enabled`).
- **The orbiting-diamond taps must prove the battlefield first.** These are
  the only taps aimed at fixed points, and their one gate was a readable wave
  counter, which stays readable with a panel open over the battle; a tap set
  went into such a panel and bought gems with medals. A set now fires only
  when the screen classifies as battle, no other app's window is in front of
  the game, and the tapped area is moving. An app sitting behind the game is
  not counted as an overlay.
- **"Finish current run, then stop" is removed.** Its flag outlived the
  runner: a farm stopped at its next death, and a farm started the next
  morning stopped again seconds later. Stop now, which never touches the
  game, is the way to stop; only the day plan ends a farm at a run boundary.
- **The daily shatter chore identifies the Shatter tab by its text.** It used
  to tap a fixed position above the tab bar, accept a brightness reading as
  proof of the tab, and select Inventory tiles, one of which it equipped. The
  tab is now tapped where its label is read, and the chore stops when it
  cannot confirm it.

## Carried from 0.5-beta

- No console window; a notification-area icon instead.
- The Dissonance run type, perk bans per run type, and one entry path for
  every coin-kind run.
- The Guild reward flow learns the milestone padlock during play and never
  strands a run on the Guild screen.
- Global behaviour **Fire Demon Mode whenever it is ready**, off by default.
- Fleet spawn waves per tier (`backend/scheduling/fleets.py`, override with
  `fleet.by_tier`).
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
- The Ultimate Weapon info popup that game 29.2 opens from a weapon card is
  not a screen the app can name. The app no longer opens it, but if you open
  it yourself during a farm, close it: the app holds hands-off until the
  battle is visible again.
- The app does not relaunch the game. When Android stops it (a Google Play
  services update does), start the game again and choose **Resume**; a
  running farm finds the game's display by itself and adopts the battle.
- The new return-strip, free-gems and display handling were validated live on
  MuMu at the reference resolution only.
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
