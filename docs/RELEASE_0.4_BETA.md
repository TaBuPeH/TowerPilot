# 0.4-beta

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

## Changes since 0.3-beta

- **The Guild reward flow no longer strands the run.** Opening the Guild
  screen to collect weekly progress crashed on accounts whose setup had not
  cut the milestone padlock image, and the run then sat on the Guild screen
  until a person returned it (the lit badge reopened it every five minutes).
  Three fixes, all in `backend/interactions/missions.py`:
  - The padlock is **learned during play**: on the Guild track the flow cuts
    it from a locked box it already sees, and accepts the cut only when the
    same glyph matches another locked box on that frame. Missing target
    only, never replaces a file; the weekly-chest track uses the same image.
    Events: `chest_lock_captured`, `guild_lock_unlearned`.
  - Without the padlock image the claim pass is **skipped**
    (`guild_claims_skipped`) and the flow walks to its return taps, the same
    contract the Daily Missions chest track already had.
  - A reward flow that raises anyway is dropped with a `mission_crash` event
    (trace and screenshot) and the flow's own exit, the "Tap To Return To
    Game" strip, is taken. Stuck recovery still never touches a screen the
    bot did not open.
- **Global behaviour: fire Demon Mode whenever it is ready**
  (`demon_mode_always`, off by default, Reward collection section of the
  profile editor). For the Demon Mode kill quests: a Demon Mode held back for
  a wall rescue earns no kills. Never during the intro sprint, every tap
  confirmed by the button's cooldown dim, attempts 15 seconds apart. Readiness
  requires the Demon Mode button and an account that owns the ability.
- Release workflow: an existing release is updated in place instead of
  failing; the tag fetch no longer rejects annotated tags.

## Carried from 0.3-beta

- Fleet spawn waves per tier, picked from the run's blueprint
  (`backend/scheduling/fleets.py`, override with `fleet.by_tier`).
- Emulator ad overlays are dismissed on window-list evidence, never refused
  on sight; unknown overlays are refused by name.
- Quest CLAIM and END ROUND are learned during play (missing target only,
  never replacing a file) instead of holding setup or a run.
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
- The Guild badge can read lit while nothing is claimable. The flow then opens
  the Guild screen every five minutes and returns within seconds; that is the
  designed badge check, not the stranding above.
- The padlock is learned only while at least two milestone boxes are locked;
  until then the guild claim pass is skipped and logged.
- The fleet table for Tiers 1–13 and 15–24 comes from published data, not
  from runs of this app; report a mismatch with the wave you saw and the
  tier, and override it meanwhile with `fleet.by_tier`.
- Optional or locked features can remain unverified. A Ready run does not mean
  every possible screen or emergency effect has been exercised.
- Stop automation before manually using the emulator. Stop now preserves a live
  battle; it does not surrender it.

The tagged prerelease is built by `.github/workflows/release.yml`. A full scan
on a pristine Windows installation remains a beta validation task.
