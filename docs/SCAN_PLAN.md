# Obtaining recognition images from the game

The application ships no game images. This includes buttons, digits, fonts,
headers, equipment, screenshots and the former application image assets.
Game images are captured locally from the installation being configured.
There is no shared-image fallback and no image download step.

## Starting with zero images

1. Connect the emulator in Setup and check the native portrait capture:
   1080 × 2560 at 360 dpi. Do not scale captures to another size.
2. Open Calibrate. “What we know” starts empty. In “What to scan next”, the
   Scan plan lists every reference target, its acquisition screen, local
   capture status and whether the selected run needs it (possibly as one
   of several alternatives).
3. Open Home and select **Extract artwork and verify setup**. Allow navigation for this
   process. Setup reads the game's installed APKs through the ADB socket,
   reassembles numbered Unity asset chunks, and extracts Sprite/Texture2D artwork
   locally. Exact source names in the shipped manifest map artwork to recognition
   targets. The starter scan then verifies native geometry, readable screen anchors,
   undimmed controls and each destination before accepting captures. It visits
   Cards, Modules, Guild/Guardian/Guild Store, Events/Bots, Store and the preset
   picker, then returns Home. It also reads visible preset names.
4. No existing images or battle digits are needed for this starter scan. The
   Modules step crops the visible inventory before opening its first tile,
   registers that tile's artwork in the description, reads the adjacent name
   and rarity, captures Close/Equip without activating Equip, and closes it.
   This is a navigation bootstrap, not a complete inventory scan. Full basic
   inventory scans use the same tile-to-description identity check. Equipped
   appearances remain separate from inventory appearances.
5. Use the basic scan to discover actual card/global/category preset names
   and the module inventory. Use the separate equipped-module phase only
   after its navigation controls and the basic module scan are available.
   It records all slots, unequips, indexes, restores and verifies the setup.
6. Gather uncommon states as they occur in normal play. A missing target is
   not permission to buy, upgrade, shatter, transfer levels, enter a tournament
   or surrender. Skip unavailable features and disable their run options.

## Acquisition order

| Step | Screens and images | Who operates the game |
|---|---|---|
| Home | Battle marker, navigation, preset picker/header/close/None | Starter scan; never presses Battle |
| Cards | Cards header, active label, card art; later preset names | Starter scan captures labels and reads preset names |
| Modules | Modules marker and detail/Equip/close controls | Starter scan opens one inventory description; no equipment changes |
| Guild / Guardians | Guild tabs, headers, chips, equipped/empty markers | Starter scan visits supported unlocked menus |
| Events / missions | Event/Bots tabs, mission pages and available claims | Starter scan visits Events/Bots; claims remain untouched |
| Store | Section headings, free/owned controls, store-font characters | Starter scan visits Store; no purchase |
| Normal battle | Wave digits, value characters, weapon icons/toggles, stats, abilities, floating bonuses | User starts/plays; setup observes |
| Results / dialogs | Results, retry/return, confirmation titles and controls | Observe naturally occurring states; no forced run end |
| Tournament | Entry/tickets, heats, leaderboard, result screens | User controls every entry and confirmation; setup observes |
| Overlays | Ad close and return controls | Observe only; no unknown-overlay taps |

`backend/player/scan_targets.json` is the text-only per-image catalogue.
`backend/player/scan_plan.py` assigns each reference to an acquisition step.
The dashboard shows reference crop dimensions when known; these are guidance,
not coordinates, resize formulas or proof that a crop is correct. Legacy
appearance variants are alternatives, not a demand to reproduce every skin
or emulator rendering.

## Verification is separate from capture

A saved crop means “captured”, not “verified”. Check the named control on a
second frame of the same screen at native size, and check a visually similar
negative screen. The crop must identify the intended object at the runtime
threshold, in the expected region, and must not identify a different object.
Font/glyph and duplicate-module checks must account for legitimate repeats.
A self-match against the source alone cannot prove recognition.

`backend/player/bootstrap_manifest.json` ships native coordinates, OCR anchor
regions, safe transitions and module descriptor registration bounds. Navigation,
module slot geometry and preset bands share this source with existing code.
`bootstrap.py` is called only through the human-started calibrator. It does not
add OCR to runtime loops. Native rail positions are currently conservative:
shifted or unavailable icons are skipped, not searched by speculative taps.
Other resolutions must be configured through Setup; captures are never resized.

Every starter cut is checked against a second frame and for uniqueness. Reports
store an image hash; the plan only says verified while that exact image remains
on disk. This verifies the observed appearance, not every possible game state.
Battle digits, chest locks on missions, rare overlays and tournament controls
still need observation during normal play. Missing runtime requirements keep
Control/runs gated. A stopped/failed scan leaves a precise step and reason; open
Home before retrying. Already saved images are kept unless replacement is chosen.

## Release boundary

All raster image extensions and local template/capture directories are ignored.
Release tests reject image assets in the current tracked working tree. Synthetic
fixtures are generated in memory or temporary test directories. The tray mark
is original programmatic geometry; it does not load game artwork.

Removing files from the current release tree does not rewrite earlier Git
commits. Those commits still contain the previously committed images. Publishing
this repository's history requires a separate history cleanup or a new clean
repository; do not treat an image-free working tree as an image-free Git history.

## Installed artwork and screen verification

`player/asset_library.py` reads only the selected emulator's installed game
package. UnityPy is pinned in requirements. Local caches include installation
identity, APK hashes, decoder version, object source identities and image hashes.
A changed game version/emulator/package path creates a new cache. Corrupt or
incomplete caches are rebuilt. Numbered `.splitN` files are joined numerically;
missing chunks fail extraction. Asset names never become output paths.

`asset_bindings` in `bootstrap_manifest.json` maps exact source sprite names to
native screen search regions. Extracted files are source artwork, not runtime
recognition templates or evidence of account ownership. `asset_verify.py` locates
source artwork using feature registration; the native rendered crop must also
match a second frame and pass the normal template quality checks before use.
Guild/Events navigation uses the measured source match location, so a tile can
move within its native rail region. No source match means no coordinate guess.

Text labels and composed controls continue to use verified screen captures;
empty font texture placeholders have no stored pixels to extract. Mapped artwork
not confirmed on the scanned screens stays explicitly unverified. Battle and
mission-only assets remain pending until those screens are observed. Extraction
never starts a battle or treats every packaged module/chip as owned.

The frontend reports image objects, distinct images, mapped targets and confirmed
screen matches separately. All APKs, source PNGs, indexes and screen evidence stay
in ignored local storage. Only text manifests, code and dependencies ship.

## Watching scan progress

The dashboard shows a progress bar and ordered steps (horizontal on desktop,
vertical on narrow screens). Each step reports waiting, running, done, skipped,
needs attention, failed or stopped. Skipped steps advance the route count but
remain visibly distinct from successful captures. Progress measures steps, not
an estimate of remaining time.

Starter scans persist timestamps, per-step messages and activity history in the
local calibration state, so refreshes preserve the record. Basic scans retain
phase start/end times and show the current inventory or restoration action.
Connection failures retain the last known steps and show a reconnecting message.
Older completed reports have no retrospective step history; the next scan
records it. No emulator actions are needed to inspect the history.

## Observe the current screen

The **Analyze this screen** action captures one native screenshot without game input.
It reads screen anchors, compares all distinct images from the current installation's
local artwork library, and shows numbered matches on that exact snapshot.
Manifest roles require the expected screen, source name and measured search region;
other matches show source artwork names only. Competing appearances remain uncertain.
The analysis does not assert ownership, enable Control, or write runtime templates.
Feature matching may miss plain icons and repeated copies. Unknown screens remain
unknown while artwork observations can still be inspected. Reports and screenshots
stay in the account's ignored calibration directory. Extraction steps use a vertical
scrolling history at every viewport size.

## Card inventory

Cards and presets now includes the inventory, in both the basic card phase and
the starter menu walk. The scanner verifies Cards, finds the top scroll boundary,
reads complete named tiles on overlapping pages, and checks the bottom boundary.
It pairs each independently reread label with the stationary icon beneath it.
It scrolls only the inventory and never taps a card or changes the active preset.
Native card geometry lives in bootstrap_manifest.json. New card images use the
local cards/catalogue namespace; existing Death Ray and Extra Orb roles retain
their runtime paths. Per-page evidence and card_manifest.json remain local.
Unreadable tiles, moving pages, unsupported geometry and page limits prevent
completion; partial observations remain available for review.

Card page coverage is confirmed by matching at least two complete cards between
consecutive settled frames. Scrolls use short drags; reaching the bottom without
verified overlap does not complete the inventory. The dashboard distinguishes
reference-capture counts from the discovered inventory and preset counts.
A fresh scan clears only the selected phase statuses.
