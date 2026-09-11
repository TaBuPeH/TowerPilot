# Reference and runtime geometry

The shipped `backend/player/bootstrap_manifest.json` remains text only and is
authored at 1080 × 2560, 360 DPI. Its `geometry_fields` table explicitly declares
the axes for reference coordinate fields. `manifest(Display(width, height, dpi))`
returns a fresh transformed copy; x values use width/1080, y values use
height/2560. Source-art crop pixels, thresholds, timing and counts do not scale.
DPI identifies a rendering context; it is not another coordinate multiplier.
Applying a transform to an already transformed manifest is rejected.

`player.geometry.Resolver` combines that reference with observations. Account-local
`learned_manifest.json` contains separate `targets` (captured templates) and
`runtime_targets` (persistent measured controls). Runtime rows contain screen,
native rectangle, display including DPI, account/connection context, manifest
content hash, verification count, confidence, source and anchor identity.
Mismatched contexts are ignored. Old template rows never become click permission.

Only explicitly stable controls persist. Scrolled lists, changing menu rails and
temporary HUD controls remain in memory for the current frame token. A new token
expires them. A measured rectangle must pass current-screen verification before
the resolver returns its tap point. Reference rectangles only define searches.

The manifest navigation driver now records the two-frame control proof through
the resolver before input. Successful fixed bottom-navigation bindings persist;
subsequent scans first match installed artwork inside that measured region, then
fall back to the reference search if necessary. Uncertain destination transitions
still stop. Runtime bindings do not become new crop targets.

## Scan wiring and remaining migration

Calibration now measures the native frame and logical display DPI before choosing
its storage path. It binds the transformed manifest for the worker; subsequent
captures enforce that measured size, so a mid-scan resolution change still stops.
Different resolution/DPI combinations use separate calibration folders. The
reference display retains its previous folder for compatibility.

`resolution_profiles.json` supplies one default x/y multiplier per display/DPI,
with explicit outliers. Overrides operate on reference values, never on an
already-scaled value. Source-art pixels are not overrideable. The 1080x1920@280
profile includes measured Home/navigation, Cards and Modules exceptions. Cards keeps its
header and tile sizes but has a shorter inventory viewport. Its overlap proof
accepts two agreeing sibling cards instead of requiring two vertical rows.

Live checks at 1080x1920@280 verified all 31 cards (22 active, 5 mastered),
6 equipped modules and all 97 inventory copies across 20 pages. The game shows
103 modules total. Neither inventory scan changed equipment. This is not yet a claim that every screen or
farming action works at that display. Remaining work:

1. Validate every run action with the display context now loaded by `bind_device`,
   which is shared by setup and runner entry points.
2. Route all remaining geometry through that context and migrate remaining
   reference constants and config rectangles into declared manifest fields.
3. Complete observed profile exceptions for menus, other menus and battle HUD.
4. Validate recognition and clicks on changed MuMu dimensions and DPI, then on
   BlueStacks, before advertising resolution-independent setup.

Run types and user settings are preserved. No game artwork is shipped or deleted
as part of this geometry change.

The worker freezes the transformed manifest at display binding, rather than
reloading profile edits during a scan. Callers receive copies of that snapshot.
Runtime readers use the manifest's `runtime_layout` regions and tab points.
Instance `runtime_geometry` overrides require matching display, manifest hash,
and profile hash. Legacy per-instance measurements are retained only on the
reference display. An old wall rectangle is not reused on a changed display.

Module grid search bounds, tile dimensions, columns, scroll gestures and dialog
search regions now live in `module_inventory`. Dialog close detection has no
blind coordinate fallback. Short profiles keep an overlapping row when paging;
scroll movement is measured from matching content before assigning list positions.

## Live validation, 2026-09-11

The fresh 1080x1920@280 UI scan completed Cards and Modules, then failed at
the Guild Members tab. Guild tabs retain native vertical placement, and the
return control is bottom-anchored. The profile now records those exceptions.
A UI-triggered observation captured the controls and the verified clicker
returned to Home. Other short-layout screens remain unvalidated; do not describe
this profile as fully supported yet.

MuMu was shut down and changed to 1080x2560@360 through its manager. Chrome Setup
launched the game and verified the actual frame. Full setup completed 46 steps
and checked 24 screens; the coin readiness check passed. Chrome Start launched
Tier 14 with global preset Farm Run. Ad Gems collection was confirmed, timed
orbit taps ran, and upgrade purchases and daily reward checks were observed.
Late-wave Nuke and rescue behavior were not validated in this pass.

The first runner attempt captured the Android launcher because its dimensions
now matched the game. Capture now resolves the game display before its first
implicit frame, including when no size mismatch occurs, and retains the matching
input display. Regression coverage checks same-size launcher/game selection.
The verified clicker also accepts native frames of other sizes and bounds its
search to the actual frame instead of rejecting everything except 1080x2560.
