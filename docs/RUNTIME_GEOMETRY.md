# Reference and runtime geometry

The shipped `backend/player/bootstrap_manifest.json` remains text only and is
authored at 1080 × 2560, 360 DPI. Its `geometry_fields` table explicitly declares
the axes for 574 coordinate fields. `manifest(Display(width, height, dpi))`
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

## Remaining resolution migration

This is not yet end-to-end arbitrary-resolution support. The full scanner still
has its existing native-display preflight and legacy geometry consumers in
capture, flow capture, inventory, HUD and runtime configuration. Those must use
the transformed manifest before that preflight can be removed. No changed-DPI
live scan has been validated by this change. In particular:

1. Detect actual game-display dimensions/DPI through the existing socket ADB
   connection, then bind one immutable display context for the scan/runner.
2. Route all scanner/search geometry through that context and migrate remaining
   reference constants and config rectangles into declared manifest fields.
3. Separate template/image storage by rendering context, so an old-size image
   cannot be reused merely because its filename exists.
4. Validate recognition and clicks on changed MuMu dimensions and DPI, then on
   BlueStacks, before advertising resolution-independent setup.

Run types and user settings are preserved. No game artwork is shipped or deleted
as part of this geometry change.
