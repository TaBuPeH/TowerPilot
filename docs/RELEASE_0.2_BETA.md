# 0.2-beta

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

## Changes since 0.1-beta

- **Emulator ad overlays are dismissed, not refused.** MuMu Store draws a
  fullscreen promo over the game a minute or two after boot, invisible from the
  Windows desktop. The starter scan used to stop with "The game must be visible
  with no other app or overlay covering it". Now every stage that trusts the
  screen reads the emulator's own window list first, closes or force-stops a
  known ad owner, verifies it is gone and continues. The setup preflight, each
  scanner step and the live orchestrator (once a minute while the screen is
  unrecognised) all sweep. An overlay the app cannot name is still refused, by
  window name, so you know what to close. Nothing is ever tapped blindly.
- **Missing quest CLAIM no longer holds preparation.** The CLAIM button only
  exists while a quest is finished, so "Prepare missing controls" and Full setup
  record it as optional instead of ending in "needs attention" or stopping on
  a mission-screen route it could not verify. The quest flow cuts the image
  itself the next time it claims a quest (missing target only, never
  replacing a file), the same way Ultimate Weapon labels are learned during play.
- **END ROUND is learned during play.** The side menu only reads "open" once
  its exit button image exists, and setup cuts END ROUND only on the tier it
  surrendered its own run at. A farm on a higher tier never had it, so the
  8h quest, guild and event flows never ran. The runner now cuts it from the
  open menu itself (one OCR every 10 seconds while missing), verified live on a
  tier 14 farm: END ROUND, then the quest visit, then CLAIM, all learned in
  under a minute.
- **No captured image ever ships.** Every image comes from your own game on
  your own machine; the packages carry the scan and learner code only.
- **Full source in every package.** Both ZIPs now carry the tests, the agent
  rule book (`CLAUDE.md`, `AGENTS.md`), the `.claude/` skills, agents, hooks and
  workflow helpers, the project-local `.codex/config.toml` and the release
  workflow, so an extracted folder is also a working checkout.

Included: text manifests, Farm/Tournament/Shard recipes, local artwork extraction,
screen mapping, account separation, and reward collection. No game artwork,
screenshots, installed game packages, credentials, or personal run settings ship.

## Beta limits

- The portable build is an extracted folder, not an installer. Keep all files
  together in a writable location. Local account data lives in that folder.
- Resolution scaling is experimental. Other resolutions, including 1080 × 1920,
  have not completed full validation. Use the reference display above.
- MuMu native-resolution coin farming has live validation. BlueStacks has been
  tested but has needed scan retries/targeted repairs.
- Only MuMu Store is a known ad owner. Another emulator's ad shows up as an
  `overlay_unknown` event naming the window; report it so it can be added.
- Optional or locked features can remain unverified. A Ready run does not mean
  every possible screen or emergency effect has been exercised.
- Stop automation before manually using the emulator. Stop now preserves a live
  battle; it does not surrender it.

The tagged prerelease is built by `.github/workflows/release.yml`. A full scan
on a pristine Windows installation remains a beta validation task.
