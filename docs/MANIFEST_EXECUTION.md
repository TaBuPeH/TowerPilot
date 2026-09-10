# Manifest execution

The text-only source is `backend/player/bootstrap_manifest.json` (version 11).
It describes 59 screen/context definitions and 83 transitions. The normal
Remap worker currently executes 39 safe navigation transitions. Describing a
screen does not mean all of its controls have been made executable.

## What the user's Remap button does

1. Check the emulator and native display contract.
2. Extract/cache artwork from the installed game and bind sprite names to the manifest.
3. Verify Home before any navigation. Available required Home targets must all pass.
4. Locate each control in its bounded native search area and verify its identity
   and position in two frames. Tap once; verify the destination before collecting.
5. Explicitly select tabs in Guild, Events, Perks and Themes: opening a menu can
   restore the last tab and scroll position.
6. Read all card inventory pages, equipped module slots, module inventory pages,
   and the supported scrollable lists. Keep progress visible throughout.
7. Return Home and save discoveries under the selected account/emulator.

The main UI keeps Remap and its vertical progress first, with saved knowledge
separate from choices for targeted repair. Advanced sections remain expanded.
The live preview refreshes during the worker. Scan progress includes module
pages/tiles and collection pages, with confirmed endpoints reported separately.

## Actual connected implementation

`player.mapping_session.Session` adapts the calibration worker to
`player.manifest_driver.Driver`. It supplies observations, extracted-art or
verified-anchor control matching, taps, discovery collection and progress.
The normal worker executes the manifest's ordered safe route list. The generic
graph walker exists but is not the normal worker's traversal mode.

Each confirmed transition records its source screen, destination, native rect,
identity, image hash and manifest version in account-local
`navigation_manifest.json`. Control crops use versioned paths such as
`mapping/v11/route_0.png`, so inserting a route cannot reuse an older route's image.
Pixels, extracted assets, account values and screenshots never enter the release.

Feature matching handles detailed artwork. Flat navigation sprites use an
explicitly enabled silhouette check; white Close glyphs exclude the colored
backplate. Both operate only within their declared search areas. Text anchors
are a setup fallback. Native cropped text is tried before enlargement; verified
small anchor images then replace repeated OCR for stable screen/control checks.
A dimmed parent screen or a different selected tab invalidates that proof.
Runtime control matching does not introduce general OCR navigation.

Module inventory icons are captured before clicking. Their enlarged descriptor
icons must match at a stable position in two consecutive frames before reading
the adjacent name and rarity. This tolerates the opening animation without
lowering the matching threshold. The normal scan reads occupied primary/assist
slots and the full inventory without unequipping anything. The separate
explicit equipment scan retains its snapshot/restore journal.

`player.collection_scan` uses a manifest viewport, waits for scrolling to settle,
parks at the top, saves overlapping native pages and local text positions, and
requires two unchanged downward gestures before declaring the end. It returns
the list to the top before fixed-region captures. A bounded incomplete scan
remains incomplete. No shop purchase or quest claim is part of this scan.

## Live verification on MuMu

The September 9 pass identified 31 cards. The module pass identified 67 inventory
copies plus 6 equipped modules, matching the game's 73 total; it read 39 distinct
inventory module names. No equipped slot changed.

Individual route tests successfully visited Guild/Guardian/Store, Events/Missions/
Bots/Event Shop, the main Store, preset picker, Daily Missions, Battle History,
Global Presets, all three Perks tabs, Themes, Labs and its research selection.
Guild Store, Event Shop, main Store and Battle History each produced four page
captures; Daily Missions produced two. These counts are account observations,
not shipped assumptions.

The final pass was launched by clicking **Remap this game** in the external
Chrome dashboard. It completed **44/44 steps**, checked **24 screens**, skipped
no routes, reported **0 items needing attention**, and returned Home. The final
Python suite passed **1,056 tests** with 3 skipped; all **18 frontend tests** passed.

## Boundaries that remain

- Native 1080 x 2560 at 360 dpi remains the supported rendering contract.
  Different emulator connections have isolated local calibration; this work does
  not provide arbitrary resolution scaling.
- Tournament-specific entry/reward screens stay conditional. Normal Remap
  neither buys a ticket nor starts a tournament. In-run tournament HUD/menu
  definitions share the normal battle definitions with contextual differences.
- Battle and rare-dialog captures remain a separate explicit scan. Normal Remap
  does not start a battle, surrender a run, buy items or change loadouts.
- The wider narrated catalogue contains additional read regions and collections.
  Its coverage view must continue distinguishing documented geometry from
  locally verified executable controls. Collection page text is local evidence,
  not yet a universal structured quest/shop parser.
- A control that cannot be verified stays unverified; an uncertain destination
  stops input. No missing artwork or OCR failure authorizes a coordinate-only tap.
