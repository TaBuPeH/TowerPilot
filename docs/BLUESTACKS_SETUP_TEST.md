# BlueStacks clean calibration test — 2026-09-10

The MuMu Tier 14 battle was surrendered with the user's authorization at wave
1887, returned to Home, and instance 1 was shut down. Run configurations remain
in their original profiles. No run configuration was deleted.

Automatic approval review rejected calibration deletion. Old MuMu files and
managed ADB remain on disk; this is **not** a verified no-ADB installation test.
BlueStacks uses its own empty account calibration directory and does not fall
back to MuMu captures.

Through external Chrome, selected BlueStacks (fresh), clicked Start it, then
Use this one. The existing connection workflow did not expose a way to open the
game after adopting an already-running emulator. Added Open The Tower for the
configured running emulator, using the existing launch/boot pipeline.

BlueStacks loaded the cloud session saved at 21:19. Confirmed Home, Tier 14,
1080×2560 native screenshot, and no active battle before starting Remap.

First pass extracted installed artwork and scanned Cards. Modules stopped at
inventory tile 77: Solar Dyson Sphere. The inventory crop's thin outer edge
prevented feature registration to the enlarged description. Excluding five
edge pixels yielded 38 inliers; the fallback restores the full crop rectangle
using the measured transform, retains scale/bounds guards, and still requires
two consistent frames. The saved real screenshot now reads Solar Dyson Sphere,
rare. Regression tests reject an implausible scale.

The first pass therefore failed; do not describe this test as first-pass ready.
A retry was started from Home. The current Remap path repeats Cards and Modules;
a clearer persistent resume path remains an onboarding improvement.

The new Event menu icon also needed a text-only generic-template allowlist
entry. Manifest validation now passes. No game image is added to the release.

## Final result

The retry completed 46/46 steps and checked 24 screens. It recorded 31 cards,
six equipped modules and 97 inventory copies. Highest unlocked tier was detected
as 19; setup ended its test battles and restored Home at Tier 14.

Copied the existing My Tower blueprint and policy settings plus loadouts to the
BlueStacks profile, retaining BlueStacks' freshly observed player data. The
original profiles remain preserved. This keeps the user's configured Farm,
Shard and Tournament behavior rather than the older BlueStacks Tier 1 starter.

The configured farm exposed one missing shopping label: Super Crit Chance.
Fast upgrade-panel flings had clipped its wrapped text. Setup now uses slow
overlapping strokes, and missing stat labels required by configured runs also
trigger the short battle repair even when all digits already exist. Through
Chrome, Prepare this run captured the missing label without repeating inventory
scans and returned Home at Tier 14. Final coin readiness is true, missing list
empty. Automation is stopped. Long-running farming on BlueStacks is not yet
verified by this setup test.

## MuMu regression rescan

Switched back to My Tower on MuMu through external Chrome, launched MainTower,
adopted its connection and used Open The Tower. Verified Home at Tier 14 before
clicking Remap and accepting the preparation dialog. The rescan completed
46/46 steps and checked 24 screens without repair or restart. Modules passed
the item that had failed on BlueStacks; 31 cards and six equipped modules were
recorded. Final coin readiness is true with no missing requirements. All
non-player profile fields compare equal to the pre-scan snapshot, confirming
run settings were preserved. Automation is stopped and Control is open.

This was a rescan using MuMu's existing calibration, not a clean installation.
Existing battle captures were reused, so this does not claim a fresh battle
capture or a long-running farm test. The 36 focused descriptor, flow-capture,
mapping-session and manifest tests passed before the live rescan.
