"""LF-2 (2026-10-12 Learn-screens run): scan_targets.json and
generic_templates.json list guardian/chip_bounty.png, the farming set
(tourney.CHIPS_OUT) taps Bounty, yet the manifest bound only Attack, Ally,
Fetch, Scout and Summon, so setup never mapped or cut it.

The installed art has no sprite called "Bounty". Every Sprite in the run's
asset library was searched with asset_verify.locate on the run's native
Guardian frame (logs/main/20261012_145207_calibrate_guardians_no_pills.png):
exactly one, 'Steal', matched the Bounty inventory slot (46 inliers), the
same sprite matched the equipped Bounty slot (31 inliers), and inside the
chips' shared search it located at [801, 1232, 129, 96]. On that frame every
bound chip sat 120 px above its reference_rect, so Bounty's reference_rect is
that rect moved down by the same 120 px.
"""

import paths as test_paths
import json
from pathlib import Path

from player.bootstrap_layout import manifest, writable_targets

BOUNTY = "guardian/chip_bounty.png"
LIVE_RECT = [801, 1232, 129, 96]      # 'Steal' on the run's Guardian frame
LIVE_DY = -120                        # bound chips' live rect minus reference_rect, same frame


def _center(rect):
    x, y, w, h = rect
    return x + w / 2, y + h / 2


def test_every_guardian_chip_the_scan_lists_has_an_artwork_binding():
    targets = json.loads((test_paths.BACKEND / "player" / "scan_targets.json").read_text(encoding="utf-8"))
    chips = {t["rel"] for t in targets if t["rel"].startswith("guardian/chip_")}
    bindings = manifest()["asset_bindings"]
    assert BOUNTY in chips
    assert {rel for rel in chips if bindings.get(rel, {}).get("screen") != "guardians"} == set()
    assert BOUNTY in writable_targets()


def test_the_bounty_binding_is_built_like_its_siblings_from_the_run_frame():
    bindings = manifest()["asset_bindings"]
    bounty, attack = bindings[BOUNTY], bindings["guardian/chip_attack.png"]
    assert bounty["names"] == ["Steal"]
    assert bounty["locked_art"] == attack["locked_art"] and bounty["search"] == attack["search"]
    assert bounty["locate_navigation"] is False
    x, y, w, h = LIVE_RECT
    assert bounty["reference_rect"] == [x, y - LIVE_DY, w, h]
    # third column of the first inventory row: Attack/Ally's row, Scout's column
    cx, cy = _center(bounty["reference_rect"])
    for sibling in ("guardian/chip_attack.png", "guardian/chip_ally.png"):
        assert abs(cy - _center(bindings[sibling]["reference_rect"])[1]) <= 10, sibling
    assert abs(cx - _center(bindings["guardian/chip_scout.png"]["reference_rect"])[0]) <= 10
    fields = manifest()["geometry_fields"]
    assert fields["/asset_bindings/guardian~1chip_bounty.png/search"] == "xyxy"
    assert fields["/asset_bindings/guardian~1chip_bounty.png/reference_rect"] == "xyxy"
