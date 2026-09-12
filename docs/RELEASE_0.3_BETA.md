# 0.3-beta

Recommended download: **windows-x64 portable ZIP** for Windows 10/11 x64.
Extract the whole ZIP into a writable folder, then double-click **Start Tower
Pilot.cmd**. Python and dependencies are already bundled. The launcher opens
your browser. Follow Setup, Remap, and Control. The app installs ADB locally;
this connection-tool download requires internet access.

The separate **source ZIP** requires Python 3.12 and installs dependencies on
first launch. Neither package includes an emulator or the game.

Use **1080 × 2560 portrait at 360 DPI**. Allow **20–30 minutes** for mapping,
plus installation/download time. End existing battles and start mapping on Home.
Choose your own farming tier and equipment after mapping.

## Changes since 0.2-beta

- **Fleet timetable per tier, picked automatically.** The fleet-mark Nuke,
  the Chain Lightning fleet choreography, the danger-window shots and the
  shard loop all keyed off one fixed 2495 + 1000k schedule, which is only
  right on Tier 14. `backend/scheduling/fleets.py` is now the single spawn
  table (Tower Hub, cross-checked against the community wiki, confirmed live
  on Tier 14): Tiers 1–13 from 15000 minus 250 per tier every 100; Tier 14
  2495 / 1000, Tier 15 1495 / 750, Tier 16 995 / 500, Tier 17 495 / 250,
  Tier 18 95 / 100, Tier 19 45 / 50, Tier 20 and up wave 5 every 10 (one,
  two, then three fleets); bonus fleets every 100 from 11750 minus 250 per
  tier on Tier 14 and up. The run's tier comes from its blueprint and a
  `fleet_schedule` event at start names the row in use. `fleet.by_tier` in
  config overrides a tier; the old `first_wave` / `interval` pair only
  applies to a preset that names no tier, so existing configs keep working.
- **Shard loop follows the same table.** Its sprint-cancel and Nuke waves
  derive from the block's tier (Tier 18 stays 100 / 101; Tier 19 becomes
  50 / 51) instead of hardcoded Tier 18 constants.

## Carried from 0.2-beta

- Emulator ad overlays are dismissed on window-list evidence, never refused
  on sight; unknown overlays are refused by name.
- Quest CLAIM, the weekly-chest lock and END ROUND are learned during play
  (missing target only, never replacing a file) instead of holding setup or
  a run.
- Both ZIPs carry the full source with tests, `CLAUDE.md`, `AGENTS.md`, the
  `.claude` skills, agents and hooks, `.codex/config.toml` and the release
  workflow. No captured image ever ships.

## Beta limits

- The portable build is an extracted folder, not an installer. Keep all files
  together in a writable location. Local account data lives in that folder.
- Resolution scaling is experimental. Other resolutions, including 1080 × 1920,
  have not completed full validation. Use the reference display above.
- MuMu native-resolution coin farming has live validation. BlueStacks has been
  tested but has needed scan retries/targeted repairs.
- Only MuMu Store is a known ad owner. Another emulator's ad shows up as an
  `overlay_unknown` event naming the window; report it so it can be added.
- The fleet table for Tiers 1–13 and 15–24 comes from published data, not
  from runs of this app; report a mismatch with the wave you saw and the
  tier, and override it meanwhile with `fleet.by_tier`.
- Optional or locked features can remain unverified. A Ready run does not mean
  every possible screen or emergency effect has been exercised.
- Stop automation before manually using the emulator. Stop now preserves a live
  battle; it does not surrender it.

The tagged prerelease is built by `.github/workflows/release.yml`. A full scan
on a pristine Windows installation remains a beta validation task.
